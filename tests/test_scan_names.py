"""フォルダの走査（trapnote/scan.py）が、OS による違いに左右されないこと（確認事項 160 回目）

- フォルダの中のファイルを、名前順に登録する。OS がファイルを返す順は、Windows（NTFS）では名前順だが、macOS と Linux では決まっていない。
  前は OS が返す順のまま登録していたので、対象の番号（id）の順が OS で違い、番号の順に頼る所（学習のときの一覧など）の結果が変わった
- 大文字と小文字だけが違う名前（IMG_0001.AVI と img_0001.avi）があっても、登録が失敗しない。名前の大文字と小文字を区別する置き場（Linux など）で
  だけ起きる。前は、登録が UNIQUE の違反で丸ごと失敗して、1 件も入らなかった。いまは、名前順で先の 1 つだけを登録して、数を返す
動画は空のファイル（中身は読まない）。大文字と小文字だけが違う名前は、どの OS でも試せるように、走査の結果の形で渡す"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import scan  # noqa: E402
from trapnote.project import Project  # noqa: E402


def test_files_are_registered_in_name_order(tmp_path, monkeypatch):
    root = tmp_path / "Site_2026"
    names = ["IMG_0003.AVI", "IMG_0001.AVI", "IMG_0010.AVI", "IMG_0002.MP4", "IMG_0004.JPG"]
    for st in ("St02", "St01"):
        (root / st).mkdir(parents=True)
        for n in names:
            (root / st / n).write_bytes(b"x" * 10)

    real = os.scandir

    class Reversed:                     # OS が名前の逆順に返す、という場合を作る（Windows でも、名前順でない順を試せる）。os.walk もこれを通る
        def __init__(self, path):
            with real(path) as it:
                self._entries = iter(sorted(it, key=lambda e: e.name, reverse=True))

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._entries)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    monkeypatch.setattr(scan.os, "scandir", Reversed)
    found = scan.walk_media(str(root), ["TrapNote"])
    rels = [f[0] for f in found]
    assert rels == [f"{st}/{n}" for st in ("St01", "St02") for n in sorted(names)]

    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    try:
        res = scan.register(p, found)
        assert res["added"] == 10 and res["same_name"] == 0
        assert [r["rel_path"] for r in p.db.q("SELECT rel_path FROM items ORDER BY id")] == rels
    finally:
        p.close()


def test_names_that_differ_only_in_case(tmp_path):
    root = tmp_path / "Site_2026"
    (root / "ST01").mkdir(parents=True)
    (root / "ST01" / "IMG_0001.AVI").write_bytes(b"x" * 10)
    (root / "ST01" / "IMG_0002.AVI").write_bytes(b"x" * 10)
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    try:
        # 走査の結果（相対パス、名前、種類、フォルダ、大きさ、更新時刻）。名前順 = 大文字が先
        found = [("ST01/IMG_0001.AVI", "IMG_0001.AVI", "video", "ST01", 10, 1700000000.0),
                 ("ST01/IMG_0002.AVI", "IMG_0002.AVI", "video", "ST01", 10, 1700000000.0),
                 ("ST01/img_0001.avi", "img_0001.avi", "video", "ST01", 12, 1700000100.0),
                 ("st01/IMG_0002.AVI", "IMG_0002.AVI", "video", "st01", 10, 1700000000.0)]
        res = scan.register(p, found)
        assert res["added"] == 2 and res["same_name"] == 2
        assert [r["rel_path"] for r in p.db.q("SELECT rel_path FROM items ORDER BY id")] == ["ST01/IMG_0001.AVI", "ST01/IMG_0002.AVI"]
        # もう一度走査しても、同じ（増えない。登録した方が「見つからない」にも「変更」にもならない）
        res = scan.register(p, found)
        assert res["added"] == 0 and res["gone"] == 0 and res["changed"] == 0 and res["same_name"] == 2
        assert p.db.scalar("SELECT COUNT(*) FROM items WHERE missing=0") == 2
    finally:
        p.close()


def test_rel_of_keeps_sibling_folders_outside(tmp_path):
    """ルートと名前の前半が同じ隣のフォルダは、ルートの外（絶対パスのまま返す）"""
    from trapnote.paths import norm, rel_of
    root = str(tmp_path / "N18")
    assert rel_of(str(tmp_path / "N18" / "ST01" / "a.avi"), root) == "ST01/a.avi"
    assert rel_of(str(tmp_path / "N18" / "ST01" / "a.avi"), root + os.sep) == "ST01/a.avi"
    assert rel_of(root, root) == ""
    outside = str(tmp_path / "N181220" / "ST01" / "a.avi")
    assert rel_of(outside, root) == norm(outside)
    assert rel_of(str(tmp_path / "other" / "a.avi"), root) == norm(str(tmp_path / "other" / "a.avi"))
