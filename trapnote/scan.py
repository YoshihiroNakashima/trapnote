"""フォルダの走査と data.sqlite への登録（差分）、フォルダ名のパターンの適用"""
from __future__ import annotations

import json
import logging
import os
import time

from .db import now_iso
from .paths import kind_of, norm, rel_key, rel_of
from .pattern import compile_pattern, extract

log = logging.getLogger(__name__)


def walk_media(root: str, skip_names, progress=None, cancel=None) -> list[tuple[str, str, str, str, int, float]]:
    """(rel_path, name, kind, folder, size, mtime) を集める。TrapNote / StayTime などの出力フォルダと隠しフォルダは飛ばす"""
    root = norm(root)
    skip = {str(s).lower() for s in (skip_names or []) if s}
    found = []
    t_last = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in skip)
        if "info.json" in filenames and "frames.csv" in filenames:    # areacheck の滞在時間パイプラインの出力は対象外
            dirnames[:] = []
            continue
        if norm(dirpath) != root and "project.json" in filenames:       # 名前を変えたプロジェクトのフォルダ（書き出しで付けた名前など）も対象外
            dirnames[:] = []
            continue
        rel_dir = rel_of(norm(dirpath), root)
        if rel_dir == norm(dirpath):
            rel_dir = ""
        with os.scandir(dirpath) as it:
            for e in it:
                if not e.is_file(follow_symlinks=False):
                    continue
                fn = e.name
                if fn.startswith(".") or fn.startswith("~$"):
                    continue
                k = kind_of(fn)
                if not k:
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                rp = f"{rel_dir}/{fn}" if rel_dir else fn
                found.append((rp, fn, k, rel_dir, st.st_size, st.st_mtime))
        now = time.time()
        if progress and now - t_last > 0.3:
            t_last = now
            progress(len(found), rel_dir)
        if cancel and cancel():
            break
    return found


def default_season(project) -> str | None:
    """パターンに {season} がないときの調査回: 設定 season_name、なければルートのフォルダ名（ルート = 調査回のフォルダという置き方）"""
    from .pattern import names_in
    if "season" in names_in(project.setting("pattern") or ""):
        return None
    return (project.setting("season_name") or "").strip() or (os.path.basename(norm(project.root).rstrip("/")) if project.root else None) or None


def register(project, found, progress=None) -> dict:
    """走査の結果を data.sqlite に反映する。新規は追加、消えたものは missing=1、戻ってきたものは missing=0。
    サイズや更新時刻が変わったものはメタデータを取り直す（判定は残す）"""
    db = project.db
    comp = compile_pattern(project.setting("pattern") or "")
    season0 = default_season(project)
    existing = {r["rel_key"]: (r["id"], r["size"], r["mtime"], r["missing"]) for r in db.q("SELECT id, rel_key, size, mtime, missing FROM items")}
    # 保存用の変換で置き換えたあとに残した元のファイル（AVI）は、もう対象にしない
    converted = {rel_key(r[0]) for r in db.q("SELECT orig_rel_path FROM items WHERE orig_rel_path IS NOT NULL")}
    seen = set()
    new_rows, changed, back = [], [], []
    now = now_iso()
    for rp, fn, k, folder, size, mtime in found:
        rk = rel_key(rp)
        if rk in converted:
            continue
        seen.add(rk)
        ex = existing.get(rk)
        if ex is None:
            pat = extract(rp, comp)
            new_rows.append((rp, rk, fn, k, folder, size, mtime, pat.get("season") or season0, pat.get("station") or None,
                             json.dumps({a: b for a, b in pat.items() if a not in ("season", "station")}, ensure_ascii=False), now))
        else:
            iid, osize, omtime, omissing = ex
            if omissing:
                back.append(iid)
            if (osize is not None and size != osize) or (omtime is not None and abs(float(mtime) - float(omtime)) > 1.0):
                changed.append((size, mtime, now, iid))
    gone = [v[0] for k, v in existing.items() if k not in seen and not v[3]]
    with db.tx() as c:
        if new_rows:
            c.executemany("INSERT INTO items(rel_path, rel_key, name, kind, folder, size, mtime, season, station, pattern_json, updated_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?)", new_rows)
        if back:
            c.executemany("UPDATE items SET missing=0 WHERE id=?", [(i,) for i in back])
        if changed:
            c.executemany("UPDATE items SET size=?, mtime=?, meta_state=0, updated_at=? WHERE id=?", changed)
        if gone:
            c.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
    db.set_meta("last_scan", now)
    return dict(found=len(found), added=len(new_rows), back=len(back), changed=len(changed), gone=len(gone))


def apply_pattern(project) -> int:
    """パターンを変えたとき、全対象の season / station を付け直す"""
    from .pattern import names_in
    db = project.db
    pattern = project.setting("pattern") or ""
    comp = compile_pattern(pattern)
    names = names_in(pattern)
    rows = db.q("SELECT id, rel_path, season, station, pattern_json FROM items")
    season0 = default_season(project)
    upd = []
    for r in rows:
        pat = extract(r["rel_path"], comp)
        try:
            old = json.loads(r["pattern_json"] or "{}")
        except Exception:
            old = {}
        keep = {k: v for k, v in old.items() if k not in pat}        # 画像名などから付けた値（YOLO の取り込み）は残す
        keep.update({a: b for a, b in pat.items() if a not in ("season", "station")})
        # パターンに {season} / {station} がなければ、画像名などから付けてある季節・地点はそのまま残す
        season = (pat.get("season") or None) if "season" in names else (r["season"] or season0)
        station = (pat.get("station") or None) if "station" in names else r["station"]
        upd.append((season, station, json.dumps(keep, ensure_ascii=False), r["id"]))
    with db.tx() as c:
        c.executemany("UPDATE items SET season=?, station=?, pattern_json=? WHERE id=?", upd)
    return len(upd)


def sample_rel_paths(project, n: int = 8) -> list[str]:
    rows = project.db.q("SELECT rel_path FROM items ORDER BY id LIMIT ?", (n,))
    return [r["rel_path"] for r in rows]
