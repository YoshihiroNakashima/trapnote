"""横断プロジェクト: 種名の対応表（label_map）と、カタログからのランダム選択（単位: 動画・種ごと・地点・調査回・地点×調査回）。
実行: python -m pytest tests -q（TRAPNOTE_FAKE_ML=1 は要らない。動画は空ファイル）"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross, scan  # noqa: E402
from trapnote.db import now_iso  # noqa: E402
from trapnote.judge import item_state, save as judge_save  # noqa: E402
from trapnote.project import Project  # noqa: E402

SEASONS = {"N240128": ["BSN0812", "BSN0590", "BSN0620"], "N240415": ["BSN0812", "BSN0590", "BSN0700"]}
# 種名の CSV の値: 種リストに対応するもの、鳥の種名（自動で鳥類）、対応しないもの
LABELS = ["Deer", "Boar", "Kiji", "Hiyodori", "AngleChange", "Empty", "Deer", "RaccoonDog"]


def make_nas(tmp_path, with_csv: bool = True):
    root = tmp_path / "nas" / "PRM_N"
    rows = []
    k = 0
    for se, stations in SEASONS.items():
        for st in stations:
            for i in range(8):
                name = f"IMG_{i:04d}.AVI"
                d = root / se / st
                d.mkdir(parents=True, exist_ok=True)
                (d / name).write_bytes(b"")
                rows.append((se, st, name, LABELS[k % len(LABELS)], f"2024/01/28 {8 + i:02d}:00:00"))
                k += 1
    if with_csv:
        with open(root / "labels.csv", "w", encoding="utf-8", newline="") as fh:
            fh.write("Season,Station,File,Species,DateTime\n")
            for r in rows:
                fh.write(",".join(r) + "\n")
    return str(root), rows


def make_cross(tmp_path):
    place = tmp_path / "Studies" / "gold"
    place.mkdir(parents=True)
    return Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")


def test_label_map_on_csv(tmp_path):
    root, rows = make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        r = cross.add_sources_under(p, root)
        assert r["added"] == ["PRM_N"]
        s = cross.source_by_name(p, "PRM_N")
        # まだ読んでいない → pending
        cand = cross.label_candidates(p)
        assert cand["pending"] == ["PRM_N"] and cand["rows"] == []
        lab = cross._labels(p, s)                       # LabelsJob の代わり
        assert set(lab["labels"]) == set(LABELS)
        assert dict(lab["code_of"])["Kiji"] == "bird" and lab["code_of"]["AngleChange"] is None and lab["code_of"]["RaccoonDog"] == "raccoon_dog"
        cand = cross.label_candidates(p)
        by = {d["label"]: d for d in cand["rows"]}
        assert by["Kiji"]["auto"] == "bird" and by["Kiji"]["current"] == "bird" and by["Kiji"]["mapped"] is None and by["Kiji"]["kind"] == "csv"
        assert by["AngleChange"]["current"] is None and cand["n_unresolved"] == 1
        assert cand["rows"][0]["label"] == "AngleChange"      # 要選択が先
        assert by["Deer"]["n"] == 2 * (len(rows) // len(LABELS))
        # 対応表: キジは鳥類、ヒヨドリは不明、AngleChange は使わない。bird は「使わない」だったのが「使う」になる
        assert not p.species.by_code()["bird"]["enabled"]
        r = cross.set_label_map(p, {"Kiji": "bird", "hiyodori": "unknown", "AngleChange": ""})
        assert r["n"] == 3 and p.species.by_code()["bird"]["enabled"]
        with pytest.raises(ValueError):
            cross.set_label_map(p, {"x": "nosuchcode"})
        # 読み直さずに対応だけ当て直る（pickle から）
        assert cross.labels_ready(p, s)
        lab2 = cross._labels(p, s)
        assert lab2["code_of"]["Hiyodori"] == "unknown" and lab2["code_of"]["AngleChange"] is None
        assert lab2["unmapped"] == [("AngleChange", len(rows) // len(LABELS))]
        cand = cross.label_candidates(p)
        by = {d["label"]: d for d in cand["rows"]}
        assert by["AngleChange"]["mapped"] == "" and by["AngleChange"]["current"] is None and by["Hiyodori"]["current"] == "unknown"
        # カタログ: 対応表を当てた種で入る。作る前は stale ではなく「まだ」
        st = cross.catalog_status(p)
        assert not st["built"]
        r = cross.build_catalog(p)
        assert r["n"] == len(rows)
        st = cross.catalog_status(p)
        assert st["built"] and not st["stale"]
        cnt = cross.catalog_counts(p, {})
        assert cnt["by_species"]["bird"] == len(rows) // len(LABELS) and cnt["by_species"]["unknown"] == len(rows) // len(LABELS)
        assert "(none)" in cnt["by_species"]        # AngleChange は判定なし
        # 対応表を変えたら「対応の当て直し」が要る（作り直さずに labels 列から当て直す。作り直したのと同じになる）
        cross.set_label_map(p, {"Kiji": "bird", "hiyodori": "bird", "AngleChange": ""})
        assert cross.catalog_status(p)["stale"] and cross.catalog_status(p)["reasons"] == ["labels"]
        r = cross.remap_catalog(p)
        assert r["n_changed"] == len(rows) // len(LABELS) and r["n_combos"] == len(LABELS) - 1       # Deer は 2 回出るので表記の組は 7
        assert not cross.catalog_status(p)["stale"]
        after_remap = {(x["rel"], x["species"], x["status"]) for x in cross.catalog_rows(p, {}, limit=10000)["rows"]}
        assert cross.catalog_counts(p, {})["by_species"]["bird"] == 2 * (len(rows) // len(LABELS))
        cross.build_catalog(p)
        after_build = {(x["rel"], x["species"], x["status"]) for x in cross.catalog_rows(p, {}, limit=10000)["rows"]}
        assert after_remap == after_build
        # 「使わない」を外すと判定なし → 種名あり に戻る（status も当て直す）
        cross.set_label_map(p, {"Kiji": "bird", "hiyodori": "bird"})
        r = cross.remap_catalog(p)
        assert r["n_changed"] == 0                          # AngleChange は自動でも当たらないので変わらない
        cross.set_label_map(p, {"Kiji": "bird", "hiyodori": "bird", "AngleChange": "unknown"})
        r = cross.remap_catalog(p)
        assert r["n_changed"] == len(rows) // len(LABELS)
        assert cross.catalog_counts(p, {"species": "unknown"})["by_status"] == {"auto": len(rows) // len(LABELS)}
        # v61 以前に作ったカタログ（labels が空）は「古い形式」で作り直しが要る（当て直しでは変わらない）
        with p.db.tx() as c:
            c.execute("UPDATE catalog SET labels=NULL")
        assert cross.catalog_status(p)["reasons"] == ["old"]
        cross.build_catalog(p)
        assert not cross.catalog_status(p)["stale"]
        # 参照先が増えたら作り直し。プロジェクトも CSV の行もない調査回は、設定 catalog_scan_folders のときだけフォルダを見て載せる
        other = tmp_path / "nas2" / "PRM_N"
        (other / "N250101" / "BSN0001").mkdir(parents=True)
        (other / "N250101" / "BSN0001" / "IMG_0001.AVI").write_bytes(b"")
        cross.add_sources_under(p, str(other))
        assert "sources" in cross.catalog_status(p)["reasons"]
        cross.build_catalog(p)
        assert cross.catalog_counts(p, {"source": "nas2_PRM_N"})["total"] == 0
        p.update_settings(dict(catalog_scan_folders=True))
        cross.build_catalog(p)
        assert cross.catalog_counts(p, {"source": "nas2_PRM_N"})["total"] == 1 and not cross.catalog_status(p)["stale"]
    finally:
        p.close()


def test_sample_units(tmp_path):
    root, rows = make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        cross.add_sources_under(p, root)
        cross.set_label_map(p, {"AngleChange": ""})
        cross.build_catalog(p)
        n_all = len(rows)
        # 全部
        r = cross.catalog_sample(p, {}, {})
        assert r["n"] == n_all and r["unit"] == "all"
        # 動画を 5 本（乱数の種で再現）
        a = cross.catalog_sample(p, {}, dict(unit="video", n=5, seed=3))
        b = cross.catalog_sample(p, {}, dict(unit="video", n=5, seed=3))
        c = cross.catalog_sample(p, {}, dict(unit="video", n=5, seed=4))
        assert a["n"] == 5 and a["ids"] == b["ids"] and a["ids"] != c["ids"] and sum(a["counts"].values()) >= 5
        # 地点を 2 か所（参照先・調査回をまたいで同じ地点名は 1 か所。その地点の動画は全部）
        r = cross.catalog_sample(p, {}, dict(unit="station", n=2, seed=1))
        assert len(r["units"]) == 2 and r["n_units_available"] == 4
        names = [u[0] for u in r["units"]]
        assert r["n"] == sum(u[1] for u in r["units"])
        rows_r = cross.catalog_rows(p, {"station": ",".join(names)}, limit=1000)
        assert rows_r["total"] == r["n"]
        # 調査回を 1 回
        r = cross.catalog_sample(p, {}, dict(unit="season", n=1, seed=1))
        assert len(r["units"]) == 1 and r["units"][0][0] in SEASONS and r["n"] == 24 and r["n_units_available"] == 2
        # 地点 × 調査回を 3 組
        r = cross.catalog_sample(p, {}, dict(unit="station_season", n=3, seed=1))
        assert len(r["units"]) == 3 and r["n"] == 24 and r["n_units_available"] == 6 and all("/" in u[0] for u in r["units"])
        # 絞り込みの中から（調査回で絞ると地点は 3 か所）
        r = cross.catalog_sample(p, {"season": "N240128"}, dict(unit="station", n=10, seed=1))
        assert r["n_units_available"] == 3 and len(r["units"]) == 3 and r["n"] == 24
        # 種ごとに 2 本（地点 × 調査回の上限 1）
        r = cross.catalog_sample(p, {}, dict(unit="species", n=2, cap=1, seed=1))
        assert all(v <= 2 for v in r["counts"].values()) and r["counts"]["deer"] == 2 and "bird" in r["counts"]
        # 古い呼び方（n_per_species）も同じ
        r2 = cross.catalog_sample(p, {}, dict(n_per_species=2, cap=1, seed=1))
        assert r2["ids"] == r["ids"]
        # 取り込み: 地点 1 か所を gold に。記録に単位が残る
        r = cross.catalog_sample(p, {}, dict(unit="station", n=1, seed=2))
        imp = cross.catalog_import(p, r["ids"], "gold", dict(unit="station", units=r["units"], params=dict(unit="station", n=1, seed=2)))
        assert imp["n"] == r["n"]
        rec = p.data["imports"][-1]
        assert rec["batch"] == "gold" and rec["params"]["units"] == r["units"] and rec["n"] == r["n"]
        # 取り込み済みは次の選択から外れる
        r3 = cross.catalog_sample(p, {}, dict(unit="station", n=10, seed=1))
        assert r3["n_units_available"] == 3
        with pytest.raises(ValueError):
            cross.catalog_sample(p, {}, dict(unit="nope", n=1))
    finally:
        p.close()


def _season_project(tmp_path, root: str, season: str):
    """調査回フォルダに TrapNote のプロジェクトを作り、動画を登録して種 kiji（横断の種リストにない）の判定を付ける"""
    sdir = os.path.join(root, season)
    sp = Project(os.path.join(sdir, "TrapNote"), root=sdir, create=True)
    found = scan.walk_media(sdir, sp.setting("skip_names"))
    scan.register(sp, found)
    scan.apply_pattern(sp)
    sp.species.add(dict(name_ja="キジ", name_en="Kiji", name_sci="Phasianus versicolor"))
    ids = [r[0] for r in sp.db.q("SELECT id FROM items ORDER BY rel_path")]
    judge_save(sp.db, ids[0], dict(species=["kiji"]), "tester")
    judge_save(sp.db, ids[1], dict(species=["deer", "kiji"]), "tester")
    judge_save(sp.db, ids[2], dict(species=["deer"]), "tester")
    return sp, ids


def test_label_map_on_project_codes(tmp_path):
    root, rows = make_nas(tmp_path, with_csv=False)
    sp, ids = _season_project(tmp_path, root, "N240128")
    sp.close()
    p = make_cross(tmp_path)
    try:
        cross.add_sources_under(p, root)
        assert "kiji" not in p.species.by_code()
        cross.build_catalog(p)
        cat = p.data["catalog"]
        assert cat["code_names"]["kiji"]["ja"] == "キジ" and cat["code_names"]["kiji"]["n"] == 2 and cat["code_names"]["kiji"]["source"] == "PRM_N/N240128"
        cnt = cross.catalog_counts(p, {})
        assert cnt["by_species"]["kiji"] == 2 and cnt["by_species"]["deer"] == 2
        cand = cross.label_candidates(p)
        by = {d["label"]: d for d in cand["rows"]}
        assert by["kiji"]["kind"] == "code" and by["kiji"]["name_ja"] == "キジ" and by["kiji"]["auto"] == "bird" and by["kiji"]["n"] == 2
        # kiji → 鳥類 と決めて当て直す（調査回プロジェクトの種コードも labels 列から）
        cross.set_label_map(p, {"kiji": "bird"})
        assert cross.remap_catalog(p)["n_changed"] == 2
        cnt = cross.catalog_counts(p, {})
        assert "kiji" not in cnt["by_species"] and cnt["by_species"]["bird"] == 2 and cnt["by_species"]["deer"] == 2
        row = cross.catalog_rows(p, {"species": "bird"}, limit=10)["rows"][0]
        assert row["status"] == "verified"
        # 取り込み: 判定は対応表を当てた形（bird）で入り、書き戻し・再取得の比較でも「同じ」になる
        rows_r = cross.catalog_rows(p, {"species": "bird"}, limit=100)
        cids = [r["id"] for r in rows_r["rows"]]
        imp = cross.catalog_import(p, cids, "test")
        assert imp["n"] == 2
        sps = sorted(j["species"] for i in [r[0] for r in p.db.q("SELECT id FROM items")] for j in item_state(p.db, i)["judgements"])
        assert sps == ["bird", "bird", "deer"]
        plan = cross.sync_plan(p, "push")
        assert plan["n_update"] == 0 and plan["n_conflict"] == 0 and plan["n_same"] == 2
        plan = cross.sync_plan(p, "pull")
        assert plan["n_update"] == 0 and plan["n_conflict"] == 0
        # 横断で直すと書き戻しの対象になる
        iid = p.db.q("SELECT id FROM items ORDER BY id")[0][0]
        judge_save(p.db, iid, dict(species=["boar"]), "tester")
        assert cross.sync_plan(p, "push")["n_update"] == 1
    finally:
        p.close()


def test_label_columns():
    """種名 CSV の列は見出しで決める: 別名の完全一致 → 別名を含む見出し"""
    cols, idx = cross.label_columns(["Season", "Station", "File", "Species", "DateTime"])
    assert cols == {"season": "Season", "station": "Station", "file": "File", "species": "Species", "datetime": "DateTime"}
    cols, _ = cross.label_columns(["season_name", "site", "video_file", "species_name", "date_time"])
    assert cols == {"season": "season_name", "station": "site", "file": "video_file", "species": "species_name", "datetime": "date_time"}
    cols, _ = cross.label_columns(["調査回", "地点", "ファイル名", "種名1", "日時"])
    assert cols["species"] == "種名1" and cols["file"] == "ファイル名"
    cols, _ = cross.label_columns(["Season", "Station", "Name", "Species"])        # Name はファイル（species_name に取られない）
    assert cols["file"] == "Name" and cols["species"] == "Species"
    assert cross.label_columns(["a", "b", "c"])[0] == {}
