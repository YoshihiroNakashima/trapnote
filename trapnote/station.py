"""地点のフォルダを選ぶ（確認事項 125 回目で決めたもの。129 回目で作った）

フォルダを開いたときに、1 本の動画までのフォルダの並びを見せて、地点（カメラを置いた所）にあたるフォルダと、あれば調査回にあたるフォルダを、押して選んでもらう。
選んだ階層は「ルートから数えて何段目か」で覚える。保存の形は、フォルダ名のパターン（project.json の settings.pattern）で、先頭に ^ を付けたもの
（pattern.py。^ = ルートの直下から段ごとに当てる）: 2 段目が地点なら ^*/{station}、1 段目が調査回で 2 段目が地点なら ^{season}/{station}。

選ばないこともできる（パターンは空）。そのときは、地点は付かず、CSV の書き出しに、フォルダの各階層が Folder1, Folder2 … の列で出る（export.py）。
公開する版（公開版と Lite）では、パターンを手で書く設定は画面に出さない。研究用の版では、どちらも使える。
"""
from __future__ import annotations

import os
from collections import Counter

from . import scan
from .paths import norm
from .pattern import compile_pattern, names_in

DEFAULT_PATTERN = "{station}/{file}"
MAX_NAMES = 6


def folder_levels(db) -> tuple[list[dict], dict[int, int], int]:
    """フォルダの階層ごとの様子。返り値: (階層ごとの dict(n=フォルダ名の数, names=はじめの数個), 深さ → 動画の数, 動画の数)"""
    rows = db.q("SELECT folder, COUNT(*) AS n FROM items WHERE missing=0 GROUP BY folder")
    names: list[dict[str, int]] = []
    depths: Counter = Counter()
    total = 0
    for r in rows:
        segs = [s for s in (r["folder"] or "").split("/") if s]
        depths[len(segs)] += r["n"]
        total += r["n"]
        for i, s in enumerate(segs):
            while len(names) <= i:
                names.append({})
            names[i][s] = names[i].get(s, 0) + r["n"]
    levels = [dict(n=len(d), names=sorted(d)[:MAX_NAMES]) for d in names]
    return levels, dict(depths), total


def example_item(db, depths: dict[int, int]) -> dict | None:
    """例に見せる動画: いちばん多い深さのフォルダにある動画のうち、名前の順で最初の 1 本"""
    if not depths:
        return None
    depth = max(depths.items(), key=lambda kv: (kv[1], kv[0]))[0]
    rows = db.q("SELECT rel_path, folder, name FROM items WHERE missing=0 ORDER BY rel_path LIMIT 5000")
    for r in rows:
        if len([s for s in (r["folder"] or "").split("/") if s]) == depth:
            return dict(rel_path=r["rel_path"], dirs=[s for s in (r["folder"] or "").split("/") if s], file=r["name"])
    r = rows[0] if rows else None
    return dict(rel_path=r["rel_path"], dirs=[s for s in (r["folder"] or "").split("/") if s], file=r["name"]) if r else None


def levels_of(pattern: str, n_dirs: int) -> dict:
    """いまのパターンが、フォルダが n_dirs 段ある動画で、何段目（0 から）を地点・調査回にしているか。当てはまる段がなければ None"""
    comp = compile_pattern(pattern or "")
    out = dict(station=None, season=None)
    n = len(comp)
    for i, pat in enumerate(comp):
        for k in ("station", "season"):
            if k in pat.groupindex:
                lv = i if getattr(comp, "from_root", False) else (n_dirs + 1) - n + i          # 後ろから当てる形は、ファイル名の段も数える
                out[k] = lv if 0 <= lv < n_dirs else None
    return out


def make_pattern(station_level: int | None, season_level: int | None) -> str:
    """選んだ階層（0 = ルートの直下）から、パターンを作る。どちらも選ばなければ空"""
    lv = [x for x in (station_level, season_level) if x is not None]
    if not lv:
        return ""
    if station_level is not None and station_level == season_level:
        raise ValueError("地点と調査回に、同じ階層は選べません")
    segs = []
    for i in range(max(lv) + 1):
        segs.append("{station}" if i == station_level else ("{season}" if i == season_level else "*"))
    return "^" + "/".join(segs)


def info(project) -> dict:
    db = project.db
    levels, depths, total = folder_levels(db)
    ex = example_item(db, depths)
    pattern = project.setting("pattern") or ""
    cur = levels_of(pattern, len(ex["dirs"]) if ex else 0)
    simple = pattern in ("", DEFAULT_PATTERN, "{season}/{station}/{file}") or pattern.startswith("^")       # この画面で作れる形か、もとからの既定か
    return dict(root=project.root, root_name=os.path.basename(norm(project.root).rstrip("/")) if project.root else "",
                example=ex, levels=levels, depths=depths, n_items=total, pattern=pattern, custom=not simple,
                station_level=cur["station"], season_level=cur["season"], has_station="station" in names_in(pattern),
                asked=project.data.get("station_asked") is not False)


def preview(project, station_level: int | None, season_level: int | None, n: int = 8) -> dict:
    """選んだ階層で、地点と調査回がどう付くかの見本（フォルダごとに 1 本、はじめの n 個）と、地点・調査回の数"""
    from .pattern import extract
    pattern = make_pattern(station_level, season_level)
    comp = compile_pattern(pattern)
    season0 = scan.default_season(project) if "season" not in names_in(pattern) else None
    rows, stations, seasons, n_no_station = [], set(), set(), 0
    for r in project.db.q("SELECT folder, MIN(rel_path) AS rel_path, COUNT(*) AS n FROM items WHERE missing=0 GROUP BY folder ORDER BY folder"):
        pat = extract(r["rel_path"], comp)
        st, se = pat.get("station") or "", pat.get("season") or season0 or ""
        if st:
            stations.add(st)
        elif station_level is not None:
            n_no_station += r["n"]
        if se:
            seasons.add(se)
        if len(rows) < n:
            rows.append(dict(folder=r["folder"] or "", n=r["n"], station=st, season=se))
    return dict(pattern=pattern, rows=rows, n_stations=len(stations), n_seasons=len(seasons), n_no_station=n_no_station)


def pick(project, station_level: int | None, season_level: int | None) -> dict:
    """選んだ階層を保存して、全部の動画の地点と調査回を付け直す。地点を選ばなければ、地点は空にする"""
    if project.is_cross:
        raise ValueError("このプロジェクトでは、地点のフォルダは選べません（調査回のプロジェクトで選びます）")
    pattern = make_pattern(station_level, season_level)
    project.update_settings({"pattern": pattern})
    n = scan.apply_pattern(project)
    names = names_in(pattern)
    db = project.db
    if "station" not in names:                 # apply_pattern は、パターンに {station} がないと前の値を残す（画像名から付けた値のため）。ここでは、選ばなかった = 付けない
        db.x("UPDATE items SET station=NULL WHERE station IS NOT NULL")
    if "season" not in names:                  # 同じく、調査回は既定（設定の名前か、ルートのフォルダ名）に戻す
        db.x("UPDATE items SET season=?", (scan.default_season(project),))
    project.data["station_asked"] = True
    project.save()
    return dict(pattern=pattern, n=n)


def skip(project) -> dict:
    """選ばずに閉じた（あとで）: もう自動では訊かない。パターンは変えない"""
    if project.data.get("station_asked") is False:
        project.data["station_asked"] = True
        project.save()
    return dict(ok=True)
