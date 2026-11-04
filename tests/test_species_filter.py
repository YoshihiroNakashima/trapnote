"""一覧の種の絞り込み: 1 種 / 複数（"deer+boar" = 全部付いている動画、"deer,boar" = どれかが付いている動画）/ none / any"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote.judge import build_where, save as save_judgement  # noqa: E402
from trapnote.project import Project  # noqa: E402


def test_species_filter_multi(tmp_path):
    root = tmp_path / "N240128"
    (root / "BSN0001").mkdir(parents=True)
    for i in range(4):
        (root / "BSN0001" / f"IMG_000{i}.AVI").write_bytes(b"")
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    try:
        from trapnote import scan
        scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
        ids = sorted(r[0] for r in p.db.q("SELECT id FROM items ORDER BY name"))
        save_judgement(p.db, ids[0], dict(species=["deer"]), "me")
        save_judgement(p.db, ids[1], dict(species=["deer", "boar"]), "me")
        save_judgement(p.db, ids[2], dict(species=["boar"]), "me")
        # ids[3] は判定なし

        def q(f):
            w, prm = build_where(f)
            return sorted(r[0] for r in p.db.q(f"SELECT id FROM items WHERE {w}", prm))
        assert q({"species": "deer"}) == sorted([ids[0], ids[1]])
        assert q({"species": "deer+boar"}) == [ids[1]]                        # 2 種が同時に映った動画
        assert q({"species": "deer,boar"}) == sorted([ids[0], ids[1], ids[2]])       # どちらか
        assert q({"species": ["deer", "boar"]}) == sorted([ids[0], ids[1], ids[2]])
        assert q({"species": "none"}) == [ids[3]]
        assert q({"species": "any"}) == sorted([ids[0], ids[1], ids[2]])
        assert q({"species": ""}) == ids
    finally:
        p.close()
