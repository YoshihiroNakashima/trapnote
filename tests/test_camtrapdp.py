"""Camtrap DP 形式の書き出し（trapnote/camtrapdp.py。確認事項 140・141 回目）

- 表の定義（列の名前・型・制約）が、公式の table schema（Camtrap DP 1.0.2）と同じ: 2026-11-16 に
  https://raw.githubusercontent.com/tdwg/camtrap-dp/1.0.2/<表>-table-schema.json を開き、fields から name・type・format と
  constraints（required・unique・enum・pattern・minimum・maximum）をこの順に取り出した JSON の SHA-256 を控えた（OFFICIAL）
- 判定から observations への対応（種・動物なし・人・車・不明・有効範囲・判定なし・要確認、AI のままの判定）、追加項目（個体数・性別・行動・ほか）
- 地点の表（座標）を読み込めば検査に通り、なければ「足りないもの」として出る
動画は空のファイル（中身は読まない）"""
from __future__ import annotations

import csv
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import camtrapdp, scan  # noqa: E402
from trapnote.judge import save as save_judgement, set_auto  # noqa: E402
from trapnote.project import Project  # noqa: E402

OFFICIAL = {"deployments": (24, "05e1dcf5260a0a7a7c1f5559d46b80f04e2a8aa0b94740441145b62b29f799e8"),
            "media": (11, "525224f16f450b8d32ac97c12f20553c11c24bc2203c469fd4cecf166d921cd1"),
            "observations": (28, "a974bbd62473f83f36ba853f99f7049303f5deacaa25796e6ef8e24d921de36b")}
META = dict(title="Boso 2026", contributor="Taro Yamada", sampling="systematicRandom", capture=["activityDetection"], individuals=False, tz="+09:00")


def test_tables_match_official_schema():
    for name, (n, sha) in OFFICIAL.items():
        assert len(camtrapdp.TABLES[name]) == n, name
        assert camtrapdp.schema_digest(name) == sha, name


def test_offset_and_datetime():
    assert camtrapdp.norm_offset("+9") == "+09:00" and camtrapdp.norm_offset("+0900") == "+09:00" and camtrapdp.norm_offset("-03:30") == "-03:30"
    assert camtrapdp.norm_offset("UTC") == "Z" and camtrapdp.norm_offset("Z") == "Z"
    assert camtrapdp.norm_offset("") is None and camtrapdp.norm_offset("9") is None and camtrapdp.norm_offset("+25:00") is None
    assert camtrapdp.iso("2026/10/20 19:05:21", "+09:00") == "2026-10-20T19:05:21+09:00"
    assert camtrapdp.iso("2026/10/20 23:59:55", "+09:00", 10.4) == "2026-10-21T00:00:05+09:00"
    assert camtrapdp.iso("", "+09:00") is None and camtrapdp.iso("x", "Z") is None
    assert camtrapdp.local_offset()[0] in "+-" and len(camtrapdp.local_offset()) == 6


def _project(tmp_path):
    root = tmp_path / "Site_2026"
    files = ["St01/IMG_0001.AVI", "St01/IMG_0002.AVI", "St01/IMG_0003.AVI", "St01/IMG_0004.MP4", "St02/IMG_0001.AVI", "St02/IMG_0002.JPG", "St02/IMG_0003.AVI", "St02/IMG_0004.AVI"]
    for rel in files:
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(b"x" * 10)
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    ids = {r["rel_path"]: r["id"] for r in p.db.q("SELECT id, rel_path FROM items")}
    assert len(ids) == len(files)
    for i, rel in enumerate(files):
        p.db.x("UPDATE items SET datetime=?, duration=?, meta_state=1 WHERE id=?", (f"2026/10/{20 + i:02d} 19:05:21", None if rel.endswith(".JPG") else 10.0, ids[rel]))
    return p, ids


def _judge(p, ids):
    db = p.db
    save_judgement(db, ids["St01/IMG_0001.AVI"], dict(species=["boar"], species_fields={"boar": {"count": ["3"], "sex": ["メス"], "behaviour": ["採食", "移動"], "juvenile": ["1"]}},
                                                      fields={"memo": ["泥浴び"]}, note="きれい"), "me")
    save_judgement(db, ids["St01/IMG_0002.AVI"], dict(species=["deer", "raccoon_dog"]), "me")
    save_judgement(db, ids["St01/IMG_0003.AVI"], dict(species=["empty"]), "me")
    save_judgement(db, ids["St01/IMG_0004.MP4"], dict(species=["area"]), "me")
    save_judgement(db, ids["St02/IMG_0001.AVI"], dict(species=["unknown"]), "you")
    save_judgement(db, ids["St02/IMG_0002.JPG"], dict(species=["human"]), "you")
    set_auto(db, ids["St02/IMG_0003.AVI"], [dict(species="macaque", confidence=0.87)], "speciesnet-test")       # AI の判定のまま（未確認）
    db.x("UPDATE items SET favorite=1 WHERE id=?", (ids["St01/IMG_0001.AVI"],))
    db.x("UPDATE items SET datetime_corrected='2026/10/27 20:00:00', time_offset_sec=3279 WHERE id=?", (ids["St02/IMG_0004.AVI"],))    # 判定なし。時計のずれを直してある


def _read(out, name):
    with open(os.path.join(out, name + ".csv"), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _stations_csv(tmp_path, text):
    fp = tmp_path / "stations_in.csv"
    fp.write_text(text, encoding="utf-8-sig")
    return str(fp)


def test_export_valid_package(tmp_path):
    p, ids = _project(tmp_path)
    try:
        _judge(p, ids)
        r = camtrapdp.import_stations(p, _stations_csv(tmp_path, "地点,緯度,経度,設置日,回収日,cameraModel,cameraHeight\nSt01,35.2101,140.1012,2026/10/01,2026/11/30,Browning-BTC-6HD,0.5\nst02,35.2202,140.1103,,,,\n"))
        assert r["rows"] == 2 and r["n_bad"] == 0 and r["n_with_coords"] == 2 and r["n_deployments"] == 2
        out = str(tmp_path / "dp")
        res = camtrapdp.export_camtrapdp(p, out, {}, META)
        assert res["problems"] == [] and res["valid"], res["problems"]
        assert (res["deployments"], res["media"], res["observations"], res["no_time"], res["no_coords"]) == (2, 8, 9, 0, 0)
        assert sorted(os.listdir(out)) == ["datapackage.json", "deployments.csv", "media.csv", "observations.csv"]
        pkg = json.load(open(os.path.join(out, "datapackage.json"), encoding="utf-8"))
        assert pkg["profile"].endswith("/1.0.2/camtrap-dp-profile.json") and [x["name"] for x in pkg["resources"]] == ["deployments", "media", "observations"]
        assert pkg["contributors"] == [dict(title="Taro Yamada", role="contact")] and pkg["project"]["observationLevel"] == ["event"]
        assert pkg["project"]["samplingDesign"] == "systematicRandom" and pkg["project"]["individualAnimals"] is False
        assert pkg["temporal"] == dict(start="2026-10-01", end="2026-11-30")
        assert pkg["spatial"]["type"] == "Polygon" and pkg["spatial"]["bbox"] == [140.1012, 35.2101, 140.1103, 35.2202]
        names = {t["scientificName"]: t for t in pkg["taxonomic"]}
        assert set(names) == {"Sus scrofa", "Cervus nippon", "Nyctereutes procyonoides", "Macaca fuscata"}
        assert names["Sus scrofa"]["taxonRank"] == "species" and names["Sus scrofa"]["vernacularNames"] == dict(eng="Wild Boar", jpn="イノシシ")
        # deployments: 調査回（ルートのフォルダ名）_地点。地点の表の開始・終了、なければ最初と最後の動画
        dep = {d["deploymentID"]: d for d in _read(out, "deployments")}
        assert set(dep) == {"Site_2026_St01", "Site_2026_St02"}
        d1, d2 = dep["Site_2026_St01"], dep["Site_2026_St02"]
        assert (d1["latitude"], d1["longitude"], d1["locationName"], d1["cameraModel"], d1["cameraHeight"]) == ("35.2101", "140.1012", "St01", "Browning-BTC-6HD", "0.5")
        assert d1["deploymentStart"] == "2026-10-01T00:00:00+09:00" and d1["deploymentEnd"] == "2026-11-30T23:59:59+09:00" and d1["deploymentComments"] == ""
        assert d2["deploymentStart"] == "2026-10-24T19:05:21+09:00" and d2["deploymentEnd"] == "2026-10-27T20:00:10+09:00" and "first/last media" in d2["deploymentComments"]
        assert d1["deploymentGroups"] == "season:Site_2026"
        # media
        md = {m["mediaID"]: m for m in _read(out, "media")}
        m1 = md["St01/IMG_0001.AVI"]
        assert (m1["deploymentID"], m1["timestamp"], m1["filePath"], m1["filePublic"], m1["fileMediatype"], m1["favorite"], m1["captureMethod"]) == \
               ("Site_2026_St01", "2026-10-20T19:05:21+09:00", "St01/IMG_0001.AVI", "false", "video/x-msvideo", "true", "activityDetection")
        assert m1["mediaComments"] == "きれい / memo: 泥浴び"
        assert md["St02/IMG_0002.JPG"]["fileMediatype"] == "image/jpeg" and md["St01/IMG_0004.MP4"]["fileMediatype"] == "video/mp4"
        assert md["St02/IMG_0004.AVI"]["timestamp"] == "2026-10-27T20:00:00+09:00"          # 直したあとの日時
        # observations
        ob = {o["observationID"]: o for o in _read(out, "observations")}
        assert all(o["observationLevel"] == "event" and o["mediaID"] == "" and o["eventID"] == o["observationID"].rsplit("#", 1)[0] for o in ob.values())
        o = ob["St01/IMG_0001.AVI#boar"]
        assert (o["observationType"], o["scientificName"], o["count"], o["sex"], o["behavior"], o["classificationMethod"], o["classifiedBy"]) == \
               ("animal", "Sus scrofa", "3", "female", "採食 | 移動", "human", "me")
        assert o["observationTags"] == "juvenile:1" and o["eventStart"] == "2026-10-20T19:05:21+09:00" and o["eventEnd"] == "2026-10-20T19:05:31+09:00"
        assert o["classificationTimestamp"] and o["classificationProbability"] == ""
        assert {"St01/IMG_0002.AVI#deer", "St01/IMG_0002.AVI#raccoon_dog"} <= set(ob)             # 2 種の動画は 2 行
        assert ob["St01/IMG_0003.AVI#empty"]["observationType"] == "blank"
        a = ob["St01/IMG_0004.MP4#area"]
        assert (a["observationType"], a["cameraSetupType"]) == ("human", "calibration")
        assert ob["St02/IMG_0001.AVI#unknown"]["observationType"] == "unknown" and ob["St02/IMG_0001.AVI#unknown"]["classifiedBy"] == "you"
        h = ob["St02/IMG_0002.JPG#human"]
        assert h["observationType"] == "human" and h["cameraSetupType"] == "" and h["eventStart"] == h["eventEnd"]     # 静止画は長さなし
        mc = ob["St02/IMG_0003.AVI#macaque"]
        assert (mc["classificationMethod"], mc["classifiedBy"], mc["classificationProbability"], mc["scientificName"]) == ("machine", "speciesnet-test", "0.870", "Macaca fuscata")
        assert ob["St02/IMG_0004.AVI#none"]["observationType"] == "unclassified" and ob["St02/IMG_0004.AVI#none"]["classificationMethod"] == ""
        # 説明を覚えている。確認済みだけに絞ると、動画も地点も減る
        assert camtrapdp.info(p)["meta"]["contributor"] == "Taro Yamada" and camtrapdp.info(p)["meta"]["sampling"] == "systematicRandom"
        res2 = camtrapdp.export_camtrapdp(p, str(tmp_path / "dp2"), dict(status="verified", station="St01"), META)
        assert (res2["deployments"], res2["media"], res2["observations"]) == (1, 4, 5) and res2["valid"]
    finally:
        p.close()


def test_export_without_coordinates_reports_what_is_missing(tmp_path):
    p, ids = _project(tmp_path)
    try:
        _judge(p, ids)
        p.db.x("UPDATE items SET flag='low', auto_json=? WHERE id=?", (json.dumps(dict(model="speciesnet-test", species=[], low=True, at="2026-11-01T10:00:00+09:00")), ids["St02/IMG_0004.AVI"]))
        p.db.x("UPDATE items SET datetime=NULL WHERE id=?", (ids["St02/IMG_0003.AVI"],))                 # 日時のない動画は書かない
        out = str(tmp_path / "dp")
        res = camtrapdp.export_camtrapdp(p, out, {}, dict(META, tz="+9"))
        assert not res["valid"] and res["no_coords"] == 2 and res["no_time"] == 1 and res["media"] == 7
        kinds = {(x["where"], x["kind"]): x["n"] for x in res["problems"]}
        assert kinds == {("datapackage.spatial", "required"): 1, ("deployments.latitude", "required"): 2, ("deployments.longitude", "required"): 2}
        low = {o["observationID"]: o for o in _read(out, "observations")}["St02/IMG_0004.AVI#none"]
        assert (low["observationType"], low["classificationMethod"], low["classifiedBy"], low["classificationTimestamp"]) == ("unknown", "machine", "speciesnet-test", "2026-11-01T10:00:00+09:00")
        assert "spatial" not in json.load(open(os.path.join(out, "datapackage.json"), encoding="utf-8"))
        i = camtrapdp.info(p)
        assert (i["n_deployments"], i["n_with_coords"], i["without_coords"], i["stations_file"]) == (2, 0, ["St01", "St02"], "")
    finally:
        p.close()


def test_meta_is_required(tmp_path):
    p, ids = _project(tmp_path)
    try:
        out = str(tmp_path / "dp")
        with pytest.raises(ValueError):
            camtrapdp.export_camtrapdp(p, out, {}, dict(META, contributor=""))
        with pytest.raises(ValueError):
            camtrapdp.export_camtrapdp(p, out, {}, dict(META, sampling=""))
        with pytest.raises(ValueError):
            camtrapdp.export_camtrapdp(p, out, {}, dict(META, tz="JST"))
        assert not os.path.exists(out)
        assert camtrapdp.export_camtrapdp(p, out, {}, dict(META, contributor=""), user_name="Hanako")["media"] == 8      # 設定の「確認者の名前」を作成者に使う
        assert json.load(open(os.path.join(out, "datapackage.json"), encoding="utf-8"))["contributors"][0]["title"] == "Hanako"
    finally:
        p.close()


def test_stations_table_template_and_bad_rows(tmp_path):
    p, ids = _project(tmp_path)
    try:
        t = camtrapdp.write_template(p, str(tmp_path / "tpl.csv"))
        assert t["rows"] == 2
        rows = list(csv.DictReader(open(t["path"], encoding="utf-8-sig", newline="")))
        assert [r["Station"] for r in rows] == ["St01", "St02"] and rows[0]["Latitude"] == "" and rows[0]["Videos"] == "4" and rows[0]["FirstVideo"] == "2026/10/20 19:05:21"
        # ひな形に座標を書き込んで読み込む。範囲の外の座標と、読めない日付は捨てて知らせる。プロジェクトにない地点は知らせる
        r = camtrapdp.import_stations(p, _stations_csv(tmp_path, "Station,Latitude,Longitude,Start,End,Videos\nSt01,35.5,139.5,2026-10-01 09:00,,4\nSt02,95.0,139.5,,,4\nSt09,35.1,139.1,x,,\n,,,,,\n"))
        assert r["rows"] == 3 and r["n_bad"] == 2 and {(b["station"], b["why"]) for b in r["bad"]} == {("St02", "coords"), ("St09", "start")}
        assert r["n_with_coords"] == 1 and r["without_coords"] == ["St02"] and r["not_in_project"] == ["st09"] and r["stations_file"]
        saved = camtrapdp.load_stations(p)
        assert saved[0]["Start"] == "2026/10/01 09:00:00" and saved[1]["Latitude"] == ""
        t2 = camtrapdp.write_template(p, str(tmp_path / "tpl2.csv"))
        assert list(csv.DictReader(open(t2["path"], encoding="utf-8-sig", newline="")))[0]["Latitude"] == "35.5"      # 読み込んである値はひな形に入る
        with pytest.raises(ValueError):
            camtrapdp.import_stations(p, _stations_csv(tmp_path, "name,lat\nA,1\n"))
    finally:
        p.close()


def test_check_package_finds_broken_values(tmp_path):
    p, ids = _project(tmp_path)
    try:
        _judge(p, ids)
        camtrapdp.import_stations(p, _stations_csv(tmp_path, "station,lat,lon\nSt01,35.2,140.1\nSt02,35.3,140.2\n"))
        out = str(tmp_path / "dp")
        assert camtrapdp.export_camtrapdp(p, out, {}, META)["valid"]
        fp = os.path.join(out, "observations.csv")
        text = open(fp, encoding="utf-8").read().replace(",animal,", ",animals,", 1).replace("+09:00", "", 1)
        open(fp, "w", encoding="utf-8", newline="").write(text)
        kinds = {(x["where"], x["kind"]) for x in camtrapdp.check_package(out)}
        assert ("observations.observationType", "enum") in kinds and ("observations.eventStart", "type") in kinds
    finally:
        p.close()


def test_description_and_licenses_are_optional(tmp_path):
    """説明（description）とライセンス（licenses）は、入れたときだけ書く（確認事項 141 回目。R の camtrapdp の write_eml() に要る）"""
    p, ids = _project(tmp_path)
    try:
        _judge(p, ids)
        camtrapdp.import_stations(p, _stations_csv(tmp_path, "station,lat,lon\nSt01,35.2,140.1\nSt02,35.3,140.2\n"))
        out = str(tmp_path / "dp")

        def pkg_of():
            with open(os.path.join(out, "datapackage.json"), encoding="utf-8") as fh:
                return json.load(fh)
        res = camtrapdp.export_camtrapdp(p, out, {}, META)                      # 入れなければ、書かない
        assert res["valid"] and "description" not in pkg_of() and "licenses" not in pkg_of()
        res = camtrapdp.export_camtrapdp(p, out, {}, dict(META, description=" 房総のカメラトラップ調査。 ", license_data="CC-BY-4.0", license_media="CC0-1.0"))
        pkg = pkg_of()
        assert res["valid"], res["problems"]
        assert pkg["description"] == "房総のカメラトラップ調査。"
        assert [(x["name"], x["scope"]) for x in pkg["licenses"]] == [("CC-BY-4.0", "data"), ("CC0-1.0", "media")]
        assert all(x["path"].startswith("https://creativecommons.org/") and x["title"] for x in pkg["licenses"])
        i = camtrapdp.info(p)                                                   # 次のために覚えている
        assert (i["meta"]["description"], i["meta"]["license_data"], i["meta"]["license_media"]) == ("房総のカメラトラップ調査。", "CC-BY-4.0", "CC0-1.0")
        assert i["licenses"] == ["CC0-1.0", "CC-BY-4.0", "CC-BY-NC-4.0"]
        camtrapdp.export_camtrapdp(p, out, {}, dict(META, license_data="CC-BY-NC-4.0"))      # データのライセンスだけ
        pkg = pkg_of()
        assert [(x["name"], x["scope"]) for x in pkg["licenses"]] == [("CC-BY-NC-4.0", "data")] and "description" not in pkg
        with pytest.raises(ValueError):
            camtrapdp.export_camtrapdp(p, out, {}, dict(META, license_media="my-own-terms"))
        # 検査: 範囲（scope）のないライセンスと、名前の書式が違うライセンスを見つける
        pkg["licenses"] = [dict(name="CC-BY-4.0"), dict(name="bad name", scope="data"), dict(scope="media")]
        with open(os.path.join(out, "datapackage.json"), "w", encoding="utf-8") as fh:
            json.dump(pkg, fh)
        kinds = {(x["where"], x["kind"]) for x in camtrapdp.check_package(out)}
        assert {("datapackage.licenses.scope", "required"), ("datapackage.licenses.name", "pattern"), ("datapackage.licenses.name", "required")} <= kinds
    finally:
        p.close()
