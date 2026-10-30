"""「動物なし」の規則: 枠はあるが分類器が全部の枠を「動物なし」と見たら（empty の確率の平均 ≥ species_empty_p）、動画を「動物なし」にする。0 なら従来どおり要確認"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import detect  # noqa: E402
from trapnote.project import Project  # noqa: E402


class _Hub:
    detector_name = "md"

    def cls_label(self):
        return "test"


_N = [0]


def _item(p):
    _N[0] += 1
    rel = f"ST/a{_N[0]}.avi"
    with p.db.tx() as c:
        cur = c.execute("INSERT INTO items(rel_path, rel_key, folder, name, kind, station, status, det_state) VALUES(?,?,?,?,?,?,?,?)", (rel, rel.lower(), "ST", os.path.basename(rel), "video", "ST", "new", 1))
        return cur.lastrowid


def test_aggregate_video_reports_empty():
    codes = ["deer", "boar", "empty"]
    pc = np.array([[0.1, 0.05, 0.85], [0.2, 0.1, 0.7], [0.05, 0.05, 0.9]], dtype=np.float32)
    r = detect.aggregate_video(pc, codes, 0.5, 0.6, 3)
    assert r["species"] == [] and abs(r["empty_mean"] - (0.85 + 0.7 + 0.9) / 3) < 1e-3 and r["empty_frac"] == 1.0
    pc2 = np.array([[0.8, 0.1, 0.1], [0.7, 0.1, 0.2]], dtype=np.float32)
    r2 = detect.aggregate_video(pc2, codes, 0.5, 0.6, 3)
    assert r2["species"][0][0] == "deer" and r2["empty_frac"] == 0.0


def test_finish_item_empty_rule(tmp_path):
    root = tmp_path / "N250101"
    root.mkdir()
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    try:
        summary = dict(animal=True, person=False, vehicle=False, max_animal=0.9, max_person=0.0, max_vehicle=0.0, n_frames=10, animal_frames=5)
        cls_empty = dict(species=[], probs={"empty": 0.85}, empty_mean=0.85, empty_frac=1.0)
        # 既定（0.8）: 動物なしに
        iid = _item(p)
        detect.finish_item(p, _Hub(), dict(id=iid, rel_path="ST/a1.avi"), dict(summary), cls_empty)
        row = p.db.q1("SELECT auto_json, flag FROM items WHERE id=?", (iid,))
        auto = json.loads(row["auto_json"])
        assert auto["species"][0]["species"] == "empty" and abs(auto["species"][0]["confidence"] - 0.85) < 1e-6 and row["flag"] is None
        assert [r[0] for r in p.db.q("SELECT species FROM judgements WHERE item_id=?", (iid,))] == ["empty"]
        # 平均が届かない → 要確認
        iid2 = _item(p)
        detect.finish_item(p, _Hub(), dict(id=iid2, rel_path="ST/a2.avi"), dict(summary), dict(cls_empty, empty_mean=0.6))
        row2 = p.db.q1("SELECT auto_json, flag FROM items WHERE id=?", (iid2,))
        assert json.loads(row2["auto_json"])["species"] == [] and row2["flag"] == "low"
        # 0 なら使わない
        p.update_settings(dict(species_empty_p=0))
        iid3 = _item(p)
        detect.finish_item(p, _Hub(), dict(id=iid3, rel_path="ST/a3.avi"), dict(summary), cls_empty)
        row3 = p.db.q1("SELECT auto_json, flag FROM items WHERE id=?", (iid3,))
        assert json.loads(row3["auto_json"])["species"] == [] and row3["flag"] == "low"
    finally:
        p.close()
