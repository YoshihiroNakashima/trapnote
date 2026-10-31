"""調査回のフォルダを 1 つだけ参照先に登録する: 親フォルダを参照先にしてその調査回だけを見る（親フォルダの種名 CSV も使う）。
カタログにはその調査回だけ。もう 1 つ足せば調査回が増え、親フォルダを登録すれば全部に広がる。地点のフォルダは断る"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross  # noqa: E402
from trapnote.project import Project  # noqa: E402

from test_cross_study import make_nas  # noqa: E402


def test_single_season_folder(tmp_path):
    root, rows = make_nas(tmp_path)
    place = tmp_path / "Studies" / "season_gold"
    place.mkdir(parents=True)
    p = Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")
    try:
        assert cross.classify_folder(root) == "parent" and cross.classify_folder(os.path.join(root, "N240128")) == "season" and cross.classify_folder(os.path.join(root, "N240128", "BSN0812")) == "station"
        with pytest.raises(ValueError):
            cross.add_source(p, os.path.join(root, "N240128", "BSN0812"))
        r = cross.add_source(p, os.path.join(root, "N240128"))
        print(r)
        assert r["added"] and r["kind"] == "tree" and r["season"] == "N240128" and cross.norm(r["root"]) == cross.norm(root) and r["name"] == "PRM_N"
        s = cross.source_by_name(p, "PRM_N")
        assert s["seasons"] == ["N240128"] and cross.source_seasons(s) == ["N240128"]
        info = cross.sources_info(p, wait=True)[0]
        assert info["seasons"] == ["N240128"] and info["only_seasons"] == ["N240128"] and info["labels"]["n_here"] == 24     # 親フォルダの CSV のうちこの調査回の分だけ
        cat = cross.build_catalog(p)
        assert cat["n"] == 24 and p.db.scalar("SELECT COUNT(DISTINCT season) FROM catalog") == 1
        # 同じ調査回をもう一度 → 登録済み。別の調査回 → 足す
        assert cross.add_source(p, os.path.join(root, "N240128"))["added"] is False
        r2 = cross.add_source(p, os.path.join(root, "N240415"))
        assert r2["added"] and r2["seasons"] == ["N240128", "N240415"] and len(p.sources()) == 1
        cat2 = cross.build_catalog(p)
        assert cat2["n"] == 48
        # 「この親フォルダを登録」で調査回のフォルダを渡しても同じ
        ru = cross.add_sources_under(p, os.path.join(root, "N240415"))
        assert ru["season"] == "N240415" and ru["skipped"] == ["PRM_N"]
        # 親フォルダを登録 → 全部に広がる
        r3 = cross.add_source(p, root)
        assert r3["added"] and r3["widened"] and "seasons" not in cross.source_by_name(p, "PRM_N")
        # 親フォルダごと登録済みのところに調査回を足そうとした → covered
        r4 = cross.add_source(p, os.path.join(root, "N240128"))
        assert r4["added"] is False and r4["covered"]
    finally:
        p.close()
