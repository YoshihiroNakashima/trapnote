"""YOLO 形式の書き出し（yolo.export_yolo。確認事項 140 回目）: 動画のフレームを切り出した画像と、同じ名前のラベルの組を書く。

- 動画から書くフレーム: 学習フレームだけ（1 動画 1 枚。既定）と、枠のあるフレームを全部
- 学習フレームは、人が確定したときに選んでいた枠のフレーム。選んでいなければ信頼度の最も高い枠のフレーム。「学習から外す」にした枠はラベルに書かない
- 地点が違えば同じファイル名でも重ならない（フォルダの各段を _ でつないだ名前）
- 「動物なし」と確定した動画に枠があれば、空のラベル（背景）。枠のない動画は書かない。静止画はコピーして、枠がなければ空のラベル
- class 番号: 0 = 動物 / 種ごと（classes.txt）
動画は OpenCV で作る小さな MJPEG。検出と種判別は偽のモデル（この試験が環境変数を立てる）"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

cv2 = pytest.importorskip("cv2")

from trapnote import detect, scan, train, yolo  # noqa: E402
from trapnote.judge import save as save_judgement  # noqa: E402
from trapnote.project import Project  # noqa: E402


def _video(path: str, moving: bool = True, n: int = 60, fps: int = 10) -> None:
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 120))
    try:
        for i in range(n):
            fr = np.full((120, 160, 3), 40, np.uint8)
            if moving:                       # 明るい塊が右へ動く（偽の検出器が枠を返す）。のっぺりした動画には枠が付かない
                x = 10 + (i * 2) % 100
                cv2.rectangle(fr, (x, 30), (x + 40, 80), (200, 200, 200), -1)
            w.write(fr)
    finally:
        w.release()


def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "1")
    root = tmp_path / "Site_2026"
    for st in ("St01", "St02"):
        (root / st).mkdir(parents=True)
    _video(str(root / "St01" / "IMG_0001.AVI"))
    _video(str(root / "St02" / "IMG_0001.AVI"))                 # 地点が違って、ファイル名が同じ
    _video(str(root / "St02" / "IMG_0002.AVI"))
    _video(str(root / "St02" / "IMG_0003.AVI"), moving=False)   # 枠が付かない
    img = np.full((120, 160, 3), 40, np.uint8)
    cv2.rectangle(img, (50, 30), (110, 90), (220, 220, 220), -1)
    cv2.imwrite(str(root / "St01" / "PIC_0001.JPG"), img)
    cv2.imwrite(str(root / "St01" / "PIC_0002.JPG"), np.full((120, 160, 3), 40, np.uint8))     # 枠が付かない静止画
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    p.db.x("UPDATE items SET duration=6.0, fps=10.0, meta_state=1, width=160, height=120 WHERE kind='video'")
    p.db.x("UPDATE items SET meta_state=1, width=160, height=120 WHERE kind='image'")
    hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
    hub.load_detector("auto")
    hub.load_classifier("speciesnet-test")
    ids = {}
    for r in p.db.q("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items ORDER BY id"):
        detect.process_item(p, hub, dict(r), True)
        ids[r["rel_path"]] = r["id"]
    return p, ids


def _tree(out):
    res = {}
    for d, _, names in os.walk(out):
        for n in names:
            res[os.path.relpath(os.path.join(d, n), out).replace("\\", "/")] = os.path.join(d, n)
    return res


def test_yolo_export_training_frames(tmp_path, monkeypatch):
    p, ids = _project(tmp_path, monkeypatch)
    try:
        a, b, c, flat = ids["St01/IMG_0001.AVI"], ids["St02/IMG_0001.AVI"], ids["St02/IMG_0002.AVI"], ids["St02/IMG_0003.AVI"]
        assert p.db.scalar("SELECT COUNT(*) FROM detections WHERE item_id=?", (a,)) > 3 and p.db.scalar("SELECT COUNT(*) FROM detections WHERE item_id=?", (flat,)) == 0
        save_judgement(p.db, a, dict(species=["boar"]), "me")
        save_judgement(p.db, b, dict(species=["deer"]), "me")
        save_judgement(p.db, c, dict(species=["empty"]), "me")            # 枠はあるが、人は「動物なし」（誤検出）
        save_judgement(p.db, flat, dict(species=["empty"]), "me")
        save_judgement(p.db, ids["St01/PIC_0001.JPG"], dict(species=["boar"]), "me")
        save_judgement(p.db, ids["St01/PIC_0002.JPG"], dict(species=["empty"]), "me")
        # 動画 a は、人が 3 枚目の枠を学習フレームに選び、その枠の時刻の別の枠を 1 つ「学習から外す」にはしない（外す枠は b で試す）
        crops_a = train.item_crops(p, a)["crops"]
        train.set_anchor(p, a, crops_a[2]["k"])
        t_a = crops_a[2]["t"]
        crops_b = train.item_crops(p, b)
        kb = crops_b["anchors"]["deer"]                                   # 選んでいない → 信頼度の最も高い枠
        t_b = next(x["t"] for x in crops_b["crops"] if x["k"] == kb)
        out = str(tmp_path / "yolo")
        r = yolo.export_yolo(p, out, {}, True, "animal0", True, False, None, None, "anchor")
        files = _tree(out)
        name_a, name_b = f"St01_IMG_0001_t{int(round(t_a * 1000)):07d}", f"St02_IMG_0001_t{int(round(t_b * 1000)):07d}"
        assert f"Boar/images/{name_a}.jpg" in files and f"Boar/labels/{name_a}.txt" in files
        assert f"Deer/images/{name_b}.jpg" in files and f"Deer/labels/{name_b}.txt" in files
        assert "Boar/images/St01_PIC_0001.JPG" in files and "Empty/images/St01_PIC_0002.JPG" in files and "classes.txt" in files
        vids = [k for k in files if "_IMG_" in k and k.endswith(".jpg")]
        assert len(vids) == 3 and not any("IMG_0003" in k for k in files), "動画 1 本から 1 枚。枠のない動画は書かない"
        assert (r["items"], r["images"], r["labels"], r["skipped"], r["frames"]) == (5, 5, 5, 1, "anchor")
        assert open(files["classes.txt"], encoding="utf-8").read() == "animal\n"
        line = open(files[f"Boar/labels/{name_a}.txt"], encoding="utf-8").read().strip().split("\n")
        assert len(line) == 1 and line[0].split()[0] == "0" and [float(v) for v in line[0].split()[1:]] == pytest.approx([0.5, 0.5, 0.4, 0.4])
        empty_label = next(k for k in files if k.startswith("Empty/labels/St02_IMG_0002_t"))
        assert open(files[empty_label], encoding="utf-8").read() == "" and open(files["Empty/labels/St01_PIC_0002.txt"], encoding="utf-8").read() == ""
        im = cv2.imread(files[f"Boar/images/{name_a}.jpg"])
        assert im is not None and im.shape[:2] == (120, 160)
        # 学習から外した枠は、ラベルに書かない（その動画の学習フレームは、残りの枠から選び直される）
        train.set_crop_excluded(p, b, [kb], True)
        out2 = str(tmp_path / "yolo2")
        yolo.export_yolo(p, out2, dict(ids=[b]), True, "animal0", True, False, None, None, "anchor")
        f2 = _tree(out2)
        assert not any(k.endswith(f"{name_b}.txt") for k in f2) and len([k for k in f2 if k.endswith(".jpg")]) == 1
        # 枠のあるフレームを全部
        out3 = str(tmp_path / "yolo3")
        r3 = yolo.export_yolo(p, out3, dict(ids=[a]), True, "animal0", True, False, None, None, "all")
        n_t = p.db.scalar("SELECT COUNT(DISTINCT t_sec) FROM detections WHERE item_id=? AND conf >= 0.2", (a,))
        assert r3["images"] == r3["labels"] == n_t > 3 and r3["frames"] == "all"
        # 画像を書かない（ラベルだけ）
        out4 = str(tmp_path / "yolo4")
        r4 = yolo.export_yolo(p, out4, dict(ids=[a]), False, "animal0", False, False, None, None, "anchor")
        assert r4["images"] == 0 and sorted(_tree(out4)) == ["classes.txt", f"labels/{name_a}.txt"]
    finally:
        p.close()


def test_yolo_export_species_classes_and_unique_names(tmp_path, monkeypatch):
    p, ids = _project(tmp_path, monkeypatch)
    try:
        save_judgement(p.db, ids["St01/IMG_0001.AVI"], dict(species=["boar"]), "me")
        save_judgement(p.db, ids["St02/IMG_0001.AVI"], dict(species=["boar"]), "me")
        out = str(tmp_path / "yolo")
        r = yolo.export_yolo(p, out, dict(status="verified"), False, "species", True, False, None, None, "anchor")
        files = _tree(out)
        jpgs = sorted(k for k in files if k.endswith(".jpg"))
        assert len(jpgs) == 2 and jpgs[0].startswith("images/St01_IMG_0001_t") and jpgs[1].startswith("images/St02_IMG_0001_t"), "同じファイル名でも地点が違えば重ならない"
        classes = open(files["classes.txt"], encoding="utf-8").read().split("\n")
        assert classes[classes.index("Boar")] == "Boar" and r["classes"][-2:] == ["Human", "Vehicle"]
        lab = open(files[jpgs[0].replace("images/", "labels/").replace(".jpg", ".txt")], encoding="utf-8").read().split()
        assert int(lab[0]) == r["classes"].index("Boar")
        # 学習に使う枠の記録（特徴量）がない動画（検出のみ）でも、信頼度の最も高い枠のフレームを書く
        fp = train.features_path(p, "St01/IMG_0001.AVI")
        os.remove(fp)
        dets = p.db.q("SELECT t_sec, category, conf, x, y, w, h, species FROM detections WHERE item_id=? ORDER BY t_sec, id", (ids["St01/IMG_0001.AVI"],))
        times, excl = yolo.frame_plan(p, dict(id=ids["St01/IMG_0001.AVI"], rel_path="St01/IMG_0001.AVI"), dets, 0.2, 0.2, "anchor")
        best = max(dets, key=lambda d: (d["conf"], -d["t_sec"]))
        assert times == [best["t_sec"]] and excl == []
        assert yolo.frame_plan(p, dict(id=ids["St02/IMG_0003.AVI"], rel_path="St02/IMG_0003.AVI"), [], 0.2, 0.2, "anchor") == ([], [])
        assert yolo._out_stem("Boar/images/N180712_BSN0870_IMG0047_img450.png") == "N180712_BSN0870_IMG0047_img450" and yolo._out_stem("A/B/V.AVI") == "A_B_V"
    finally:
        p.close()
