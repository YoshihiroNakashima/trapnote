"""方式 B（本体を学習し直した）モデルで判定したときも、学習に使う枠の記録（特徴量 npz: t・bbox・conf・判定）を残す。
以前は方式 B の枝で npz を保存していなかったので、そのモデルで判定した動画には「学習に使う枠」がなく、学習データにも入らなかった。
方式 B の記録には元のモデルの特徴量がないので、方式 A（元の特徴量に頭を付ける）の学習・付け直しには使わない（load_features が feats=None にする）。
枠はいまあるものを使う（use_existing）ときは、種判別に入れる枠のフレームだけ動画から読む（全フレームは読まない）。
実行: TRAPNOTE_FAKE_ML=1 は要らない（この試験が環境変数を立てる）。動画は OpenCV で作る小さな MJPEG"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

cv2 = pytest.importorskip("cv2")

from trapnote import detect, scan, train  # noqa: E402
from trapnote.judge import save as save_judgement  # noqa: E402
from trapnote.project import Project  # noqa: E402


def _make_video(path: str, n: int = 120, fps: int = 10) -> None:
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 120))
    try:
        for i in range(n):
            fr = np.full((120, 160, 3), 40, np.uint8)
            x = 10 + (i * 2) % 100
            cv2.rectangle(fr, (x, 30), (x + 40, 80), (200, 200, 200), -1)       # 明るい塊が右へ動く（偽の検出器が枠を返す）
            w.write(fr)
    finally:
        w.release()


def _project(tmp_path):
    root = tmp_path / "N240128"
    (root / "BSN0001").mkdir(parents=True)
    _make_video(str(root / "BSN0001" / "IMG_0001.AVI"))
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    iid = p.db.scalar("SELECT id FROM items")
    p.db.x("UPDATE items SET duration=12.0, fps=10.0, meta_state=1, width=160, height=120 WHERE id=?", (iid,))
    return p, iid


def _hub(tmp_path):
    return detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))


def test_b_model_keeps_crop_record(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    p, iid = _project(tmp_path)
    try:
        hub = _hub(tmp_path)
        hub.load_detector("auto")
        hub.load_classifier("speciesnet-test")
        # 方式 B のモデルが付いた状態（偽の分類器は 7 列を返すので 7 種のコード）
        hub.local = dict(name="B_test", kind="B", classes=["deer", "boar", "raccoon_dog", "squirrel", "empty", "human", "vehicle"])
        item = dict(p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE id=?", (iid,)))
        res = detect.process_item(p, hub, item, True)
        assert res.get("animal")
        fp = train.features_path(p, item["rel_path"])
        assert os.path.isfile(fp), "方式 B でも学習に使う枠の記録（npz）が残る"
        z = train.load_features(fp)
        assert not z["base"] and z["feats"] is None and " + B_test" in z["model"]
        assert len(z["t"]) == len(z["conf"]) == len(z["pc"]) and z["codes"] == hub.local["classes"]
        # 枠の欄に出る
        ic = train.item_crops(p, iid)
        assert ic["has_features"] and len(ic["crops"]) == len(z["t"]) and ic["crops"][0]["t"] == round(float(z["t"][0]), 3)
        # 確定すれば学習データに入る（枠の t・bbox で方式 B の学習が切り出せる）。方式 A 向けの feats はない
        save_judgement(p.db, iid, dict(species=["deer"]), "me")
        col = train.collect(p, train.train_settings(p))
        assert iid in col["items"] and col["n_nofeat"] == 0
        it = col["items"][iid]
        assert it["feats"] is None and all(l == "deer" for l in it["labels"] if l is not None) and any(l == "deer" for l in it["labels"])
        # 方式 A / SpeciesNet で「判定の付け直し」はできない（元の特徴量がない → None = 特徴量なし扱い）
        hub.local = None
        assert train.reclassify_item(p, hub, item, detect.project_settings(p)) is None
        # 枠はいまあるものを使う: 検出せずに種判別と記録だけ付け直す。読むのは種判別に入れる枠のフレームだけ
        n_frames = json.loads(p.db.q1("SELECT det_json FROM items WHERE id=?", (iid,))["det_json"])["n_frames"]
        rows = detect.existing_boxes(p.db, iid)
        s = detect.project_settings(p)
        need = detect.crop_times(rows, s)
        assert 0 < len(need) <= int(s["max_crops"]) < len({r[0] for r in rows})
        loaded = []
        orig = detect.load_frames

        def spy(project, item, device=None, times=None):
            loaded.append(times)
            return orig(project, item, device, times)
        monkeypatch.setattr(detect, "load_frames", spy)
        item = dict(p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE id=?", (iid,)))
        res2 = detect.process_item(p, hub, item, True, None, True)
        assert loaded and loaded[0] == need
        assert res2["n_frames"] == n_frames, "フレーム数の記録は検出のときのまま"
        assert len(detect.existing_boxes(p.db, iid)) == len(rows), "枠はそのまま"
        z2 = train.load_features(fp)
        assert z2["base"] and z2["feats"] is not None, "元のモデルで付け直せば方式 A の特徴量が入る"
    finally:
        p.close()
