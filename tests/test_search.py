"""高度な検索（別窓）の条件 adv（search.py）: SQL への直し方、組み合わせの決まり（項目どうしは AND、項目の中は OR）、読めない条件の扱い、
保存した検索、別窓の API。

ここで見ること:
- 地点・調査回: 複数選択（どれか）と「選んだものを除く」。"" は「地点なし」
- 種: 選んだものを除く（人の判定 / AI の判定）、全部含む
- 時刻: ふつうの範囲と日をまたぐ範囲（18:00〜06:00）。修正後の時刻があればそれで比べる
- 動画の長さ、AI の信頼度（% に丸めて比べる。AI の判定で種を選んでいればその種の信頼度）
- 記録した項目: 個体数の範囲（種の行ごと。選んだ種の行で比べる。値のない行は既定値）、選択肢、複数選択、チェック、文字、未記入、＋で増やす項目、動画ごとの項目
- 読めない条件はどれにも当てはまらない（0=1）。一覧・件数・id・一括確定・書き出しが同じ条件で動く
- 保存した検索（searches.json）と API（/search、/api/search/count、/api/search/saved、長い URL）"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import judge, scan, search  # noqa: E402
from trapnote.judge import build_where, save as save_judgement  # noqa: E402
from trapnote.project import Project  # noqa: E402


def _project(tmp_path, stations=("BSN0001", "BSN0002", "BSN0003"), per_station=2, loose=0):
    """地点ごとに per_station 本、ルート直下（地点なし）に loose 本"""
    root = tmp_path / "N240128"
    for st in stations:
        (root / st).mkdir(parents=True)
        for i in range(per_station):
            (root / st / f"IMG_{i:04d}.AVI").write_bytes(b"")
    for i in range(loose):
        (root / f"LOOSE_{i:04d}.AVI").write_bytes(b"")
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    return p


def _ids(p, where="1=1"):
    return [r[0] for r in p.db.q(f"SELECT id FROM items WHERE {where} ORDER BY rel_path")]


def _adv(**kw) -> str:
    return json.dumps(dict(v=1, **kw), ensure_ascii=False)


def _q(p, f=None, **adv):
    f = dict(f or {})
    if adv:
        f["adv"] = _adv(**adv)
    w, prm = build_where(f)
    return [r[0] for r in p.db.q(f"SELECT id FROM items WHERE {w} ORDER BY rel_path", prm)]


def _ai(p, iid, *pairs, status=None):
    """AI の判定（auto_json）を付ける。pairs = (種, 信頼度)…"""
    auto = json.dumps(dict(model="m", species=[dict(species=s, confidence=c) for s, c in pairs]))
    p.db.x("UPDATE items SET auto_json=?, det_state=1 WHERE id=?", (auto, iid))
    if status:
        p.db.x("UPDATE items SET status=? WHERE id=?", (status, iid))


# ---------- 地点・調査回 ----------
def test_station_and_season_pick(tmp_path):
    p = _project(tmp_path, loose=1)
    try:
        a, b, c = (_ids(p, f"station='{s}'") for s in ("BSN0001", "BSN0002", "BSN0003"))
        none = _ids(p, "station IS NULL")
        assert len(a) == len(b) == len(c) == 2 and len(none) == 1
        every = _ids(p)
        assert _q(p, station={"in": ["BSN0001", "BSN0003"]}) == sorted(a + c)             # どれか
        assert _q(p, station={"in": ["BSN0001", "BSN0003"], "not": True}) == sorted(b + none, key=every.index)    # 選んだものを除く（地点なしは残る）
        assert _q(p, station={"in": [""]}) == none                                         # 地点なし
        assert _q(p, station={"in": ["", "BSN0002"]}) == sorted(b + none, key=every.index)
        assert _q(p, station={"in": [""], "not": True}) == sorted(a + b + c)
        assert _q(p, station={"in": ["", "BSN0002"], "not": True}) == sorted(a + c)
        assert _q(p, station={"in": ["nowhere"]}) == []
        # 簡易の絞り込み（地点 1 つ）とは AND
        assert _q(p, {"station": "BSN0001"}, station={"in": ["BSN0001", "BSN0002"]}) == a
        assert _q(p, {"station": "BSN0003"}, station={"in": ["BSN0001", "BSN0002"]}) == []
        # 調査回
        p.db.x("UPDATE items SET season='S2' WHERE station='BSN0002'")
        p.db.x("UPDATE items SET season='S3' WHERE station='BSN0003'")
        assert _q(p, season={"in": ["S2", "S3"]}) == sorted(b + c)
        assert _q(p, season={"in": ["S2"], "not": True}) == sorted(a + c + none, key=every.index)
        assert _q(p, season={"in": ["S2", "S3"]}, station={"in": ["BSN0003"], "not": True}) == b     # 項目どうしは AND
    finally:
        p.close()


# ---------- 種 ----------
def test_species_exclude_and_all(tmp_path):
    p = _project(tmp_path, stations=("BSN0001",), per_station=5)
    try:
        ids = _ids(p)
        save_judgement(p.db, ids[0], dict(species=["deer"]), "me")
        save_judgement(p.db, ids[1], dict(species=["deer", "boar"]), "me")
        save_judgement(p.db, ids[2], dict(species=["human"]), "me")
        _ai(p, ids[3], ("deer", 0.8), ("boar", 0.6), status="auto")           # AI だけ（人の判定なし）
        with p.db.tx() as c:
            for sp in ("deer", "boar"):
                c.execute("INSERT INTO judgements(item_id, species, origin, created_at) VALUES(?,?,?,?)", (ids[3], sp, "auto", "2026-11-13T00:00:00"))
        _ai(p, ids[0], ("boar", 0.9))                                           # 人は deer、AI は boar
        # 人の判定で「選んだものを除く」: 判定のない動画・AI だけの動画も残る
        assert _q(p, species={"in": ["deer"], "not": True}) == [ids[2], ids[3], ids[4]]
        assert _q(p, species={"in": ["deer", "human"], "not": True}) == [ids[3], ids[4]]
        assert _q(p, species={"in": ["boar"], "not": True}) == [ids[0], ids[2], ids[3], ids[4]]
        # AI の判定で「選んだものを除く」: AI の判定がない動画も残る
        assert _q(p, species={"in": ["boar"], "not": True, "src": "ai"}) == [ids[1], ids[2], ids[4]]
        assert _q(p, species={"in": ["deer"], "not": True, "src": "ai"}) == [ids[0], ids[1], ids[2], ids[4]]
        # 含む向き（別窓は簡易の鍵に写すが、adv でも同じ意味になる）
        assert _q(p, species={"in": ["deer", "boar"]}) == [ids[0], ids[1]]
        assert _q(p, species={"in": ["deer", "boar"], "all": True}) == [ids[1]]
        assert _q(p, species={"in": ["deer", "boar"], "all": True, "src": "ai"}) == [ids[3]]
        assert _q(p, species={"in": ["deer", "boar"], "src": "ai"}) == [ids[0], ids[3]]
        # 簡易の種の絞り込みと合わせる: deer が付いていて human は付いていない
        assert _q(p, {"species": "deer"}, species={"in": ["boar"], "not": True}) == [ids[0]]
    finally:
        p.close()


# ---------- 時刻・長さ ----------
def test_time_of_day_and_duration(tmp_path):
    p = _project(tmp_path, stations=("BSN0001",), per_station=7)
    try:
        ids = _ids(p)
        times = ["2024/01/28 05:59:59", "2024/01/28 06:00:30", "2024/01/28 12:00:00", "2024/01/28 17:59:00", "2024/01/28 18:00:00", "2024/01/29 23:30:00", None]
        for iid, t in zip(ids, times):
            p.db.x("UPDATE items SET datetime=? WHERE id=?", (t, iid))
        assert _q(p, time={"from": "06:00", "to": "17:59"}) == [ids[1], ids[2], ids[3]]
        assert _q(p, time={"from": "18:00", "to": "06:00"}) == [ids[0], ids[1], ids[4], ids[5]]        # 日をまたぐ。終わりの分（06:00 台）は入る
        assert _q(p, time={"from": "18:00", "to": "05:59"}) == [ids[0], ids[4], ids[5]]
        assert _q(p, time={"from": "18:00"}) == [ids[4], ids[5]]
        assert _q(p, time={"to": "06:00"}) == [ids[0], ids[1]]
        assert _q(p, time={"from": "6:00", "to": "06:00"}) == [ids[1]]                                   # 1 桁の時も読む
        # 修正後の時刻があればそれで比べる（12:00 → 20:00）
        p.db.x("UPDATE items SET datetime_corrected='2024/01/28 20:00:00' WHERE id=?", (ids[2],))
        assert _q(p, time={"from": "18:00", "to": "06:00"}) == [ids[0], ids[1], ids[2], ids[4], ids[5]]
        # 日付だけの日時（時刻がない）は、時刻の条件に当てはまらない
        p.db.x("UPDATE items SET datetime='2024/01/28' WHERE id=?", (ids[6],))
        assert ids[6] not in _q(p, time={"to": "06:00"})
        # 簡易の日付の絞り込みと合わせる
        assert _q(p, {"date_from": "2024-01-29"}, time={"from": "18:00", "to": "06:00"}) == [ids[5]]
        # 動画の長さ（秒）。長さが分からない動画は当てはまらない
        for iid, d in zip(ids, (3.0, 10.0, 10.5, 30.0, 60.0, 61.0, None)):
            p.db.x("UPDATE items SET duration=? WHERE id=?", (d, iid))
        assert _q(p, duration={"min": 10, "max": 60}) == ids[1:5]
        assert _q(p, duration={"min": 60}) == [ids[4], ids[5]]
        assert _q(p, duration={"max": 10}) == [ids[0], ids[1]]
        assert _q(p, duration={"min": 20, "max": 5}) == []                                               # 逆の範囲は 0 件（エラーにはしない）
        assert _q(p, duration={"min": 10, "max": 60}, time={"from": "18:00", "to": "06:00"}) == [ids[1], ids[2], ids[4]]
    finally:
        p.close()


# ---------- 枠の数の範囲 ----------
def test_boxes_range_is_detected_only(tmp_path):
    """adv の枠の数の範囲は検出済みの動画だけ。簡易の鍵 boxes の範囲（0-2）は、まだ検出していない動画（n_boxes = 0）も拾うので、
    別窓は 0 から始まる範囲を adv に入れる"""
    p = _project(tmp_path, stations=("BSN0001",), per_station=5)
    try:
        ids = _ids(p)
        # 0: 未検出 / 1: 検出済みで枠 0 / 2: 枠 1 / 3: 枠 2 / 4: 枠 5
        for iid, (det, n) in zip(ids, ((0, 0), (1, 0), (1, 1), (1, 2), (1, 5))):
            p.db.x("UPDATE items SET det_state=?, n_boxes=? WHERE id=?", (det, n, iid))
        assert _q(p, boxes={"min": 0, "max": 2}) == ids[1:4]
        assert _q(p, boxes={"min": 0}) == ids[1:]
        assert _q(p, boxes={"max": 1}) == ids[1:3]
        assert _q(p, boxes={"min": 2, "max": 5}) == ids[3:]
        assert _q(p, {"boxes": "0-2"}) == ids[0:4]            # 簡易の鍵の範囲: 未検出も入る
        assert _q(p, {"boxes": "0"}) == [ids[1]]              # 簡易の鍵の 0: 検出済みで枠なし
        assert _q(p, {"boxes": "1+"}) == ids[2:]
        assert _q(p, {"det": "done"}, boxes={"max": 0}) == [ids[1]]
    finally:
        p.close()


# ---------- AI の信頼度 ----------
def test_ai_confidence(tmp_path):
    p = _project(tmp_path, stations=("BSN0001",), per_station=5)
    try:
        ids = _ids(p)
        _ai(p, ids[0], ("deer", 0.95))
        _ai(p, ids[1], ("deer", 0.596), ("boar", 0.9))
        _ai(p, ids[2], ("boar", 0.30))
        _ai(p, ids[3])                                # AI が種を決められなかった（種なし）
        assert _q(p, conf={"min": 90}) == [ids[0], ids[1]]
        assert _q(p, conf={"max": 60}) == [ids[1], ids[2]]
        assert _q(p, conf={"min": 60, "max": 60}) == [ids[1]]                    # 0.596 は画面で 60% と出るので 60 に当たる
        assert _q(p, conf={"min": 0}) == [ids[0], ids[1], ids[2]]                # 種のない AI の判定・AI の判定のない動画は当てはまらない
        # AI の判定で種を選んでいれば、その種の信頼度
        assert _q(p, {"species": "deer", "species_src": "ai"}, conf={"min": 90}) == [ids[0]]
        assert _q(p, {"species": "deer,boar", "species_src": "ai"}, conf={"min": 90}) == [ids[0], ids[1]]
        assert _q(p, {"species": "deer", "species_src": "ai"}, conf={"max": 60}) == [ids[1]]
        # 人の判定で種を選んでいるときは、AI のどの種の信頼度でもよい
        save_judgement(p.db, ids[1], dict(species=["deer"]), "me")
        assert _q(p, {"species": "deer"}, conf={"min": 90}) == [ids[1]]
    finally:
        p.close()


# ---------- 記録した項目（種の行ごと） ----------
def test_fields_per_species_row(tmp_path):
    p = _project(tmp_path, stations=("BSN0001",), per_station=8)
    try:
        ids = _ids(p)
        cnt = lambda **kw: dict(key="count", scope="species", **kw)
        save_judgement(p.db, ids[0], dict(species=["deer"], species_fields={"deer": {"count": ["1"]}}), "me")
        save_judgement(p.db, ids[1], dict(species=["deer"], species_fields={"deer": {"count": ["3"], "sex": ["オス"]}}), "me")
        save_judgement(p.db, ids[2], dict(species=["deer", "boar"], species_fields={"deer": {"count": ["1"], "sex": ["メス"]}, "boar": {"count": ["4"], "sex": ["オス"]}}), "me")
        save_judgement(p.db, ids[3], dict(species=["deer"]), "me")                         # 個体数を入れずに確定（画面には既定の 1 が出る）
        save_judgement(p.db, ids[4], dict(species=["empty"]), "me")                        # 動物なし（項目の欄がない）
        _ai(p, ids[5], ("deer", 0.9), status="auto")                                       # AI だけ（人の記録ではない）
        with p.db.tx() as c:
            c.execute("INSERT INTO judgements(item_id, species, origin, created_at) VALUES(?,?,?,?)", (ids[5], "deer", "auto", "2026-11-13T00:00:00"))
        save_judgement(p.db, ids[6], dict(species=["boar"], species_fields={"boar": {"count": ["10"]}}), "me")
        # ids[7] は判定なし
        assert _q(p, fields=[cnt(min=3)]) == [ids[1], ids[2], ids[6]]
        assert _q(p, fields=[cnt(min=2, max=5)]) == [ids[1], ids[2]]
        assert _q(p, fields=[cnt(max=1)]) == [ids[0], ids[2]]                              # 既定値を知らせなければ、値の入っている行だけ
        assert _q(p, fields=[cnt(max=1, dflt="1")]) == [ids[0], ids[2], ids[3]]            # 値のない行は既定の 1 として比べる（AI だけ・動物なし・判定なしは入らない）
        assert _q(p, fields=[cnt(min=2, dflt="1")]) == [ids[1], ids[2], ids[6]]            # 既定値が範囲の外なら、値のない行は入らない
        assert _q(p, fields=[cnt(blank=True)]) == [ids[3]]                                 # 未記入
        assert _q(p, fields=[cnt(blank=True, dflt="1")]) == []                             # 既定値のある項目に未記入はない
        assert _q(p, fields=[cnt(min=3, blank=True)]) == [ids[1], ids[2], ids[3], ids[6]]  # 項目の中は OR
        assert _q(p, fields=[cnt(filled=True)]) == [ids[0], ids[1], ids[2], ids[6]]
        # 種を選んでいるとき（人の判定・含む向き）は、選んだ種の行で比べる: 「シカが 3 頭以上」に、シカ 1 頭 + イノシシ 4 頭の動画は入らない
        assert _q(p, {"species": "deer"}, fields=[cnt(min=3)]) == [ids[1]]
        assert _q(p, {"species": "boar"}, fields=[cnt(min=3)]) == [ids[2], ids[6]]
        assert _q(p, {"species": "deer,boar"}, fields=[cnt(min=3)]) == [ids[1], ids[2], ids[6]]
        assert _q(p, {"species": "deer+boar"}, fields=[cnt(min=3)]) == [ids[2]]            # 両方が付いている動画のうち、どちらかの行が 3 以上
        assert _q(p, {"species": "any"}, fields=[cnt(min=3)]) == [ids[1], ids[2], ids[6]]  # 種を名指ししていなければ、どの種の行でもよい
        assert _q(p, {"species": "deer", "species_src": "ai"}, fields=[cnt(min=3)]) == []  # AI の判定で選んだ種には当てない（AI が deer と見た動画に人の記録がない）
        # 種の行ごとの項目が 2 つ: 同じ 1 行が両方を満たす（オスが 3 頭以上。シカのメス 1 + イノシシのオス 4 は、イノシシの行で当たる）
        sex = lambda *v, **kw: dict(key="sex", scope="species", **({"in": list(v)} if v else {}), **kw)
        assert _q(p, fields=[cnt(min=3), sex("オス")]) == [ids[1], ids[2]]
        assert _q(p, fields=[cnt(max=1), sex("オス")]) == []                               # シカの行は 1 頭でメス、イノシシの行は 4 頭でオス → どの行も両方は満たさない
        assert _q(p, {"species": "deer"}, fields=[cnt(min=3), sex("オス")]) == [ids[1]]
        assert _q(p, fields=[sex("オス", "メス")]) == [ids[1], ids[2]]
        assert _q(p, fields=[sex(blank=True)]) == [ids[0], ids[3], ids[6]]                 # 性別が未記入の行がある（動物なしの行は数えない）
        assert _q(p, fields=[sex("メス", blank=True)]) == [ids[0], ids[2], ids[3], ids[6]]
        # 数として読めない値は範囲に当てはまらない
        with p.db.tx() as c:
            jid = c.execute("SELECT id FROM judgements WHERE item_id=?", (ids[3],)).fetchone()[0]
            c.execute("INSERT INTO field_values(item_id, judgement_id, field_key, seq, value) VALUES(?,?,?,?,?)", (ids[3], jid, "count", 0, "たくさん"))
        assert _q(p, fields=[cnt(max=1)]) == [ids[0], ids[2]]
        assert _q(p, fields=[cnt(min=0)]) == [ids[0], ids[1], ids[2], ids[6]]
    finally:
        p.close()


# ---------- 記録した項目（選択肢・複数選択・チェック・文字・＋で増やす項目・動画ごと） ----------
def test_fields_types_and_item_scope(tmp_path):
    p = _project(tmp_path, stations=("BSN0001",), per_station=6)
    try:
        ids = _ids(p)
        fd = lambda key, scope="species", **kw: dict(key=key, scope=scope, **kw)
        # 0: シカ、行動 2 件（＋で増やす項目）、幼獣あり、動画ごとのメモ・天気（複数選択）
        save_judgement(p.db, ids[0], dict(species=["deer"], species_fields={"deer": {"behaviour": ["移動", "採食"], "behaviour_start": ["1.5", "12"], "juvenile": ["1"]}},
                                          fields={"memo": ["足跡が 100% 残る"], "weather": ["雪;くもり"]}), "me")
        save_judgement(p.db, ids[1], dict(species=["deer"], species_fields={"deer": {"behaviour": ["休息"]}}, fields={"memo": ["Deer_at night"], "weather": ["晴れ"]}), "me")
        save_judgement(p.db, ids[2], dict(species=["boar"], fields={"weather": ["雪"]}), "me")
        save_judgement(p.db, ids[3], dict(species=["empty"], fields={"memo": ["風で草が揺れた"]}), "me")
        # 4: 判定なしだが動画ごとの項目だけ入っている（Timelapse から読んだ場合など）/ 5: 何もない
        p.db.x("INSERT INTO field_values(item_id, judgement_id, field_key, seq, value) VALUES(?,?,?,?,?)", (ids[4], None, "memo", 0, "あとで見る"))
        # 選択肢（＋で増やす項目は、どれかの記録が当たればよい）
        assert _q(p, fields=[fd("behaviour", **{"in": ["採食"]})]) == [ids[0]]
        assert _q(p, fields=[fd("behaviour", **{"in": ["採食", "休息"]})]) == [ids[0], ids[1]]
        assert _q(p, fields=[fd("behaviour", blank=True)]) == [ids[2]]                      # 行動の記録が 1 件もない種の行（動物なしは数えない）
        assert _q(p, fields=[fd("behaviour_start", min=10)]) == [ids[0]]                    # 動画の中の秒（time の項目）も範囲
        assert _q(p, fields=[fd("behaviour_start", max=1)]) == []
        # チェック
        assert _q(p, fields=[fd("juvenile", check=True)]) == [ids[0]]
        assert _q(p, fields=[fd("juvenile", check=False)]) == [ids[1], ids[2]]              # 印のない種の行（動物なし・判定なしは入らない）
        assert _q(p, fields=[fd("juvenile", check=False, dflt="1")]) == []                  # 既定が「あり」の項目: 値のない行は「あり」
        assert _q(p, fields=[fd("juvenile", check=True, dflt="1")]) == [ids[0], ids[1], ids[2]]
        # 動画ごとの項目: 文字（% _ は文字として探す。英字は大文字と小文字を区別しない）
        memo = lambda **kw: fd("memo", "item", **kw)
        assert _q(p, fields=[memo(text="足跡")]) == [ids[0]]
        assert _q(p, fields=[memo(text="100%")]) == [ids[0]]
        assert _q(p, fields=[memo(text="%")]) == [ids[0]]
        assert _q(p, fields=[memo(text="deer_at")]) == [ids[1]]
        assert _q(p, fields=[memo(text="_")]) == [ids[1]]
        assert _q(p, fields=[memo(filled=True)]) == [ids[0], ids[1], ids[3], ids[4]]
        assert _q(p, fields=[memo(blank=True)]) == [ids[2], ids[5]]
        assert _q(p, fields=[memo(text="足跡", blank=True)]) == [ids[0], ids[2], ids[5]]     # 項目の中は OR
        assert _q(p, fields=[memo(text="草", dflt="草むら")]) == [ids[2], ids[3], ids[5]]    # 既定値が当たるなら、値のない動画も当たり（CSV の書き出しと同じ扱い）
        # 複数選択（"雪;くもり" のように入っている。選んだもののどれかを含む）
        wx = lambda *v, **kw: fd("weather", "item", multi=True, **({"in": list(v)} if v else {}), **kw)
        assert _q(p, fields=[wx("雪")]) == [ids[0], ids[2]]
        assert _q(p, fields=[wx("くもり", "晴れ")]) == [ids[0], ids[1]]
        assert _q(p, fields=[wx("くも")]) == []                                             # 途中までの一致は当たらない
        assert _q(p, fields=[wx("晴れ", blank=True)]) == [ids[1], ids[3], ids[4], ids[5]]
        # 種の行ごとの項目と動画ごとの項目は AND
        assert _q(p, fields=[fd("behaviour", **{"in": ["移動", "休息"]}), wx("雪")]) == [ids[0]]
        assert _q(p, {"status": "verified"}, fields=[memo(filled=True)]) == [ids[0], ids[1], ids[3]]
        # 人が足した項目（fields.json にあとから足した key）も、key を指定すれば同じに探せる
        save_judgement(p.db, ids[5], dict(species=["deer"], species_fields={"deer": {"antler": ["4"]}}, fields={"snow_cm": ["35"]}), "me")
        assert _q(p, fields=[fd("antler", min=3)]) == [ids[5]]
        assert _q(p, fields=[fd("snow_cm", "item", min=30, max=40)]) == [ids[5]]
        assert _q(p, fields=[fd("antler", min=3), fd("snow_cm", "item", max=10)]) == []
    finally:
        p.close()


# ---------- 読めない条件 ----------
BAD = [
    "{bad json", "[1, 2]", '"text"', {"v": 2}, {"nope": 1}, {"station": {"in": []}}, {"station": {"in": "BSN0001"}}, {"station": {"in": [1]}},
    {"station": {"in": ["a"], "not": "yes"}}, {"station": {"in": ["a"], "extra": 1}}, {"season": ["a"]},
    {"species": {"in": [""]}}, {"species": {"in": ["deer"], "src": "robot"}},
    {"time": {}}, {"time": {"from": "25:00"}}, {"time": {"from": "18:60"}}, {"time": {"from": 18}}, {"time": {"from": "18:00:00"}},
    {"duration": {}}, {"duration": {"min": "abc"}}, {"duration": {"min": True}}, {"duration": [1, 2]}, {"conf": {"min": float("nan")}}, {"boxes": {}}, {"boxes": "2+"},
    {"fields": {}}, {"fields": [{"key": "count", "scope": "species"}]}, {"fields": [{"key": "", "scope": "species", "min": 1}]},
    {"fields": [{"key": "count", "scope": "row", "min": 1}]}, {"fields": [{"key": "count", "scope": "species", "in": []}]},
    {"fields": [{"key": "count", "scope": "species", "check": "yes"}]}, {"fields": [{"key": "count", "scope": "species", "min": 1, "sql": "1=1"}]},
    {"fields": [{"key": "count", "scope": "species", "text": 5}]},
]


@pytest.mark.parametrize("bad", BAD)
def test_unreadable_matches_nothing(tmp_path, bad):
    """読めない条件は ValueError（別窓の件数の API は 400 で返す）。一覧などは「どれにも当てはまらない」になる（条件が抜けて全部に当たる、にはしない）"""
    with pytest.raises(ValueError):
        search.parse(bad)
    p = _project(tmp_path, stations=("BSN0001",), per_station=2)
    try:
        f = {"adv": bad if isinstance(bad, str) else json.dumps(bad)}
        assert _q(p, f) == []
        if isinstance(bad, dict):
            assert _q(p, {"adv": bad}) == []
        assert judge.count_items(p.db, f) == 0
    finally:
        p.close()


def test_parse_accepts_string_and_dict():
    assert search.parse(None) == {} and search.parse("") == {} and search.parse({}) == {} and search.parse("{}") == {}
    d = {"v": 1, "station": {"in": ["a", "a", "b"], "not": True}, "time": {"from": "6:05", "to": ""}, "conf": {"min": "50", "max": None},
         "fields": [{"key": "count", "scope": "species", "min": "2", "max": "", "dflt": "1"}]}
    for src in (d, json.dumps(d)):
        a = search.parse(src)
        assert a["station"] == dict(vals=["a", "b"], neg=True)
        assert a["time"] == ("06:05", None)
        assert a["conf"] == (50.0, None)
        assert a["fields"][0]["lo"] == 2.0 and a["fields"][0]["hi"] is None and a["fields"][0]["dflt"] == "1"
    assert search.where({}) == ([], []) and search.where({"adv": ""}) == ([], [])
    assert search.where({"adv": "{}"}) == ([], [])                     # 条件のない adv は何も足さない
    assert search.where({"adv": "{bad"}) == (["0=1"], [])


# ---------- 一覧・件数・id・一括確定・書き出しが同じ条件で動く ----------
def test_adv_goes_everywhere_filters_go(tmp_path):
    from trapnote import export as export_mod
    from trapnote.server import FILTER_KEYS, _filters
    assert "adv" in FILTER_KEYS
    p = _project(tmp_path)
    try:
        a = _ids(p, "station='BSN0001'")
        b = _ids(p, "station='BSN0002'")
        adv = _adv(station={"in": ["BSN0001", "BSN0002"]})
        f = _filters({"adv": adv, "status": "all", "station": ""})            # 画面から来た形（空・all は落ちる）
        assert f == {"adv": adv}
        assert _filters({"adv": json.loads(adv)}) == {"adv": json.loads(adv)}  # POST の本文では dict でもよい
        assert _filters({"adv": ""}) == {}
        assert judge.count_items(p.db, f) == 4
        assert [r["id"] for r in judge.list_items(p.db, f, "path", False, 0, 100)] == a + b
        assert judge.list_ids(p.db, f, "path", False) == a + b
        assert judge.index_of(p.db, f, "path", False, b[0]) == 2
        assert judge.neighbor(p.db, f, "path", False, a[1], 1)["id"] == b[0]
        assert judge.neighbor(p.db, f, "path", False, b[1], 1) is None           # 3 つ目の地点へは進まない
        assert [it["id"] for it, _, _, _ in export_mod._iter_items(p.db, f, sort="path")] == a + b
        # 一括確定は、条件に当てはまる「判定あり・未確認」だけ
        for iid in _ids(p):
            _ai(p, iid, ("deer", 0.9), status="auto")
            p.db.x("INSERT INTO judgements(item_id, species, origin, created_at) VALUES(?,?,?,?)", (iid, "deer", "auto", "2026-11-13T00:00:00"))
        assert judge.bulk_verify_count(p.db, f) == 4
        assert judge.bulk_verify(p.db, f, "me") == 4
        assert _ids(p, "status='verified'") == a + b
        # 読めない条件では一括確定は 1 件も動かない
        assert judge.bulk_verify_count(p.db, {"adv": "{bad"}) == 0 and judge.bulk_verify(p.db, {"adv": "{bad"}, "me") == 0
        assert len(_ids(p, "status='verified'")) == 4
        # 担当の割り当ても同じ条件
        assert judge.set_assignee(p.db, {"adv": _adv(station={"in": ["BSN0001"], "not": True})}, "A") == 4
        assert _ids(p, "assignee='A'") == b + _ids(p, "station='BSN0003'")
    finally:
        p.close()


# ---------- 保存した検索 ----------
def test_saved_searches(tmp_path):
    from trapnote.server import FILTER_KEYS
    p = _project(tmp_path, stations=("BSN0001",), per_station=1)
    try:
        pdir = p.dir
        assert search.load_saved(pdir) == []
        adv = _adv(time={"from": "18:00", "to": "06:00"}, fields=[dict(key="count", scope="species", min=2)])
        rows = search.save_search(pdir, " 夜のシカ ", {"species": "deer", "adv": adv, "status": "", "station": "all", "favorite": True, "ids": [1], "junk": "x"}, FILTER_KEYS)
        assert [r["name"] for r in rows] == ["夜のシカ"]
        assert rows[0]["filter"] == {"species": "deer", "favorite": "1", "adv": json.loads(adv)}       # 空・知らない鍵・ids は落とす。adv は dict で持つ
        assert rows[0]["saved_at"]
        search.save_search(pdir, "a 未確認", {"status": "unverified"}, FILTER_KEYS)
        assert [r["name"] for r in search.load_saved(pdir)] == ["a 未確認", "夜のシカ"]             # 名前の順
        # 同じ名前は置き換える（overwrite=False なら断る）
        with pytest.raises(ValueError):
            search.save_search(pdir, "夜のシカ", {"species": "boar"}, FILTER_KEYS, overwrite=False)
        rows = search.save_search(pdir, "夜のシカ", {"species": "boar"}, FILTER_KEYS)
        assert len(rows) == 2 and [r["filter"] for r in rows if r["name"] == "夜のシカ"] == [{"species": "boar"}]
        # 名前なし・読めない adv は保存しない
        for name, flt in (("", {"status": "new"}), ("  ", {}), ("x" * 81, {}), ("bad", {"adv": "{bad"}), ("bad", {"adv": {"nope": 1}}), ("bad", ["x"])):
            with pytest.raises(ValueError):
                search.save_search(pdir, name, flt, FILTER_KEYS)
        assert len(search.load_saved(pdir)) == 2
        # ファイルはプロジェクトのフォルダの searches.json。保存した条件はそのまま絞り込みに使える
        with open(os.path.join(pdir, "searches.json"), encoding="utf-8") as fh:
            assert [r["name"] for r in json.load(fh)["searches"]] == ["a 未確認", "夜のシカ"]
        search.save_search(pdir, "夜", {"adv": adv}, FILTER_KEYS)
        saved = [r for r in search.load_saved(pdir) if r["name"] == "夜"][0]["filter"]
        assert build_where(saved) == build_where({"adv": adv})
        # 削除（ない名前は何もしない）
        assert [r["name"] for r in search.delete_search(pdir, "夜のシカ")] == ["a 未確認", "夜"]
        assert [r["name"] for r in search.delete_search(pdir, "ない")] == ["a 未確認", "夜"]
        # 壊れたファイルは空として読む
        with open(os.path.join(pdir, "searches.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        assert search.load_saved(pdir) == []
    finally:
        p.close()


# ---------- 別窓のページと API ----------
def _session(tmp_path, monkeypatch, p):
    """設定・状態を tmp に向けた Session に、開いたプロジェクトを持たせる（走査のジョブは動かさない）"""
    from trapnote import config
    from trapnote.server import Session
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    s = Session()
    s.project = p
    return s


APPKEY_WARNING = "ignore:It is recommended to use web.AppKey"      # server.py の app["session"] への警告（この試験で初めて make_app を通るので出る）


@pytest.mark.filterwarnings(APPKEY_WARNING)
def test_search_api(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from trapnote.server import make_app
    p = _project(tmp_path)
    s = _session(tmp_path, monkeypatch, p)
    a = _ids(p, "station='BSN0001'")

    async def run():
        async with TestClient(TestServer(make_app(s))) as c:
            # 別窓のページ（版の印が入り、キャッシュしない）
            r = await c.get("/search")
            html = await r.text()
            assert r.status == 200 and "/static/search.js?v=" in html and "__V__" not in html and r.headers["Cache-Control"] == "no-store"
            for name in ("search.js", "search_shared.js", "search_main.js", "i18n_search.js", "search.css"):
                assert (await c.get(f"/static/{name}")).status == 200, name
            # 件数: total = 条件に当てはまる数、all = 条件なしの数
            adv = _adv(station={"in": ["BSN0001", "BSN0002"]})
            r = await c.post("/api/search/count", json={"filters": {"adv": adv, "station": "", "status": "all"}})
            assert r.status == 200 and await r.json() == {"total": 4, "all": 6}
            r = await c.post("/api/search/count", json={"filters": {}})
            assert await r.json() == {"total": 6, "all": 6}
            r = await c.post("/api/search/count", json={"filters": {"adv": "{bad"}})
            assert r.status == 400 and "読めません" in (await r.json())["error"]
            # 一覧・件数・id の列は、URL の adv で同じ条件になる（本体の listQuery が S.filter.adv を付ける）
            r = await c.get("/api/items", params={"adv": adv, "sort": "path", "count": "1", "limit": "200"})
            d = await r.json()
            assert d["total"] == 4 and [x["id"] for x in d["rows"]][:2] == a
            assert (await (await c.get("/api/items/count", params={"adv": adv})).json())["total"] == 4
            assert len((await (await c.get("/api/items/ids", params={"adv": adv})).json())["ids"]) == 4
            assert (await (await c.get("/api/items", params={"adv": "{bad", "count": "1"})).json())["total"] == 0     # 読めない条件は 0 件（エラーで一覧が止まらない）
            # 保存した検索
            assert await (await c.get("/api/search/saved")).json() == {"searches": []}
            r = await c.post("/api/search/saved", json={"name": "2 地点", "filter": {"adv": adv, "status": "new", "text": ""}})
            rows = (await r.json())["searches"]
            assert r.status == 200 and rows[0]["name"] == "2 地点" and rows[0]["filter"] == {"status": "new", "adv": json.loads(adv)}
            r = await c.post("/api/search/saved", json={"name": "2 地点", "filter": {}, "overwrite": False})
            assert r.status == 400
            r = await c.post("/api/search/saved", json={"name": "", "filter": {}})
            assert r.status == 400
            assert len((await (await c.get("/api/search/saved")).json())["searches"]) == 1
            r = await c.post("/api/search/saved/delete", json={"name": "2 地点"})
            assert await r.json() == {"searches": []}

    try:
        asyncio.run(run())
    finally:
        s.close()


@pytest.mark.filterwarnings(APPKEY_WARNING)
def test_long_condition_fits_in_url(tmp_path, monkeypatch):
    """地点を 400 個選んだ条件（URL にすると約 2.6 万バイト）でも一覧が読める（serve が URL の長さの上限を上げている。aiohttp の既定は 8190 バイト）"""
    import aiohttp
    from trapnote.server import serve
    p = _project(tmp_path)
    s = _session(tmp_path, monkeypatch, p)
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    adv = _adv(station={"in": ["BSN0001"] + [f"調査地点その{i:04d}" for i in range(400)]})

    async def run():
        ready = asyncio.Event()
        task = asyncio.create_task(serve(s, "127.0.0.1", port, lambda _p: ready.set()))
        await asyncio.wait_for(ready.wait(), 10)
        try:
            async with aiohttp.ClientSession() as c:
                async with c.get(f"http://127.0.0.1:{port}/api/items", params={"adv": adv, "count": "1"}) as r:
                    assert len(str(r.url)) > 20000
                    assert r.status == 200 and (await r.json())["total"] == 2
        finally:
            async with aiohttp.ClientSession() as c:
                await c.post(f"http://127.0.0.1:{port}/api/quit", data="{}")
            await asyncio.wait_for(task, 10)

    asyncio.run(run())
