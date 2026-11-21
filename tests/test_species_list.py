"""種リストと、種の登録（確認事項 149 回目）
- 種リストのはじめの中身: 既定の一覧（日本の哺乳類を中心にした 31 項目）か、空（画面が英語のときに作るプロジェクト。国も空）
- CSV からまとめて登録: ひな形を書き出す → 書き換える → 読み込む（読む前に、何が変わるかが分かる。見出しの違いで名前のない行を足さない）
- SpeciesNet が知っている種の一覧（trapnote/taxa.py）: 国ごとの候補、種リストの行が自動判定で付くかどうか、学名からの対応づけ
- 亜種まで書いたラベル（家畜のヤギなど）は、そのラベルだけに当てる
モデルのファイルは、小さな偽物を作って使う（本物は要らない）"""
from __future__ import annotations

import csv
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import detect, species, taxa  # noqa: E402
from trapnote.project import Project  # noqa: E402
from trapnote.species import SpeciesList  # noqa: E402

LABELS = [
    "u01;mammalia;artiodactyla;cervidae;cervus;nippon;sika deer",
    "u02;mammalia;artiodactyla;cervidae;muntiacus;reevesi;reeves' muntjac",
    "u03;mammalia;artiodactyla;cervidae;;;cervidae family",
    "u04;mammalia;artiodactyla;bovidae;capra;aegagrus;wild goat",
    "u05;mammalia;artiodactyla;bovidae;capra;aegagrus hircus;domestic goat",
    "u06;mammalia;artiodactyla;bovidae;capricornis;sumatraensis;sumatran serow",
    "u07;mammalia;artiodactyla;bovidae;capricornis;;capricornis species",
    "u08;mammalia;carnivora;canidae;vulpes;vulpes;red fox",
    "u09;mammalia;carnivora;felidae;panthera;tigris;tiger",
    "u10;mammalia;primates;hominidae;homo;sapiens;human",
    "u11;aves;galliformes;phasianidae;syrmaticus;soemmerringii;copper pheasant",
    "u12;aves;;;;;bird",
    "u13;;;;;;blank",
    "u14;;;;;;vehicle",
    "u15;mammalia;;;;;mammal",
]
GEOFENCE = {
    "mammalia;artiodactyla;cervidae;cervus;nippon": {"allow": {"JPN": [], "CHN": []}},
    "mammalia;artiodactyla;bovidae;capricornis;sumatraensis": {"allow": {"MYS": [], "IDN": []}},
    "mammalia;carnivora;felidae;panthera;tigris": {"allow": {"MYS": [], "IND": []}},
    "mammalia;carnivora;canidae;vulpes;vulpes": {"block": {"MYS": []}},
    "aves;galliformes;phasianidae;syrmaticus;soemmerringii": {"allow": {"JPN": []}},
}


@pytest.fixture()
def models(tmp_path):
    """モデルの置き場（kagglehub の形）に、ラベルの一覧と geofence だけを置く"""
    d = tmp_path / "models" / "kagglehub" / "models" / "google" / "speciesnet" / "pyTorch" / "v9" / "1"
    d.mkdir(parents=True)
    (d / "info.json").write_text(json.dumps(dict(version="9.9", classifier_labels="labels.txt", geofence="geofence.json")), encoding="utf-8")
    (d / "labels.txt").write_text("\n".join(LABELS) + "\n", encoding="utf-8")
    (d / "geofence.json").write_text(json.dumps(GEOFENCE), encoding="utf-8")
    return str(tmp_path / "models")


def _tx(models):
    t = taxa.load(models, "kaggle:google/speciesnet/pyTorch/v9/1")
    assert t is not None and t.source == "9.9"
    return t


# ---------- はじめの中身 ----------
def test_species_list_starts_default_or_empty(tmp_path):
    a = SpeciesList(str(tmp_path / "a" / "species.csv"))
    assert len(a.rows) == 31 and {"boar", "deer", "bird"} <= {r["code"] for r in a.rows}
    b = SpeciesList(str(tmp_path / "b" / "species.csv"), initial="empty", sync=False)
    assert b.rows == [] and os.path.isfile(b.path)
    assert SpeciesList(b.path, initial="empty", sync=False).rows == []          # 開き直しても空のまま（「鳥類」の行を足さない）
    assert [f["code"] for f in b.to_dict()["fixed"]] == ["empty", "human", "vehicle", "area", "unknown"]      # 固定の項目は、いつもある
    # 既定の一覧の 2 項目は、SpeciesNet v4 のラベルに合わせてある。前の値のリストは、開いたときに直る
    by = {r["code"]: r for r in a.rows}
    assert by["goat"]["speciesnet"] == "capra aegagrus hircus" and by["cetartiodactyla"]["speciesnet"] == "artiodactyla"
    by["goat"]["speciesnet"] = "capra hircus"
    species.save_csv(a.path, a.rows)
    assert {r["code"]: r for r in SpeciesList(a.path).rows}["goat"]["speciesnet"] == "capra aegagrus hircus"


def test_project_created_in_english_starts_empty(tmp_path):
    (tmp_path / "en" / "St01").mkdir(parents=True)
    (tmp_path / "ja" / "St01").mkdir(parents=True)
    p = Project(str(tmp_path / "en" / "TrapNote"), root=str(tmp_path / "en"), create=True, species_init="empty")
    try:
        assert p.species.rows == [] and p.setting("country") == "" and p.data["species_init"] == "empty"
        p.species.add(dict(name_en="Tiger", name_sci="Panthera tigris"))
    finally:
        p.close()
    p = Project(str(tmp_path / "en" / "TrapNote"))                               # 開き直す: 登録した種だけ。既定の一覧も「鳥類」も足されない。国も空のまま
    try:
        assert [r["code"] for r in p.species.rows] == ["tiger"] and p.setting("country") == ""
    finally:
        p.close()
    p = Project(str(tmp_path / "ja" / "TrapNote"), root=str(tmp_path / "ja"), create=True)      # 既定（日本語の画面）: 今までどおり
    try:
        assert len(p.species.rows) == 31 and p.setting("country") == "JPN" and "species_init" not in p.data
    finally:
        p.close()


# ---------- CSV からまとめて登録 ----------
def _rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.reader(fh)]


def test_template_roundtrip_and_import(tmp_path):
    sl = SpeciesList(str(tmp_path / "species.csv"))
    tpl = str(tmp_path / "out" / "template.csv")
    assert species.write_template(tpl, sl.rows, "ja") == dict(path=tpl, rows=31)
    table = _rows(tpl)
    notes = [r for r in table if r and r[0].startswith("#")]
    assert len(notes) == len(species.TEMPLATE_NOTES["ja"]) and table[len(notes)] == ["和名", "英名", "学名", "CSV表記", "キー", "使う", "code"]
    assert table[len(notes) + 1][:4] == ["イノシシ", "Wild Boar", "Sus scrofa", "Boar"] and table[len(notes) + 1][5:] == ["1", "boar"]
    # 書き出したままを読むと、変わるところはない
    d = species.read_import(tpl)
    assert len(d["rows"]) == 31 and d["unknown"] == [] and d["no_name"] == [] and set(d["heads"]) == {"name_ja", "name_en", "name_sci", "csv_label", "key", "enabled", "code"}
    assert {x["action"] for x in sl.plan_import(d["rows"])} == {"same"}
    # 書き換える: 新しい種 2 つ（1 つは学名なし）、もうある種の学名とキーを直す、空の行、名前のない行、固定の項目、同じ種を 2 回
    with open(tpl, "a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerows([["カワウソ", "Eurasian Otter", "Lutra lutra", "", "", "", ""], ["ヌートリア", "", "", "", "", "0", ""], ["", "", "", "", "", "", ""],
                     ["", "", "", "Nameless", "", "", ""], ["動物なし", "Empty", "", "", "", "", ""], ["カワウソ", "Eurasian Otter", "Lutra lutra", "", "", "", ""]])
    text = open(tpl, encoding="utf-8-sig").read().replace("ニホンジカ,Sika Deer,Cervus nippon,Deer,2,", "ニホンジカ,Sika Deer,Cervus nippon yesoensis,Deer,D,")
    open(tpl, "w", encoding="utf-8-sig", newline="").write(text)
    d = species.read_import(tpl)
    plan = sl.plan_import(d["rows"])
    acts = [x["action"] for x in plan]
    assert (acts.count("new"), acts.count("update"), acts.count("fixed"), acts.count("dup"), acts.count("same")) == (2, 1, 1, 1, 30) and len(d["no_name"]) == 1
    upd = next(x for x in plan if x["action"] == "update")
    assert upd["code"] == "deer" and upd["changes"] == ["name_sci", "key"]
    before = len(sl.rows)
    res = sl.apply_import(d["rows"])
    assert (res["new"], res["update"]) == (2, 1) and len(sl.rows) == before + 2
    by = {r["name_ja"]: r for r in sl.rows}
    assert by["カワウソ"]["enabled"] == 1 and by["カワウソ"]["speciesnet"] == "lutra lutra" and by["カワウソ"]["csv_label"] == "EurasianOtter" and by["カワウソ"]["code"] == "eurasian_otter"
    assert by["ヌートリア"]["enabled"] == 0 and by["ヌートリア"]["name_sci"] == ""                    # 「使う」に 0 と書いた種は、使わないで登録
    deer = by["ニホンジカ"]
    assert deer["name_sci"] == "Cervus nippon yesoensis" and deer["key"] == "D" and deer["csv_label"] == "Deer" and deer["code"] == "deer"
    assert deer["speciesnet"] == "cervus nippon"                                                    # 学名を直したので、自動判定の対応も付け直す（一覧がないときは、はじめの 2 語）
    assert {x["action"] for x in SpeciesList(sl.path).plan_import(species.read_import(tpl)["rows"])} <= {"same", "fixed", "dup"}      # もう一度読んでも、増えない


def test_import_reads_other_headers_and_refuses_unknown_ones(tmp_path):
    sl = SpeciesList(str(tmp_path / "species.csv"), initial="empty", sync=False)

    def f(name, text, enc="utf-8-sig"):
        p = tmp_path / name
        p.write_text(text, encoding=enc)
        return str(p)
    d = species.read_import(f("dwc.csv", "scientificName,vernacularName,Notes\nPanthera tigris,Tiger,x\nTapirus indicus,Malayan Tapir,\n"))
    assert [(r["name_sci"], r["name_en"]) for r in d["rows"]] == [("Panthera tigris", "Tiger"), ("Tapirus indicus", "Malayan Tapir")] and d["unknown"] == ["Notes"]
    d = species.read_import(f("sjis.csv", "和名（必須）,学名,CSV 表記\nトラ,Panthera tigris,Tora\n", enc="cp932"))                   # Excel で保存した Shift_JIS。かっこの中は見ない
    assert d["rows"] == [dict(name_ja="トラ", name_sci="Panthera tigris", csv_label="Tora", line=2)]
    d = species.read_import(f("en.csv", "# note\nname,scientific_name,use\nTiger,Panthera tigris,1\n# skipped\nSun bear,Helarctos malayanus,\n"))
    assert [(r["line"], r["name_en"]) for r in d["rows"]] == [(3, "Tiger"), (5, "Sun bear")]
    sl.apply_import(d["rows"])
    assert [(r["name_en"], r["enabled"], r["csv_label"]) for r in sl.rows] == [("Tiger", 1, "Tiger"), ("Sun bear", 1, "Sunbear")]
    for bad in ("種,メモ\nトラ,x\n", "a,b\n1,2\n"):                                                 # 名前の列がない: 読まない（前は、名前のない行を 1 つ足していた）
        with pytest.raises(ValueError):
            species.read_import(f("bad.csv", bad))
    with pytest.raises(ValueError):
        species.read_import(f("empty.csv", "# only a note\n"))
    assert len(sl.rows) == 2


def test_english_template(tmp_path):
    sl = SpeciesList(str(tmp_path / "species.csv"), initial="empty", sync=False)
    tpl = str(tmp_path / "t.csv")
    species.write_template(tpl, sl.rows, "en")
    table = _rows(tpl)
    assert table[-1] == ["name", "scientific_name", "csv_label", "key", "use", "code"] and all(r[0].startswith("#") for r in table[:-1])
    sl.add(dict(name_ja="トラ", name_en="Tiger", name_sci="Panthera tigris"))
    species.write_template(tpl, sl.rows, "en")                                    # 和名の入った種があれば、英語のひな形にも和名の列を残す
    assert _rows(tpl)[-2] == ["name", "name_ja", "scientific_name", "csv_label", "key", "use", "code"] and _rows(tpl)[-1][:3] == ["Tiger", "トラ", "Panthera tigris"]


# ---------- 登録と、名前の重なり ----------
def test_add_keeps_csv_labels_unique_and_reuses_same_species(tmp_path):
    sl = SpeciesList(str(tmp_path / "species.csv"))
    n = len(sl.rows)
    assert sl.add(dict(name_en="sika deer"))["code"] == "deer" and len(sl.rows) == n                  # 同じ英名（大文字小文字は見ない）: もうある種を返す
    r = sl.add(dict(name_en="White-tailed Deer", name_sci="Odocoileus virginianus", csv_label="Deer"))
    assert r["code"] == "deer" and len(sl.rows) == n                                                   # 同じ CSV 表記を名指しした: もうある種を返す（前と同じ）
    res = sl.apply_import([dict(name_en="Empty Quarter Gazelle", csv_label="Empty"), dict(name_en="Boar", name_sci="Sus barbatus")])
    by = {r["name_en"]: r for r in sl.rows}
    assert res["new"] == 2 and by["Empty Quarter Gazelle"]["csv_label"] == "Empty2" and by["Boar"]["csv_label"] == "Boar2" and by["Boar"]["code"] == "boar_2"   # CSV 表記もコードも重ねない
    assert sl.label("boar_2", "ja") == "Boar" and sl.label("boar", "en") == "Wild Boar"               # 和名がなければ英名を出す
    sl.add(dict(name_sci="Lutra lutra"))
    assert sl.label("lutra_lutra", "ja") == "Lutra lutra"                                             # 名前がなければ学名


def test_add_many_and_defaults(tmp_path, models):
    tx = _tx(models)
    sl = SpeciesList(str(tmp_path / "species.csv"), initial="empty", sync=False)
    res = sl.add_many([dict(name_en="Tiger", name_sci="Panthera tigris", rank="種", speciesnet="panthera tigris"), dict(name_en="Sumatran Serow", name_sci="Capricornis sumatraensis"), dict(name_en="")], tx)
    assert res == dict(added=2, existed=0) and [r["speciesnet"] for r in sl.rows] == ["panthera tigris", "capricornis sumatraensis"]
    sl.rows[0]["enabled"] = 0
    assert sl.add_many([dict(name_en="tiger")], tx) == dict(added=0, existed=1) and sl.rows[0]["enabled"] == 1      # もうある種は「使う」にするだけ
    assert sl.add_defaults() == dict(added=31) and sl.add_defaults() == dict(added=0) and len(sl.rows) == 33


# ---------- SpeciesNet が知っている種の一覧 ----------
def test_taxa_candidates_levels_and_suggestions(tmp_path, models):
    assert taxa.load(str(tmp_path / "nothing")) is None                           # モデルをまだ取ってきていない
    tx = _tx(models)
    assert [d["common"] for d in tx.rows if d["rank"] == "species"][:2] == ["sika deer", "reeves' muntjac"] and all(d["common"] not in ("human", "blank", "vehicle") for d in tx.rows)
    assert tx.countries() == ["CHN", "IDN", "IND", "JPN", "MYS"]
    names = lambda c, rank="species": [x["sci"] for x in tx.candidates(c) if x["rank"] == rank]   # noqa: E731
    assert names("JPN") == ["Syrmaticus soemmerringii", "Capra aegagrus", "Capra aegagrus hircus", "Cervus nippon", "Muntiacus reevesi", "Vulpes vulpes"]
    assert names("MYS") == ["Capra aegagrus", "Capra aegagrus hircus", "Capricornis sumatraensis", "Muntiacus reevesi", "Panthera tigris"]     # キツネは block、シカとヤマドリは allow の外
    assert len(names("")) == 8
    assert names("JPN", "genus") == [] and names("MYS", "genus") == ["Capricornis"]               # 属などの区分は、その国に残る種があるものだけ
    assert names("JPN", "class") == ["Aves", "Mammalia"] and names("JPN", "family") == ["Cervidae"]
    for lab in LABELS:                                                            # 国で除くかどうかは、自動判定（detect.should_geofence）と同じ決まり
        d = taxa.parse_label(lab)
        for c in ("JPN", "MYS", "USA", ""):
            assert tx.blocked(d, c) == (bool(c) and detect.should_geofence(lab, c, GEOFENCE)), (lab, c)
    assert [tx.level(v) for v in ("cervus nippon", "capricornis", "cervidae", "artiodactyla", "mammalia", "capra aegagrus hircus", "capra hircus", "lutra lutra", "", "Cervus nippon（シカ）")] == \
           ["species", "genus", "family", "order", "class", "species", "", "", "", "species"]
    assert tx.suggest("Cervus nippon") == ("cervus nippon", "species") and tx.suggest("Capricornis crispus") == ("capricornis", "genus")       # その種のラベルがなければ、同じ属
    assert tx.suggest("Capra aegagrus hircus") == ("capra aegagrus hircus", "species") and tx.suggest("Cervus nippon yesoensis") == ("cervus nippon", "species")
    assert tx.suggest("Lutra lutra") == ("lutra lutra", "") and tx.suggest("Cervidae") == ("cervidae", "family") and tx.suggest("") == ("", "")


def test_list_rows_get_ai_mapping_from_scientific_name(tmp_path, models):
    tx = _tx(models)
    sl = SpeciesList(str(tmp_path / "species.csv"), initial="empty", sync=False)
    a = sl.add(dict(name_ja="ニホンカモシカ", name_sci="Capricornis crispus"), tx)
    b = sl.add(dict(name_ja="カワウソ", name_sci="Lutra lutra"), tx)
    c = sl.add(dict(name_ja="ヌートリア"), tx)
    assert (a["speciesnet"], b["speciesnet"], c["speciesnet"]) == ("capricornis", "lutra lutra", "")
    assert sl.ai_status(tx) == {a["code"]: "genus", b["code"]: "none", c["code"]: "none"}
    assert sl.ai_status(None) == {a["code"]: "unknown", b["code"]: "unknown", c["code"]: "none"}      # 一覧をまだ読めない: 値のある行は「未確認」
    # 種リストの窓の表で学名を直す → 対応を付け直す。人が対応の列を書き換えた行は、そのまま。名前のない行は捨てる
    rows = [dict(r) for r in sl.rows]
    rows[1]["name_sci"] = "Vulpes vulpes"
    rows[0]["speciesnet"] = "capricornis sumatraensis"
    rows.append(dict(code="", name_ja="トラ", name_en="", name_sci="Panthera tigris", enabled=1))
    rows.append(dict(code="", name_ja="", name_en="", name_sci="", enabled=1))
    sl.update(rows, tx)
    by = {r["name_ja"]: r for r in sl.rows}
    assert len(sl.rows) == 4 and by["カワウソ"]["speciesnet"] == "vulpes vulpes" and by["ニホンカモシカ"]["speciesnet"] == "capricornis sumatraensis" and by["トラ"]["speciesnet"] == "panthera tigris"
    assert sl.ai_status(tx)[by["トラ"]["code"]] == "species"


def test_subspecies_label_maps_only_to_its_own_row():
    rows = [dict(code="goat", enabled=1, speciesnet="capra aegagrus hircus", rank="種"), dict(code="wild_goat", enabled=1, speciesnet="capra aegagrus", rank="種"),
            dict(code="deer", enabled=1, speciesnet="cervus nippon", rank="種"), dict(code="off", enabled=0, speciesnet="vulpes vulpes", rank="種")]
    codes = dict(zip([x.split(";")[-1] for x in LABELS], detect.build_label_map(LABELS, rows)))
    assert codes["domestic goat"] == "goat" and codes["wild goat"] == "wild_goat" and codes["sika deer"] == "deer"
    assert codes["red fox"] == "unknown" and codes["tiger"] == "unknown" and codes["blank"] == "empty" and codes["human"] == "human"      # 「使う」でない種と、リストにない種は「不明」


# ---------- API ----------
@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey")
def test_species_api(tmp_path, monkeypatch, models):
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    from trapnote import config
    from trapnote.server import Session, make_app
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    s = Session()
    s.settings.data.update(models_dir=models, language="en")
    monkeypatch.setattr(s.bg, "submit", lambda job, front=False: job)             # 走査のジョブは動かさない
    root = tmp_path / "Site"
    (root / "St01").mkdir(parents=True)
    tpl = str(tmp_path / "tpl.csv")

    async def run():
        async with TestClient(TestServer(make_app(s))) as c:
            post = lambda url, body: c.post(url, json=body)   # noqa: E731
            r = await post("/api/project/open", dict(path=str(root), create=True))
            assert r.status == 200, await r.text()
            assert s.project.species.rows == [] and s.project.setting("country") == ""      # 英語の画面で作ったプロジェクト: 種は空、国も空
            s.project.update_settings(dict(speciesnet_model="kaggle:google/speciesnet/pyTorch/v9/1"))
            st = await (await c.get("/api/species/status")).json()
            assert (st["available"], st["n_species"], st["ai"], st["country"], st["species_init"]) == (True, 0, [], "", "empty")
            # 候補: 国を渡すと、その国で除かれないものだけ
            d = await (await c.get("/api/species/candidates", params={"country": "MYS"})).json()
            assert d["available"] and d["country_known"] and [x["name"] for x in d["rows"] if x["rank"] == "species"][-1] == "Tiger"
            assert not (await (await c.get("/api/species/candidates", params={"country": "XXX"})).json())["country_known"]
            r = await (await post("/api/species/add_many", dict(values=["panthera tigris", "capricornis sumatraensis"]))).json()
            assert (r["added"], r["existed"]) == (2, 0) and [x["name_en"] for x in r["species"]] == ["Sumatran Serow", "Tiger"]
            d = await (await c.get("/api/species/candidates", params={"country": "MYS"})).json()
            assert {x["sci"]: x["have"] for x in d["rows"] if x["have"]} == {"Capricornis sumatraensis": "sumatran_serow", "Panthera tigris": "tiger"}
            # 1 種ずつ: 学名から対応が決まる。学名を打ったときの問い合わせ
            assert await (await c.get("/api/species/suggest", params={"sci": "Capricornis crispus"})).json() == dict(available=True, value="capricornis", level="genus")
            r = await (await post("/api/species/add", dict(name_en="Otter", name_sci="Lutra lutra"))).json()
            assert r["added"]["speciesnet"] == "lutra lutra"
            st = await (await c.get("/api/species/status")).json()
            assert st["ai"] == ["sumatran_serow", "tiger"] and st["no_ai"] == ["otter"] and st["status"]["otter"] == "none"
            # CSV: ひな形 → 書き足す → 何が変わるか → 登録
            r = await (await post("/api/species/template", dict(path=tpl))).json()
            assert r["rows"] == 3 and open(tpl, encoding="utf-8-sig").read().splitlines()[len(species.TEMPLATE_NOTES["en"])] == "name,scientific_name,csv_label,key,use,code"
            with open(tpl, "a", encoding="utf-8-sig", newline="") as fh:
                fh.write("Red Fox,Vulpes vulpes,,,,\n")
            pv = await (await post("/api/species/import/preview", dict(path=tpl))).json()
            assert (pv["new"], pv["update"], pv["same"]) == (1, 0, 3) and len(s.project.species.rows) == 3      # 見るだけでは変わらない
            r = await (await post("/api/species/import", dict(path=tpl))).json()
            assert (r["new"], r["update"]) == (1, 0) and len(r["species"]) == 4
            r = await post("/api/species/import/preview", dict(path=str(tmp_path / "none.csv")))
            assert r.status == 400
            # 既定の一覧を足す
            r = await (await post("/api/species/add_many", dict(defaults=True))).json()
            assert r["added"] == 30 and len(r["species"]) == 34                  # アカギツネは、もうある（同じ学名）

    asyncio.run(run())


def test_species_model_job_loads_the_classifier(tmp_path):
    """種リストの窓の「いまモデルを取ってくる」: 自動判定と同じ取り方で、プロジェクトの設定の SpeciesNet を読み込む（ここでは読み込みを偽物に置き換える）"""
    from trapnote.jobs import SpeciesModelJob
    (tmp_path / "s").mkdir()
    p = Project(str(tmp_path / "s" / "TrapNote"), root=str(tmp_path / "s"), create=True)
    calls = []

    class Hub:
        def load_classifier(self, name, progress=None, stop=None):
            calls.append(name)
            progress("loading")

    class Ctx:
        project = p

        def model_hub(self):
            return Hub()
    try:
        job = SpeciesModelJob()
        job.run(Ctx())
        assert calls == [p.setting("speciesnet_model")] and (job.done, job.total, job.current) == (1, 1, "loading")
    finally:
        p.close()
