"""種リスト（<プロジェクト>/TrapNote/species.csv）と固定項目"""
from __future__ import annotations

import csv
import io
import os
import re

from .config import RESOURCES_DIR

COLS = ["code", "name_ja", "name_en", "name_sci", "rank", "csv_label", "non_native", "speciesnet", "key", "enabled"]

# 種とは別の固定項目（どのプロジェクトにもある。CSV 表記は既存の PRM_N の値に合わせる。Empty と Area は既存の値）
FIXED = [
    dict(code="empty", name_ja="動物なし", name_en="Empty", csv_label="Empty", speciesnet="blank", key="N"),
    dict(code="human", name_ja="人", name_en="Human", csv_label="Human", speciesnet="human", key="H"),
    dict(code="vehicle", name_ja="車", name_en="Vehicle", csv_label="Vehicle", speciesnet="vehicle", key="V"),
    dict(code="area", name_ja="有効範囲", name_en="Area", csv_label="Area", speciesnet="", key="A"),
    dict(code="unknown", name_ja="不明", name_en="Unknown", csv_label="Unknown", speciesnet="", key="U"),
]
FIXED_CODES = {f["code"] for f in FIXED}
ENCODINGS = ("utf-8-sig", "cp932", "latin-1")


def _code_of(row: dict) -> str:
    c = (row.get("code") or "").strip().lower()
    if not c:
        base = (row.get("name_en") or row.get("name_sci") or row.get("name_ja") or "sp").strip().lower()
        c = re.sub(r"[^a-z0-9]+", "_", base).strip("_") or "sp"
    return c


def _squash(s: str) -> str:
    """比較用: 小文字にして英数字と仮名・漢字以外を除く"""
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+", "", (s or "").strip().lower())


# Timelapse のテンプレートでよく使われる略称・別名 → 種コード（既定の種リストのコード）。前方一致でも当てる
SYNONYMS = {
    "sika": "deer", "sikadeer": "deer", "shika": "deer", "cervusnippon": "deer", "nihonjika": "deer", "deer": "deer", "シカ": "deer",
    "wildboar": "boar", "inoshishi": "boar", "susscrofa": "boar", "pig": "boar", "boar": "boar", "イノシシ": "boar",
    "tanuki": "raccoon_dog", "raccoondog": "raccoon_dog", "racoondog": "raccoon_dog", "rdog": "raccoon_dog", "rcdog": "raccoon_dog", "タヌキ": "raccoon_dog",
    "raccoon": "raccoon", "racoon": "raccoon", "procyon": "raccoon", "アライグマ": "raccoon",
    "fox": "fox", "redfox": "fox", "kitsune": "fox", "キツネ": "fox",
    "serow": "serow", "kamoshika": "serow", "カモシカ": "serow",
    "monkey": "macaque", "macaque": "macaque", "saru": "macaque", "サル": "macaque",
    "hare": "hare", "rabbit": "hare", "usagi": "hare", "ウサギ": "hare", "ノウサギ": "hare",
    "civet": "civet", "palmcivet": "civet", "maskedpalmcivet": "civet", "hakubishin": "civet", "ハクビシン": "civet",
    "badger": "badger", "anaguma": "badger", "アナグマ": "badger",
    "marten": "marten", "ten": "marten", "テン": "marten",
    "weasel": "weasel", "itachi": "weasel", "イタチ": "weasel",
    "bear": "bear", "blackbear": "bear", "kuma": "bear", "クマ": "bear",
    "cat": "cat", "neko": "cat", "ネコ": "cat", "dog": "dog", "inu": "dog", "イヌ": "dog",
    "squirrel": "squirrel", "risu": "squirrel", "リス": "squirrel", "muntjac": "muntjac", "kyon": "muntjac", "キョン": "muntjac",
    "goat": "goat", "ヤギ": "goat", "bat": "bat", "コウモリ": "bat", "dormouse": "dormouse", "ヤマネ": "dormouse",
    "mouse": "rodent", "rat": "rodent", "rodent": "rodent", "nezumi": "rodent", "ネズミ": "rodent", "smallmammal": "small_mammal", "mammal": "mammal",
    "bird": "bird", "birds": "bird", "tori": "bird", "鳥": "bird",
    "human": "human", "humans": "human", "person": "human", "people": "human", "hito": "human", "ヒト": "human", "人": "human",
    "vehicle": "vehicle", "car": "vehicle", "cars": "vehicle", "bike": "vehicle", "truck": "vehicle", "車": "vehicle",
    "empty": "empty", "none": "empty", "blank": "empty", "noanimal": "empty", "nothing": "empty", "null": "empty", "なし": "empty",
    "unknown": "unknown", "unk": "unknown", "unidentified": "unknown", "不明": "unknown", "area": "area", "有効範囲": "area",
}


def _norm_row(row: dict) -> dict:
    d = {k: (row.get(k) or "").strip() for k in COLS}
    d["code"] = _code_of(row)
    d["non_native"] = 1 if str(d["non_native"]).strip().lower() in ("1", "true", "yes", "*", "外来") else 0
    d["enabled"] = 0 if str(d["enabled"]).strip().lower() in ("0", "false", "no", "") else 1
    d["key"] = d["key"][:1].upper() if d["key"] else ""
    if not d["csv_label"]:
        d["csv_label"] = re.sub(r"[^A-Za-z0-9]+", "", d["name_en"]) or d["code"]
    return d


def read_text_any(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _sniff(text: str) -> str:
    head = text[:4096]
    try:
        return csv.Sniffer().sniff(head, delimiters=",\t;").delimiter
    except Exception:
        return "\t" if head.count("\t") > head.count(",") else ","


def load_csv(path: str) -> list[dict]:
    text = read_text_any(path)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=_sniff(text)))
    out, seen = [], set()
    for r in rows:
        r = {(k or "").strip().lower(): v for k, v in r.items()}
        if not any((v or "").strip() for v in r.values()):
            continue
        d = _norm_row(r)
        if d["code"] in seen or d["code"] in FIXED_CODES:
            continue
        seen.add(d["code"])
        out.append(d)
    return out


def default_list() -> list[dict]:
    return load_csv(str(RESOURCES_DIR / "species_default.csv"))


def save_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


class SpeciesList:
    def __init__(self, path: str):
        self.path = path
        self.rows: list[dict] = load_csv(path) if os.path.isfile(path) else default_list()
        if not os.path.isfile(path):
            save_csv(path, self.rows)
        else:
            self._sync_defaults()

    def _sync_defaults(self) -> None:
        """古い既定で作った種リストの手直し: 小型哺乳類の speciesnet 列（mammalia だと哺乳類全部がここに入る）を空に、
        既定に後から足した行（鳥類など）を「使わない」で足す"""
        changed = False
        # SpeciesNet のラベルにない日本の種（学名）は属で対応させる（SpeciesNet v4 のラベル一覧で確かめた）
        genus_fix = {"capricornis crispus": "capricornis", "meles anakuma": "meles", "martes melampus": "martes", "mustela itatsi": "mustela",
                     "macaca fuscata": "macaca", "lepus brachyurus": "lepus", "sciurus lis": "sciurus", "glirulus japonicus": "gliridae", "vulpes vulpes": "vulpes"}
        for r in self.rows:
            if r["code"] == "small_mammal" and (r.get("speciesnet") or "").strip().lower() == "mammalia":
                r["speciesnet"] = ""
                changed = True
            sn = (r.get("speciesnet") or "").strip().lower()
            if sn in genus_fix:
                r["speciesnet"] = genus_fix[sn]
                changed = True
        have = {r["code"] for r in self.rows}
        for d in default_list():
            if d["code"] not in have and d["code"] in ("bird",):
                self.rows.append(dict(d, enabled=0))
                changed = True
        if changed:
            save_csv(self.path, self.rows)

    def by_code(self) -> dict[str, dict]:
        d = {r["code"]: r for r in self.rows}
        for f in FIXED:
            d[f["code"]] = dict(f, rank="固定", non_native=0, enabled=1, fixed=True)
        return d

    def enabled(self) -> list[dict]:
        return [r for r in self.rows if r["enabled"]]

    def label(self, code: str, lang: str = "ja") -> str:
        r = self.by_code().get(code)
        if not r:
            return code
        return r.get("name_ja" if lang == "ja" else "name_en") or r.get("name_en") or code

    def csv_label(self, code: str) -> str:
        r = self.by_code().get(code)
        return (r.get("csv_label") if r else None) or code

    def code_for_label(self, label: str) -> str | None:
        """CSV の値（Deer、Boar、Empty、和名、英名、学名、コード）から種のコードを引く（完全一致）"""
        s = (label or "").strip().lower()
        if not s:
            return None
        for code, r in self.by_code().items():
            for k in ("csv_label", "code", "name_en", "name_ja", "name_sci"):
                if (r.get(k) or "").strip().lower() == s:
                    return code
        return None

    def guess_code(self, label: str) -> str | None:
        """Timelapse などの略称・記号から種コードの当たりを付ける（完全一致 → 記号を除いた一致 → 略称表 → 前方・部分一致）。
        当たらなければ None（画面で人が選ぶ）"""
        exact = self.code_for_label(label)
        if exact:
            return exact
        key = _squash(label)
        key = re.sub(r"x{2,}$", "", key) or key          # Timelapse で桁をそろえるための X の詰め物（DeerXX、CatXXXX）を外す
        if not key:
            return None
        rows = self.by_code()
        names = {}
        for code, r in rows.items():
            for k in ("csv_label", "code", "name_en", "name_ja", "name_sci"):
                v = _squash(r.get(k) or "")
                if v:
                    names.setdefault(v, code)
        if key in names:
            return names[key]
        if key in names:
            return names[key]
        if key in SYNONYMS and SYNONYMS[key] in rows:
            return SYNONYMS[key]
        if re.match(r"^b_", (label or "").strip().lower()) and "bird" in rows:   # B_Kiji のような「鳥の種」の付け方
            return "bird"
        for syn, code in SYNONYMS.items():          # 略称を含む（"SikaDeer2" など）
            if code in rows and len(syn) >= 4 and (key.startswith(syn) or syn in key):
                return code
        for v, code in names.items():               # 名前の前方・部分一致（"Racc" → RaccoonDog）
            if len(key) >= 3 and (v.startswith(key) or key in v):
                return code
        return None

    def update(self, rows: list[dict]) -> None:
        out, seen = [], set()
        for r in rows:
            d = _norm_row({k: str(v) if v is not None else "" for k, v in r.items()})
            if d["code"] in seen or d["code"] in FIXED_CODES:
                continue
            seen.add(d["code"])
            out.append(d)
        self.rows = out
        save_csv(self.path, self.rows)

    def add(self, row: dict) -> dict:
        """1 種を登録する（同じ英名・学名・和名があればそれを返す）"""
        d = _norm_row({k: str(v) if v is not None else "" for k, v in row.items()})
        if not d.get("speciesnet") and d.get("name_sci"):       # 学名があれば SpeciesNet の対応にも使う（属 種 の 2 語か、科などの 1 語）
            d["speciesnet"] = " ".join(d["name_sci"].split()[:2]).lower()
        for r in self.rows:
            for k in ("name_sci", "name_en", "name_ja", "csv_label"):
                if d.get(k) and r.get(k) and d[k].strip().lower() == r[k].strip().lower():
                    if not r["enabled"]:
                        r["enabled"] = 1
                        save_csv(self.path, self.rows)
                    return r
        base = d["code"]
        if base in FIXED_CODES or any(r["code"] == base for r in self.rows):
            i = 2
            while any(r["code"] == f"{base}_{i}" for r in self.rows):
                i += 1
            d["code"] = f"{base}_{i}"
        d["enabled"] = 1
        self.rows.append(d)
        save_csv(self.path, self.rows)
        return d

    def set_enabled(self, codes: list[str]) -> None:
        s = set(codes)
        for r in self.rows:
            r["enabled"] = 1 if r["code"] in s else 0
        save_csv(self.path, self.rows)

    def to_dict(self) -> dict:
        return dict(species=self.rows, fixed=[dict(f, fixed=True) for f in FIXED])
