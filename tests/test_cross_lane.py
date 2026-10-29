"""cross レーンの処理（種名 CSV の読み込み・カタログの作成）は、開いているプロジェクトとは別に自分で横断プロジェクトを開いて動き、終わったら開いている方に反映する"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from test_cross_labels import make_cross, make_nas  # noqa: E402
from trapnote import cross  # noqa: E402
from trapnote.jobs import CatalogBuildJob, Lane, LabelsJob  # noqa: E402


class _Ctx:
    def __init__(self):
        self.project = None

    def db(self):
        return self.project.db if self.project else None


def _wait(lane, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = lane.status()
        if not st["current"] and not st["queued"]:
            return st
        time.sleep(0.05)
    raise RuntimeError("終わらない")


def test_detached_jobs(tmp_path):
    root, rows = make_nas(tmp_path)
    p = make_cross(tmp_path)
    cross.add_sources_under(p, root)
    pdir = p.dir
    ctx = _Ctx()
    lane = Lane("cross", ctx)
    try:
        # 開いているプロジェクトがなくても動く（id は負の数）
        j = lane.submit(LabelsJob("PRM_N", pdir))
        assert j.id is not None and j.id < 0
        _wait(lane)
        assert j.state == "done" and "件" in j.message
        s = cross.source_by_name(p, "PRM_N")
        assert cross.labels_ready(p, s)                     # 読んだものはメモリと cache/labels に残っていて、開いている方からも使える
        # 開いている横断プロジェクトと同じでも自分で開き、終わったら project.json の catalog を読み直させる
        ctx.project = p
        j = lane.submit(CatalogBuildJob("full", pdir))
        _wait(lane)
        assert j.state == "done" and j.result["n"] == len(rows)
        st = cross.catalog_status(p)
        assert st["built"] and st["n"] == len(rows) and not st["stale"]
        # 開いている側で対応表を変えて当て直し（auto → remap）。開いている側の label_map は裏の処理でも見える（project.json 経由）
        cross.set_label_map(p, {"Kiji": "bird", "AngleChange": ""})
        assert cross.catalog_status(p)["reasons"] == ["labels"]
        j = lane.submit(CatalogBuildJob("auto", pdir))
        _wait(lane)
        assert j.state == "done" and "当て直しました" in j.message
        assert not cross.catalog_status(p)["stale"]
        assert cross.catalog_counts(p, {})["by_species"]["bird"] == len(rows) // 8 * 2
        # 開いている側で update_data した項目は裏の処理の保存で消えない
        p.update_data(label_map_at="x")
        j = lane.submit(CatalogBuildJob("full", pdir))
        _wait(lane)
        from trapnote.config import read_json
        d = read_json(os.path.join(pdir, "project.json"), None)
        assert d["label_map_at"] == "x" and d["catalog"]["n"] == len(rows) and d["label_map"] == {"kiji": "bird", "anglechange": ""}
        # 途中で止める: カタログは途中までで「作り直しが要る」
        j = CatalogBuildJob("full", pdir)
        j.stop_flag.set()
        lane.submit(j)
        _wait(lane)
        assert j.state == "stopped" and cross.catalog_status(p)["stale"]
    finally:
        lane.close()
        p.close()
