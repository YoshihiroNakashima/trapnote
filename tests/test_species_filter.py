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
        assert q({"species": "multi"}) == [ids[1]]                            # 2 種以上
        save_judgement(p.db, ids[2], dict(species=["boar", "human"]), "me")
        assert q({"species": "multi"}) == [ids[1]]                            # 人は数えない
        # 人の判定と AI の判定を分ける: ids[3] は AI が deer + boar と見て（auto_json）、人はまだ（judgements は origin=auto の行だけ）
        import json
        auto = json.dumps(dict(model="m", species=[dict(species="deer", confidence=0.8), dict(species="boar", confidence=0.6)]))
        p.db.x("UPDATE items SET auto_json=?, status='auto' WHERE id=?", (auto, ids[3]))
        with p.db.tx() as c:
            for sp in ("deer", "boar"):
                c.execute("INSERT INTO judgements(item_id, species, origin, created_at) VALUES(?,?,?,?)", (ids[3], sp, "auto", "2026-11-04T00:00:00"))
        assert q({"species": "multi"}) == [ids[1]]                            # 人の判定では ids[3] は 2 種ではない（AI の行は数えない）
        assert q({"species": "multi", "species_src": "ai"}) == [ids[3]]       # AI の判定では ids[3] だけ
        assert q({"species": "deer", "species_src": "ai"}) == [ids[3]]
        assert q({"species": "deer+boar", "species_src": "ai"}) == [ids[3]]
        assert q({"species": "none", "species_src": "ai"}) == [ids[0], ids[1], ids[2]]   # AI の判定がない
        assert q({"species": "none"}) == [ids[3]]                              # 人の判定がない
        assert q({"species": "any"}) == sorted([ids[0], ids[1], ids[2]])
        assert q({"species": ""}) == ids
    finally:
        p.close()
