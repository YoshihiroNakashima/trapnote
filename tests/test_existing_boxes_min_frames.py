"""枠はいまあるものを使う（use_existing）とき、「動物あり」の判定は、検出のときに見たフレームの数（det_json の n_frames）で数える。

検出では、動物の枠（信頼度が基準以上）が animal_min_frames（既定 2）フレーム以上に付いた動画だけが「動物あり」になり、種の判別に進む。
以前は、いまある枠を使って種判別を付け直すとき、このフレーム数を「読んだフレームの数」（種判別に入れる枠のフレームだけ）で頭打ちにしていた。
そのため、枠が 1 フレームにしかない動画は、動物の枠が 1 フレームでも「動物あり」になり、検出では動物なしだった動画が種の判別に進んで、学習の材料にも入った
（Second_500_2 で 125 本、Third_500 で 211 本がそうなるところだった。確認事項 117 回目）。
YOLO のラベルなど人が付けた枠（det_json の n_frames = 1）は、1 フレームの枠でも動物ありのまま。

動画は OpenCV で作る小さな MJPEG"""
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
from trapnote.project import Project  # noqa: E402


def _make_video(path: str, n: int = 120, fps: int = 10) -> None:
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 120))
    try:
        for i in range(n):
            fr = np.full((120, 160, 3), 40, np.uint8)
            cv2.rectangle(fr, (10 + i % 100, 30), (50 + i % 100, 80), (200, 200, 200), -1)
            w.write(fr)
    finally:
        w.release()


def _project(tmp_path, boxes, n_frames, animal):
    """boxes = [(秒, 種類, 信頼度)]。前の検出の記録として入れる（種判別はまだ: cls_state = 0）"""
    root = tmp_path / "N240128"
    (root / "BSN0001").mkdir(parents=True)
    _make_video(str(root / "BSN0001" / "IMG_0001.AVI"))
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    iid = p.db.scalar("SELECT id FROM items")
    af = len({t for t, cat, cf in boxes if cat == "animal" and cf >= 0.2})
    det = dict(n_frames=n_frames, animal_frames=af, person_frames=0, vehicle_frames=0, max_animal=max([cf for _, cat, cf in boxes if cat == "animal"], default=0.0),
               max_person=0.0, max_vehicle=0.0, max_area=0.05, animal_threshold=0.2, person_threshold=0.2, model="MDv1000-redwood", animal=animal, person=False, vehicle=False)
    p.db.x("UPDATE items SET duration=12.0, fps=10.0, meta_state=1, width=160, height=120, det_state=1, cls_state=0, det_json=? WHERE id=?", (json.dumps(det), iid))
    with p.db.tx() as c:
        for t, cat, cf in boxes:
            c.execute("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h) VALUES(?,?,?,?,?,?,?,?)", (iid, t, cat, cf, 0.2, 0.3, 0.25, 0.4))
    return p, iid


def _run(p, iid, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
    hub.load_classifier("speciesnet-test")
    item = dict(p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE id=?", (iid,)))
    res = detect.process_item(p, hub, item, True, None, True)
    r = p.db.q1("SELECT det_state, cls_state, det_json FROM items WHERE id=?", (iid,))
    return res, r, os.path.isfile(train.features_path(p, item["rel_path"]))


def test_one_frame_of_boxes_stays_no_animal(tmp_path, monkeypatch):
    # 検出で 100 フレームを見て、動物の枠は 1 フレームだけ → 検出では動物なし。枠が 1 フレームにしかなくても、付け直しで動物ありにならない
    p, iid = _project(tmp_path, [(1.0, "animal", 0.8)], n_frames=100, animal=False)
    try:
        res, r, has_npz = _run(p, iid, tmp_path, monkeypatch)
        dj = json.loads(r["det_json"])
        assert not res.get("animal") and not dj["animal"], "検出のときと同じく、動物なしのまま"
        assert dj["n_frames"] == 100 and dj["animal_frames"] == 1
        assert r["det_state"] == 1 and r["cls_state"] == 0 and not has_npz, "種の判別には進まない（学習の材料にも入らない）"
        assert len(detect.existing_boxes(p.db, iid)) == 1, "枠はそのまま"
    finally:
        p.close()


def test_two_frames_of_boxes_is_animal(tmp_path, monkeypatch):
    p, iid = _project(tmp_path, [(1.0, "animal", 0.8), (1.2, "animal", 0.7), (3.0, "animal", 0.1)], n_frames=100, animal=True)
    try:
        res, r, has_npz = _run(p, iid, tmp_path, monkeypatch)
        assert res.get("animal") and json.loads(r["det_json"])["animal"]
        assert r["cls_state"] == 1 and has_npz
    finally:
        p.close()


def test_human_made_box_on_one_frame_is_classified(tmp_path, monkeypatch):
    # YOLO のラベルなど人が付けた枠: 記録のフレーム数は 1 → 1 フレームの枠でも動物ありで、種の判別に進む（前と同じ）
    p, iid = _project(tmp_path, [(1.0, "animal", 1.0)], n_frames=1, animal=True)
    try:
        res, r, has_npz = _run(p, iid, tmp_path, monkeypatch)
        assert res.get("animal") and json.loads(r["det_json"])["animal"]
        assert r["cls_state"] == 1 and has_npz
    finally:
        p.close()
