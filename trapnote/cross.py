"""横断プロジェクト（2 階層目）: ほかのプロジェクト（調査回ごとのプロジェクト = 1 階層目）の動画を参照して、調査回をまたぐ作業をする。

- project.json に kind = "cross" と sources（参照先プロジェクトの名前とフォルダ）。フォルダは走査しない。
- 対象の相対パスは「<参照先の名前>/<参照先での相対パス>」。絶対パスは参照先プロジェクトのルート（この PC で解決）から引く（Project.abs_path）。
- 取り込みは参照先から対象（メタデータ・判定・AI の判定・検出の枠・特徴量・枠の画像）を写す。動画本体は写さない（参照先の NAS などから直接読む）。
- 選び方: 種ごとの本数（人の判定から。地点 × 調査回の上限、連続動画の間引き、地点の除外、乱数の種）/ 地点を丸ごと / 参照先を丸ごと / CSV の一覧。
  使った条件は project.json の imports に残す（論文の方法に書けるように）。
- 書き戻し（横断で確定した判定を参照先へ）と再取得（参照先で直した判定を横断へ）は push / pull。"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import random
import shutil
from collections import defaultdict
from datetime import datetime

from .db import now_iso
from .judge import _apply_state, item_state
from .paths import key, norm, rel_key, safe_name
from .species import FIXED_CODES

log = logging.getLogger(__name__)
HUMAN_ORIGINS = ("csv", "manual")
NOT_SPECIES = {"human", "vehicle", "unknown", "area"}      # 種ごとの抽出で既定では外す固定項目（empty = 動物なしは残す）
ITEM_COPY_SKIP = {"id", "rel_path", "rel_key", "folder", "src_dir", "src_id", "src_rel", "batch", "imported_at", "src_updated_at", "src_sig",
                  "orig_rel_path", "orig_size", "archived_at", "assignee"}


# ---------- 参照先 ----------
def open_source(path: str):
    """参照先プロジェクトを開く（ルートか TrapNote フォルダ）。横断プロジェクトは参照先にできない"""
    from .train import open_source as _open
    p = _open(path)
    if p.is_cross:
        p.close()
        raise ValueError(f"横断プロジェクトは参照先にできません: {path}")
    return p


def source_by_name(project, name: str) -> dict | None:
    for s in project.sources():
        if s.get("name") == name:
            return s
    return None


def add_source(project, path: str) -> dict:
    """参照先を登録する。名前は参照先プロジェクトの名前（相対パスの先頭になるので / や \\ は _ に）。最初の参照先なら種リストと追加項目を写す"""
    if not project.is_cross:
        raise ValueError("横断プロジェクトではありません")
    src = open_source(path)
    try:
        if key(src.dir) == key(project.dir):
            raise ValueError("自分自身は参照先にできません")
        name = safe_name(src.name).replace("/", "_") or "source"
        for s in project.sources():
            if key(s["dir"]) == key(src.dir):
                return dict(name=s["name"], dir=s["dir"], added=False)
            if s["name"] == name:
                raise ValueError(f"同じ名前の参照先があります: {name}（参照先プロジェクトの名前を変えてください）")
        first = not project.sources()
        project.data.setdefault("sources", []).append(dict(name=name, dir=src.dir, added_at=now_iso()))
        project.forget_source_roots()
        if first:
            copy_lists(project, src)
        project.save()
        return dict(name=name, dir=src.dir, added=True)
    finally:
        src.close()


def copy_lists(project, src) -> None:
    """種リストと追加項目の定義を参照先から写す（最初の参照先のとき）"""
    from .fields import Fields
    from .species import SpeciesList
    for fn in ("species.csv", "fields.json"):
        a, b = os.path.join(src.dir, fn), os.path.join(project.dir, fn)
        if os.path.isfile(a):
            shutil.copyfile(a, b)
    project.species = SpeciesList(os.path.join(project.dir, "species.csv"))
    project.fields = Fields(os.path.join(project.dir, "fields.json"))


def remove_source(project, name: str) -> dict:
    n = int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (name + "/%",)) or 0)
    if n:
        raise ValueError(f"{name} から取り込んだ対象が {n} 件あるので外せません（先に対象を消してください）")
    project.data["sources"] = [s for s in project.sources() if s.get("name") != name]
    project.forget_source_roots()
    project.save()
    return dict(ok=True)


def source_summary(src, name: str, project=None) -> dict:
    """参照先の様子: 件数、確認済み、人の判定のあるもの、地点、調査回、種ごとの本数（人の判定）"""
    db = src.db
    where = "missing=0"
    n_items = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where}") or 0)
    n_verified = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND status='verified'") or 0)
    n_human = int(db.scalar(f"SELECT COUNT(DISTINCT i.id) FROM items i JOIN judgements j ON j.item_id=i.id WHERE i.{where} AND (i.status='verified' OR j.origin IN ('csv','manual'))") or 0)
    n_det = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND det_state=1") or 0)
    stations = [dict(station=r[0] or "", n=r[1]) for r in db.q(f"SELECT station, COUNT(*) FROM items WHERE {where} GROUP BY station ORDER BY station")]
    seasons = [r[0] or "" for r in db.q(f"SELECT DISTINCT season FROM items WHERE {where} ORDER BY season")]
    species = [dict(code=r[0], n=r[1]) for r in db.q(
        f"SELECT j.species, COUNT(DISTINCT i.id) FROM items i JOIN judgements j ON j.item_id=i.id WHERE i.{where} AND (i.status='verified' OR j.origin IN ('csv','manual')) GROUP BY j.species ORDER BY 2 DESC")]
    n_in = int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (name + "/%",)) or 0) if project is not None else 0
    return dict(name=name, dir=src.dir, root=src.root, project_name=src.name, n_items=n_items, n_verified=n_verified, n_human=n_human, n_det=n_det,
                stations=stations, seasons=seasons, species=species, n_imported=n_in,
                speciesnet_model=src.setting("speciesnet_model"), pattern=src.setting("pattern"))


def sources_info(project) -> list[dict]:
    """登録した参照先の様子（開けないものは error）"""
    out = []
    for s in project.sources():
        try:
            src = open_source(s["dir"])
        except Exception as e:
            out.append(dict(name=s["name"], dir=s["dir"], ok=False, error=str(e), n_imported=int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (s["name"] + "/%",)) or 0)))
            continue
        try:
            d = source_summary(src, s["name"], project)
            d.update(ok=True, available=bool(src.root and os.path.isdir(src.root)))
            out.append(d)
        finally:
            src.close()
    return out


# ---------- 候補を集める・選ぶ ----------
def _human_rows(src, only_verified: bool) -> dict[int, dict]:
    """人の判定のある対象 {src_id: dict(id, rel, name, station, season, dt, status, species(set))}"""
    cond = "i.status='verified'" if only_verified else "(i.status='verified' OR j.origin IN ('csv','manual'))"
    rows = src.db.q("SELECT i.id, i.rel_path, i.name, i.station, i.season, COALESCE(i.datetime_corrected, i.datetime) AS dt, i.status, j.species, j.origin "
                    f"FROM items i JOIN judgements j ON j.item_id=i.id WHERE i.missing=0 AND {cond}")
    out: dict[int, dict] = {}
    for r in rows:
        if r["status"] != "verified" and r["origin"] not in HUMAN_ORIGINS:
            continue                                     # 確認済みでない対象の AI の判定は人の判定ではない
        d = out.get(r["id"])
        if d is None:
            d = out[r["id"]] = dict(id=r["id"], rel=r["rel_path"], name=r["name"], station=r["station"] or "", season=r["season"] or "", dt=r["dt"] or "", status=r["status"], species=set())
        if r["species"] != "area":
            d["species"].add(r["species"])
    return out


def _parse_dt(s: str) -> float | None:
    if not s:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).timestamp()
        except ValueError:
            continue
    return None


def _thin(rows: list[dict], gap_min: float) -> list[dict]:
    """同じ地点で gap_min 分以内に続く動画は 1 本だけ（時刻のないものはそのまま）"""
    if gap_min <= 0:
        return rows
    out, last = [], {}
    for r in sorted(rows, key=lambda x: (x["station"], x["dt"] or "", x["id"])):
        ts = _parse_dt(r["dt"])
        if ts is None:
            out.append(r)
            continue
        prev = last.get(r["station"])
        if prev is not None and ts - prev < gap_min * 60:
            continue
        last[r["station"]] = ts
        out.append(r)
    return out


def select_items(project, params: dict, progress=None) -> dict:
    """params に従って参照先から対象を選ぶ。返り値 dict(picked=[(name, src_dir, src_row)], counts={species: n}, by_source={name: n}, n=...)。
    mode: species（種ごとに n_per_species 本）/ stations（地点を丸ごと）/ all（参照先を丸ごと）/ csv（Season, Station, File の一覧）"""
    mode = params.get("mode") or "species"
    want_sources = set(params.get("sources") or []) or {s["name"] for s in project.sources()}
    exclude_st = {str(x) for x in (params.get("exclude_stations") or [])}
    seed = int(params.get("seed") or 1)
    only_verified = bool(params.get("only_verified"))
    picked: list[tuple[str, str, dict]] = []
    counts: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    already = {rel_key(r[0]) for r in project.db.q("SELECT rel_path FROM items")}
    csv_rows = parse_csv_list(params.get("csv_text") or "") if mode == "csv" else []
    pool: dict[str, list[dict]] = defaultdict(list)          # 種 → 候補（mode = species）
    for s in project.sources():
        name = s["name"]
        if name not in want_sources:
            continue
        if progress:
            progress(name)
        src = open_source(s["dir"])
        try:
            if mode == "species":
                rows = _human_rows(src, only_verified)
                cands = [r for r in rows.values() if r["station"] not in exclude_st]
                if params.get("single_only", True):
                    cands = [r for r in cands if len(r["species"]) <= 1]
                for r in _thin(cands, float(params.get("gap_min") or 0)):
                    for sp in r["species"]:
                        pool[sp].append(dict(r, src_name=name, src_dir=src.dir))
            elif mode == "stations":
                want_st = {str(x) for x in (params.get("stations") or [])}
                for r in src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0"):
                    if (r["station"] or "") in want_st and (r["station"] or "") not in exclude_st:
                        picked.append((name, src.dir, dict(r)))
            elif mode == "all":
                for r in src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0"):
                    if (r["station"] or "") not in exclude_st:
                        picked.append((name, src.dir, dict(r)))
            elif mode == "csv":
                for row in csv_rows:
                    if row.get("season") and row["season"] != name and row["season"].lower() != src.name.lower():
                        continue
                    if row.get("station"):
                        hits = src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0 AND station=? AND LOWER(name)=LOWER(?)", (row["station"], row["file"]))
                    else:
                        hits = src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0 AND LOWER(name)=LOWER(?)", (row["file"],))
                    for r in hits:
                        picked.append((name, src.dir, dict(r)))
                        row["_hit"] = True
        finally:
            src.close()
    if mode == "species":
        n_per = int(params.get("n_per_species") or 0)
        cap = int(params.get("cap") or 0)
        want_sp = set(params.get("species") or []) or {sp for sp in pool if sp not in NOT_SPECIES}
        rng = random.Random(seed)
        seen: set[tuple[str, int]] = set()
        for sp in sorted(want_sp):
            cands = list(pool.get(sp, []))
            rng.shuffle(cands)
            per_cell: dict[tuple, int] = defaultdict(int)
            n_sp = 0
            for r in cands:
                if n_per and n_sp >= n_per:
                    break
                cell = (r["src_name"], r["station"], r["season"])
                if cap and per_cell[cell] >= cap:
                    continue
                k = (r["src_name"], r["id"])
                if k in seen:
                    continue
                per_cell[cell] += 1
                seen.add(k)
                n_sp += 1
                picked.append((r["src_name"], r["src_dir"], r))
                counts[sp] += 1
    # 既に取り込んであるものは除く・重複を除く
    uniq: list[tuple[str, str, dict]] = []
    seen2: set[tuple[str, int]] = set()
    n_already = 0
    for name, sdir, r in picked:
        k = (name, int(r["id"]))
        if k in seen2:
            continue
        seen2.add(k)
        if rel_key(f"{name}/{r['rel'] if 'rel' in r else r['rel_path']}") in already:
            n_already += 1
            continue
        uniq.append((name, sdir, r))
        by_source[name] += 1
    n_unmatched = sum(1 for row in csv_rows if not row.get("_hit")) if mode == "csv" else 0
    return dict(picked=uniq, counts=dict(counts), by_source=dict(by_source), n=len(uniq), n_already=n_already, n_csv=len(csv_rows), n_unmatched=n_unmatched,
                available={sp: len(v) for sp, v in pool.items()} if mode == "species" else {})


def parse_csv_list(text: str) -> list[dict]:
    """Season, Station, File の一覧（列名は大文字小文字を問わない。日本語の 調査回・地点・ファイル も可。rel_path 列があればそこから）"""
    if not text.strip():
        return []
    rd = csv.reader(io.StringIO(text.strip()))
    rows = list(rd)
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    alias = {"season": ("season", "調査回", "期"), "station": ("station", "地点", "site"), "file": ("file", "filename", "name", "ファイル", "ファイル名"), "rel": ("rel_path", "relativepath", "path")}
    idx = {}
    for k, names in alias.items():
        for i, h in enumerate(head):
            if h in names:
                idx[k] = i
                break
    body = rows[1:] if idx else rows
    if not idx:                                          # 見出しがなければ 列 = Season, Station, File とみなす
        idx = {"season": 0, "station": 1, "file": 2} if len(rows[0]) >= 3 else {"file": len(rows[0]) - 1}
    out = []
    for r in body:
        if not any(x.strip() for x in r):
            continue
        g = lambda k: (r[idx[k]].strip() if k in idx and idx[k] < len(r) else "")
        rel = g("rel").replace("\\", "/")
        if rel and "file" not in idx:
            parts = rel.split("/")
            out.append(dict(season=parts[0] if len(parts) >= 3 else "", station=parts[-2] if len(parts) >= 2 else "", file=parts[-1]))
        else:
            out.append(dict(season=g("season"), station=g("station"), file=g("file") or os.path.basename(rel)))
    return [x for x in out if x["file"]]


def plan(project, params: dict) -> dict:
    sel = select_items(project, params)
    sel.pop("picked", None)
    return sel


# ---------- 取り込み ----------
def import_items(project, params: dict, batch: str, progress=None, stop=None) -> dict:
    """select_items() で選んだ対象を参照先から写す。返り値 dict(n, n_already, counts, by_source)"""
    from .train import crop_path, features_path
    sel = select_items(project, params, lambda name: progress and progress(0, 0, f"{name} を調べています"))
    picked = sel["picked"]
    db = project.db
    cols = [r["name"] for r in db.q("PRAGMA table_info(items)")]
    copy_cols = [c for c in cols if c not in ITEM_COPY_SKIP]
    by_src: dict[str, list[dict]] = defaultdict(list)
    sdir_of: dict[str, str] = {}
    for name, sdir, r in picked:
        by_src[name].append(r)
        sdir_of[name] = sdir
    n_done = 0
    now = now_iso()
    for name, rows in by_src.items():
        if stop and stop():
            break
        src = open_source(sdir_of[name])
        try:
            fdir = project.cache_dir("features")
            os.makedirs(fdir, exist_ok=True)
            for i0 in range(0, len(rows), 200):
                if stop and stop():
                    break
                chunk = rows[i0:i0 + 200]
                with db.tx() as c:
                    for r in chunk:
                        sid = int(r["id"])
                        srow = src.db.q1("SELECT * FROM items WHERE id=?", (sid,))
                        if srow is None:
                            continue
                        srow = dict(srow)
                        src_rel = srow["rel_path"]
                        rel = f"{name}/{src_rel}"
                        vals = {c_: srow.get(c_) for c_ in copy_cols}
                        st0 = item_state(src.db, sid)
                        vals.update(rel_path=rel, rel_key=rel_key(rel), folder=os.path.dirname(rel), season=srow.get("season") or name,
                                    src_dir=src.dir, src_id=sid, src_rel=src_rel, batch=batch or None, imported_at=now, src_updated_at=srow.get("updated_at"),
                                    src_sig=state_sig(st0, srow.get("train_exclude"), srow.get("recheck")), updated_at=now)
                        names = list(vals.keys())
                        cur = c.execute(f"INSERT OR IGNORE INTO items({', '.join(names)}) VALUES({', '.join('?' * len(names))})", [vals[k] for k in names])
                        if cur.rowcount == 0:
                            continue
                        nid = cur.lastrowid
                        _apply_state(c, nid, st0, None)
                        c.execute("UPDATE items SET status=?, verified_by=?, verified_at=?, agree=?, recheck=?, updated_at=? WHERE id=?",
                                  (srow.get("status") or "new", srow.get("verified_by"), srow.get("verified_at"), srow.get("agree"), srow.get("recheck") or 0, now, nid))
                        c.executemany("INSERT INTO auto_results(item_id, species, confidence, model, created_at) VALUES(?,?,?,?,?)",
                                      [(nid, a["species"], a["confidence"], a["model"], a["created_at"]) for a in src.db.q("SELECT species, confidence, model, created_at FROM auto_results WHERE item_id=? ORDER BY id", (sid,))])
                        c.executemany("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                      [(nid, d["t_sec"], d["category"], d["conf"], d["x"], d["y"], d["w"], d["h"], d["species"], d["sp_conf"])
                                       for d in src.db.q("SELECT t_sec, category, conf, x, y, w, h, species, sp_conf FROM detections WHERE item_id=? ORDER BY id", (sid,))])
                        # 特徴量と枠の画像（あれば写す。なければあとで自動判定の「枠はいまあるものを使う」で付く）
                        fa, fb = features_path(src, src_rel), features_path(project, rel)
                        if os.path.isfile(fa) and not os.path.isfile(fb):
                            try:
                                shutil.copyfile(fa, fb)
                            except OSError as e:
                                log.warning("特徴量を写せません %s: %s", fa, e)
                        ca, cb = os.path.dirname(crop_path(src, src_rel, 0)), os.path.dirname(crop_path(project, rel, 0))
                        if os.path.isdir(ca) and not os.path.isdir(cb):
                            try:
                                shutil.copytree(ca, cb)
                            except OSError as e:
                                log.warning("枠の画像を写せません %s: %s", ca, e)
                        n_done += 1
                        if progress and n_done % 20 == 0:
                            progress(n_done, len(picked), rel)
        finally:
            src.close()
    rec = dict(at=now, batch=batch, params={k: v for k, v in params.items() if k != "csv_text"}, n=n_done, counts=sel["counts"], by_source=sel["by_source"],
               n_csv=sel.get("n_csv"), n_unmatched=sel.get("n_unmatched"))
    project.data.setdefault("imports", []).append(rec)
    project.save()
    return dict(n=n_done, n_already=sel["n_already"], counts=sel["counts"], by_source=sel["by_source"], n_csv=sel.get("n_csv"), n_unmatched=sel.get("n_unmatched"))


# ---------- 走査の代わり: 参照先の動画があるかを確かめる ----------
def check_missing(project, progress=None, stop=None) -> dict:
    db = project.db
    rows = db.q("SELECT id, rel_path, missing FROM items")
    gone, back = [], []
    for n, r in enumerate(rows):
        if stop and stop():
            break
        ok = os.path.isfile(project.abs_path(r["rel_path"]))
        if not ok and not r["missing"]:
            gone.append(r["id"])
        elif ok and r["missing"]:
            back.append(r["id"])
        if progress and n % 200 == 0:
            progress(n, len(rows), r["rel_path"])
    with db.tx() as c:
        if gone:
            c.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
        if back:
            c.executemany("UPDATE items SET missing=0 WHERE id=?", [(i,) for i in back])
    db.set_meta("last_scan", now_iso())
    return dict(found=len(rows) - len(gone), added=0, back=len(back), changed=0, gone=len(gone))


# ---------- 書き戻し・再取得 ----------
def state_sig(state: dict, train_exclude=0, recheck=0) -> str:
    """判定の署名（種と項目の値、状態、メモ、学習から外す、見直し）。取り込み・同期のときに控え、どちらが変わったかの判断に使う"""
    js = sorted((j["species"], json.dumps(j.get("fields") or {}, sort_keys=True, ensure_ascii=False)) for j in state.get("judgements", []))
    return json.dumps([js, state.get("status") or "new", state.get("fields") or {}, state.get("note") or "", int(train_exclude or 0), int(recheck or 0)], sort_keys=True, ensure_ascii=False)


def _differs(a: dict, b: dict) -> bool:
    return state_sig(a) != state_sig(b)


def sync_plan(project, direction: str) -> dict:
    """push（横断 → 参照先）/ pull（参照先 → 横断）で何が変わるかを数える。
    push: 横断で確認済みにした（取り込み後に更新された）対象のうち、参照先と内容が違うもの。参照先も取り込み後に変わっていれば衝突。
    pull: 参照先で取り込み後に更新された対象のうち、横断と違うもの。横断でも変えていれば衝突"""
    db = project.db
    out = dict(n_update=0, n_conflict=0, n_same=0, by_source={}, conflicts=[])
    for s in project.sources():
        name = s["name"]
        rows = db.q("SELECT id, src_id, rel_path, status, verified_at, updated_at, imported_at, src_updated_at, src_sig, train_exclude, recheck FROM items WHERE src_dir=? AND src_id IS NOT NULL", (s["dir"],))
        if not rows:
            continue
        try:
            src = open_source(s["dir"])
        except Exception as e:
            out["by_source"][name] = dict(error=str(e))
            continue
        try:
            n_up = n_conf = n_same = 0
            for r in rows:
                here = item_state(db, r["id"])
                there = item_state(src.db, r["src_id"])
                if not there:
                    continue
                srow = src.db.q1("SELECT updated_at, train_exclude, recheck FROM items WHERE id=?", (r["src_id"],))
                sig_here = state_sig(here, r["train_exclude"], r["recheck"])
                sig_there = state_sig(there, srow["train_exclude"], srow["recheck"])
                here_changed = sig_here != (r["src_sig"] or "")
                there_changed = sig_there != (r["src_sig"] or "")
                if sig_here == sig_there:
                    n_same += 1
                    continue
                if direction == "push":
                    if not here_changed:
                        n_same += 1
                        continue
                    if there_changed and there["status"] == "verified":
                        n_conf += 1
                        out["conflicts"].append(dict(id=r["id"], rel_path=r["rel_path"], here=[j["species"] for j in here["judgements"]], there=[j["species"] for j in there["judgements"]]))
                    else:
                        n_up += 1
                else:
                    if not there_changed:
                        n_same += 1
                        continue
                    if here_changed and here["status"] == "verified":
                        n_conf += 1
                        out["conflicts"].append(dict(id=r["id"], rel_path=r["rel_path"], here=[j["species"] for j in here["judgements"]], there=[j["species"] for j in there["judgements"]]))
                    else:
                        n_up += 1
            out["by_source"][name] = dict(update=n_up, conflict=n_conf, same=n_same)
            out["n_update"] += n_up; out["n_conflict"] += n_conf; out["n_same"] += n_same
        finally:
            src.close()
    out["conflicts"] = out["conflicts"][:200]
    return out


def sync_apply(project, direction: str, by: str, overwrite_conflicts: bool = False, progress=None, stop=None) -> dict:
    """sync_plan の「更新」を実際に書く。overwrite_conflicts なら衝突も push/pull の側で上書きする"""
    from .judge import update_agree
    db = project.db
    n_done = n_skip = 0
    for s in project.sources():
        rows = db.q("SELECT id, src_id, rel_path, status, updated_at, imported_at, src_updated_at, src_sig, train_exclude, recheck FROM items WHERE src_dir=? AND src_id IS NOT NULL", (s["dir"],))
        if not rows:
            continue
        src = open_source(s["dir"])
        try:
            changed_src_ids, changed_ids = [], []
            for n, r in enumerate(rows):
                if stop and stop():
                    break
                if progress and n % 50 == 0:
                    progress(n, len(rows), r["rel_path"])
                here = item_state(db, r["id"])
                there = item_state(src.db, r["src_id"])
                if not there:
                    continue
                srow = src.db.q1("SELECT updated_at, train_exclude, recheck FROM items WHERE id=?", (r["src_id"],))
                sig_here = state_sig(here, r["train_exclude"], r["recheck"])
                sig_there = state_sig(there, srow["train_exclude"], srow["recheck"])
                here_changed = sig_here != (r["src_sig"] or "")
                there_changed = sig_there != (r["src_sig"] or "")
                if sig_here == sig_there:
                    if r["src_sig"] != sig_here:
                        with db.tx() as c:
                            c.execute("UPDATE items SET src_sig=? WHERE id=?", (sig_here, r["id"]))
                    continue
                now = now_iso()
                if direction == "push":
                    if not here_changed:
                        continue
                    if there_changed and there["status"] == "verified" and not overwrite_conflicts:
                        n_skip += 1
                        continue
                    with src.db.tx() as c:
                        c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                                  (r["src_id"], now, "cross_push", by, json.dumps(there, ensure_ascii=False), json.dumps(here, ensure_ascii=False)))
                        _apply_state(c, r["src_id"], here, by)
                        c.execute("UPDATE items SET train_exclude=?, recheck=?, updated_at=? WHERE id=?", (int(r["train_exclude"] or 0), int(r["recheck"] or 0), now, r["src_id"]))
                    with db.tx() as c:
                        c.execute("UPDATE items SET src_updated_at=?, src_sig=? WHERE id=?", (now, sig_here, r["id"]))
                    changed_src_ids.append(r["src_id"])
                else:
                    if not there_changed:
                        continue
                    if here_changed and here["status"] == "verified" and not overwrite_conflicts:
                        n_skip += 1
                        continue
                    with db.tx() as c:
                        c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                                  (r["id"], now, "cross_pull", by, json.dumps(here, ensure_ascii=False), json.dumps(there, ensure_ascii=False)))
                        _apply_state(c, r["id"], there, by)
                        c.execute("UPDATE items SET train_exclude=?, recheck=?, src_updated_at=?, src_sig=?, updated_at=? WHERE id=?",
                                  (int(srow["train_exclude"] or 0), int(srow["recheck"] or 0), srow["updated_at"], sig_there, now, r["id"]))
                    changed_ids.append(r["id"])
                n_done += 1
            if changed_src_ids:
                update_agree(src.db, changed_src_ids)
            if changed_ids:
                update_agree(db, changed_ids)
        finally:
            src.close()
    return dict(n=n_done, n_skipped=n_skip)


# ---------- 選んだ動画をフォルダへ写す・一覧を書く ----------
def export_list(project, path: str, f: dict | None = None) -> dict:
    """対象の一覧（Season, Station, File, Source, Batch, Status, Species）を CSV に"""
    from .judge import build_where
    where, p = build_where(dict(f or {}, include_missing=True))
    rows = project.db.q(f"SELECT id, rel_path, src_rel, season, station, name, batch, status FROM items WHERE {where} ORDER BY rel_path", p)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Source", "Season", "Station", "File", "Batch", "Status", "Species", "RelPathInSource"])
        for r in rows:
            sp = ";".join(j["species"] for j in project.db.q("SELECT species FROM judgements WHERE item_id=? ORDER BY id", (r["id"],)))
            w.writerow([r["rel_path"].split("/")[0], r["season"] or "", r["station"] or "", r["name"], r["batch"] or "", r["status"], sp, r["src_rel"] or ""])
    return dict(path=path, n=len(rows))


def copy_videos(project, out_dir: str, f: dict | None = None, progress=None, stop=None) -> dict:
    """対象の動画を out_dir/<参照先>/<参照先での相対パス> に写す（研究用の手元の複製や配布に）"""
    from .judge import build_where
    where, p = build_where(dict(f or {}))
    rows = project.db.q(f"SELECT id, rel_path FROM items WHERE {where} ORDER BY rel_path", p)
    n_ok = n_miss = 0
    for n, r in enumerate(rows):
        if stop and stop():
            break
        src = project.abs_path(r["rel_path"])
        dst = os.path.join(out_dir, *r["rel_path"].split("/"))
        if progress and n % 10 == 0:
            progress(n, len(rows), r["rel_path"])
        if not os.path.isfile(src):
            n_miss += 1
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            n_ok += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n_ok += 1
    return dict(n=n_ok, n_missing=n_miss, out_dir=norm(out_dir))
