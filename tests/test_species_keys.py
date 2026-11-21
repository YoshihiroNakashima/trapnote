"""種のショートカットキー（確認事項 154 回目）と、再生の下の欄の並び
- SpeciesList.set_keys: キーを付ける・外す・移す。英字は大文字に。固定項目のキーと、画面がほかの操作に使うキーは付けられない（そのときは、何も変えない）
- POST /api/species {keys}: 保存して、種リストを返す。付けられないキーは 400
- 画面の「付けられないキー」（static/species.js の SP_KEYS_RESERVED）が、サーバーの KEYS_RESERVED と同じ集まりで、
  判定の画面（static/app.js の onKey）が、種のキーより先に使っているキーであること
- 画面: 「開いたときの動き」の欄が「明るさの補正」の欄の左、「キー設定」のボタンが「＋ 登録」と「その他…」の間"""
from __future__ import annotations

import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from trapnote import species  # noqa: E402
from trapnote.species import SpeciesList  # noqa: E402


def _keys(sl) -> dict:
    return {r["code"]: r["key"] for r in sl.rows if r["key"]}


def _static(name: str) -> str:
    return open(os.path.join(ROOT, "static", name), encoding="utf-8").read()


def test_set_keys_assigns_clears_and_moves(tmp_path):
    sl = SpeciesList(str(tmp_path / "species.csv"))
    assert _keys(sl)["boar"] == "1" and _keys(sl)["deer"] == "2" and "fox" not in _keys(sl)      # 既定の一覧: イノシシ 1、ニホンジカ 2、アカギツネはキーなし
    assert sl.set_keys({"fox": "f", "raccoon": "R"}) == dict(changed=["fox", "raccoon"], moved=[])      # 英字は大文字に
    assert _keys(sl)["fox"] == "F" and _keys(sl)["raccoon"] == "R" and _keys(sl)["boar"] == "1"          # 渡さなかった種は、そのまま
    # 同じキーを別の種に付けると、前の種から外れる
    assert sl.set_keys({"serow": "1"}) == dict(changed=["boar", "serow"], moved=["boar"])
    assert "boar" not in _keys(sl) and _keys(sl)["serow"] == "1"
    # 「使う」でない種が持っていたキーも、同じように外れる
    assert not next(r for r in sl.rows if r["code"] == "goat")["enabled"]
    sl.set_keys({"goat": "9"})
    assert sl.set_keys({"deer": "9"}) == dict(changed=["deer", "goat"], moved=["goat"])
    assert _keys(sl)["deer"] == "9" and "goat" not in _keys(sl)
    # 2 つの種のキーを入れ替える（1 回で渡す）
    assert sl.set_keys({"deer": "1", "serow": "9"}) == dict(changed=["deer", "serow"], moved=[])
    # 外す。知らないコードは、何もしない。同じ値なら、変わらない
    assert sl.set_keys({"fox": "", "no_such": "Z"}) == dict(changed=["fox"], moved=[])
    assert "fox" not in _keys(sl) and "Z" not in _keys(sl).values()
    assert sl.set_keys({"raccoon": "r"}) == dict(changed=[], moved=[])
    # ほかの操作と分け合うキー（F・B・L・- +）と、記号は付けられる
    assert sl.set_keys({"fox": "b", "dog": "-", "bear": "@"}) == dict(changed=["fox", "dog", "bear"], moved=[])
    k = _keys(sl)
    assert (k["deer"], k["muntjac"], k["serow"], k["fox"], k["dog"], k["bear"], k["raccoon"]) == ("1", "3", "9", "B", "-", "@", "R") and "boar" not in k
    assert len(set(k.values())) == len(k)                # 同じキーを持つ種はない
    assert _keys(SpeciesList(sl.path)) == k              # ファイルに残っている


@pytest.mark.parametrize("keys, word", [
    ({"fox": " "}, "Space"), ({"fox": ","}, "「,」"), ({"fox": "."}, "「.」"), ({"fox": "<"}, "「<」"), ({"fox": ">"}, "「>」"), ({"fox": "["}, "「[」"), ({"fox": "]"}, "「]」"),
    ({"fox": "n"}, "動物なし"), ({"fox": "H"}, "「人」"), ({"fox": "v"}, "「車」"), ({"fox": "A"}, "有効範囲"), ({"fox": "u"}, "不明"),
    ({"fox": "x", "dog": "X"}, "同じキー"),
    ({"empty": "E"}, "固定項目"),
])
def test_set_keys_refuses_and_changes_nothing(tmp_path, keys, word):
    sl = SpeciesList(str(tmp_path / "species.csv"))
    before = _keys(sl)
    with pytest.raises(ValueError) as e:
        sl.set_keys(dict(keys, serow="S"))               # 一緒に渡した、付けられるキーも入れない
    assert word in str(e.value)
    assert _keys(sl) == before == _keys(SpeciesList(sl.path))


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey")
def test_species_keys_api(tmp_path, monkeypatch):
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    from trapnote import config
    from trapnote.server import Session, make_app
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    s = Session()
    s.settings.data.update(language="ja")                                         # 日本語の画面で作るプロジェクト: 既定の一覧から始まる
    monkeypatch.setattr(s.bg, "submit", lambda job, front=False: job)             # 走査のジョブは動かさない
    root = tmp_path / "Site"
    (root / "St01").mkdir(parents=True)

    async def run():
        async with TestClient(TestServer(make_app(s))) as c:
            post = lambda url, body: c.post(url, json=body)   # noqa: E731
            r = await post("/api/project/open", dict(path=str(root), create=True))
            assert r.status == 200, await r.text()
            r = await post("/api/species", dict(keys={"fox": "f", "serow": "1"}))
            assert r.status == 200, await r.text()
            d = await r.json()
            by = {x["code"]: x["key"] for x in d["species"]}
            assert by["fox"] == "F" and by["serow"] == "1" and by["boar"] == "" and by["deer"] == "2"
            assert [x["key"] for x in d["fixed"]] == ["N", "H", "V", "A", "U"]
            n = len(d["species"])
            r = await post("/api/species", dict(keys={"dog": "N"}))
            assert r.status == 400 and "動物なし" in (await r.json())["error"]
            r = await post("/api/species", dict(keys={"dog": "."}))
            assert r.status == 400
            d = await (await c.get("/api/species")).json()
            assert {x["code"]: x["key"] for x in d["species"]}["dog"] == "" and len(d["species"]) == n       # 断ったときは、何も変わらない。種の数も変わらない

    asyncio.run(run())


def test_reserved_keys_match_the_screen():
    """サーバーの KEYS_RESERVED = 画面の SP_KEYS_RESERVED。どれも、app.js の onKey が、種のキーを引く行より前で使っている"""
    js = _static("species.js")
    m = re.search(r"const SP_KEYS_RESERVED = \{([^}]*)\};", js)
    assert m
    screen = set(re.findall(r"'(.)':", m.group(1)))
    assert screen == set(species.KEYS_RESERVED) and len(screen) == 7
    app = _static("app.js")
    on_key = app[app.index("function onKey(e) {"):]
    lookup = on_key.index("speciesKeyMap()[k.toUpperCase()]")                 # 種のキーを引く行
    for ch in species.KEYS_RESERVED:
        assert f"k === '{ch}'" in on_key[:lookup], ch
    # 種と分け合うキー（F・B・L・- + =）は、種に付いていなければ、ほかの操作に使う（種が先）
    shared = re.search(r"const SP_KEYS_SHARED = \{([^}]*)\};", js)
    assert shared
    for ch in re.findall(r"'?([A-Z+=-])'?:", shared.group(1)):
        assert (f"!speciesKeyMap()['{ch}']" in on_key[:lookup]) or ("!speciesKeyMap()[k]" in on_key[:lookup] and f"k === '{ch}'" in on_key[:lookup]), ch
    assert not set(k["key"] for k in species.FIXED) & set("FBL")              # 固定項目のキーは、分け合うキーと重ならない


def test_screen_order_and_keys_button():
    html = _static("index.html")
    assert html.index('id="vbBoxMode"') < html.index('id="vbOpenMode"') < html.index('id="vbEnhance"') < html.index('id="vbEdit"')      # 開いたときの動き → 明るさの補正
    assert html.index('id="btnAddSpecies"') < html.index('id="btnSpKeys"') < html.index('id="btnOtherSpecies"')
    assert "$('#btnSpKeys').onclick" in _static("judge.js") and "function speciesKeysDialog()" in _static("species.js")
    i18n = _static("i18n.js")
    for key in ("sp_keys_btn", "sp_keys_tip", "kd_title", "kd_help", "kd_auto", "kd_auto_tip", "kd_auto_done", "kd_auto_none", "kd_clear", "kd_none", "kd_fixed", "kd_saved", "kd_used",
                "kd_msg_moved", "kd_msg_fixed", "kd_msg_reserved", "kd_msg_shared", "kd_w_play", "kd_w_frame", "kd_w_speed", "kd_w_fav", "kd_w_enh", "kd_w_loop"):
        assert i18n.count(f" {key}: '") == 2, key                             # 日本語と英語の両方にある
