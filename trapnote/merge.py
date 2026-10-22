"""判定の持ち寄りと統合（複数人で分担するとき）

書き出し: 判定・追加項目の値・確認者だけを 1 つの JSON（judgements_<名前>_<日付>.json）に出す。メディアと検出結果は含まない
統合: 相対パス（補助にサイズと長さ）で照合し、確認済み > 自動判定 > 未処理 の規則で取り込む。
      両方が確認済みで内容が違うものは自動で決めず、衝突として残して人が選ぶ"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from . import VERSION
from .db import Database, now_iso
from .judge import _apply_state, item_state, update_agree
from .paths import rel_key, safe_name

log = logging.getLogger(__name__)
RANK = {"new": 0, "auto": 1, "verified": 2}


def export_judgements(project, path: str | None, by: str, f: dict | None = None) -> dict:
    db = project.db
    from .judge import build_where
    where, p = build_where(dict(f or {}, include_missing=True))
    rows = db.q(f"SELECT id, rel_path, size, duration, status FROM items WHERE {where} AND (status != 'new' OR favorite = 1 OR time_offset_sec IS NOT NULL)", p)
    items = []
    for r in rows:
        st = item_state(db, r["id"])
        ex = db.q1("SELECT favorite, datetime_corrected, time_offset_sec, time_note FROM items WHERE id=?", (r["id"],))
        items.append(dict(rel_path=r["rel_path"], size=r["size"], duration=r["duration"], favorite=int(ex["favorite"] or 0),
                          datetime_corrected=ex["datetime_corrected"], time_offset_sec=ex["time_offset_sec"], time_note=ex["time_note"], **st))
    if not path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        path = os.path.join(project.dir, "exports", f"judgements_{safe_name(by or 'user')}_{stamp}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dict(app="trapnote", version=VERSION, kind="judgements", by=by, exported_at=now_iso(), project=project.name,
                n=len(items), species=project.species.rows, fields=project.fields.fields, items=items)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return dict(path=path, n=len(items))


def _same(a: dict, b: dict) -> bool:
    ka = sorted((j["species"], json.dumps(j.get("fields") or {}, sort_keys=True, ensure_ascii=False)) for j in a.get("judgements", []))
    kb = sorted((j["species"], json.dumps(j.get("fields") or {}, sort_keys=True, ensure_ascii=False)) for j in b.get("judgements", []))
    return ka == kb and (a.get("fields") or {}) == (b.get("fields") or {}) and (a.get("note") or "") == (b.get("note") or "")


def import_judgements(project, path: str, by: str) -> dict:
    db = project.db
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("kind") != "judgements":
        raise ValueError("TrapNote の判定ファイルではありません")
    keymap: dict = {}
    stemmap: dict = {}
    for r in db.q("SELECT id, rel_key, rel_path, orig_rel_path, size, duration FROM items"):
        keymap[r["rel_key"]] = (r["id"], r["size"], r["duration"])
        if r["orig_rel_path"]:
            keymap.setdefault(rel_key(r["orig_rel_path"]), (r["id"], None, r["duration"]))      # 変換前の名前。サイズは比べない
        stem = rel_key(os.path.splitext(r["rel_path"])[0])
        stemmap[stem] = None if stem in stemmap else (r["id"], None, r["duration"])
    known = project.species.by_code()
    n_applied = n_kept = n_conflict = n_missing = n_same = 0
    conflicts = json.loads(db.get_meta("conflicts") or "{}")
    with db.tx() as c:
        for e in data.get("items", []):
            hit = keymap.get(rel_key(e.get("rel_path", ""))) or stemmap.get(rel_key(os.path.splitext(e.get("rel_path", ""))[0]))
            if hit is None:
                n_missing += 1
                continue
            iid, size, dur = hit
            if e.get("size") is not None and size is not None and int(e["size"]) != int(size):
                log.info("サイズが違うので飛ばします: %s", e.get("rel_path"))
                n_missing += 1
                continue
            for j in e.get("judgements", []):
                if j["species"] not in known:
                    log.info("種リストにない種 %s（%s）", j["species"], e.get("rel_path"))
            if e.get("favorite"):
                c.execute("UPDATE items SET favorite=1 WHERE id=?", (iid,))
            if e.get("time_offset_sec") is not None:
                mine_off = c.execute("SELECT time_offset_sec, datetime FROM items WHERE id=?", (iid,)).fetchone()
                if mine_off and mine_off[0] is None:
                    from .media import shift_dt
                    c.execute("UPDATE items SET datetime_corrected=?, time_offset_sec=?, time_note=? WHERE id=?",
                              (shift_dt(mine_off[1], e["time_offset_sec"]), int(e["time_offset_sec"]), e.get("time_note"), iid))
            mine = item_state(db, iid)
            theirs = dict(status=e.get("status") or "new", verified_by=e.get("verified_by"), verified_at=e.get("verified_at"),
                          note=e.get("note") or "", assignee=mine.get("assignee") or e.get("assignee"), flag=e.get("flag"),
                          judgements=e.get("judgements", []), fields=e.get("fields", {}))
            rm, rt = RANK.get(mine.get("status"), 0), RANK.get(theirs["status"], 0)
            if rt > rm:
                _apply_state(c, iid, theirs, by)
                n_applied += 1
            elif rt < rm:
                n_kept += 1
            elif rm == 2:       # 両方が確認済み
                if _same(mine, theirs):
                    n_same += 1
                else:
                    conflicts[str(iid)] = dict(theirs=theirs, from_file=os.path.basename(path), by=data.get("by"), at=now_iso())
                    n_conflict += 1
            elif _same(mine, theirs):
                n_same += 1
            else:               # 両方が自動判定・未処理で中身が違う: 判定のある方、同じなら新しい方
                if theirs["judgements"] and (not mine.get("judgements") or (theirs.get("verified_at") or "") >= (mine.get("verified_at") or "")):
                    _apply_state(c, iid, theirs, by)
                    n_applied += 1
                else:
                    n_kept += 1
            c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                      (iid, now_iso(), "merge", by, json.dumps(mine, ensure_ascii=False), json.dumps(theirs, ensure_ascii=False)))
    db.set_meta("conflicts", json.dumps(conflicts, ensure_ascii=False))
    update_agree(db)
    return dict(applied=n_applied, kept=n_kept, same=n_same, conflicts=n_conflict, missing=n_missing, total=len(data.get("items", [])),
                by=data.get("by"), exported_at=data.get("exported_at"))


def list_conflicts(db: Database) -> list[dict]:
    conflicts = json.loads(db.get_meta("conflicts") or "{}")
    out = []
    for iid, d in conflicts.items():
        r = db.q1("SELECT id, rel_path, name, station, datetime FROM items WHERE id=?", (int(iid),))
        if r is None:
            continue
        out.append(dict(item=dict(r), mine=item_state(db, int(iid)), theirs=d["theirs"], by=d.get("by"), from_file=d.get("from_file")))
    return out


def resolve_conflict(db: Database, item_id: int, choice: str, by: str) -> None:
    conflicts = json.loads(db.get_meta("conflicts") or "{}")
    d = conflicts.pop(str(item_id), None)
    if d and choice == "theirs":
        with db.tx() as c:
            before = item_state(db, item_id)
            _apply_state(c, item_id, d["theirs"], by)
            c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                      (item_id, now_iso(), "resolve", by, json.dumps(before, ensure_ascii=False), json.dumps(d["theirs"], ensure_ascii=False)))
        update_agree(db, [item_id])
    db.set_meta("conflicts", json.dumps(conflicts, ensure_ascii=False))
