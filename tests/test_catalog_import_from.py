"""別の横断プロジェクトからカタログを持ってくる: 参照先・対応表・種リスト・行・種名 CSV のキャッシュが写り、取り込み済みの印はこちらの対象で付く。名前が重なって場所が違えば止まる"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross  # noqa: E402
from trapnote.project import Project  # noqa: E402

from test_cross_study import make_nas, make_cross  # noqa: E402


def test_import_catalog_from(tmp_path):
    root, rows = make_nas(tmp_path)
    a = make_cross(tmp_path)                                   # 元: カタログあり（種名 CSV を読んでキャッシュもできている）
    try:
        cross.set_label_map(a, {"RaccoonDog": "raccoon_dog"})
        n_a = a.db.scalar("SELECT COUNT(*) FROM catalog")
        assert n_a == 48
        a_dir, a_root = a.dir, a.root
        cat_ids = [r["id"] for r in a.db.q("SELECT id FROM catalog ORDER BY id LIMIT 3")]
    finally:
        a.close()
    place = tmp_path / "Studies" / "season"
    place.mkdir(parents=True)
    b = Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")
    try:
        with pytest.raises(ValueError):
            cross.prepare_catalog_import(b, str(tmp_path / "Studies" / "nowhere"))
        prep = cross.prepare_catalog_import(b, a_root)
        print(prep)
        assert prep["sources_added"] == ["PRM_N"] and prep["lists_copied"] and cross.label_map(b).get("raccoondog") == "raccoon_dog"
        r = cross.import_catalog_from(b, a_root)
        print(r)
        assert r["n"] == 48 and r["n_cache"] >= 1 and "labels" not in r["stale"] and "sources" not in r["stale"]
        st = cross.catalog_status(b)
        assert st["built"] and not st["stale"] and st["n"] == 48
        assert b.data["catalog"]["imported_from"]["dir"] == a_dir
        # 種名 CSV のキャッシュがそのまま効く（読み直さない）
        assert cross.labels_ready(b, cross.source_by_name(b, "PRM_N"))
        # こちらで取り込むと印が付く。もう一度持ってきても印は残る
        cross.catalog_import(b, cat_ids, "g")
        assert b.db.scalar("SELECT COUNT(*) FROM catalog WHERE item_id IS NOT NULL") == 3
        cross.import_catalog_from(b, a_root)
        assert b.db.scalar("SELECT COUNT(*) FROM catalog WHERE item_id IS NOT NULL") == 3 and b.db.scalar("SELECT COUNT(*) FROM catalog") == 48
        # 同じ名前で場所が違う参照先があれば止まる
        c_place = tmp_path / "Studies" / "other"
        c_place.mkdir(parents=True)
        c = Project(str(c_place / "TrapNote"), root=str(c_place), create=True, kind="cross")
        try:
            (tmp_path / "nas2" / "PRM_N" / "N250101" / "BSN0001").mkdir(parents=True)
            cross.add_source(c, str(tmp_path / "nas2" / "PRM_N"))
            assert c.sources()[0]["name"] == "PRM_N"
            with pytest.raises(ValueError):
                cross.prepare_catalog_import(c, a_root)
        finally:
            c.close()
    finally:
        b.close()
