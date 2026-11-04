"""1 動画 1 枚の学習フレーム: 確定したときに右の欄で選んでいた枠（crop_anchor）を学習に使う。選んでいなければ信頼度が最も高い静止でない枠。
設定 train_frames: one（既定）/ one_plus（+ 同じ種とモデルも判定した静止でない枠）/ all（従来の全部）。
種が複数の動画は種ごとに 1 枚。「動物なし」の枠は one なら 1 枚。枠を選び直したとき（枠はいまあるものを使う）は印を同じ枠に付け替える"""
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

from test_static_boxes import _fake_detect, _video_project  # noqa: E402


def _labels(p, iid, **over):
    return train.collect(p, train.train_settings(p, over))["items"][iid]["labels"]


def test_one_frame_default_and_choice(tmp_path):
    p = _video_project(tmp_path)
    try:
        ids = [r[0] for r in p.db.q("SELECT id FROM items ORDER BY name")]
        iid = ids[0]
        rock = [0.10, 0.60, 0.15, 0.15]
        deer_t = lambda t: [0.2 + 0.05 * t, 0.3, 0.2, 0.25]
        frames = [(t, [rock, deer_t(t)]) for t in np.arange(0.0, 10.0, 0.5)]
        crops = [(0.0, rock), (2.0, deer_t(2.0)), (4.0, rock), (6.0, deer_t(6.0)), (8.0, deer_t(8.0))]
        # 信頼度: 静止枠（倒木）が最大、シカは k=3 が最大。モデルの第 1 候補: k=1 は boar（合わない）、ほかは deer
        pc = [[0.6, 0.3, 0.1], [0.2, 0.7, 0.1], [0.6, 0.3, 0.1], [0.8, 0.1, 0.1], [0.7, 0.2, 0.1]]
        _fake_detect(p, iid, frames, crops, pc=pc)
        rel = p.db.scalar("SELECT rel_path FROM items WHERE id=?", (iid,))
        fp = train.features_path(p, rel)
        with np.load(fp) as z:
            d = {k: z[k] for k in z.files}
        d["conf"] = np.array([0.95, 0.80, 0.93, 0.90, 0.85], dtype=np.float32)
        np.savez(fp, **d)
        # 確定前: 枠の欄は人の種がなくてもモデルの第 1 種から仮の学習フレームを出す（信頼度が最も高い静止でない枠 = k=3）
        ic = train.item_crops(p, iid)
        assert ic["anchor_auto"] and ic["anchors"] == {"deer": 3} and [c["anchor"] for c in ic["crops"]] == [None, None, None, "deer", None]
        save_judgement(p.db, iid, dict(species=["deer"]), "me")
        # one（既定）: 学習フレーム 1 枚（自動 = 静止でない枠で信頼度最大 k=3）+ 静止枠の「動物なし」は信頼度最大の 1 枚（k=0）
        col = train.collect(p, train.train_settings(p))
        assert col["items"][iid]["labels"] == ["empty", None, None, "deer", None] and col["n_anchor_auto"] == 1 and col["n_anchor_manual"] == 0 and col["train_frames"] == "one"
        assert col["items"][iid]["anchors"] == {"deer": 3}
        # one_plus: 同じ種で静止でなくモデルも deer の枠（k=4）を足す。k=1 はモデルが boar なので足さない。静止枠の動物なしは全部
        assert _labels(p, iid, train_frames="one_plus") == ["empty", None, "empty", "deer", "deer"]
        # all: 従来どおり
        assert _labels(p, iid, train_frames="all") == ["empty", "deer", "empty", "deer", "deer"]
        # 人が k=1 を選んで確定（モデルは boar と言っているが人の選択が勝つ。信頼度 0.80 で下限 0.5 以上）
        assert train.set_anchor(p, iid, 1) == {"deer": 1}
        assert json.loads(p.db.scalar("SELECT crop_anchor FROM items WHERE id=?", (iid,))) == {"deer": 1}
        col = train.collect(p, train.train_settings(p))
        assert col["items"][iid]["labels"] == ["empty", "deer", None, None, None] and col["n_anchor_manual"] == 1 and col["n_anchor_auto"] == 0
        ic = train.item_crops(p, iid)
        assert not ic["anchor_auto"] and ic["anchors"] == {"deer": 1} and ic["crops"][1]["anchor"] == "deer" and ic["species"] == ["deer"]
        # 選んだ枠を学習から外すと、学習フレームは自動に戻る（外していない枠で信頼度最大）
        train.set_crop_excluded(p, iid, [1], True)
        ic = train.item_crops(p, iid)
        assert ic["anchor_auto"] and ic["anchors"] == {"deer": 3}
        train.set_crop_excluded(p, iid, [1], False)
        # 記録を消す → 自動
        assert train.set_anchor(p, iid, None) == {} and train.item_crops(p, iid)["anchor_auto"]
        # 動物なしで確定した動画（枠は全部誤検出）: one なら信頼度最大の 1 枚だけ「動物なし」
        save_judgement(p.db, iid, dict(species=["empty"]), "me")
        assert _labels(p, iid) == ["empty", None, None, None, None]
        assert _labels(p, iid, train_frames="all") == ["empty"] * 5
    finally:
        p.close()


def test_two_species(tmp_path):
    p = _video_project(tmp_path)
    try:
        iid = p.db.scalar("SELECT id FROM items ORDER BY name LIMIT 1")
        box = lambda t: [0.2 + 0.05 * t, 0.3, 0.2, 0.25]
        frames = [(t, [box(t)]) for t in np.arange(0.0, 10.0, 0.5)]
        crops = [(0.0, box(0.0)), (2.0, box(2.0)), (4.0, box(4.0)), (6.0, box(6.0))]
        pc = [[0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.6, 0.3, 0.1], [0.2, 0.7, 0.1]]       # deer, boar, deer, boar
        _fake_detect(p, iid, frames, crops, pc=pc)
        rel = p.db.scalar("SELECT rel_path FROM items WHERE id=?", (iid,))
        fp = train.features_path(p, rel)
        with np.load(fp) as z:
            d = {k: z[k] for k in z.files}
        d["conf"] = np.array([0.7, 0.6, 0.9, 0.8], dtype=np.float32)
        np.savez(fp, **d)
        save_judgement(p.db, iid, dict(species=["deer", "boar"]), "me")
        # 種ごとに 1 枚（モデルがその種と言う枠で信頼度最大）: deer → k=2、boar → k=3
        col = train.collect(p, train.train_settings(p))
        assert col["items"][iid]["anchors"] == {"boar": 3, "deer": 2} and col["items"][iid]["labels"] == [None, None, "deer", "boar"]
        ic = train.item_crops(p, iid)
        assert ic["anchors"] == {"boar": 3, "deer": 2} and ic["species"] == ["boar", "deer"]
        # 人が k=0 を選んで確定 → モデルの候補 deer の枠として記録（boar の枠はそのまま）
        assert train.set_anchor(p, iid, 0) == {"boar": 3, "deer": 0} or train.set_anchor(p, iid, 0) == {"deer": 0}
        col = train.collect(p, train.train_settings(p))
        assert col["items"][iid]["labels"] == ["deer", None, None, "boar"]
        # one_plus: 残りの枠のうち静止でなくモデルも同じ種のもの（k=1 boar、k=2 deer）を足す
        assert _labels(p, iid, train_frames="one_plus") == ["deer", "boar", "deer", "boar"]
    finally:
        p.close()


def test_remap_marks(tmp_path):
    p = _video_project(tmp_path)
    try:
        iid = p.db.scalar("SELECT id FROM items ORDER BY name LIMIT 1")
        box = lambda t: [0.2 + 0.05 * t, 0.3, 0.2, 0.25]
        frames = [(t, [box(t)]) for t in np.arange(0.0, 10.0, 0.5)]
        crops = [(0.0, box(0.0)), (2.0, box(2.0)), (4.0, box(4.0))]
        _fake_detect(p, iid, frames, crops)
        save_judgement(p.db, iid, dict(species=["deer"]), "me")
        train.set_anchor(p, iid, 2)
        train.set_crop_excluded(p, iid, [0], True)
        # 枠を選び直して順番が変わった（4.0 → 0.0 → 2.0）: 印は同じ枠（同じ秒・位置）に付け替わる
        old_t = np.array([0.0, 2.0, 4.0], dtype=np.float32); old_b = np.array([box(0.0), box(2.0), box(4.0)], dtype=np.float32)
        new_t = [4.0, 0.0, 2.0]; new_b = [box(4.0), box(0.0), box(2.0)]
        train.remap_crop_marks(p, iid, old_t, old_b, new_t, new_b)
        r = p.db.q1("SELECT crop_excl, crop_anchor FROM items WHERE id=?", (iid,))
        assert json.loads(r["crop_excl"]) == [1] and json.loads(r["crop_anchor"]) == {"deer": 0}
        # 対応する枠がなくなれば印は消える
        train.remap_crop_marks(p, iid, np.array(new_t, dtype=np.float32), np.array(new_b, dtype=np.float32), [8.0], [box(8.0)])
        r = p.db.q1("SELECT crop_excl, crop_anchor FROM items WHERE id=?", (iid,))
        assert r["crop_excl"] is None and r["crop_anchor"] is None
    finally:
        p.close()
