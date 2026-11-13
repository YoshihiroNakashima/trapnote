"""枠はいまあるものを使う（use_existing）とき、動画のフレームの秒が枠の秒とぴったり合わなくても、種判別まで進む。

AVI を MP4 に変換して fps が 30 → 29.9 になった動画では、枠の秒（1.4、2.0 …）にちょうどのフレームがなく、いちばん近いフレーム（1.405、2.007 …）を読む。
以前は、種判別に入れる枠の秒をそのフレームの秒に書き換えてから、枠を選び直していた。書き換えた分だけ「選んだ枠から 0.6 秒以内は後回し」の判定が変わるので、
最初に選ばなかった枠（= フレームを読んでいない枠）が選ばれ、KeyError（処理の記録には「detect: 2.2」）で失敗した
（First_200 を元の SpeciesNet で判定し直したとき、3,460 本のうち 103 本。確認事項 115 回目）。失敗しなかった動画でも、流し直すたびに選ばれる枠が変わりえた。
いまは、枠の秒は書き換えず、いちばん近いフレームを使うだけにする（選ばれる枠は、何度流しても同じ）。

枠の秒と信頼度は、実際に失敗した動画 1 本（66 枠）のもの。動画は OpenCV で作る小さな MJPEG（29.9 fps）。"""
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

# (秒, MegaDetector の信頼度)。0.2 秒ごと（30 fps の AVI で検出したときの秒）
BOXES = [(0.8, 0.529), (1.0, 0.855), (1.2, 0.898), (1.4, 0.903), (1.6, 0.923), (1.8, 0.927), (2.0, 0.943), (2.2, 0.962), (2.4, 0.975), (2.6, 0.967), (2.8, 0.979),
         (3.0, 0.982), (3.2, 0.982), (3.4, 0.985), (3.6, 0.977), (3.8, 0.968), (4.0, 0.976), (4.2, 0.959), (4.4, 0.961), (4.6, 0.954), (4.8, 0.953), (5.0, 0.969),
         (5.2, 0.957), (5.4, 0.936), (5.6, 0.927), (5.8, 0.926), (6.0, 0.93), (6.2, 0.913), (6.4, 0.903), (6.6, 0.902), (6.8, 0.91), (7.0, 0.9), (7.2, 0.896),
         (7.4, 0.888), (7.6, 0.73), (7.8, 0.87), (8.0, 0.835), (8.2, 0.805), (8.4, 0.635), (8.6, 0.778), (8.8, 0.706), (9.0, 0.635), (9.2, 0.369), (9.4, 0.644),
         (9.6, 0.696), (9.8, 0.705), (10.0, 0.726), (10.2, 0.74), (10.4, 0.725), (10.6, 0.694), (10.8, 0.648), (11.0, 0.7), (11.2, 0.86), (11.4, 0.871), (11.6, 0.824),
         (11.8, 0.822), (12.0, 0.732), (12.2, 0.565), (12.4, 0.494), (12.6, 0.451), (12.8, 0.438), (13.0, 0.284), (13.2, 0.433), (13.4, 0.522), (13.6, 0.655), (13.8, 0.401)]
FPS = 29.9


def _make_video(path: str, n: int = 600, fps: float = FPS) -> None:
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 120))
    try:
        for i in range(n):
            fr = np.full((120, 160, 3), 40, np.uint8)
            x = 10 + (i // 3) % 100
            cv2.rectangle(fr, (x, 30), (x + 40, 80), (200, 200, 200), -1)
            w.write(fr)
    finally:
        w.release()


def _project(tmp_path):
    root = tmp_path / "N180721"
    (root / "BSN0909").mkdir(parents=True)
    _make_video(str(root / "BSN0909" / "IMG_0085.AVI"))
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    iid = p.db.scalar("SELECT id FROM items")
    det = dict(n_frames=100, animal_frames=len(BOXES), person_frames=0, vehicle_frames=0, max_animal=0.985, max_person=0.0, max_vehicle=0.0, max_area=0.06,
               animal_threshold=0.2, person_threshold=0.2, model="MDv1000-redwood", animal=True, person=False, vehicle=False)
    p.db.x("UPDATE items SET duration=20.07, fps=?, meta_state=1, width=160, height=120, det_state=1, det_json=? WHERE id=?", (FPS, json.dumps(det), iid))
    with p.db.tx() as c:
        for t, conf in BOXES:            # 前の検出（30 fps の AVI）の枠。位置は少しずつ動く
            c.execute("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h) VALUES(?,?,?,?,?,?,?,?)", (iid, t, "animal", conf, round(0.1 + t / 40, 4), 0.3, 0.25, 0.4))
    return p, iid


def _item(p, iid):
    return dict(p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE id=?", (iid,)))


def test_existing_boxes_when_frame_times_do_not_match(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    p, iid = _project(tmp_path)
    try:
        hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
        hub.load_classifier("speciesnet-test")
        s = detect.project_settings(p)
        rows = detect.existing_boxes(p.db, iid)
        assert len(rows) == len(BOXES)
        picked = detect.pick_crops(rows, float(s["animal_threshold"]), int(s["max_crops"]), float(s.get("crop_min_gap_sec", 0.6)), int(s.get("crop_per_frame", 2)))
        want = [(r[0], round(r[2], 3), round(r[3], 4)) for _, r in picked]       # 選ばれる枠（秒・信頼度・位置）を、選ばれた順に
        assert len(want) == int(s["max_crops"]) == 12
        need = detect.crop_times(rows, s)
        got = sorted(t for t, _ in detect.load_frames(p, _item(p, iid), "cpu", need))
        assert len(got) == len(need) and not all(t in got for t in need), "この動画では、枠の秒にちょうどのフレームがない（近いフレームの秒が返る）"
        assert max(abs(a - b) for a, b in zip(got, need)) < 0.5 / FPS + 0.001, "ずれは半フレーム以内"

        def crops_now():
            z = train.load_features(train.features_path(p, _item(p, iid)["rel_path"]))
            assert z["base"] and z["feats"] is not None and len(z["feats"]) == len(z["t"])
            return [(round(float(t), 3), round(float(c), 3), round(float(b[0]), 4)) for t, c, b in zip(z["t"], z["conf"], z["bbox"])]

        res = detect.process_item(p, hub, _item(p, iid), True, None, True)      # 以前はここで KeyError: 2.2
        assert res.get("animal")
        r = p.db.q1("SELECT det_state, cls_state, det_json, meta_error FROM items WHERE id=?", (iid,))
        assert r["det_state"] == 1 and r["cls_state"] == 1 and r["meta_error"] is None
        assert json.loads(r["det_json"])["n_frames"] == 100, "フレーム数の記録は検出のときのまま"
        assert json.loads(r["det_json"])["animal_frames"] == len({t for t, c in BOXES if c >= 0.2})
        assert [x[:3] + x[3:] for x in detect.existing_boxes(p.db, iid)] == rows, "枠は秒も信頼度も位置もそのまま（秒を書き換えない）"
        assert crops_now() == want, "種判別に入れた枠は、もとの秒で選んだ枠と同じ（並びも同じ）"

        # 確定して、学習フレームを人が選ぶ（信頼度がいちばん高い枠ではない枠にする）
        save_judgement(p.db, iid, dict(species=["deer"]), "me")
        assert train.set_anchor(p, iid, 5) == {"deer": 5}

        # もう一度流しても同じ枠が選ばれ、人が選んだ学習フレームも残る
        res2 = detect.process_item(p, hub, _item(p, iid), True, None, True)
        assert res2.get("animal")
        assert crops_now() == want
        assert [x[:3] + x[3:] for x in detect.existing_boxes(p.db, iid)] == rows
        assert train.crop_anchor_dict(p.db.q1("SELECT crop_anchor FROM items WHERE id=?", (iid,))["crop_anchor"]) == {"deer": 5}

        # 学習データに入り、人が選んだ枠が学習フレームになる
        col = train.collect(p, train.train_settings(p))
        assert iid in col["items"] and col["items"][iid]["feats"] is not None
        assert [k for k, l in enumerate(col["items"][iid]["labels"]) if l == "deer"] == [5]
    finally:
        p.close()
