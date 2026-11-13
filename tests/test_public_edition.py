"""公開版と Lite の実装版（確認事項 129 回目）で足したものの試験:
版の切り替え（config.edition）、動画の書き出しの「MP4 に変換」の範囲と一覧の CSV（files_out）、
ルートから当てるパターン（pattern の ^）と地点のフォルダを選ぶ処理（station）、地点を選ばないときの CSV の列（export.folder_cols）、
抜き取りの確認（spot）"""
from __future__ import annotations

import csv
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import config, export, files_out, judge, scan, spot, station  # noqa: E402
from trapnote.db import now_iso  # noqa: E402
from trapnote.pattern import compile_pattern, extract, names_in  # noqa: E402
from trapnote.project import Project  # noqa: E402

FILES = ["Area1/ST01/100RECNX/IMG_0001.AVI", "Area1/ST01/100RECNX/IMG_0002.AVI", "Area1/ST02/100RECNX/IMG_0001.AVI",
         "Area2/ST03/100RECNX/IMG_0001.AVI", "Area2/ST03/100RECNX/IMG_0002.AVI", "Area2/ST03/100RECNX/IMG_0003.AVI"]


def _project(tmp_path, files=FILES):
    root = tmp_path / "Survey"
    for i, rel in enumerate(files):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(bytes([65 + i]) * (100 + i))
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    ids = {r["rel_path"]: r["id"] for r in p.db.q("SELECT id, rel_path FROM items")}
    assert len(ids) == len(files)
    return p, ids


# ---------- 版の切り替え ----------
def test_edition_from_env(monkeypatch):
    monkeypatch.delenv("TRAPNOTE_EDITION", raising=False)
    assert config.edition() == "public"                       # 既定は公開版
    for v, want in (("lite", "lite"), ("research", "research"), ("public", "public"), (" Research ", "research"), ("x", "public"), ("", "public")):
        monkeypatch.setenv("TRAPNOTE_EDITION", v)
        assert config.edition() == want
    assert config.EDITIONS == ("public", "lite", "research")


def test_conv_mode():
    from trapnote.server import _conv_mode
    assert _conv_mode("all") == "all"
    assert _conv_mode(True) is True and _conv_mode("unplayable") is True
    assert _conv_mode(False) is False and _conv_mode(None) is False and _conv_mode("") is False


# ---------- 動画の書き出し: MP4 に変換する範囲、一覧の CSV ----------
def test_wants_mp4():
    w = files_out.wants_mp4
    # 変換しない
    assert not w(False, "video", "mjpeg", "pcm_s16le", "avi")
    # 画像は変換しない
    assert not w("all", "image", None, None, "jpg")
    # ブラウザで再生できない動画だけ（True）: AVI（MJPEG）は変換、H.264 の MP4 と MOV はそのまま
    assert w(True, "video", "mjpeg", "pcm_s16le", "avi")
    assert not w(True, "video", "h264", "aac", "mp4")
    assert not w(True, "video", "h264", "aac", "mov")
    assert w(True, "video", "hevc", "aac", "mp4")
    # 全部（"all"）: MP4 でない動画は全部。すでに H.264 の MP4 は、そのまま
    assert w("all", "video", "mjpeg", "", "avi")
    assert w("all", "video", "h264", "aac", "mov")
    assert not w("all", "video", "h264", "aac", "mp4")
    assert w("all", "video", "hevc", "aac", "mp4")


def _read_list(out):
    with open(os.path.join(out, files_out.LIST_NAME), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == files_out.LIST_COLS
    return {r[0]: dict(zip(files_out.LIST_COLS, r)) for r in rows[1:]}


def test_list_csv(tmp_path):
    p, ids = _project(tmp_path)
    try:
        a, b = ids["Area1/ST01/100RECNX/IMG_0001.AVI"], ids["Area1/ST02/100RECNX/IMG_0001.AVI"]
        judge.save(p.db, a, {"species": ["deer", "boar"]}, "tester")
        out = str(tmp_path / "out")
        r = files_out.export_files(p, [a, b], out, keep_tree=False)
        assert r["n"] == 2 and r["list_csv"] == os.path.join(out, files_out.LIST_NAME)
        rows = _read_list(out)
        assert sorted(rows) == ["Area1_ST01_100RECNX_IMG_0001.AVI", "Area1_ST02_100RECNX_IMG_0001.AVI"]
        ra = rows["Area1_ST01_100RECNX_IMG_0001.AVI"]
        assert ra["SourceFile"] == "IMG_0001.AVI" and ra["SourceFolder"].replace("\\", "/").endswith("Survey/Area1/ST01/100RECNX")
        assert ra["Station"] == "100RECNX" and ra["Season"] == "Survey" and ra["Status"] == "verified"
        assert ra["Species"].lower().split(";") == ["deer", "boar"]
        rb = rows["Area1_ST02_100RECNX_IMG_0001.AVI"]
        assert rb["Species"] == "" and rb["Status"] == "new"
        # 同じ書き出し先にもう一度（別の動画を足す）: 前の行は残り、新しい行が足される
        c = ids["Area2/ST03/100RECNX/IMG_0003.AVI"]
        r2 = files_out.export_files(p, [a, c], out, keep_tree=False)
        assert r2["n"] == 1 and r2["n_same"] == 1
        rows = _read_list(out)
        assert sorted(rows) == ["Area1_ST01_100RECNX_IMG_0001.AVI", "Area1_ST02_100RECNX_IMG_0001.AVI", "Area2_ST03_100RECNX_IMG_0003.AVI"]
    finally:
        p.close()


# ---------- パターン: ルートから当てる（^） ----------
def test_pattern_from_root():
    c = compile_pattern("^*/{station}")
    assert c.from_root and names_in("^*/{station}") == ["station"]
    assert extract("Area1/ST01/100RECNX/IMG_0001.AVI", c) == {"station": "ST01"}
    assert extract("Area1/ST02/IMG_0001.AVI", c) == {"station": "ST02"}            # 地点の下の深さが違ってもよい
    assert extract("Area1/IMG_0001.AVI", c) == {"station": ""}                     # 選んだ階層より浅い: 地点なし（ファイル名には当てない）
    assert extract("IMG_0001.AVI", c) == {"station": ""}
    c2 = compile_pattern("^{season}/{station}")
    assert extract("2026_spring/ST01/sub/IMG.AVI", c2) == {"season": "2026_spring", "station": "ST01"}
    # もとの形（後ろから当てる）は変わらない
    c3 = compile_pattern("{station}/{file}")
    assert not c3.from_root and extract("Area1/ST01/100RECNX/IMG_0001.AVI", c3) == {"station": "100RECNX"}
    assert extract("a/b/c.avi", compile_pattern("")) == {}


def test_station_make_pattern_and_levels():
    assert station.make_pattern(1, None) == "^*/{station}"
    assert station.make_pattern(0, None) == "^{station}"
    assert station.make_pattern(1, 0) == "^{season}/{station}"
    assert station.make_pattern(0, 2) == "^{station}/*/{season}"
    assert station.make_pattern(None, 0) == "^{season}"
    assert station.make_pattern(None, None) == ""
    with pytest.raises(ValueError):
        station.make_pattern(1, 1)
    assert station.levels_of("{station}/{file}", 3) == dict(station=2, season=None)          # 既定 = 動画のすぐ上のフォルダ
    assert station.levels_of("{season}/{station}/{file}", 3) == dict(station=2, season=1)
    assert station.levels_of("^*/{station}", 3) == dict(station=1, season=None)
    assert station.levels_of("", 3) == dict(station=None, season=None)
    assert station.levels_of("{station}/{file}", 0) == dict(station=None, season=None)       # 動画がルートの直下: 当てはまる階層がない


def test_station_pick(tmp_path):
    p, ids = _project(tmp_path)
    try:
        info = station.info(p)
        assert info["example"]["dirs"] == ["Area1", "ST01", "100RECNX"] and info["example"]["file"] == "IMG_0001.AVI"
        assert [lv["n"] for lv in info["levels"]] == [2, 3, 1] and info["levels"][1]["names"] == ["ST01", "ST02", "ST03"]
        assert info["station_level"] == 2 and info["has_station"] and not info["custom"] and info["n_items"] == 6
        assert info["asked"] is True                                   # 印のないプロジェクト（前からあるもの）は、自動では訊かない
        pv = station.preview(p, 1, None)
        assert pv["pattern"] == "^*/{station}" and pv["n_stations"] == 3 and pv["n_seasons"] == 1 and pv["n_no_station"] == 0
        assert {r["folder"]: r["station"] for r in pv["rows"]} == {"Area1/ST01/100RECNX": "ST01", "Area1/ST02/100RECNX": "ST02", "Area2/ST03/100RECNX": "ST03"}
        assert p.setting("pattern") == "{station}/{file}"              # 見本では、何も変えない
        # 2 段目を地点に
        p.data["station_asked"] = False
        r = station.pick(p, 1, None)
        assert r["pattern"] == "^*/{station}" and r["n"] == 6 and p.setting("pattern") == "^*/{station}" and p.data["station_asked"] is True
        got = {x["rel_path"]: (x["station"], x["season"]) for x in p.db.q("SELECT rel_path, station, season FROM items")}
        assert got["Area1/ST01/100RECNX/IMG_0002.AVI"] == ("ST01", "Survey") and got["Area2/ST03/100RECNX/IMG_0003.AVI"] == ("ST03", "Survey")
        assert export.folder_cols(p) == []
        # 1 段目を調査回、2 段目を地点に
        station.pick(p, 1, 0)
        got = {x["rel_path"]: (x["station"], x["season"]) for x in p.db.q("SELECT rel_path, station, season FROM items")}
        assert got["Area2/ST03/100RECNX/IMG_0001.AVI"] == ("ST03", "Area2")
        assert station.info(p)["season_level"] == 0
        # 地点を選ばない: 地点は空、調査回は既定（ルートのフォルダ名）に戻る。CSV には Folder1〜3 の列
        r = station.pick(p, None, None)
        assert r["pattern"] == "" and p.setting("pattern") == ""
        assert {(x["station"], x["season"]) for x in p.db.q("SELECT station, season FROM items")} == {(None, "Survey")}
        assert export.folder_cols(p) == ["Folder1", "Folder2", "Folder3"]
        path = str(tmp_path / "x.csv")
        export.export_csv(p, path, {})
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 6 and rows[0]["Station"] == "" and (rows[0]["Folder1"], rows[0]["Folder2"], rows[0]["Folder3"]) == ("Area1", "ST01", "100RECNX")
        info = station.info(p)
        assert info["station_level"] is None and not info["has_station"]
        # 走査し直しても（新しい動画にも）同じ決め方が当たる
        station.pick(p, 1, None)
        f = tmp_path / "Survey" / "Area1" / "ST09" / "IMG_0009.AVI"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"z" * 50)
        scan.register(p, scan.walk_media(p.root, p.setting("skip_names")))
        assert p.db.q1("SELECT station FROM items WHERE rel_path=?", ("Area1/ST09/IMG_0009.AVI",))["station"] == "ST09"
    finally:
        p.close()


def test_station_skip(tmp_path):
    p, _ = _project(tmp_path)
    try:
        p.data["station_asked"] = False
        assert station.info(p)["asked"] is False
        station.skip(p)
        assert p.data["station_asked"] is True and p.setting("pattern") == "{station}/{file}"
    finally:
        p.close()


# ---------- 抜き取りの確認 ----------
def test_upper_bound():
    assert spot.upper_bound(0, 100) == pytest.approx(0.0295, abs=0.0005)        # 100 本で 0 → 約 3 %
    assert spot.upper_bound(0, 300) == pytest.approx(0.0099, abs=0.0003)        # 300 本で 0 → 約 1 %
    assert spot.upper_bound(2, 100) == pytest.approx(0.0616, abs=0.0005)
    assert spot.upper_bound(5, 5) == 1.0 and spot.upper_bound(0, 0) is None
    e = spot.estimate(5000, 100, 2)
    assert e["est"] == 100 and e["est_hi"] == 309 and e["share"] == pytest.approx(0.02)
    assert spot.estimate(5000, 0, 0) == dict(share=None, share_hi=None, est=None, est_hi=None)


def _mark(p, ids, boxed):
    """検出済みにして、AI の判定（未確認）を付ける。boxed に入っていない動画は、枠なしで「動物なし」"""
    for rel, iid in ids.items():
        nb = 1 if rel in boxed else 0
        p.db.x("UPDATE items SET det_state=1, n_boxes=?, status='auto' WHERE id=?", (nb, iid))
        p.db.x("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
               (iid, "deer" if nb else "empty", 0.9, "auto", "m", now_iso(), None))


def test_spot_check(tmp_path):
    files = [f"ST{s:02d}/IMG_{i:04d}.AVI" for s in (1, 2) for i in range(1, 11)]           # 20 本
    p, ids = _project(tmp_path, files)
    try:
        boxed = {rel for rel in files if rel.endswith(("0001.AVI", "0002.AVI"))}            # 4 本に枠 → 枠なしは 16 本
        _mark(p, ids, boxed)
        assert judge.bulk_verify_count(p.db, {}) == 20 and spot.pool_count(p.db, {}) == 16
        assert spot.pool_count(p.db, {"station": "ST01"}) == 8
        st = spot.status(p)
        assert st["active"] is None and st["history"] == [] and st["default_n"] == 100
        # 抜き取る数が母数より多ければ、全部
        st = spot.start(p, {}, 100, "tester", seed=7)
        a = st["active"]
        assert a["n_pool"] == 16 and a["n_sample"] == 16 and a["n_done"] == 0 and a["est"] is None
        assert spot.cancel(p)["filters"] == {} and spot.status(p)["active"] is None and "spot_checks" not in p.data
        # 5 本を抜き取る（種が同じなら、同じ動画が選ばれる）
        a = spot.start(p, {"station": "ST01"}, 5, "tester", seed=7)["active"]
        assert a["n_pool"] == 8 and a["n_sample"] == 5 and len(a["ids"]) == 5 and a["filters"] == {"station": "ST01"}
        unboxed_st01 = {iid for rel, iid in ids.items() if rel.startswith("ST01/") and rel not in boxed}
        assert set(a["ids"]) <= unboxed_st01
        with pytest.raises(ValueError):
            spot.start(p, {}, 5, "tester")                                # 途中のものがあるうちは、始められない
        sample = a["ids"]
        spot.cancel(p)
        assert spot.start(p, {"station": "ST01"}, 5, "tester", seed=7)["active"]["ids"] == sample
        # 4 本を確認: 動物あり 1、人だけ 1、動物なし 2
        judge.save(p.db, sample[0], {"species": ["empty"]}, "tester")
        judge.save(p.db, sample[1], {"species": ["deer"]}, "tester")
        judge.save(p.db, sample[2], {"species": ["human"]}, "tester")
        judge.save(p.db, sample[3], {"species": ["empty"]}, "tester")
        a = spot.status(p)["active"]
        assert (a["n_done"], a["n_animal"], a["n_other"], a["n_empty"]) == (4, 1, 1, 2)
        assert a["share"] == pytest.approx(0.25) and a["est"] == 2 and a["est_hi"] >= a["est"]
        # 記録して終える: project.json に残り、始めたときの絞り込みが返る
        r = spot.finish(p, "tester")
        assert r["filters"] == {"station": "ST01"}
        rec = r["result"]
        assert (rec["n_pool"], rec["n_sample"], rec["n_done"], rec["n_animal"], rec["n_other"]) == (8, 5, 4, 1, 1) and rec["seed"] == 7
        assert spot.status(p)["active"] is None and len(spot.status(p)["history"]) == 1
        p2 = Project(p.dir)                                               # 開き直しても残っている
        try:
            assert len(p2.data["spot_checks"]) == 1 and p2.data["spot_checks"][0]["n_animal"] == 1 and "spot_active" not in p2.data
        finally:
            p2.close()
        with pytest.raises(ValueError):
            spot.finish(p, "tester")                                      # 途中のものがない
        # 確認した動画は、一括確定の対象（未確認）から外れている → 次の母数は減る
        assert spot.pool_count(p.db, {"station": "ST01"}) == 4
        # 枠のない未確認の動画がなければ、始められない
        with pytest.raises(ValueError):
            spot.start(p, {"boxes": "1"}, 5, "tester")
    finally:
        p.close()
