"""学習データの枠の選別: 目視は動画単位なので、倒木や石の誤検出の枠にも動画の種が付いてしまう →
動画の間ずっと同じ位置にある枠（静止枠）は「動物なし」として学習する（動く枠が 1 つでもあるとき）。
この選別（train_drop_static）は既定オフ（人が学習フレームを確認する前提）なので、ここでは明示的にオンにして見る。枠の判定の上位 3 位に動画の種が入らない枠を外す選別（check_agree）も既定オフ。
ここでは枠の選別そのものを見るので train_frames="all"（全部の枠）で集める（1 動画 1 枚の学習フレームは tests/test_train_frames.py）"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import train  # noqa: E402
from trapnote.judge import save as save_judgement  # noqa: E402
from trapnote.project import Project  # noqa: E402


def _video_project(tmp_path):
    root = tmp_path / "N240128"
    (root / "BSN0001").mkdir(parents=True)
    (root / "BSN0001" / "IMG_0001.AVI").write_bytes(b"")
    (root / "BSN0001" / "IMG_0002.AVI").write_bytes(b"")
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    from trapnote import scan
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    return p


def _fake_detect(p, iid: int, frames: list[tuple[float, list]], crops: list[tuple[float, list]], codes=("deer", "boar", "empty"), pc=None):
    """detections 表に全フレームの枠、特徴量 npz に学習に使う枠（crops）を作る。frames = [(t, [[x,y,w,h], ...])]"""
    with p.db.tx() as c:
        c.execute("DELETE FROM detections WHERE item_id=?", (iid,))
        for t, boxes in frames:
            for b in boxes:
                c.execute("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h) VALUES(?,?,?,?,?,?,?,?)", (iid, t, "animal", 0.8, *b))
        c.execute("UPDATE items SET det_state=1, cls_state=1 WHERE id=?", (iid,))
    rel = p.db.scalar("SELECT rel_path FROM items WHERE id=?", (iid,))
    fp = train.features_path(p, rel)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    n = len(crops)
    pc = np.asarray(pc if pc is not None else [[0.6, 0.3, 0.1]] * n, dtype=np.float32)
    np.savez(fp, feats=np.random.default_rng(0).normal(0, 1, (n, 8)).astype(np.float32), codes=np.array(list(codes)), pc=pc,
             t=np.array([t for t, _ in crops], dtype=np.float32), bbox=np.array([b for _, b in crops], dtype=np.float32), conf=np.full(n, 0.9, dtype=np.float32))


def test_static_boxes(tmp_path):
    p = _video_project(tmp_path)
    try:
        ids = [r[0] for r in p.db.q("SELECT id FROM items ORDER BY name")]
        rock = [0.10, 0.60, 0.15, 0.15]                                   # 倒木: 全フレームで同じ位置
        deer_t = lambda t: [0.2 + 0.05 * t, 0.3, 0.2, 0.25]                # シカ: 右へ動く
        frames = [(t, [rock, deer_t(t)]) for t in np.arange(0.0, 10.0, 0.5)]
        crops = [(0.0, rock), (2.0, deer_t(2.0)), (4.0, rock), (6.0, deer_t(6.0)), (8.0, deer_t(8.0))]
        _fake_detect(p, ids[0], frames, crops)
        save_judgement(p.db, ids[0], dict(species=["deer"]), "me")
        # 静止枠の判定そのもの
        st = train.item_static_crops(p.db, ids[0], np.array([b for _, b in crops], dtype=np.float32))
        assert st == [True, False, True, False, False]
        # 学習データ: 倒木の枠は「動物なし」、シカの枠は deer
        col = train.collect(p, train.train_settings(p, dict(train_frames="all", train_drop_static=True)))
        it = col["items"][ids[0]]
        assert it["labels"] == ["empty", "deer", "empty", "deer", "deer"] and col["n_static"] == 2 and it["static"] == st
        # 設定を切れば（既定）、全部 deer
        col2 = train.collect(p, train.train_settings(p, dict(train_frames="all")))
        assert col2["items"][ids[0]]["labels"] == ["deer"] * 5 and col2["n_static"] == 0
        # 「動物なし」として学習しない設定なら、静止枠は学習から外す（None）
        col3 = train.collect(p, train.train_settings(p, dict(train_use_empty=False, train_frames="all", train_drop_static=True)))
        assert col3["items"][ids[0]]["labels"] == [None, "deer", None, "deer", "deer"]
        # 枠の欄（item_crops）にも印（静止の印は設定に関わらず出る。static_applies は設定がオンのときだけ）
        ic = train.item_crops(p, ids[0])
        assert not ic["static_applies"] and [c["static"] for c in ic["crops"]] == st
        p.update_settings(dict(train_drop_static=True))
        assert train.item_crops(p, ids[0])["static_applies"]
        p.update_settings(dict(train_drop_static=False))
        # 全部が静止（休んでいる動物）なら判定しない: 全部 deer のまま
        still = [0.4, 0.4, 0.2, 0.2]
        frames2 = [(t, [still]) for t in np.arange(0.0, 10.0, 0.5)]
        crops2 = [(0.0, still), (3.0, still), (6.0, still)]
        _fake_detect(p, ids[1], frames2, crops2)
        save_judgement(p.db, ids[1], dict(species=["boar"]), "me")
        col4 = train.collect(p, train.train_settings(p, dict(train_frames="all", train_drop_static=True)))
        assert col4["items"][ids[1]]["labels"] == ["boar"] * 3 and not any(col4["items"][ids[1]]["static"])
        assert not train.item_crops(p, ids[1])["static_applies"]
        # 短い動画（3 秒未満）やフレームが少ないものも判定しない
        frames3 = [(t, [rock, deer_t(t)]) for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
        _fake_detect(p, ids[1], frames3, [(0.0, rock), (2.0, deer_t(2.0))])
        assert train.item_static_crops(p.db, ids[1], np.array([rock, deer_t(2.0)], dtype=np.float32)) == [False, False]
    finally:
        p.close()


def test_check_agree(tmp_path):
    """枠の判定の上位 3 位に動画の種が入らない枠を外す（既定オフ）。静止枠より後に効く"""
    p = _video_project(tmp_path)
    try:
        iid = p.db.scalar("SELECT id FROM items ORDER BY name LIMIT 1")
        box = lambda t: [0.2 + 0.05 * t, 0.3, 0.2, 0.25]
        frames = [(t, [box(t)]) for t in np.arange(0.0, 10.0, 0.5)]
        crops = [(0.0, box(0.0)), (4.0, box(4.0)), (8.0, box(8.0))]
        codes = ("deer", "boar", "empty", "hare")
        pc = [[0.7, 0.2, 0.05, 0.05], [0.0, 0.5, 0.3, 0.2], [0.4, 0.3, 0.2, 0.1]]       # 2 枚目は deer が上位 3 位に入らない
        _fake_detect(p, iid, frames, crops, codes=codes, pc=pc)
        save_judgement(p.db, iid, dict(species=["deer"]), "me")
        col = train.collect(p, train.train_settings(p, dict(train_frames="all")))
        assert col["items"][iid]["labels"] == ["deer"] * 3 and col["n_disagree"] == 0
        col2 = train.collect(p, train.train_settings(p, dict(train_check_agree=True, train_frames="all")))
        assert col2["items"][iid]["labels"] == ["deer", None, "deer"] and col2["n_disagree"] == 1
        assert json.loads(json.dumps(train.train_settings(p)))["train_check_agree"] is False and train.train_settings(p)["train_drop_static"] is False
    finally:
        p.close()
