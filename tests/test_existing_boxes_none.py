"""枠はいまあるものを使う（use_existing）とき、検出が済んでいて枠が 1 つもない対象は、何も変えずにそのままにする。

以前は「枠がなければ普通に検出」だったので、
- 人が画面で枠を全部消した動画は、検出からやり直されて、消した枠が戻った（Third_500 に 22 本あった）。
- 検出のあとで変換された動画には、前になかった枠が付いた（First_200 の 18 本。うち 3 本は種の判別に進んだ）。
- 検出済みで枠のない動画を、全フレーム読み直していた（Second_500_2 で 399 本、Third_500 で 701 本）。
まだ検出していない対象（det_state = 0）は、前と同じく普通に検出する（確認事項 117 回目）。

動画は OpenCV で作る小さな MJPEG。検出器は偽物（明るい塊に枠を返す）"""
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
            cv2.rectangle(fr, (10 + (i * 2) % 100, 30), (50 + (i * 2) % 100, 80), (200, 200, 200), -1)       # 偽の検出器が枠を返す明るい塊
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


def _item(p, iid):
    return dict(p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE id=?", (iid,)))


def _state(p, iid):
    return dict(p.db.q1("SELECT det_state, det_json, cls_state, cls_json, auto_json, n_boxes, meta_error FROM items WHERE id=?", (iid,)))


def test_all_boxes_deleted_by_hand_stay_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    p, iid = _project(tmp_path)
    try:
        hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
        hub.load_detector("auto")
        hub.load_classifier("speciesnet-test")
        res = detect.process_item(p, hub, _item(p, iid), True)                 # 普通の検出: 枠が付く
        assert res.get("animal") and len(detect.existing_boxes(p.db, iid)) > 0
        detect.save_boxes(p, iid, [])                                          # 人が画面で枠を全部消した
        assert detect.existing_boxes(p.db, iid) == [] and detect.detected_without_boxes(p.db, iid)
        before = _state(p, iid)
        assert before["det_state"] == 1 and json.loads(before["det_json"])["edited"] and not json.loads(before["det_json"])["animal"]
        loaded = []
        orig = detect.load_frames
        monkeypatch.setattr(detect, "load_frames", lambda *a, **k: (loaded.append(a), orig(*a, **k))[1])
        res2 = detect.process_item(p, hub, _item(p, iid), True, None, True)    # 枠はいまあるものを使う
        assert res2.get("kept") and not res2.get("animal")
        assert detect.existing_boxes(p.db, iid) == [], "消した枠は戻らない"
        assert _state(p, iid) == before, "記録は何も変わらない"
        assert not loaded, "動画は読まない"
        assert not os.path.isfile(train.features_path(p, _item(p, iid)["rel_path"]))
    finally:
        p.close()


def test_detected_with_no_boxes_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    p, iid = _project(tmp_path)
    try:
        hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
        hub.load_detector("auto")
        hub.load_classifier("speciesnet-test")
        # 検出済みで、何も見つからなかった記録（検出のあとで動画が変換された、という場面: いま検出し直せば枠が付く）
        det = dict(n_frames=100, animal_frames=0, person_frames=0, vehicle_frames=0, max_animal=0.0, max_person=0.0, max_vehicle=0.0, max_area=0.0,
                   animal_threshold=0.2, person_threshold=0.2, model="MDv1000-redwood", animal=False, person=False, vehicle=False)
        auto = dict(model="MDv1000-redwood", species=[dict(species="empty", confidence=1.0)], low=False)
        p.db.x("UPDATE items SET det_state=1, cls_state=0, det_json=?, auto_json=? WHERE id=?", (json.dumps(det), json.dumps(auto), iid))
        before = _state(p, iid)
        res = detect.process_item(p, hub, _item(p, iid), True, None, True)
        assert res.get("kept") and [x["species"] for x in res["results"]] == ["empty"] and res["flag"] is None
        assert detect.existing_boxes(p.db, iid) == [] and _state(p, iid) == before
    finally:
        p.close()


def test_not_yet_detected_is_still_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    p, iid = _project(tmp_path)
    try:
        hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
        hub.load_detector("auto")
        hub.load_classifier("speciesnet-test")
        assert _state(p, iid)["det_state"] == 0 and not detect.detected_without_boxes(p.db, iid)
        res = detect.process_item(p, hub, _item(p, iid), True, None, True)     # まだ検出していない対象は、いまある枠を使う指定でも普通に検出する
        assert not res.get("kept") and res.get("animal")
        assert len(detect.existing_boxes(p.db, iid)) > 0 and _state(p, iid)["det_state"] == 1 and _state(p, iid)["cls_state"] == 1
    finally:
        p.close()
