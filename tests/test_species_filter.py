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


def test_det_crops_and_train_filters(tmp_path):
    """検出の絞り込み「学習枠あり」（分類器に入れた枠がある = 橙の学習フレームがある）と「学習に入る」（確認済み・学習枠あり・外していない・不明でない）、並べ替え「学習枠の数」"""
    import json
    from trapnote.judge import order_sql
    root = tmp_path / "N240128"
    (root / "BSN0001").mkdir(parents=True)
    for i in range(5):
        (root / "BSN0001" / f"IMG_000{i}.AVI").write_bytes(b"")
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    try:
        from trapnote import scan
        scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
        ids = sorted(r[0] for r in p.db.q("SELECT id FROM items ORDER BY name"))
        # 0: 枠 5・確認済み deer / 1: 枠 3・未確認 / 2: 枠 0（人だけ）確認済み human / 3: 枠 4・確認済み deer だが学習から外す / 4: 枠 2・確認済み unknown
        for iid, n in zip(ids, (5, 3, 0, 4, 2)):
            p.db.x("UPDATE items SET det_state=1, cls_state=1, cls_json=? WHERE id=?", (json.dumps(dict(n_crops=n)), iid))
        save_judgement(p.db, ids[0], dict(species=["deer"]), "me")
        save_judgement(p.db, ids[2], dict(species=["human"]), "me")
        save_judgement(p.db, ids[3], dict(species=["deer"]), "me")
        p.db.x("UPDATE items SET train_exclude=1 WHERE id=?", (ids[3],))
        save_judgement(p.db, ids[4], dict(species=["unknown"]), "me")

        def q(f, sort=None, desc=False):
            w, prm = build_where(f)
            if sort:
                _, order = order_sql(sort, desc)
                return [r[0] for r in p.db.q(f"SELECT id FROM items WHERE {w} ORDER BY {order}", prm)]
            return sorted(r[0] for r in p.db.q(f"SELECT id FROM items WHERE {w}", prm))
        assert q({"det": "crops"}) == sorted([ids[0], ids[1], ids[3], ids[4]])
        assert q({"det": "train"}) == [ids[0]]
        assert q({}, sort="crops", desc=True) == [ids[0], ids[3], ids[1], ids[4], ids[2]]
        # 動物なしで確定した動画に枠があれば「動物なし」の見本として学習に入る
        save_judgement(p.db, ids[1], dict(species=["empty"]), "me")
        assert q({"det": "train"}) == sorted([ids[0], ids[1]])
    finally:
        p.close()
