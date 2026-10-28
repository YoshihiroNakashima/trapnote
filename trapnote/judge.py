"""対象の一覧（絞り込み・並べ替え・ページング）、詳細、判定の保存と確認、取り消し"""
from __future__ import annotations

import re

import json
import logging
import os

from .db import Database, now_iso, now_stamp
from .species import FIXED_CODES

log = logging.getLogger(__name__)

STATUSES = ("new", "auto", "verified")
EFF_DT = "COALESCE(items.datetime_corrected, items.datetime)"      # 修正後があればそれ、なければ元の日時
SORTS = {
    "datetime": f"COALESCE({EFF_DT}, '')",
    "name": "items.name",
    "path": "items.rel_path",
    "status": "items.status",
    "updated": "COALESCE(items.updated_at, '')",
    "boxes": "items.n_boxes",
    "station": f"COALESCE(items.station, '') || '/' || COALESCE({EFF_DT}, '')",
    "confidence": "COALESCE((SELECT MIN(confidence) FROM judgements j WHERE j.item_id=items.id AND j.origin='auto'), 2)",
    "id": "items.id",
}


# ---------- 絞り込み ----------
def build_where(f: dict) -> tuple[str, list]:
    w, p = ["1=1"], []
    if f.get("station"):
        w.append("items.station = ?"); p.append(f["station"])
    if f.get("season"):
        w.append("items.season = ?"); p.append(f["season"])
    if f.get("folder"):
        w.append("items.folder = ?"); p.append(f["folder"])
    if f.get("folder_prefix"):
        fp = str(f["folder_prefix"]).strip("/")
        w.append("(items.folder = ? OR items.folder LIKE ? ESCAPE '\\')")
        p += [fp, fp.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"]
    if f.get("favorite") in (True, "1", 1, "true"):
        w.append("items.favorite = 1")
    st = f.get("status")
    if st in STATUSES:
        w.append("items.status = ?"); p.append(st)
    elif st == "unverified":
        w.append("items.status != 'verified'")
    kind = f.get("kind")
    if kind in ("video", "image"):
        w.append("items.kind = ?"); p.append(kind)
    sp = f.get("species")
    if sp == "none":
        w.append("NOT EXISTS (SELECT 1 FROM judgements j WHERE j.item_id = items.id)")
    elif sp == "any":
        w.append("EXISTS (SELECT 1 FROM judgements j WHERE j.item_id = items.id AND j.species NOT IN ('empty','area'))")
    elif sp:
        w.append("EXISTS (SELECT 1 FROM judgements j WHERE j.item_id = items.id AND j.species = ?)"); p.append(sp)
    if f.get("date_from"):
        w.append(f"{EFF_DT} >= ?"); p.append(str(f["date_from"]).replace("-", "/")[:19])
    if f.get("date_to"):
        w.append(f"{EFF_DT} <= ?"); p.append(str(f["date_to"]).replace("-", "/")[:10] + " 23:59:59")
    if f.get("corrected") in (True, "1", 1, "true"):
        w.append("items.datetime_corrected IS NOT NULL")
    if f.get("assignee"):
        w.append("items.assignee = ?"); p.append(f["assignee"])
    if f.get("flag"):
        w.append("items.flag = ?"); p.append(f["flag"])
    ag = f.get("agree")                      # 人の判定とモデルの判定の比較: agree / partial / disagree / differ（部分＋不一致）/ nohuman（モデルだけ）/ any（モデルあり）
    if ag == "agree":
        w.append("items.agree = 1")
    elif ag == "partial":
        w.append("items.agree = 2")
    elif ag == "disagree":
        w.append("items.agree = 3")
    elif ag == "differ":
        w.append("items.agree IN (2, 3)")
    elif ag == "nohuman":
        w.append("items.auto_json IS NOT NULL AND items.agree IS NULL")
    elif ag == "any":
        w.append("items.auto_json IS NOT NULL")
    elif ag == "rechecked":
        w.append("items.recheck = 1")
    if f.get("exclude") in (True, "1", 1, "true"):
        w.append("items.train_exclude = 1")
    bx = str(f.get("boxes") or "")             # 枠の数: 0 / 1 / 2 / 3+（3 以上）/ 2-5（範囲）
    if bx:
        m = re.match(r"^(\d+)(\+|-(\d+))?$", bx)
        if m:
            lo = int(m.group(1))
            if m.group(2) == "+":
                w.append("items.n_boxes >= ?"); p.append(lo)
            elif m.group(3):
                w.append("items.n_boxes BETWEEN ? AND ?"); p += [lo, int(m.group(3))]
            else:
                w.append("items.n_boxes = ?"); p.append(lo)
            if lo == 0 and not m.group(2):
                w.append("items.det_state = 1")      # 枠 0 は検出済みで枠がないもの（未検出は含めない）
    det = f.get("det")                       # 検出の状態: todo（未）/ done（済）/ failed（失敗）/ animal / person / vehicle / none（検出で動物なし）
    if det == "todo":
        w.append("items.det_state = 0")
    elif det == "done":
        w.append("items.det_state = 1")
    elif det == "failed":
        w.append("items.det_state = 2")
    elif det in ("animal", "person", "vehicle"):
        w.append(f"items.det_state = 1 AND items.det_json LIKE '%\"{det}\": true%'")
    elif det == "none":
        w.append("items.det_state = 1 AND items.det_json LIKE '%\"animal\": false%' AND items.det_json LIKE '%\"person\": false%' AND items.det_json LIKE '%\"vehicle\": false%'")
    if f.get("missing") in (True, "1", 1, "true"):
        w.append("items.missing = 1")
    elif not f.get("include_missing"):
        w.append("items.missing = 0")
    if f.get("text"):
        w.append("(items.rel_path LIKE ? ESCAPE '\\' OR items.note LIKE ? ESCAPE '\\')")
        t = "%" + str(f["text"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        p += [t, t]
    if f.get("ids"):
        ids = [int(i) for i in f["ids"]]
        w.append(f"items.id IN ({','.join('?' * len(ids))})"); p += ids
    return " AND ".join(w), p


def order_sql(sort: str, desc: bool) -> tuple[str, str]:
    expr = SORTS.get(sort or "datetime", SORTS["datetime"])
    d = "DESC" if desc else "ASC"
    return expr, f"{expr} {d}, items.id {d}"


# ---------- 一覧 ----------
ROW_COLS = ("id, rel_path, name, kind, folder, season, station, datetime, datetime_source, duration, width, height, "
            "status, verified_by, assignee, missing, meta_state, flag, note, det_state, cls_state, favorite, datetime_corrected, time_offset_sec, auto_json, agree, recheck, train_exclude, n_boxes")


def _parse_auto(r: dict) -> None:
    """items.auto_json → r["auto"]（モデルの判定の種コードの列）"""
    a = r.pop("auto_json", None)
    try:
        d = json.loads(a) if a else None
    except Exception:
        d = None
    r["auto"] = [dict(species=x["species"], confidence=x.get("confidence")) for x in (d or {}).get("species", [])] if d else None
    r["auto_model"] = (d or {}).get("model") if d else None


def count_items(db: Database, f: dict) -> int:
    where, p = build_where(f)
    return int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where}", p) or 0)


def list_items(db: Database, f: dict, sort: str, desc: bool, offset: int, limit: int) -> list[dict]:
    where, p = build_where(f)
    _, order = order_sql(sort, desc)
    rows = db.q(f"SELECT {ROW_COLS} FROM items WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*p, int(limit), int(offset)))
    out = [dict(r) for r in rows]
    for r in out:
        _parse_auto(r)
    attach_species(db, out)
    return out


HUMAN_ORIGINS = ("csv", "manual")


def human_codes(status: str, judgements: list[dict]) -> set[str]:
    """人の判定の種: 確認済みなら全部、そうでなければ CSV / Timelapse / 手動で付けたもの（「有効範囲」は除く）"""
    return {j["species"] for j in judgements if (status == "verified" or j.get("origin") in HUMAN_ORIGINS) and j["species"] != "area"}


def compare_codes(human: set[str], auto: list[str]) -> int | None:
    """1 一致 / 2 部分一致 / 3 不一致 / None 比べられない"""
    a = {x for x in auto if x != "area"}
    if not human or not a:
        return None
    if a == human:
        return 1
    if a & human:
        return 2
    return 3


def update_agree(db: Database, item_ids: list[int] | None = None) -> int:
    """items.agree を計算し直す（モデルの判定 auto_json があるものだけ）。item_ids を省くと全部。返り値は変えた数"""
    if item_ids is not None and not item_ids:
        return 0
    if item_ids is not None and len(item_ids) > 900:          # SQL の変数の上限を超えないように分ける
        return sum(update_agree(db, item_ids[i:i + 900]) for i in range(0, len(item_ids), 900))
    where = "auto_json IS NOT NULL"
    params: tuple = ()
    if item_ids is not None:
        where += f" AND id IN ({','.join('?' * len(item_ids))})"
        params = tuple(item_ids)
    rows = db.q(f"SELECT id, status, agree, auto_json FROM items WHERE {where}", params)
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    js: dict[int, list[dict]] = {i: [] for i in ids}
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        qs = ",".join("?" * len(part))
        for j in db.q(f"SELECT item_id, species, origin FROM judgements WHERE item_id IN ({qs})", part):
            js[j["item_id"]].append(dict(species=j["species"], origin=j["origin"]))
    changes = []
    for r in rows:
        try:
            auto = [x["species"] for x in json.loads(r["auto_json"]).get("species", [])]
        except Exception:
            auto = []
        v = compare_codes(human_codes(r["status"], js[r["id"]]), auto)
        if v != r["agree"]:
            changes.append((v, r["id"]))
    if changes:
        db.xmany("UPDATE items SET agree=? WHERE id=?", changes)
    return len(changes)


def attach_species(db: Database, rows: list[dict]) -> None:
    if not rows:
        return
    ids = [r["id"] for r in rows]
    by = {r["id"]: [] for r in rows}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        qs = ",".join("?" * len(part))
        for j in db.q(f"SELECT item_id, species, confidence, origin FROM judgements WHERE item_id IN ({qs}) ORDER BY id", part):
            by[j["item_id"]].append(dict(species=j["species"], confidence=j["confidence"], origin=j["origin"]))
    for r in rows:
        r["judgements"] = by.get(r["id"], [])
        r["species"] = [j["species"] for j in r["judgements"]]


def index_of(db: Database, f: dict, sort: str, desc: bool, item_id: int) -> int | None:
    """いまの並びでの位置（0 始まり）"""
    where, p = build_where(f)
    expr, _ = order_sql(sort, desc)
    cur = db.q1(f"SELECT {expr} AS k FROM items WHERE id=?", (item_id,))
    if cur is None:
        return None
    k = cur["k"]
    op = "<" if not desc else ">"
    n = db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND (({expr}) {op} ? OR (({expr}) = ? AND items.id {op} ?))", (*p, k, k, item_id))
    return int(n or 0)


def neighbor(db: Database, f: dict, sort: str, desc: bool, item_id: int, step: int) -> dict | None:
    where, p = build_where(f)
    expr, order = order_sql(sort, desc)
    cur = db.q1(f"SELECT {expr} AS k FROM items WHERE id=?", (item_id,))
    if cur is None:
        return None
    k = cur["k"]
    forward = (step > 0) != desc        # 並びの向きを考えた「後ろ」
    op = ">" if forward else "<"
    d = "ASC" if forward else "DESC"
    r = db.q1(f"SELECT {ROW_COLS} FROM items WHERE {where} AND (({expr}) {op} ? OR (({expr}) = ? AND items.id {op} ?)) "
              f"ORDER BY {expr} {d}, items.id {d} LIMIT 1", (*p, k, k, item_id))
    if r is None:
        return None
    out = [dict(r)]
    attach_species(db, out)
    return out[0]


def facets(db: Database) -> dict:
    """絞り込みの候補（地点、調査回、担当、フォルダ）と状態の数"""
    stations = [r[0] for r in db.q("SELECT DISTINCT station FROM items WHERE station IS NOT NULL AND station != '' ORDER BY station")]
    seasons = [r[0] for r in db.q("SELECT DISTINCT season FROM items WHERE season IS NOT NULL AND season != '' ORDER BY season")]
    assignees = [r[0] for r in db.q("SELECT DISTINCT assignee FROM items WHERE assignee IS NOT NULL AND assignee != '' ORDER BY assignee")]
    # 件数はまとめて 1 回の走査で数える（数十万件のプロジェクトでも 1 秒以内に）
    one = db.q1("SELECT COUNT(*), SUM(status='new'), SUM(status='auto'), SUM(status='verified'), SUM(meta_state=0), SUM(favorite=1), "
                "SUM(datetime_corrected IS NOT NULL), SUM(det_state=1), SUM(flag='low'), SUM(agree=1), SUM(agree=2), SUM(agree=3), "
                "SUM(auto_json IS NOT NULL), SUM(recheck=1), SUM(train_exclude=1) FROM items NOT INDEXED WHERE missing=0")   # 索引経由より表の走査が速い
    v = [int(x or 0) for x in one]
    total = v[0]
    counts = {k: n for k, n in (("new", v[1]), ("auto", v[2]), ("verified", v[3])) if n}
    meta_pending, favorites, corrected, det_done, low = v[4], v[5], v[6], v[7], v[8]
    agree = {1: v[9], 2: v[10], 3: v[11]}
    auto_n, recheck_n, excluded = v[12], v[13], v[14]
    missing = int(db.scalar("SELECT COUNT(*) FROM items WHERE missing=1") or 0)
    sp_counts = {r[0]: r[1] for r in db.q("SELECT species, COUNT(DISTINCT item_id) FROM judgements GROUP BY species")}
    folders = [dict(folder=r[0], n=r[1], verified=r[2], favorite=r[3]) for r in
               db.q("SELECT folder, COUNT(*), SUM(status='verified'), SUM(favorite) FROM items WHERE missing=0 GROUP BY folder ORDER BY folder")]
    by_station = [dict(station=r[0], total=r[1], verified=r[2]) for r in
                  db.q("SELECT COALESCE(station,''), COUNT(*), SUM(status='verified') FROM items WHERE missing=0 GROUP BY station ORDER BY station")]
    return dict(stations=stations, seasons=seasons, assignees=assignees, counts=counts, total=total, missing=missing,
                meta_pending=meta_pending, species_counts=sp_counts, by_station=by_station, folders=folders, favorites=favorites, corrected=corrected,
                det_done=det_done, low=low, agree=dict(match=agree.get(1, 0), partial=agree.get(2, 0), mismatch=agree.get(3, 0), auto=auto_n, rechecked=recheck_n), excluded=excluded)


# ---------- 詳細と判定 ----------
def _field_values(db: Database, item_id: int) -> tuple[dict, dict]:
    """(対象ごと {key: [値 (seq 順)]}, 種の行ごと {judgement_id: {key: [値]}})"""
    item_f: dict[str, dict[int, str]] = {}
    sp_f: dict[int, dict[str, dict[int, str]]] = {}
    for r in db.q("SELECT judgement_id, field_key, seq, value FROM field_values WHERE item_id=? ORDER BY seq, id", (item_id,)):
        if r["judgement_id"] is None:
            item_f.setdefault(r["field_key"], {})[r["seq"]] = r["value"]
        else:
            sp_f.setdefault(r["judgement_id"], {}).setdefault(r["field_key"], {})[r["seq"]] = r["value"]

    def squash(d: dict[str, dict[int, str]]) -> dict[str, list]:
        out = {}
        for k, m in d.items():
            n = max(m.keys()) + 1 if m else 0
            out[k] = [m.get(i, "") for i in range(n)]
        return out

    return squash(item_f), {jid: squash(d) for jid, d in sp_f.items()}


def item_state(db: Database, item_id: int) -> dict:
    """判定の状態（取り消しの記録と持ち寄りに使う形）"""
    it = db.q1("SELECT status, verified_by, verified_at, note, assignee, flag, agree, recheck FROM items WHERE id=?", (item_id,))
    if it is None:
        return {}
    js = [dict(r) for r in db.q("SELECT id, species, confidence, origin, model, created_at, created_by FROM judgements WHERE item_id=? ORDER BY id", (item_id,))]
    item_f, sp_f = _field_values(db, item_id)
    for j in js:
        j["fields"] = sp_f.get(j["id"], {})
        del j["id"]
    return dict(status=it["status"], verified_by=it["verified_by"], verified_at=it["verified_at"], note=it["note"],
                assignee=it["assignee"], flag=it["flag"], agree=it["agree"], recheck=it["recheck"], judgements=js, fields=item_f)


def detail(db: Database, item_id: int) -> dict | None:
    r = db.q1("SELECT * FROM items WHERE id=?", (item_id,))
    if r is None:
        return None
    d = dict(r)
    d["pattern"] = json.loads(d.pop("pattern_json") or "{}")
    d["det"] = json.loads(d.pop("det_json") or "null")
    d["cls"] = json.loads(d.pop("cls_json") or "null")
    _parse_auto(d)
    st = item_state(db, item_id)
    d["judgements"] = st.get("judgements", [])
    d["fields"] = st.get("fields", {})
    d["auto_results"] = [dict(x) for x in db.q("SELECT species, confidence, model, created_at FROM auto_results WHERE item_id=? ORDER BY id", (item_id,))]
    d["history_n"] = int(db.scalar("SELECT COUNT(*) FROM history WHERE item_id=?", (item_id,)) or 0)
    return d


def _apply_state(c, item_id: int, state: dict, by: str | None) -> None:
    """判定の状態をそのまま書く（保存と取り消しの両方で使う）"""
    c.execute("DELETE FROM field_values WHERE item_id=?", (item_id,))
    c.execute("DELETE FROM judgements WHERE item_id=?", (item_id,))
    for j in state.get("judgements", []):
        cur = c.execute("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                        (item_id, j["species"], j.get("confidence"), j.get("origin") or "manual", j.get("model"),
                         j.get("created_at") or now_iso(), j.get("created_by") or by))
        jid = cur.lastrowid
        for k, vals in (j.get("fields") or {}).items():
            for i, v in enumerate(vals if isinstance(vals, list) else [vals]):
                if v is None or v == "":
                    continue
                c.execute("INSERT INTO field_values(item_id, judgement_id, field_key, seq, value) VALUES(?,?,?,?,?)", (item_id, jid, k, i, str(v)))
    for k, vals in (state.get("fields") or {}).items():
        for i, v in enumerate(vals if isinstance(vals, list) else [vals]):
            if v is None or v == "":
                continue
            c.execute("INSERT INTO field_values(item_id, judgement_id, field_key, seq, value) VALUES(?,?,?,?,?)", (item_id, None, k, i, str(v)))
    c.execute("UPDATE items SET status=?, verified_by=?, verified_at=?, note=?, assignee=?, flag=?, updated_at=? WHERE id=?",
              (state.get("status") or "new", state.get("verified_by"), state.get("verified_at"), state.get("note") or "",
               state.get("assignee"), state.get("flag"), now_iso(), item_id))


def save(db: Database, item_id: int, payload: dict, by: str) -> dict:
    """手動の判定を保存して「確認済み」にする。payload: species=[code...], fields={key:[...]}, species_fields={code:{key:[...]}}, note, assignee,
    status（省略 = verified。'new' で判定を消す）"""
    before = item_state(db, item_id)
    if not before:
        raise KeyError(item_id)
    codes = []
    for c in payload.get("species") or []:
        c = str(c).strip().lower()
        if c and c not in codes:
            codes.append(c)
    status = payload.get("status") or "verified"
    if status == "new":
        codes = []
    if status == "verified" and not codes:
        raise ValueError("種を 1 つ以上選ぶか、「動物なし」などを選んでください")
    # 「動物なし」はほかの種と一緒には付けない
    if "empty" in codes and len(codes) > 1:
        codes = [c for c in codes if c != "empty"]
    prev_by_code = {j["species"]: j for j in before.get("judgements", [])}
    js = []
    for c in codes:
        prev = prev_by_code.get(c)
        js.append(dict(species=c, confidence=(prev or {}).get("confidence"),
                       origin="manual", model=(prev or {}).get("model"),
                       created_at=now_iso(), created_by=by,
                       fields=(payload.get("species_fields") or {}).get(c) or {}))
    state = dict(status=status, verified_by=by if status == "verified" else None, verified_at=now_iso() if status == "verified" else None,
                 note=(payload.get("note") or "").strip(), assignee=payload.get("assignee", before.get("assignee")),
                 flag=None if status == "verified" else before.get("flag"), judgements=js, fields=payload.get("fields") or {})
    with db.tx() as c:
        _apply_state(c, item_id, state, by)
        cur = c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                        (item_id, now_iso(), "save", by, json.dumps(before, ensure_ascii=False), json.dumps(state, ensure_ascii=False)))
        hid = cur.lastrowid
    update_agree(db, [item_id])
    if status == "verified" and before.get("status") != "verified" and before.get("agree") in (2, 3):
        db.x("UPDATE items SET recheck=1 WHERE id=?", (item_id,))      # 食い違いを人が見直して確定した
    return dict(history_id=hid, state=item_state(db, item_id), agree=db.scalar("SELECT agree FROM items WHERE id=?", (item_id,)))


def set_auto(db: Database, item_id: int, results: list[dict], model: str, keep_verified: bool = True, keep_origins: tuple = ()) -> bool:
    """モデル（または CSV 取り込み）の判定を付ける。確認済みのものは変えない（記録だけ残す）。
    keep_origins に挙げた由来（例 "csv" = CSV / Timelapse で人が付けたもの）の判定が既にあれば、それも変えず記録だけ残す。
    返り値: 判定を書き換えたか"""
    it = db.q1("SELECT status FROM items WHERE id=?", (item_id,))
    if it is None:
        return False
    with db.tx() as c:
        if model.startswith("csv"):
            c.execute("DELETE FROM auto_results WHERE item_id=? AND model=?", (item_id, model))
        else:                      # モデルの判定は最新の 1 回分だけ残す（モデルを替えて判定し直したときに古いのと混ざらないように）
            c.execute("DELETE FROM auto_results WHERE item_id=? AND model NOT LIKE 'csv%'", (item_id,))
        for r in results:
            c.execute("INSERT INTO auto_results(item_id, species, confidence, model, created_at) VALUES(?,?,?,?,?)",
                      (item_id, r["species"], r.get("confidence"), model, now_iso()))
        if it["status"] == "verified" and keep_verified:
            return False
        if keep_origins:
            qs = ",".join("?" * len(keep_origins))
            if c.execute(f"SELECT 1 FROM judgements WHERE item_id=? AND origin IN ({qs}) LIMIT 1", (item_id, *keep_origins)).fetchone():
                return False
        c.execute("DELETE FROM judgements WHERE item_id=?", (item_id,))
        c.execute("DELETE FROM field_values WHERE item_id=? AND judgement_id IS NOT NULL", (item_id,))
        for r in results:
            c.execute("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                      (item_id, r["species"], r.get("confidence"), r.get("origin") or "auto", model, now_iso(), None))
        c.execute("UPDATE items SET status=?, updated_at=? WHERE id=?", ("auto" if results else "new", now_iso(), item_id))
    update_agree(db, [item_id])
    return True


def undo(db: Database, history_id: int | None, by: str) -> dict | None:
    """直前の保存を取り消す（history_id を省くと最後の save）"""
    if history_id is None:
        r = db.q1("SELECT * FROM history WHERE action='save' ORDER BY id DESC LIMIT 1")
    else:
        r = db.q1("SELECT * FROM history WHERE id=? AND action='save'", (history_id,))
    if r is None:
        return None
    undone = db.q1("SELECT 1 FROM history WHERE action='undo' AND after_json=? LIMIT 1", (str(r["id"]),))
    if undone:
        # もう取り消し済みなら、その前の save を探す
        prev = db.q1("SELECT * FROM history WHERE action='save' AND id<? AND id NOT IN (SELECT CAST(after_json AS INTEGER) FROM history WHERE action='undo') ORDER BY id DESC LIMIT 1", (r["id"],))
        if prev is None:
            return None
        r = prev
    before = json.loads(r["before_json"] or "{}")
    item_id = r["item_id"]
    with db.tx() as c:
        _apply_state(c, item_id, before, by)
        c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                  (item_id, now_iso(), "undo", by, None, str(r["id"])))
    update_agree(db, [item_id])
    return dict(item_id=item_id, state=item_state(db, item_id))


def bulk_verify_count(db: Database, f: dict) -> int:
    where, p = build_where(f)
    return int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND status='auto' AND EXISTS (SELECT 1 FROM judgements j WHERE j.item_id=items.id)", p) or 0)


def bulk_verify(db: Database, f: dict, by: str) -> int:
    """絞り込みに入っている「判定あり・未確認」の対象をまとめて確認済みにする（履歴は action='bulk'。Ctrl+Z の対象にしない）"""
    where, p = build_where(f)
    rows = db.q(f"SELECT id FROM items WHERE {where} AND status='auto' AND EXISTS (SELECT 1 FROM judgements j WHERE j.item_id=items.id)", p)
    ids = [r["id"] for r in rows]
    if not ids:
        return 0
    now = now_iso()
    with db.tx() as c:
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            qs = ",".join("?" * len(part))
            before = {r["id"]: r["agree"] for r in c.execute(f"SELECT id, agree FROM items WHERE id IN ({qs})", part).fetchall()}
            c.execute(f"UPDATE items SET status='verified', verified_by=?, verified_at=?, flag=CASE WHEN flag='low' THEN NULL ELSE flag END, updated_at=? WHERE id IN ({qs})",
                      (by, now, now, *part))
            c.execute(f"UPDATE judgements SET created_by=COALESCE(created_by, ?) WHERE item_id IN ({qs})", (by, *part))
            c.executemany("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                          [(i, now, "bulk", by, json.dumps(dict(status="auto", agree=before.get(i))), json.dumps(dict(status="verified"))) for i in part])
    update_agree(db, ids)
    # 食い違っていたものを一括で確定した場合も「見直し済み」にはしない（人が見ていないため）
    return len(ids)


def set_assignee(db: Database, f: dict, assignee: str) -> int:
    where, p = build_where(f)
    cur = db.x(f"UPDATE items SET assignee=? WHERE id IN (SELECT id FROM items WHERE {where})", (assignee or None, *p))
    return cur.rowcount


def apply_time_offset(db: Database, f: dict, offset_sec: int | None, note: str, by: str) -> int:
    """絞り込みに当てはまる対象に時刻の修正（ずれ）を付ける。offset_sec=None で修正を消す。元の日時（datetime）は変えない"""
    from .media import shift_dt
    where, p = build_where(dict(f, include_missing=True))
    rows = db.q(f"SELECT id, datetime FROM items WHERE {where}", p)
    n = 0
    with db.tx() as c:
        for r in rows:
            if offset_sec is None:
                c.execute("UPDATE items SET datetime_corrected=NULL, time_offset_sec=NULL, time_note=NULL, updated_at=? WHERE id=?", (now_iso(), r["id"]))
            else:
                c.execute("UPDATE items SET datetime_corrected=?, time_offset_sec=?, time_note=?, updated_at=? WHERE id=?",
                          (shift_dt(r["datetime"], offset_sec), int(offset_sec), (note or "").strip() or None, now_iso(), r["id"]))
            n += 1
        c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                  (0, now_iso(), "time", by, json.dumps(f, ensure_ascii=False), json.dumps(dict(offset_sec=offset_sec, note=note, n=n), ensure_ascii=False)))
    return n


def set_favorite(db: Database, item_id: int, on: bool) -> None:
    db.x("UPDATE items SET favorite=?, updated_at=? WHERE id=?", (1 if on else 0, now_iso(), item_id))


def set_exclude(db: Database, item_id: int, on: bool) -> None:
    db.x("UPDATE items SET train_exclude=?, updated_at=? WHERE id=?", (1 if on else 0, now_iso(), item_id))


def list_ids(db: Database, f: dict, sort: str, desc: bool, limit: int = 2000000) -> list[int]:
    """いまの絞り込み・並びでの id の列（全部選択・範囲選択用）"""
    where, p = build_where(f)
    _, order = order_sql(sort, desc)
    return [r["id"] for r in db.q(f"SELECT id FROM items WHERE {where} ORDER BY {order} LIMIT ?", (*p, int(limit)))]


def bulk_mark(db: Database, item_ids: list[int], action: str) -> int:
    """まとめて印を付ける: favorite / unfavorite / exclude / include"""
    if not item_ids:
        return 0
    col, val = {"favorite": ("favorite", 1), "unfavorite": ("favorite", 0), "exclude": ("train_exclude", 1), "include": ("train_exclude", 0)}[action]
    n = 0
    with db.tx() as c:
        for chunk in range(0, len(item_ids), 500):
            part = item_ids[chunk:chunk + 500]
            qs = ",".join("?" * len(part))
            n += c.execute(f"UPDATE items SET {col}=?, updated_at=? WHERE id IN ({qs})", (val, now_iso(), *part)).rowcount
    return n


def bulk_judge(db: Database, item_ids: list[int], payload: dict, by: str) -> dict:
    """選択した全部に同じ判定（payload は save と同じ形。status='new' なら消す）を付ける。1 枚ずつ save するので履歴も 1 枚ずつ残る"""
    n = failed = 0
    hids = []
    for iid in item_ids:
        try:
            res = save(db, iid, dict(payload), by)
            hids.append(res["history_id"])
            n += 1
        except Exception as e:
            failed += 1
            log.warning("まとめて判定: %s に失敗: %s", iid, e)
    return dict(n=n, failed=failed, history_ids=hids)


def delete_items(project, item_ids: list[int], to_trash: bool = True) -> dict:
    """動画（画像）をごみ箱へ（send2trash。なければ消す）、対象を DB から消す（判定・枠も一緒に）。再生用の変換とサムネイルも消す"""
    from .paths import abs_of
    from . import media
    db = project.db
    if not item_ids:
        return dict(n=0, failed=0, trashed=0)
    try:
        from send2trash import send2trash
    except Exception:
        send2trash = None
    n = failed = trashed = 0
    errors = []
    for chunk in range(0, len(item_ids), 500):
        part = item_ids[chunk:chunk + 500]
        qs = ",".join("?" * len(part))
        rows = db.q(f"SELECT id, rel_path FROM items WHERE id IN ({qs})", part)
        done = []
        for r in rows:
            src = abs_of(r["rel_path"], project.root)
            try:
                if os.path.isfile(src):
                    if to_trash and send2trash is not None:
                        send2trash(os.path.normpath(src))
                        trashed += 1
                    else:
                        os.remove(src)
                try:
                    vp = media.video_cache_path(project.cache_dir("video"), r["rel_path"], "h264")
                    if os.path.isfile(vp):
                        os.remove(vp)
                    tp = media.thumb_cache_path(project.cache_dir("thumb"), r["rel_path"])
                    if os.path.isfile(tp):
                        os.remove(tp)
                except Exception:
                    pass
                done.append(r["id"])
                n += 1
            except Exception as e:
                failed += 1
                errors.append(f"{r['rel_path']}: {e}")
        if done:
            with db.tx() as c:
                qs2 = ",".join("?" * len(done))
                for tbl in ("detections", "field_values", "auto_results", "judgements", "history"):
                    c.execute(f"DELETE FROM {tbl} WHERE item_id IN ({qs2})", done)
                c.execute(f"DELETE FROM items WHERE id IN ({qs2})", done)
    return dict(n=n, failed=failed, trashed=trashed, errors=errors[:5])


def set_note(db: Database, item_id: int, note: str) -> None:
    db.x("UPDATE items SET note=?, updated_at=? WHERE id=?", (note.strip(), now_iso(), item_id))
