"""親フォルダ参照の NAS を見る分（tree_scan）: 結果は cache/nas_seen.json に残り、キャッシュが切れていれば前回の結果ですぐ返して（stale）裏で見直す。
tree_season_project は <調査回>/TrapNote だけ見る（地点フォルダを全部 stat しない）"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross  # noqa: E402
from trapnote.project import Project  # noqa: E402

from test_cross_study import make_nas  # noqa: E402


def test_tree_scan_snapshot_and_background(tmp_path, monkeypatch):
    root, rows = make_nas(tmp_path)
    place = tmp_path / "Studies" / "x"
    place.mkdir(parents=True)
    p = Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")
    try:
        cross.add_sources_under(p, root)
        s = cross.source_by_name(p, "PRM_N")
        v = cross.tree_scan(p, s)
        assert v["stale"] is False and v["seasons"] == ["N240128", "N240415"] and v["proj_seasons"] == [] and [f[0] for f in v["files"]] == [cross.norm(os.path.join(root, "labels.csv"))]
        seen = json.load(open(os.path.join(p.dir, "cache", "nas_seen.json"), encoding="utf-8"))
        assert seen["PRM_N"]["seasons"] == ["N240128", "N240415"]
        # 2 回目はキャッシュ（NAS を見ない）
        calls = []
        orig = cross._tree_scan_now
        monkeypatch.setattr(cross, "_tree_scan_now", lambda s_: (calls.append(1), orig(s_))[1])
        assert cross.tree_scan(p, s)["stale"] is False and not calls
        # キャッシュが切れた → 前回の結果（stale）をすぐ返し、裏で見直す
        cross.clear_fs_cache()
        # 調査回を 1 つ足しておく: 前回の結果には無く、見直しの後にはある
        (tmp_path / "nas" / "PRM_N" / "N250101" / "BSN0001").mkdir(parents=True)
        v2 = cross.tree_scan(p, s)
        assert v2["stale"] is True and v2["seasons"] == ["N240128", "N240415"]
        for _ in range(100):
            if calls and not cross._SCANNING:
                break
            time.sleep(0.05)
        assert calls
        v3 = cross.tree_scan(p, s)
        assert v3["stale"] is False and v3["seasons"] == ["N240128", "N240415", "N250101"]
        # 窓の表にも stale と seen_at が出る
        cross.clear_fs_cache()
        info = cross.sources_info(p)[0]
        assert info["stale"] is True and info["seen_at"] and info["seasons"] == ["N240128", "N240415", "N250101"]
        for _ in range(100):
            if not cross._SCANNING:
                break
            time.sleep(0.05)
        # 見直す（今見る）
        r = cross.refresh_sources(p)
        assert "PRM_N" in r["elapsed"] and cross.sources_info(p)[0]["stale"] is False
    finally:
        p.close()


def test_tree_season_project_only_trapnote_dir(tmp_path):
    root, rows = make_nas(tmp_path)
    # 地点フォルダの中に「名前を付けたプロジェクト」があっても、調査回のプロジェクトとは見なさない（NAS で地点フォルダを全部 stat しないため）
    d = tmp_path / "nas" / "PRM_N" / "N240128" / "BSN0812" / "Named"
    d.mkdir(parents=True)
    json.dump(dict(kind="folder", root_is_parent=True), open(d / "project.json", "w"))
    assert cross.tree_season_project(root, "N240128") is None
    sp = tmp_path / "nas" / "PRM_N" / "N240415"
    q = Project(str(sp / "TrapNote"), root=str(sp), create=True)
    q.close()
    cross.clear_fs_cache()
    assert cross.tree_season_project(root, "N240415") == cross.norm(str(sp / "TrapNote"))
    # 横断プロジェクトなら None
    cx = tmp_path / "nas" / "PRM_N" / "N240128"
    c = Project(str(cx / "TrapNote"), root=str(cx), create=True, kind="cross")
    c.close()
    cross.clear_fs_cache()
    assert cross.tree_season_project(root, "N240128") is None
