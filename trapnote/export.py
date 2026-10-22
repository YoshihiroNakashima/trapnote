"""CSV の書き出しと取り込み

書き出し（1 行 = 1 対象 × 1 種）: 先頭 5 列は PRM_N の既存 CSV と同じ Season, Station, File, DateTime, Species。
  Species は種リストの CSV 表記（Deer、Boar、Empty、Area など）。DateTime は YYYY/MM/DD HH:MM:SS
取り込み: 既存の CSV（Season/Station/File/Species など）の種を、未確認の判定（由来 csv）として入れる"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from collections import Counter

from . import media
from .db import Database, now_iso
from .judge import build_where, order_sql, update_agree
from .paths import rel_key
from .species import read_text_any

log = logging.getLogger(__name__)
BASE_COLS = ["Season", "Station", "File", "DateTime", "DateTimeCorrected", "Species"]
ITEM_COLS = ["Path", "OriginalFile", "Kind", "DateTimeSource", "DateTimeChanged", "TimeOffset_s", "TimeNote", "Duration_s", "Width", "Height", "Status", "Verified", "VerifiedBy", "VerifiedAt",
             "Assignee", "Favorite", "Note", "Flag", "DetAnimal", "DetPerson", "DetVehicle", "DetMaxConf", "DetModel", "AutoJudgement", "Agree", "Recheck", "TrainExclude"]    # Det* は検出の要約、AutoJudgement はモデルの判定、Agree は人の判定との比較（match / partial / mismatch）、Recheck は食い違いを見直して確定した（1/0）、TrainExclude は学習から外す（1/0）
SP_COLS = ["Species_ja", "Species_sci", "Confidence", "Origin", "AutoSpecies"]


def _sniff(text: str) -> str:
    head = text[:4096]
    try:
        return csv.Sniffer().sniff(head, delimiters=",\t;").delimiter
    except Exception:
        return "\t" if head.count("\t") > head.count(",") else ","


def _iter_items(db: Database, f: dict, sort: str = "station", desc: bool = False, chunk: int = 2000):
    where, p = build_where(f)
    _, order = order_sql(sort, desc)
    off = 0
    while True:
        rows = db.q(f"SELECT * FROM items WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*p, chunk, off))
        if not rows:
            return
        ids = [r["id"] for r in rows]
        qs = ",".join("?" * len(ids))
        js: dict[int, list[dict]] = {}
        for j in db.q(f"SELECT * FROM judgements WHERE item_id IN ({qs}) ORDER BY id", ids):
            js.setdefault(j["item_id"], []).append(dict(j))
        fv: dict[tuple, dict[str, dict[int, str]]] = {}
        for v in db.q(f"SELECT * FROM field_values WHERE item_id IN ({qs}) ORDER BY seq, id", ids):
            fv.setdefault((v["item_id"], v["judgement_id"]), {}).setdefault(v["field_key"], {})[v["seq"]] = v["value"] or ""
        auto: dict[int, list[str]] = {}
        for a in db.q(f"SELECT item_id, species, confidence FROM auto_results WHERE item_id IN ({qs}) ORDER BY id", ids):
            auto.setdefault(a["item_id"], []).append(f"{a['species']}:{a['confidence']:.2f}" if a["confidence"] is not None else a["species"])
        for r in rows:
            yield dict(r), js.get(r["id"], []), fv, auto.get(r["id"], [])
        off += chunk


def _join(vals: dict[int, str] | None) -> str:
    if not vals:
        return ""
    n = max(vals.keys()) + 1
    return ";".join((vals.get(i) or "") for i in range(n))


def _det_cols(it: dict) -> list:
    """検出の要約（det_json）から CSV の列へ"""
    if not it.get("det_json"):
        return ["", "", "", "", ""]
    try:
        d = json.loads(it["det_json"])
    except Exception:
        return ["", "", "", "", ""]
    mx = max(d.get("max_animal") or 0, d.get("max_person") or 0, d.get("max_vehicle") or 0)
    return [int(bool(d.get("animal"))), int(bool(d.get("person"))), int(bool(d.get("vehicle"))), f"{mx:.3f}", d.get("model") or ""]


def _auto_cols(it: dict, sp: dict) -> list:
    """モデルの判定（CSV 表記を ; で）と人の判定との比較"""
    try:
        a = json.loads(it["auto_json"]) if it.get("auto_json") else None
    except Exception:
        a = None
    codes = [x["species"] for x in (a or {}).get("species", [])]
    return [";".join(sp.get(c, {}).get("csv_label") or c for c in codes), {1: "match", 2: "partial", 3: "mismatch"}.get(it.get("agree"), ""), int(it.get("recheck") or 0), int(it.get("train_exclude") or 0)]


def _pattern_cols(db: Database) -> list[str]:
    """パターンの season / station 以外の項目名（site、camera など）"""
    names = []
    for r in db.q("SELECT pattern_json FROM items WHERE pattern_json IS NOT NULL AND pattern_json != '{}' LIMIT 200"):
        try:
            for k in json.loads(r["pattern_json"]).keys():
                if k not in names:
                    names.append(k)
        except Exception:
            pass
    return names


def export_csv(project, path: str, f: dict, lang: str = "ja", include_unjudged: bool = True) -> dict:
    """長い形式の CSV。返り値は行数など"""
    db = project.db
    sp = project.species.by_code()
    fields = project.fields.fields
    item_fields = [x for x in fields if x["scope"] == "item"]
    sp_fields = [x for x in fields if x["scope"] == "species"]
    extra = _pattern_cols(db)
    header = BASE_COLS + extra + ITEM_COLS + SP_COLS + [x["key"] for x in item_fields] + [x["key"] for x in sp_fields]
    n_rows = n_items = 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for it, js, fv, auto in _iter_items(db, f):
            n_items += 1
            pat = {}
            try:
                pat = json.loads(it.get("pattern_json") or "{}")
            except Exception:
                pass
            base = [it.get("season") or "", it.get("station") or "", it["name"], it.get("datetime") or "", it.get("datetime_corrected") or it.get("datetime") or ""]
            ex = [pat.get(k, "") for k in extra]
            icols = [it["rel_path"], os.path.basename(it["orig_rel_path"]) if it.get("orig_rel_path") else "", it["kind"], it.get("datetime_source") or "",
                     1 if it.get("datetime_corrected") else 0, it.get("time_offset_sec") if it.get("time_offset_sec") is not None else 0, it.get("time_note") or "",
                     f"{it['duration']:.2f}" if it.get("duration") is not None else "", it.get("width") or "", it.get("height") or "",
                     it["status"], 1 if it["status"] == "verified" else 0, it.get("verified_by") or "", it.get("verified_at") or "",
                     it.get("assignee") or "", int(it.get("favorite") or 0), it.get("note") or "", it.get("flag") or ""] + _det_cols(it) + _auto_cols(it, sp)
            ifv = fv.get((it["id"], None), {})
            ivals = [(_join(ifv.get(x["key"])) or (x.get("default") or "")) for x in item_fields]
            if not js:
                if include_unjudged:
                    w.writerow(base + [""] + ex + icols + ["", "", "", "", ";".join(auto)] + ivals + [""] * len(sp_fields))
                    n_rows += 1
                continue
            for j in js:
                s = sp.get(j["species"], {})
                sfv = fv.get((it["id"], j["id"]), {})
                svals = [_join(sfv.get(x["key"])) for x in sp_fields]
                w.writerow(base + [s.get("csv_label") or j["species"]] + ex + icols +
                           [s.get("name_ja") or "", s.get("name_sci") or "", f"{j['confidence']:.3f}" if j.get("confidence") is not None else "",
                            j.get("origin") or "", ";".join(auto)] + ivals + svals)
                n_rows += 1
    os.replace(tmp, path)
    return dict(path=path, rows=n_rows, items=n_items)


def export_records_csv(project, path: str, f: dict) -> dict:
    """＋で増やした記録（repeat の項目）を 1 行 = 1 記録で書く"""
    db = project.db
    sp = project.species.by_code()
    rep = [x for x in project.fields.fields if x["repeat"]]
    keys = [x["key"] for x in rep]
    header = BASE_COLS + ["Path", "Seq"] + keys
    n = 0
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for it, js, fv, _ in _iter_items(db, f):
            base = [it.get("season") or "", it.get("station") or "", it["name"], it.get("datetime") or "", it.get("datetime_corrected") or it.get("datetime") or ""]
            targets = [("", fv.get((it["id"], None), {}))] + [(sp.get(j["species"], {}).get("csv_label") or j["species"], fv.get((it["id"], j["id"]), {})) for j in js]
            for label, d in targets:
                seqs = set()
                for k in keys:
                    seqs |= set((d.get(k) or {}).keys())
                for s in sorted(seqs):
                    row = [(d.get(k) or {}).get(s, "") for k in keys]
                    if not any(row):
                        continue
                    w.writerow(base + [label, it["rel_path"], s + 1] + row)
                    n += 1
    os.replace(tmp, path)
    return dict(path=path, rows=n)


def write_index_csv(project, path: str | None = None) -> dict:
    """判定の索引 judgements.csv（areacheck の CSV 読み込みと R 用。rel_path、Season、Station、File、DateTime、Species、Status …）"""
    path = path or os.path.join(project.dir, "judgements.csv")
    return export_csv(project, path, dict(include_missing=True), include_unjudged=True)


# ---------- 取り込み ----------
def csv_preview(path: str, max_rows: int = 6) -> dict:
    text = read_text_any(path)
    delim = _sniff(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    for row in reader:
        if any(c.strip() for c in row):
            rows.append(row)
        if len(rows) >= max_rows + 1:
            break
    if not rows:
        return dict(columns=[], rows=[], delimiter=delim)
    header = [c.strip() for c in rows[0]]
    low = [h.lower() for h in header]

    def find(*names):
        for n in names:
            if n in low:
                return header[low.index(n)]
        return ""
    guess = dict(path=[c for c in (find("season"), find("station"), find("file", "filename", "path", "filepath")) if c],
                 species=find("species", "species_name", "label", "種"), datetime=find("datetime", "date_time", "timestamp", "日時"),
                 station=find("station"), season=find("season"))
    return dict(columns=header, rows=rows[1:max_rows + 1], delimiter=delim, guess=guess)


def csv_values(path: str, column: str, limit: int = 200, sl=None) -> dict:
    """列の値と件数（と種リストでの当たり）"""
    text = read_text_any(path)
    delim = _sniff(text)
    rows = csv.DictReader(io.StringIO(text), delimiter=delim)
    cnt = Counter()
    n = 0
    for r in rows:
        n += 1
        cnt[(r.get(column) or "").strip()] += 1
    return dict(n=n, values=[dict(value=v, count=c, guess=(sl.guess_code(v) if sl and v else None), exact=(sl.code_for_label(v) if sl and v else None))
                             for v, c in cnt.most_common(limit)])


def import_csv(project, path: str, path_cols: list[str], species_col: str, datetime_col: str | None,
               mapping: dict[str, str] | None, by: str | None = None, overwrite_verified: bool = False, corrected_col: str | None = None,
               as_verified: bool = False) -> dict:
    """CSV の種を判定として取り込む。path_cols を / でつないで相対パスにする（Season/Station/File）。
    mapping は CSV の値 → 種コード（空なら種リストの CSV 表記・英名・和名・学名・コードで自動対応）。
    対応しない値の行は飛ばして報告する"""
    db = project.db
    text = read_text_any(path)
    delim = _sniff(text)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
    sl = project.species
    mapping = {k.strip().lower(): v for k, v in (mapping or {}).items()}
    z = bool(project.setting("z_is_utc"))
    keymap: dict = {}
    stemmap: dict = {}
    for r in db.q("SELECT id, rel_key, rel_path, orig_rel_path, status FROM items"):
        keymap[r["rel_key"]] = (r["id"], r["status"])
        if r["orig_rel_path"]:                      # 保存用の変換の前の名前（.AVI）でも引ける
            keymap.setdefault(rel_key(r["orig_rel_path"]), (r["id"], r["status"]))
        stem = rel_key(os.path.splitext(r["rel_path"])[0])
        stemmap[stem] = None if stem in stemmap else (r["id"], r["status"])    # 拡張子抜きで 1 つに決まるときだけ
    # 名前だけでも引けるように（Season/Station の列がないとき）
    by_name: dict[str, list[tuple[int, str]]] = {}
    # Season / Station / File の項目で引く（パターンで付けた season・station と名前。フォルダの並びが CSV と違っても照合できる）
    by_fields: dict[tuple, list[tuple[int, str]]] = {}
    root_name = os.path.basename((project.root or "").rstrip("/\\")).lower()
    for r in db.q("SELECT id, name, status, season, station FROM items"):
        nm = r["name"].lower(); stem = os.path.splitext(nm)[0]
        by_name.setdefault(nm, []).append((r["id"], r["status"]))
        se, st = (r["season"] or "").lower(), (r["station"] or "").lower()
        for k in {(se, st, nm), (se, st, stem), (root_name, st, nm), (root_name, st, stem), ("", st, nm), ("", st, stem)}:
            by_fields.setdefault(k, []).append((r["id"], r["status"]))

    def by_field_lookup(parts: list[str]):
        """parts = [Season, Station, File] か [Station, File]。1 つに決まるときだけ"""
        if len(parts) == 3:
            se, st, fn = (x.lower() for x in parts)
        elif len(parts) == 2:
            se, (st, fn) = "", (x.lower() for x in parts)
        else:
            return None
        for k in ((se, st, fn), (se, st, os.path.splitext(fn)[0]), ("", st, fn), ("", st, os.path.splitext(fn)[0])):
            cands = by_fields.get(k) or []
            if len(cands) == 1:
                return cands[0]
        return None
    per_item: dict[int, dict] = {}
    unmatched_paths: Counter = Counter()
    unmatched_labels: Counter = Counter()
    n_rows = 0
    for r in rows:
        n_rows += 1
        parts = [(r.get(c) or "").strip().replace("\\", "/").strip("/") for c in path_cols]
        parts = [p for p in parts if p]
        if not parts:
            continue
        rk = rel_key("/".join(parts))
        hit = keymap.get(rk)
        if hit is None:
            hit = stemmap.get(rel_key(os.path.splitext("/".join(parts))[0]))    # 拡張子が違う（AVI → MP4 など）
        if hit is None:
            hit = by_field_lookup(parts)                                         # Season / Station / File の項目で
        if hit is None and len(parts) == 1:
            cands = by_name.get(parts[0].lower()) or []
            hit = cands[0] if len(cands) == 1 else None
        if hit is None:
            unmatched_paths["/".join(parts)] += 1
            continue
        iid, status = hit
        label = (r.get(species_col) or "").strip()
        code = mapping.get(label.lower()) or sl.code_for_label(label) or sl.guess_code(label)
        if not code:
            if label:
                unmatched_labels[label] += 1
            continue
        d = per_item.setdefault(iid, dict(status=status, codes=[], dt=None, dtc=None))
        if code not in d["codes"]:
            d["codes"].append(code)
        if datetime_col and not d["dt"]:
            d["dt"] = media.parse_dt_any(r.get(datetime_col) or "", z)
        if corrected_col and not d["dtc"]:
            d["dtc"] = media.parse_dt_any(r.get(corrected_col) or "", z)
    n_set = n_skipped = 0
    with db.tx() as c:
        for iid, d in per_item.items():
            if d["status"] == "verified" and not overwrite_verified:
                n_skipped += 1
                continue
            codes = d["codes"]
            if "empty" in codes and len(codes) > 1:
                codes = [x for x in codes if x != "empty"]
            c.execute("DELETE FROM judgements WHERE item_id=?", (iid,))
            c.execute("DELETE FROM field_values WHERE item_id=? AND judgement_id IS NOT NULL", (iid,))
            for code in codes:
                c.execute("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                          (iid, code, None, "manual" if as_verified else "csv", os.path.basename(path), now_iso(), by))
            if d["dt"]:
                c.execute("UPDATE items SET datetime=?, datetime_source='csv' WHERE id=?", (d["dt"], iid))
            if d["dtc"]:
                base = d["dt"] or (c.execute("SELECT datetime FROM items WHERE id=?", (iid,)).fetchone() or [None])[0]
                off = None
                if base:
                    try:
                        from datetime import datetime as _dt
                        off = int((_dt.strptime(d["dtc"], media.DT_FMT) - _dt.strptime(base, media.DT_FMT)).total_seconds())
                    except ValueError:
                        off = None
                if off:          # 元と違うときだけ修正として持つ
                    c.execute("UPDATE items SET datetime_corrected=?, time_offset_sec=?, time_note=COALESCE(time_note, 'csv') WHERE id=?", (d["dtc"], off, iid))
            if as_verified and codes:
                c.execute("UPDATE items SET status='verified', verified_by=?, verified_at=?, updated_at=? WHERE id=?", (by, now_iso(), now_iso(), iid))
            else:
                c.execute("UPDATE items SET status='auto', verified_by=NULL, verified_at=NULL, updated_at=? WHERE id=?", (now_iso(), iid))
            n_set += 1
    update_agree(db)
    return dict(rows=n_rows, items=n_set, skipped_verified=n_skipped,
                unmatched_paths=len(unmatched_paths), unmatched_path_examples=[p for p, _ in unmatched_paths.most_common(5)],
                unmatched_labels=[dict(value=v, count=n) for v, n in unmatched_labels.most_common(50)])
