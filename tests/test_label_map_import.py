"""別のプロジェクトから種名の対応表を持ってくる: こちらにない表記だけ足す / 上書き、対応先の種がなければ相手の種リストから写す"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross  # noqa: E402
from trapnote.project import Project  # noqa: E402


def _cross(tmp_path, name):
    place = tmp_path / "Studies" / name
    place.mkdir(parents=True)
    return Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")


def test_import_label_map(tmp_path):
    a = _cross(tmp_path, "a")
    try:
        kiji = a.species.add(dict(name_ja="キジ", name_en="Kiji", name_sci="Phasianus versicolor"))
        cross.set_label_map(a, {"RaccoonDog": "raccoon_dog", "Bird": "bird", "Pheasant": kiji["code"], "Sika": "deer"})
        a_root = a.root
    finally:
        a.close()
    b = _cross(tmp_path, "b")
    try:
        with pytest.raises(ValueError):
            cross.import_label_map(b, str(tmp_path / "Studies" / "nowhere"))
        cross.set_label_map(b, {"Sika": "boar"})                         # わざと違う対応
        r = cross.import_label_map(b, a_root)
        print(r)
        lm = cross.label_map(b)
        assert r["n_added"] == 3 and r["n_kept"] == 1 and r["n_updated"] == 0 and lm["sika"] == "boar" and lm["raccoondog"] == "raccoon_dog"
        assert "kiji" in b.species.by_code() and lm["pheasant"] == "kiji" and r["species_added"] == ["kiji"]     # 種リストにない種は相手から写した
        r2 = cross.import_label_map(b, a_root, overwrite=True)
        assert r2["n_updated"] == 1 and cross.label_map(b)["sika"] == "deer" and r2["n_added"] == 0
    finally:
        b.close()
