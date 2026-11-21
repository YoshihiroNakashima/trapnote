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
# 鳥の種名（ローマ字・和名・学名の一部）を含む表記は「鳥類（種不明）」の当たりにする（Kiji、KijiHato、Shirohara、Phasianidae、B_Hiyodori など。対応表で変えられる）
# 英数字の 5 文字以上と仮名・漢字は部分一致、4 文字は前方・後方一致、3 文字以下は完全一致（Banana が鳥にならないように）
BIRD_HINTS = ("kiji", "hato", "bato", "tsugumi", "shirohara", "phasianidae", "bird", "aves", "tori", "dori", "mokin", "sagi", "kirei", "hiyodori", "karasu", "garasu", "kakesu",
              "yamadori", "mejiro", "uguisu", "suzume", "mozu", "aoji", "kuroji", "akahara", "jyoubitaki", "joubitaki", "ruribitaki", "kojukei", "kamo", "gamo", "nosuri",
              "sashiba", "fukurou", "fukuro", "kawasemi", "shijukara", "yamagara", "higara", "enaga", "komadori", "ikaru", "shime", "tsubame", "hibari", "kuina", "ban",
              "corvus", "turdus", "passer", "columba", "streptopelia", "gallus", "accipiter", "ardea", "egretta", "strix",
              "jay", "tit", "dove", "crow", "owl", "pigeon", "pheasant", "thrush", "bunting", "heron", "egret", "hawk", "eagle", "kite", "buzzard", "warbler", "bulbul",
              "sparrow", "finch", "flycatcher", "robin", "wagtail", "woodpecker", "rail", "duck", "kingfisher", "cuckoo", "swallow", "starling", "grosbeak", "partridge",
              "キジ", "ハト", "ツグミ", "シロハラ", "ヒヨドリ", "カラス", "カケス", "ヤマドリ", "メジロ", "ウグイス", "スズメ", "モズ", "アオジ", "クロジ", "アカハラ", "ジョウビタキ",
              "ルリビタキ", "コジュケイ", "カモ", "ノスリ", "サシバ", "フクロウ", "カワセミ", "シジュウカラ", "ヤマガラ", "エナガ", "コマドリ", "イカル", "シメ", "ツバメ", "ヒバリ", "サギ", "鳥")


def is_bird_label(key: str) -> bool:
    """比較用に詰めた表記（_squash）が鳥の種名らしいか"""
    for h in BIRD_HINTS:
        if not h.isascii():
            if h in key:
                return True
        elif len(h) >= 5:
            if h in key:
                return True
        elif len(h) == 4:
            if key.startswith(h) or key.endswith(h):
                return True
        elif key == h:
            return True
    return False


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


# ---------- CSV からまとめて登録（確認事項 149 回目） ----------
# 読める見出し: ひな形（write_template）の見出し、TrapNote の列の名前、日本語・英語の言い方、Darwin Core の名前。大文字小文字、空白、_、-、かっこの中は見ない
IMPORT_HEADS = {
    "code": ("code", "コード"),
    "name_ja": ("name_ja", "和名", "名前", "日本語名", "japanesename", "japanese"),
    "name_en": ("name_en", "英名", "name", "englishname", "english", "commonname", "vernacularname"),
    "name_sci": ("name_sci", "学名", "scientificname", "scientific", "latinname"),
    "rank": ("rank", "階級", "taxonrank"),
    "csv_label": ("csv_label", "csv表記", "csv", "label"),
    "non_native": ("non_native", "外来", "外来種"),
    "speciesnet": ("speciesnet",),
    "key": ("key", "キー"),
    "enabled": ("enabled", "使う", "use"),
}
NAME_COLS = ("name_ja", "name_en", "name_sci")
TEMPLATE_HEADS = {"ja": [("name_ja", "和名"), ("name_en", "英名"), ("name_sci", "学名"), ("csv_label", "CSV表記"), ("key", "キー"), ("enabled", "使う"), ("code", "code")],
                  "en": [("name_en", "name"), ("name_sci", "scientific_name"), ("csv_label", "csv_label"), ("key", "key"), ("enabled", "use"), ("code", "code")]}
TEMPLATE_NOTES = {
    "ja": ["# 種リストのひな形です。1 行に 1 種を書いて保存し、TrapNote の「種を登録」→「CSV からまとめて登録」で読み込みます。",
           "# 和名か英名のどちらかは必ず入れます。学名を入れると、自動判定（SpeciesNet）と Camtrap DP 形式の書き出しで使えます。",
           "# CSV表記 = CSV に書き出すときの種の名前（空なら英名から作ります）。キー = 判定のときに押すキー 1 文字。使う = 1 なら画面に出す、0 なら出さない（空は 1）。",
           "# code は TrapNote が付けた印です。もうある行の code は変えないでください。新しく足す種は、空のままにします。",
           "# 例: ニホンジカ,Sika Deer,Cervus nippon,Deer,2,1,",
           "# 「#」で始まる行は読みません。"],
    "en": ["# Template of the species list. Write one species per row, save, and load it in TrapNote: \"Register species\" > \"Register many from a CSV\".",
           "# name is required. With scientific_name, the species can be used by automatic identification (SpeciesNet) and in Camtrap DP exports.",
           "# csv_label = name of the species written to exported CSVs (made from name when empty). key = one key pressed while judging. use = 1 to show, 0 to hide (empty means 1).",
           "# code is a mark given by TrapNote. Do not change the code of existing rows. Leave it empty for species you add.",
           "# Example: Red fox,Vulpes vulpes,RedFox,3,1,",
           "# Rows starting with \"#\" are not read."],
}


def _head_key(h: str) -> str:
    return re.sub(r"[\s_\-]+", "", re.sub(r"[（(].*?[）)]", "", (h or "").strip().lower()))


_HEAD_OF = {_head_key(s): col for col, syns in IMPORT_HEADS.items() for s in syns}


def write_template(path: str, rows: list[dict], lang: str = "ja") -> dict:
    """種リストのひな形の CSV を書く（いまの種リストの行つき。先頭は「#」で始まる説明の行）。Excel で開けるように BOM つきの UTF-8"""
    lang = "en" if lang == "en" else "ja"
    heads = list(TEMPLATE_HEADS[lang])
    if lang == "en" and any((r.get("name_ja") or "").strip() for r in rows):      # 英語の画面でも、和名の入った種リストなら和名の列を残す
        heads.insert(1, ("name_ja", "name_ja"))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for note in TEMPLATE_NOTES[lang]:
            w.writerow([note])
        w.writerow([h for _, h in heads])
        for r in rows:
            w.writerow([(1 if r.get("enabled") else 0) if c == "enabled" else (r.get(c) or "") for c, _ in heads])
    os.replace(tmp, path)
    return dict(path=path, rows=len(rows))


def read_import(path: str) -> dict:
    """種の CSV（ひな形を書き換えたものなど）を読む。「#」で始まる行は読まない。
    返り値: rows = [dict(line = 行の番号, 読めた列…)]、heads = 読めた列、unknown = 読まなかった見出し、no_name = 名前（和名・英名・学名）のない行の番号。
    名前の列が 1 つも見つからなければ ValueError（前は、見出しが違うと名前のない行を 1 つ足していた）"""
    text = read_text_any(path)
    kept = [(n, ln) for n, ln in enumerate(text.splitlines(keepends=True), start=1) if ln.strip() and not ln.lstrip().lstrip('"').startswith("#")]
    if not kept:
        raise ValueError("CSV に行がありません")
    body = "".join(ln for _, ln in kept)
    table = list(csv.reader(io.StringIO(body), delimiter=_sniff(body)))
    header = table[0] if table else []
    cols = [_HEAD_OF.get(_head_key(h)) for h in header]
    if not any(c in NAME_COLS for c in cols):
        raise ValueError("名前の列（和名・英名・学名）が見つかりません。「ひな形を書き出す」で作った CSV の見出しを使ってください")
    rows, no_name = [], []
    for (line, _), cells in zip(kept[1:], table[1:]):
        d = {c: (cells[i].strip() if i < len(cells) else "") for i, c in enumerate(cols) if c}
        if not any(d.values()):
            continue
        if not any(d.get(k) for k in NAME_COLS):
            no_name.append(line)
            continue
        d["line"] = line
        rows.append(d)
    return dict(rows=rows, heads=[c for c in cols if c], unknown=[h.strip() for h, c in zip(header, cols) if not c and h.strip()], no_name=no_name)


def _flag(v) -> int:
    return 0 if str(v).strip().lower() in ("0", "false", "no", "") else 1


def _suggest(name_sci: str, taxa=None) -> str:
    """学名 → speciesnet の列の値（自動判定で、どのラベルをこの種に集めるか）。SpeciesNet の種の一覧（taxa）があれば、それで決める
    （その種のラベルがなく、同じ属のラベルがあれば属）。なければ、学名のはじめの 2 語"""
    if taxa is not None:
        return taxa.suggest(name_sci)[0]
    return " ".join((name_sci or "").split()[:2]).lower()


class SpeciesList:
    def __init__(self, path: str, initial: str = "default", sync: bool = True):
        """initial = 種リストのファイルがまだないときの中身: default（日本の哺乳類を中心にした既定の一覧）/ empty（種は空。固定項目だけ。確認事項 149 回目）。
        sync = 既定の一覧から始めたリストの手直し（_sync_defaults）をするか。空から始めたリストではしない（「鳥類」の行が足されないように）"""
        self.path = path
        if os.path.isfile(path):
            self.rows: list[dict] = load_csv(path)
            if sync:
                self._sync_defaults()
        else:
            self.rows = [] if initial == "empty" else default_list()
            save_csv(path, self.rows)

    def _sync_defaults(self) -> None:
        """古い既定で作った種リストの手直し: 小型哺乳類の speciesnet 列（mammalia だと哺乳類全部がここに入る）を空に、
        既定に後から足した行（鳥類など）を「使わない」で足す"""
        changed = False
        # SpeciesNet のラベルにない日本の種（学名）は属で対応させる（SpeciesNet v4 のラベル一覧で確かめた）
        genus_fix = {"capra hircus": "capra aegagrus hircus", "cetartiodactyla": "artiodactyla",      # SpeciesNet v4 のラベルに合わせる（家畜のヤギ、偶蹄目。確認事項 149 回目）
                     "capricornis crispus": "capricornis", "meles anakuma": "meles", "martes melampus": "martes", "mustela itatsi": "mustela",
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
        return r.get("name_ja" if lang == "ja" else "name_en") or r.get("name_en") or r.get("name_ja") or r.get("name_sci") or code     # その言語の名前がなければ、もう一方の言語 → 学名

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
        if "bird" in rows and is_bird_label(key):
            return "bird"                           # 鳥の種名（Kiji、KijiHato、Shirohara、Phasianidae、キジバト など）
        for v, code in names.items():               # 名前の前方・部分一致（"Racc" → RaccoonDog）
            if len(key) >= 3 and (v.startswith(key) or key in v):
                return code
        return None

    def update(self, rows: list[dict], taxa=None) -> None:
        """種リストの窓の表をそのまま保存する。学名を入れた・書き換えた行は、自動判定の対応（speciesnet の列）を学名から付ける
        （人が speciesnet の列を書き換えた行と、前の学名から自動で付く値でなかった行は、そのまま）。名前が 1 つもない行は捨てる"""
        old = {r["code"]: r for r in self.rows}
        out, seen = [], set()
        for r in rows:
            if not any(str(r.get(k) or "").strip() for k in NAME_COLS):
                continue
            d = _norm_row({k: str(v) if v is not None else "" for k, v in r.items()})
            if d["code"] in seen or d["code"] in FIXED_CODES:
                continue
            o = old.get(d["code"]) if str(r.get("code") or "").strip() else None
            if d["name_sci"]:
                if o is None:
                    d["speciesnet"] = d["speciesnet"] or _suggest(d["name_sci"], taxa)
                elif (o.get("name_sci") or "") != d["name_sci"] and d["speciesnet"] == (o.get("speciesnet") or ""):
                    old_sci = o.get("name_sci") or ""
                    if d["speciesnet"].strip().lower() in {"", _suggest(old_sci, taxa), _suggest(old_sci, None), " ".join(old_sci.lower().split()[:1])}:
                        d["speciesnet"] = _suggest(d["name_sci"], taxa)
            seen.add(d["code"])
            out.append(d)
        self.rows = out
        save_csv(self.path, self.rows)

    def _find(self, d: dict) -> dict | None:
        """同じ種とみなす行: code が同じ → 学名・英名・和名・CSV 表記のどれかが同じ（大文字小文字は見ない）"""
        c = (d.get("code") or "").strip().lower()
        if c:
            for r in self.rows:
                if r["code"] == c:
                    return r
        for k in ("name_sci", "name_en", "name_ja", "csv_label"):
            v = (d.get(k) or "").strip().lower()
            if v:
                for r in self.rows:
                    if (r.get(k) or "").strip().lower() == v:
                        return r
        return None

    def _match(self, d: dict) -> dict | None:
        """まとめて登録するとき（CSV、候補の一覧、既定の一覧）に、行 d と同じ種とみなす行。_find より厳しい（別の項目を 1 つにまとめないため）:
          code が同じ → その行
          学名が同じ行のうち、英名か和名も同じもの → その行
          学名（属 種の 2 語以上）が同じ行が 1 つだけ → その行（同じ種。名前が違えば、書き換えになる）
          学名のない行は、英名か和名が同じ行
        科・目・綱などの 1 語の学名は、名前も同じときだけ同じとみなす（「哺乳類（種不明）」と「小型哺乳類（種不明）」は、どちらも Mammalia）"""
        low = lambda v: str(v or "").strip().lower()   # noqa: E731
        c = low(d.get("code"))
        if c:
            for r in self.rows:
                if r["code"] == c:
                    return r
        sci, en, ja = low(d.get("name_sci")), low(d.get("name_en")), low(d.get("name_ja"))
        if sci:
            cands = [r for r in self.rows if low(r.get("name_sci")) == sci]
            for r in cands:
                if (en and low(r.get("name_en")) == en) or (ja and low(r.get("name_ja")) == ja):
                    return r
            return cands[0] if len(cands) == 1 and len(sci.split()) >= 2 else None
        for k, v in (("name_en", en), ("name_ja", ja)):
            if v:
                for r in self.rows:
                    if low(r.get(k)) == v:
                        return r
        return None

    def _new_row(self, row: dict, taxa=None) -> dict:
        """新しい種の行を作る（まだ足さない）。code はほかと重ならないように付け、学名があれば speciesnet の列も入れる"""
        d = _norm_row({k: str(v) if v is not None else "" for k, v in row.items()})
        if not d.get("speciesnet") and d.get("name_sci"):       # 学名があれば SpeciesNet の対応にも使う（属 種 の 2 語か、科などの 1 語）
            d["speciesnet"] = _suggest(d["name_sci"], taxa)
        base = d["code"]
        if base in FIXED_CODES or any(r["code"] == base for r in self.rows):
            i = 2
            while any(r["code"] == f"{base}_{i}" for r in self.rows):
                i += 1
            d["code"] = f"{base}_{i}"
        # CSV 表記は、ほかの種・固定項目と重ならないようにする（重なると、CSV を読み戻すときにどの種か決まらない）
        used = {(r.get("csv_label") or "").strip().lower() for r in self.rows} | {f["csv_label"].lower() for f in FIXED}
        label, i = d["csv_label"], 2
        while label.strip().lower() in used:
            label = f"{d['csv_label']}{i}"
            i += 1
        d["csv_label"] = label
        return d

    def add(self, row: dict, taxa=None) -> dict:
        """1 種を登録する（同じ英名・学名・和名・CSV 表記の種があればそれを返す）。taxa = SpeciesNet の種の一覧（taxa.Taxa。あれば、学名から自動判定の対応を決める）"""
        probe = _norm_row({k: str(v) if v is not None else "" for k, v in row.items()})
        probe.pop("code", None)
        r = self._find(probe)
        if r is not None:
            if not r["enabled"]:
                r["enabled"] = 1
                save_csv(self.path, self.rows)
            return r
        d = self._new_row(row, taxa)
        d["enabled"] = 1
        self.rows.append(d)
        save_csv(self.path, self.rows)
        return d

    def add_many(self, rows: list[dict], taxa=None) -> dict:
        """何種かをまとめて登録する（候補の一覧から選んだもの、既定の一覧）。もうある種は「使う」にするだけ。返り値は、足した数と、もうあった数"""
        n_new = n_have = 0
        for row in rows:
            if not any(str(row.get(k) or "").strip() for k in NAME_COLS):
                continue
            r = self._match({k: v for k, v in row.items() if k != "code"})
            if r is not None:
                r["enabled"] = 1
                n_have += 1
                continue
            d = self._new_row(row, taxa)
            d["enabled"] = 1
            self.rows.append(d)
            n_new += 1
        save_csv(self.path, self.rows)
        return dict(added=n_new, existed=n_have)

    def add_defaults(self) -> dict:
        """既定の一覧（日本の哺乳類を中心にした 31 項目）のうち、まだない種を足す。「使う」は既定の一覧のまま"""
        n = 0
        for d in default_list():
            if self._match(d) is None:
                self.rows.append(self._new_row(d))
                n += 1
        save_csv(self.path, self.rows)
        return dict(added=n)

    # ---- CSV からまとめて登録 ----
    @staticmethod
    def _val(k: str, v) -> str:
        if k in ("enabled", "non_native"):
            return str(_flag(v))
        s = str(v if v is not None else "").strip()
        return s[:1].upper() if k == "key" else s

    def plan_import(self, items: list[dict]) -> list[dict]:
        """read_import で読んだ行を、いまの種リストと比べる（何も変えない）。返り値は行ごとに dict(line, name, sci, action, changes)。
        action: new（新しく登録する）/ update（もうある種。changes の列を書き換える）/ same（もうあって、変わるところがない）/
                fixed（動物なし・人・車・有効範囲・不明は、種リストとは別の固定の項目なので読まない）/ dup（この CSV の中に同じ種がもうあった）
        もうある種の、CSV で空の欄は変えない（空で消すことはしない）"""
        fixed_names = {str(f[k]).strip().lower() for f in FIXED for k in ("code", "name_ja", "name_en", "csv_label") if f.get(k)}
        out: list[dict] = []
        seen: set = set()
        for d in items:
            ident = (d.get("code") or "").strip().lower() or tuple(str(d.get(k) or "").strip().lower() for k in NAME_COLS)
            names = {str(d.get(k) or "").strip().lower() for k in NAME_COLS} - {""}
            base = dict(line=d.get("line"), name=next((d[k] for k in ("name_ja", "name_en", "name_sci") if d.get(k)), ""), sci=d.get("name_sci") or "", changes=[])
            if (d.get("code") or "").strip().lower() in FIXED_CODES or names & fixed_names:
                out.append(dict(base, action="fixed"))
                continue
            if ident in seen:
                out.append(dict(base, action="dup"))
                continue
            seen.add(ident)
            r = self._match(d)
            if r is None:
                out.append(dict(base, action="new"))
                continue
            ch = [k for k in COLS if k != "code" and k in d and str(d[k]).strip() != "" and self._val(k, d[k]) != self._val(k, r.get(k))]
            out.append(dict(base, action="update" if ch else "same", changes=ch, code=r["code"]))
        return out

    def apply_import(self, items: list[dict], taxa=None) -> dict:
        """plan_import のとおりに登録・書き換えをする。新しい種は、「使う」の列が空なら使う（1）。
        学名を書き換えた種は、自動判定の対応（speciesnet の列）が前の学名から自動で付けたもののままなら、新しい学名で付け直す"""
        plan = self.plan_import(items)
        counts = dict(new=0, update=0, same=0, fixed=0, dup=0)
        for d, p in zip(items, plan):
            counts[p["action"]] += 1
            if p["action"] == "new":
                row = {k: v for k, v in d.items() if k in COLS}
                if str(row.get("enabled") or "").strip() == "":
                    row["enabled"] = "1"
                self.rows.append(self._new_row(row, taxa))
            elif p["action"] == "update":
                r = self._match(d)
                old_sci = r.get("name_sci") or ""
                autos = {"", _suggest(old_sci, taxa), _suggest(old_sci, None), " ".join(old_sci.lower().split()[:1])}      # 前の学名から自動で付く値（種・属）
                for k in p["changes"]:
                    r[k] = int(self._val(k, d[k])) if k in ("enabled", "non_native") else self._val(k, d[k])
                if "name_sci" in p["changes"] and "speciesnet" not in p["changes"] and (r.get("speciesnet") or "").strip().lower() in autos:
                    r["speciesnet"] = _suggest(r["name_sci"], taxa)
        save_csv(self.path, self.rows)
        return dict(counts, plan=plan)

    def ai_status(self, taxa=None) -> dict[str, str]:
        """種のコード → 自動判定（SpeciesNet）での対応:
        species / genus / family / order / class = その段で SpeciesNet のラベルに当たる（種なら、その種のラベルがある。属なら、その属のラベルをこの種に集める）、
        none = 対応がない（speciesnet の列が空か、当たるラベルがない。自動判定では付かず、目で判別するときだけ使える）、
        unknown = まだ分からない（SpeciesNet の種の一覧をまだ読めない。列には値がある）"""
        out: dict[str, str] = {}
        for r in self.rows:
            v = (r.get("speciesnet") or "").strip()
            out[r["code"]] = "none" if not v else ((taxa.level(v) or "none") if taxa is not None else "unknown")
        return out

    def set_enabled(self, codes: list[str]) -> None:
        s = set(codes)
        for r in self.rows:
            r["enabled"] = 1 if r["code"] in s else 0
        save_csv(self.path, self.rows)

    def to_dict(self) -> dict:
        return dict(species=self.rows, fixed=[dict(f, fixed=True) for f in FIXED])
