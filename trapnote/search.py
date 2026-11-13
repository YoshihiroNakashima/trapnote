"""高度な検索（別窓 /search）のサーバー側: 条件（JSON）を SQL の WHERE に直す、保存した検索（<プロジェクト>/searches.json）、別窓のページと API。

絞り込みの鍵 adv（JSON の文字列か dict）に、簡易の絞り込み（judge.build_where の鍵）では表せない条件を入れる。簡易の鍵で表せるもの
（地点 1 つ、状態、日付、枠の数 など）は簡易の鍵のまま使う。adv の形（v = 1）:

  {"v": 1,
   "station":  {"in": ["BSN0001", "BSN0002"], "not": true},     地点を複数選ぶ。not = 選んだものを除く。"" は「地点なし」
   "season":   {"in": ["N240128"]},                             調査回（地点と同じ形）
   "species":  {"in": ["deer", "boar"], "not": true, "all": false, "src": "ai"},
                                                                種。not = 選んだ種が付いていない動画。all = 全部付いている動画（なければ どれか）。
                                                                src = "ai" は AI の判定（items.auto_json）、なければ人の判定（確認済み / CSV / 手動）
   "time":     {"from": "18:00", "to": "06:00"},                時刻（HH:MM。from > to なら日をまたぐ。修正後の時刻があればそれ）
   "duration": {"min": 5, "max": 60},                           動画の長さ（秒）
   "boxes":    {"min": 0, "max": 2},                            枠の数の範囲（検出済みの動画だけ。0 から始まる範囲に使う。未検出の動画は入れない）
   "conf":     {"min": 50, "max": 90},                          AI の信頼度（%。画面の表示と同じく四捨五入した値で比べる）
   "fields":   [{"key": "count", "scope": "species", "min": 2, "max": 5, "dflt": "1"},     記録した項目（fields.json。人が足した項目も）
                {"key": "sex", "scope": "species", "in": ["オス", "メス"], "blank": true},
                {"key": "tags", "scope": "item", "in": ["雪"], "multi": true},
                {"key": "juvenile", "scope": "species", "check": true},
                {"key": "memo", "scope": "item", "text": "足跡", "filled": true}]}

組み合わせ: 項目どうしは AND。1 つの項目の中で選んだ値どうしは OR（fields の 1 件の中の in / 範囲 / text / filled / blank も OR）。項目をまたぐ OR はない。
記録した項目のうち種の行ごとの項目（scope = species）は、人の判定の同じ 1 行（1 種）が全部の条件を満たす動画に当たる。
種を選んで絞り込んでいるとき（人の判定・含む向き）は、選んだ種の行だけを見る（「シカが 3 頭以上」）。AI の信頼度も、AI の判定で種を選んでいればその種の信頼度。
dflt = 値を入れていない行に画面が出す既定値（個体数の 1 など）。値のない行は既定値が入っているものとして比べる。

読めない条件は「どれにも当てはまらない」（0=1）にする。条件が抜けたまま、一括の操作や書き出しが広い範囲に掛からないように。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading

from .config import read_json, write_json
from .db import now_iso

log = logging.getLogger(__name__)

ADV_VERSION = 1
EFF_DT = "COALESCE(items.datetime_corrected, items.datetime)"            # judge.EFF_DT と同じ（修正後があればそれ）
HUMAN_ROW = "(items.status = 'verified' OR j.origin IN ('csv','manual'))"   # 人の判定の行（judge.build_where の種の絞り込みと同じ決め方）
MAX_PICK = 2000              # 1 つの項目で選べる値の数
MAX_FIELDS = 100             # 記録した項目の条件の数
YES = "('1','true')"         # チェックの項目の「あり」
TOP_KEYS = ("v", "station", "season", "species", "time", "duration", "boxes", "conf", "fields")
FIELD_KEYS = ("key", "scope", "in", "multi", "min", "max", "text", "check", "filled", "blank", "dflt")
SAVED_FILE = "searches.json"
MAX_SAVED = 200
MAX_NAME = 80
_saved_lock = threading.Lock()


# ---------- 条件を読む ----------
def _only(d, allowed, what: str) -> dict:
    if not isinstance(d, dict):
        raise ValueError(f"{what}: 形が違います")
    extra = sorted(set(d) - set(allowed))
    if extra:
        raise ValueError(f"{what}: 知らない鍵 {', '.join(map(str, extra))}")
    return d


def _strs(v, what: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(v, list) or not v:
        raise ValueError(f"{what}: 値を 1 つ以上選んでください")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or (x == "" and not allow_empty):
            raise ValueError(f"{what}: 値が文字列ではありません")
        if x not in out:
            out.append(x)
    if len(out) > MAX_PICK:
        raise ValueError(f"{what}: 選んだ数が多すぎます（{MAX_PICK} まで）")
    return out


def _num(v, what: str) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError(f"{what}: 数ではありません")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: 数ではありません")
    if math.isnan(x) or math.isinf(x):
        raise ValueError(f"{what}: 数ではありません")
    return x


def _flag(v, what: str) -> bool:
    if v is None:
        return False
    if not isinstance(v, bool):
        raise ValueError(f"{what}: true / false ではありません")
    return v


def _range(d, what: str) -> tuple[float | None, float | None]:
    d = _only(d, ("min", "max"), what)
    lo, hi = _num(d.get("min"), what), _num(d.get("max"), what)
    if lo is None and hi is None:
        raise ValueError(f"{what}: 範囲がありません")
    return lo, hi


def _clock(v, what: str) -> str | None:
    if v is None or v == "":
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", v) if isinstance(v, str) else None
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"{what}: 時刻は HH:MM で入れてください")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _parse_field(c, i: int) -> dict:
    what = f"項目 {i + 1}"
    c = _only(c, FIELD_KEYS, what)
    key = c.get("key")
    if not isinstance(key, str) or not key or len(key) > 100:
        raise ValueError(f"{what}: key がありません")
    if c.get("scope") not in ("species", "item"):
        raise ValueError(f"{what}: scope は species か item")
    out: dict = dict(key=key, scope=c["scope"])
    if c.get("in") is not None:
        out["vals"] = _strs(c["in"], what)
        out["multi"] = _flag(c.get("multi"), what)
    lo, hi = _num(c.get("min"), what), _num(c.get("max"), what)
    if lo is not None or hi is not None:
        out["lo"], out["hi"] = lo, hi
    if c.get("text") not in (None, ""):
        if not isinstance(c["text"], str) or len(c["text"]) > 200:
            raise ValueError(f"{what}: text が文字列ではありません")
        out["text"] = c["text"]
    if c.get("check") is not None:
        if not isinstance(c["check"], bool):
            raise ValueError(f"{what}: check は true / false")
        out["check"] = c["check"]
    out["filled"], out["blank"] = _flag(c.get("filled"), what), _flag(c.get("blank"), what)
    if c.get("dflt") not in (None, ""):
        if not isinstance(c["dflt"], str) or len(c["dflt"]) > 200:
            raise ValueError(f"{what}: dflt が文字列ではありません")
        out["dflt"] = c["dflt"]
    if not (out.get("vals") or "lo" in out or "text" in out or "check" in out or out["filled"] or out["blank"]):
        raise ValueError(f"{what}: 条件がありません")
    return out


def parse(adv) -> dict:
    """adv（JSON の文字列か dict）を確かめて、SQL を作りやすい形にする。空なら {}。読めなければ ValueError"""
    if adv is None or adv == "" or adv == {}:
        return {}
    if isinstance(adv, (str, bytes)):
        try:
            adv = json.loads(adv)
        except ValueError:
            raise ValueError("JSON として読めません")
    d = _only(adv, TOP_KEYS, "高度な検索")
    if d.get("v", ADV_VERSION) != ADV_VERSION:
        raise ValueError(f"知らない版です: v = {d.get('v')}")
    out: dict = {}
    for k, name in (("station", "地点"), ("season", "調査回")):
        if d.get(k) is not None:
            c = _only(d[k], ("in", "not"), name)
            out[k] = dict(vals=_strs(c.get("in"), name, allow_empty=True), neg=_flag(c.get("not"), name))
    if d.get("species") is not None:
        c = _only(d["species"], ("in", "not", "all", "src"), "種")
        if c.get("src") not in (None, "", "human", "ai"):
            raise ValueError("種: src は human か ai")
        out["species"] = dict(vals=_strs(c.get("in"), "種"), neg=_flag(c.get("not"), "種"), all=_flag(c.get("all"), "種"), ai=c.get("src") == "ai")
    if d.get("time") is not None:
        c = _only(d["time"], ("from", "to"), "時刻")
        a, b = _clock(c.get("from"), "時刻"), _clock(c.get("to"), "時刻")
        if a is None and b is None:
            raise ValueError("時刻: 範囲がありません")
        out["time"] = (a, b)
    if d.get("duration") is not None:
        out["duration"] = _range(d["duration"], "動画の長さ")
    if d.get("boxes") is not None:
        out["boxes"] = _range(d["boxes"], "枠の数")
    if d.get("conf") is not None:
        out["conf"] = _range(d["conf"], "AI の信頼度")
    if d.get("fields") is not None:
        fs = d["fields"]
        if not isinstance(fs, list) or len(fs) > MAX_FIELDS:
            raise ValueError("記録した項目: 形が違います")
        if fs:
            out["fields"] = [_parse_field(c, i) for i, c in enumerate(fs)]
    return out


# ---------- SQL ----------
def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _pick_term(col: str, vals: list[str], neg: bool) -> tuple[str, list]:
    """地点・調査回: 選んだ値のどれか（neg なら どれでもない）。"" は「なし」（NULL か空）"""
    named = [v for v in vals if v != ""]
    none = len(named) != len(vals)
    parts = []
    if not neg:
        if named:
            parts.append(f"{col} IN ({_marks(len(named))})")
        if none:
            parts.append(f"({col} IS NULL OR {col} = '')")
        return "(" + " OR ".join(parts) + ")", named
    if named:
        parts.append(f"({col} IS NULL OR {col} NOT IN ({_marks(len(named))}))")
    if none:
        parts.append(f"({col} IS NOT NULL AND {col} != '')")
    return "(" + " AND ".join(parts) + ")", named


def _species_terms(c: dict) -> tuple[list[str], list]:
    codes = c["vals"]
    if c["ai"]:
        tpl = "(items.auto_json IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(items.auto_json, '$.species') e WHERE json_extract(e.value, '$.species') {cond}))"
    else:
        tpl = f"EXISTS (SELECT 1 FROM judgements j WHERE j.item_id = items.id AND {HUMAN_ROW} AND j.species {{cond}})"
    many = f"IN ({_marks(len(codes))})"
    if c["neg"]:
        return ["NOT " + tpl.format(cond=many)], list(codes)
    if c["all"] and len(codes) > 1:
        return [tpl.format(cond="= ?") for _ in codes], list(codes)
    return [tpl.format(cond=many)], list(codes)


def _picked_codes(f: dict, a: dict, ai: bool) -> list[str]:
    """種を選んで（含む向きで）絞り込んでいるときの種コード。記録した項目・AI の信頼度を、その種の行に当てるために使う"""
    codes: list[str] = []
    sp = f.get("species")
    if sp and (str(f.get("species_src") or "") == "ai") == ai:
        if isinstance(sp, str):
            if sp not in ("none", "any", "multi"):
                codes = [x.strip() for x in re.split(r"[+,]", sp) if x.strip()]
        else:
            codes = [str(x) for x in sp if str(x)]
    c = a.get("species")
    if c and not c["neg"] and c["ai"] == ai:
        codes += [x for x in c["vals"] if x not in codes]
    return codes


def _field_term(c: dict, link: str) -> tuple[str, list]:
    """記録した項目 1 つの条件。link = 値の行の当て方（種の行ごと: v.judgement_id = j.id / 動画ごと: v.item_id = items.id AND v.judgement_id IS NULL）"""
    key = c["key"]
    row = f"SELECT 1 FROM field_values v WHERE {link} AND v.field_key = ?"
    stored = f"EXISTS ({row} AND COALESCE(v.value, '') != '')"          # 値が入っている
    dflt = c.get("dflt") or ""
    ors: list[str] = []
    p: list = []
    dflt_hit = False                # 既定値がこの条件に当てはまる → 値のない行も当たり
    if c.get("vals"):
        vals = c["vals"]
        if c.get("multi"):          # 複数選択の項目は "a;b;c" で入っている
            ors.append(f"EXISTS ({row} AND (" + " OR ".join(["(';' || v.value || ';') LIKE ? ESCAPE '\\'"] * len(vals)) + "))")
            p += [key] + ["%;" + _like(x) + ";%" for x in vals]
            dflt_hit = dflt_hit or bool(set(x for x in dflt.split(";") if x) & set(vals))
        else:
            ors.append(f"EXISTS ({row} AND v.value IN ({_marks(len(vals))}))")
            p += [key] + list(vals)
            dflt_hit = dflt_hit or dflt in vals
    if "lo" in c:
        lo, hi = c["lo"], c["hi"]
        conds = ["TRIM(v.value) NOT GLOB '*[^0-9.eE+-]*'", "v.value GLOB '*[0-9]*'"]      # 数として読める値だけ
        nums: list = []
        if lo is not None:
            conds.append("CAST(v.value AS REAL) >= ?"); nums.append(lo)
        if hi is not None:
            conds.append("CAST(v.value AS REAL) <= ?"); nums.append(hi)
        ors.append(f"EXISTS ({row} AND " + " AND ".join(conds) + ")")
        p += [key] + nums
        try:
            d = float(dflt) if dflt else None
        except ValueError:
            d = None
        dflt_hit = dflt_hit or (d is not None and (lo is None or d >= lo) and (hi is None or d <= hi))
    if "text" in c:
        ors.append(f"EXISTS ({row} AND v.value LIKE ? ESCAPE '\\')")
        p += [key, "%" + _like(c["text"]) + "%"]
        dflt_hit = dflt_hit or c["text"].lower() in dflt.lower()
    if c.get("check") is True:
        ors.append(f"EXISTS ({row} AND v.value IN {YES})")
        p.append(key)
        dflt_hit = dflt_hit or dflt in ("1", "true")
    elif c.get("check") is False:   # 印なし = 「あり」が入っていない（既定が「あり」の項目は、値のない行は「あり」）
        if dflt in ("1", "true"):
            ors.append(f"({stored} AND NOT EXISTS ({row} AND v.value IN {YES}))")
            p += [key, key]
        else:
            ors.append(f"NOT EXISTS ({row} AND v.value IN {YES})")
            p.append(key)
    if c.get("filled"):
        if dflt:
            ors.append("1=1")       # 既定値のある項目は、値を入れていなくても既定値が入っている扱い
        else:
            ors.append(stored)
            p.append(key)
    if (c.get("blank") and not dflt) or dflt_hit:      # 未記入（既定値のある項目に未記入はない）、または既定値が当たり
        ors.append(f"NOT {stored}")
        p.append(key)
    if not ors:
        return "0=1", []
    return "(" + " OR ".join(ors) + ")", p


def where(f: dict) -> tuple[list[str], list]:
    """絞り込み f の adv を SQL の条件の列（AND でつなぐ）と変数の列に。judge.build_where から呼ぶ。読めない条件は 0=1"""
    try:
        a = parse(f.get("adv"))
    except ValueError as e:
        log.warning("高度な検索の条件を読めません（どれにも当てはまらない扱いにします）: %s", e)
        return ["0=1"], []
    w: list[str] = []
    p: list = []
    for k, col in (("station", "items.station"), ("season", "items.season")):
        if k in a:
            s, q = _pick_term(col, a[k]["vals"], a[k]["neg"])
            w.append(s); p += q
    if "species" in a:
        ss, q = _species_terms(a["species"])
        w += ss; p += q
    if "time" in a:
        t0, t1 = a["time"]
        hm = f"substr({EFF_DT}, 12, 5)"
        if t0 and t1:
            cond, q = (f"{hm} BETWEEN ? AND ?", [t0, t1]) if t0 <= t1 else (f"({hm} >= ? OR {hm} <= ?)", [t0, t1])      # from > to = 日をまたぐ
        elif t0:
            cond, q = f"{hm} >= ?", [t0]
        else:
            cond, q = f"{hm} <= ?", [t1]
        w.append(f"(length({EFF_DT}) >= 16 AND {cond})"); p += q
    if "duration" in a:
        lo, hi = a["duration"]
        if lo is not None:
            w.append("items.duration >= ?"); p.append(lo)
        if hi is not None:
            w.append("items.duration <= ?"); p.append(hi)
    if "boxes" in a:                # 枠の数の範囲は検出済みの動画だけ（簡易の鍵 boxes の「枠 0 = 検出済みで枠なし」と同じ考え方。未検出の n_boxes = 0 は入れない）
        lo, hi = a["boxes"]
        w.append("items.det_state = 1")
        if lo is not None:
            w.append("items.n_boxes >= ?"); p.append(lo)
        if hi is not None:
            w.append("items.n_boxes <= ?"); p.append(hi)
    if "conf" in a:
        lo, hi = a["conf"]
        pc = "ROUND(100.0 * json_extract(e.value, '$.confidence'))"       # 画面と同じく % に丸めて比べる
        conds, q = [], []
        if lo is not None:
            conds.append(f"{pc} >= ?"); q.append(lo)
        if hi is not None:
            conds.append(f"{pc} <= ?"); q.append(hi)
        codes = _picked_codes(f, a, ai=True)
        if codes:
            conds.append(f"json_extract(e.value, '$.species') IN ({_marks(len(codes))})"); q += codes
        w.append(f"(items.auto_json IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(items.auto_json, '$.species') e WHERE {' AND '.join(conds)}))")
        p += q
    fields = a.get("fields") or []
    per_row = [c for c in fields if c["scope"] == "species"]
    if per_row:                     # 種の行ごとの項目: 人の判定の同じ 1 行が全部を満たす（動物なし・有効範囲の行には項目がない）
        codes = _picked_codes(f, a, ai=False)
        row = f"SELECT 1 FROM judgements j WHERE j.item_id = items.id AND {HUMAN_ROW} AND j.species NOT IN ('empty','area')"
        q = []
        if codes:
            row += f" AND j.species IN ({_marks(len(codes))})"; q += codes
        for c in per_row:
            s, qq = _field_term(c, "v.judgement_id = j.id")
            row += " AND " + s; q += qq
        w.append(f"EXISTS ({row})"); p += q
    for c in fields:
        if c["scope"] == "item":
            s, q = _field_term(c, "v.item_id = items.id AND v.judgement_id IS NULL")
            w.append(s); p += q
    return w, p


# ---------- 保存した検索（<プロジェクト>/searches.json） ----------
def clean_filter(src, keys) -> dict:
    """保存する絞り込み: 知っている鍵の、空でない値だけ。adv は読めることを確かめて dict で持つ。読めなければ ValueError"""
    if not isinstance(src, dict):
        raise ValueError("絞り込みの形が違います")
    out: dict = {}
    for k in keys:
        v = src.get(k)
        if v in (None, "", "all") or k == "ids":
            continue
        if k == "adv":
            parse(v)
            v = json.loads(v) if isinstance(v, (str, bytes)) else v
            if not v:
                continue
        elif isinstance(v, bool):
            v = "1" if v else ""
            if not v:
                continue
        elif isinstance(v, (int, float)):
            v = str(v)
        elif not isinstance(v, str):
            raise ValueError(f"絞り込みの {k} の値が文字列ではありません")
        out[k] = v
    return out


def _saved_path(pdir: str) -> str:
    return os.path.join(pdir, SAVED_FILE)


def load_saved(pdir: str) -> list[dict]:
    d = read_json(_saved_path(pdir), None)
    rows = d.get("searches") if isinstance(d, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"].strip() and isinstance(r.get("filter"), dict):
            out.append(dict(name=r["name"].strip(), filter=r["filter"], saved_at=str(r.get("saved_at") or "")))
    return sorted(out, key=lambda r: r["name"].lower())


def save_search(pdir: str, name, flt, keys, overwrite: bool = True) -> list[dict]:
    """名前を付けて保存する（同じ名前があれば置き換える。overwrite=False なら ValueError）。返り値は保存後の一覧"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("名前を入れてください")
    if len(name) > MAX_NAME:
        raise ValueError(f"名前が長すぎます（{MAX_NAME} 文字まで）")
    flt = clean_filter(flt, keys)
    with _saved_lock:
        rows = load_saved(pdir)
        same = [r for r in rows if r["name"] == name]
        if same and not overwrite:
            raise ValueError(f"同じ名前の検索があります: {name}")
        rows = [r for r in rows if r["name"] != name]
        if len(rows) >= MAX_SAVED:
            raise ValueError(f"保存できるのは {MAX_SAVED} 個までです。使わないものを消してください")
        rows.append(dict(name=name, filter=flt, saved_at=now_iso()))
        rows.sort(key=lambda r: r["name"].lower())
        write_json(_saved_path(pdir), dict(searches=rows))
        return rows


def delete_search(pdir: str, name) -> list[dict]:
    name = str(name or "").strip()
    with _saved_lock:
        rows = load_saved(pdir)
        left = [r for r in rows if r["name"] != name]
        if len(left) != len(rows):
            write_json(_saved_path(pdir), dict(searches=left))
        return left


# ---------- 別窓のページと API ----------
def register(app, S, io) -> None:
    """高度な検索の別窓（/search）と API（/api/search/…）を足す。server.make_app から呼ぶ（S = Session、io = 裏のスレッドで動かす小道具）"""
    from aiohttp import web

    from . import BUILD, VERSION, judge
    from .config import STATIC_DIR
    from .server import FILTER_KEYS, ApiError, _body, _filters, jr

    async def page(request):
        html = (STATIC_DIR / "search.html").read_text(encoding="utf-8").replace("__V__", f"{VERSION}.{BUILD}")
        return web.Response(text=html, content_type="text/html", charset="utf-8", headers={"Cache-Control": "no-store"})

    async def count(request):
        """いまの条件に当てはまる数（total）と、条件なしの数（all）。別窓が条件を変えるたびに呼ぶ。読めない条件は 400 で理由を返す"""
        p = S.need()
        b = await _body(request)
        f = _filters(b.get("filters") or {})
        try:
            parse(f.get("adv"))
        except ValueError as e:
            raise ApiError(f"高度な検索の条件を読めません: {e}")

        def run():
            base = {k: f[k] for k in ("missing", "include_missing") if k in f}     # 「全」は、ファイルのない動画の扱いだけ同じにした数
            return dict(total=judge.count_items(p.db, f), all=judge.count_items(p.db, base))
        return jr(await io(run))

    async def saved_get(request):
        p = S.need()
        return jr(dict(searches=await io(load_saved, p.dir)))

    async def saved_post(request):
        p = S.need()
        b = await _body(request)
        try:
            rows = await io(save_search, p.dir, b.get("name"), b.get("filter") or {}, FILTER_KEYS, bool(b.get("overwrite", True)))
        except ValueError as e:
            raise ApiError(str(e))
        return jr(dict(searches=rows))

    async def saved_delete(request):
        p = S.need()
        b = await _body(request)
        return jr(dict(searches=await io(delete_search, p.dir, b.get("name"))))

    r = app.router
    r.add_get("/search", page)
    r.add_post("/api/search/count", count)
    r.add_get("/api/search/saved", saved_get)
    r.add_post("/api/search/saved", saved_post)
    r.add_post("/api/search/saved/delete", saved_delete)
