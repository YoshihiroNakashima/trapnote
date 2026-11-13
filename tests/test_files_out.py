"""右クリック「動画ファイルを書き出す…」（files_out.export_files）: 階層のまま / たたむ（<フォルダ名>_<ファイル名>）、同じ名前の衝突、見つからないもの、2 回目は飛ばす"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import files_out, scan  # noqa: E402
from trapnote.project import Project  # noqa: E402


def _season(tmp_path):
    root = tmp_path / "season"
    files = {"ST01/IMG_0001.AVI": b"a" * 1000, "ST01/IMG_0002.AVI": b"b" * 2000, "ST02/sub/IMG_0001.AVI": b"c" * 500, "ROOT.MP4": b"d" * 100}
    for rel, data in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    ids = {r["rel_path"]: r["id"] for r in p.db.q("SELECT id, rel_path FROM items")}
    assert len(ids) == 4
    return p, ids, files


def test_plan_names():
    assert files_out.flat_name("ST01/2025/VID.AVI") == "ST01_2025_VID.AVI"
    assert files_out.flat_name("VID.AVI") == "VID.AVI"
    names = files_out.plan_names(["A/V.AVI", "A_V.AVI", "a/v.avi", "B/V.AVI"], keep_tree=False)
    assert names == {"A/V.AVI": "A_V.AVI", "A_V.AVI": "A_V_2.AVI", "a/v.avi": "a_v_3.avi", "B/V.AVI": "B_V.AVI"}
    assert files_out.plan_names(["A/V.AVI"], keep_tree=True) == {"A/V.AVI": "A/V.AVI"}


def test_export_files_tree_and_flat(tmp_path):
    p, ids, files = _season(tmp_path)
    try:
        prog = []
        out = str(tmp_path / "out_tree")
        r = files_out.export_files(p, list(ids.values()), out, keep_tree=True, progress=lambda a, b, m: prog.append((a, b)))
        assert r["n"] == 4 and r["n_missing"] == 0 and not r["stopped"] and r["keep_tree"]
        for rel, data in files.items():
            assert open(os.path.join(out, *rel.split("/")), "rb").read() == data
        assert prog[-1] == (4, 4)
        # 2 回目: 同じ大きさのものは飛ばす
        r2 = files_out.export_files(p, list(ids.values()), out, keep_tree=True)
        assert r2["n"] == 0 and r2["n_same"] == 4

        out2 = str(tmp_path / "out_flat")
        r = files_out.export_files(p, list(ids.values()), out2, keep_tree=False)
        assert r["n"] == 4 and not r["keep_tree"]
        assert sorted(os.listdir(out2)) == ["ROOT.MP4", "ST01_IMG_0001.AVI", "ST01_IMG_0002.AVI", "ST02_sub_IMG_0001.AVI", "video_list.csv"]      # 一覧の CSV は必ず付く（確認事項 129 回目）
        assert open(os.path.join(out2, "ST02_sub_IMG_0001.AVI"), "rb").read() == b"c" * 500
        assert r["files"] == ["ROOT.MP4", "ST01_IMG_0001.AVI", "ST01_IMG_0002.AVI", "ST02_sub_IMG_0001.AVI"]

        # 見つからないもの・知らない id
        os.remove(tmp_path / "season" / "ST01" / "IMG_0002.AVI")
        out3 = str(tmp_path / "out3")
        r = files_out.export_files(p, [ids["ST01/IMG_0002.AVI"], ids["ROOT.MP4"], 99999], out3, keep_tree=False)
        assert r["n"] == 1 and r["n_missing"] == 1 and sorted(os.listdir(out3)) == ["ROOT.MP4", "video_list.csv"]

        # 止める: 最初から止まっていれば何も写さない
        out4 = str(tmp_path / "out4")
        r = files_out.export_files(p, [ids["ROOT.MP4"]], out4, keep_tree=True, stop=lambda: True)
        assert r["stopped"] and r["n"] == 0
    finally:
        p.close()


def test_reveal_missing_folder(tmp_path):
    assert files_out.reveal(str(tmp_path / "nothing" / "x.avi")) is False
