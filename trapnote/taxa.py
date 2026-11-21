"""SpeciesNet が知っている種の一覧（ラベル）と、国ごとの制限（geofence）を、モデルのファイルから直接読む（確認事項 149 回目）。

torch もモデルの読み込みも要らない。種リストの窓が、次のことに使う:
  - 候補の一覧: 調査地の国にいる種（SpeciesNet の geofence で、その国で除かれないラベル）を並べて、チェックで種リストに登録する
  - 種リストの各行が、自動判定で付くかどうか（学名が SpeciesNet のラベルにあるか、属・科などで対応するか、対応がないか）
  - 学名から、種リストの speciesnet の列（どのラベルをその種に集めるか）を決める
ファイルは、SpeciesNet のモデルを取ってきたときに一緒に置かれる（<モデルの置き場>/kagglehub/models/google/speciesnet/…/info.json が、
ラベルの一覧 classifier_labels と geofence のファイルの名前を持つ）。まだ取ってきていなければ、load() は None。

ラベルは「uuid;綱;目;科;属;種;英名」。対応の決まりは detect.build_label_map と同じ（2 語 = 属 ＋ 種。1 語 = 属・科・目・綱のどれか）。
"""
from __future__ import annotations

import glob
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

RANKS_JA = {"species": "種", "genus": "属", "family": "科", "order": "目", "class": "綱"}
LEVELS = ("species", "genus", "family", "order", "class")
_SKIP_COMMON = {"blank", "vehicle", "human", "animal", "no cv result", "unknown"}
_lock = threading.Lock()
_cache: dict = {}


def parse_label(label: str) -> dict:
    parts = [p.strip() for p in label.split(";")]
    while len(parts) < 7:
        parts.append("")
    d = dict(label=label, cls=parts[1].lower(), order=parts[2].lower(), family=parts[3].lower(), genus=parts[4].lower(), species=parts[5].lower(), common=parts[6].lower())
    if d["species"] and d["genus"]:
        d.update(rank="species", sci=f"{d['genus'].capitalize()} {d['species']}", value=f"{d['genus']} {d['species']}")
    else:
        lv = next((k for k in ("genus", "family", "order", "cls") if d[k]), "")
        d.update(rank="class" if lv == "cls" else lv, sci=d[lv].capitalize() if lv else "", value=d[lv] if lv else "")
    return d


def find_info(models_dir: str, model: str = "") -> str | None:
    """info.json の場所。model = プロジェクトの設定 speciesnet_model（kaggle:<持ち主>/<名前>/<枠組み>/<版>/<番号>、またはローカルのフォルダ）。
    その場所になければ、kagglehub の置き場でいちばん新しい speciesnet の info.json"""
    m = (model or "").strip()
    cands = []
    if m and os.path.isdir(m):
        cands.append(os.path.join(m, "info.json"))
    if m.startswith("kaggle:") and models_dir:
        cands.append(os.path.join(models_dir, "kagglehub", "models", *m[len("kaggle:"):].split("/"), "info.json"))
    for c in cands:
        if os.path.isfile(c):
            return c
    if not models_dir:
        return None
    found = [p for p in glob.glob(os.path.join(models_dir, "kagglehub", "models", "**", "info.json"), recursive=True) if "speciesnet" in p.replace("\\", "/").lower()]
    return max(found, key=os.path.getmtime) if found else None


class Taxa:
    def __init__(self, labels: list[str], geofence: dict | None, source: str = ""):
        self.source = source
        self.geofence = geofence or {}
        self.rows = [d for d in (parse_label(x) for x in labels) if d["rank"] and d["common"] not in _SKIP_COMMON and not (d["genus"] == "homo" and d["species"] == "sapiens")]
        self._binomial = {d["value"] for d in self.rows if d["rank"] == "species"}
        self._by_level = {lv: {d["cls" if lv == "class" else lv] for d in self.rows if d["cls" if lv == "class" else lv]} for lv in ("genus", "family", "order", "class")}
        self._countries: list[str] | None = None

    # ---- 国 ----
    def countries(self) -> list[str]:
        """geofence に出てくる国（ISO 3166-1 alpha-3）"""
        if self._countries is None:
            s: set = set()
            for rule in self.geofence.values():
                s.update((rule.get("allow") or {}).keys())
                s.update((rule.get("block") or {}).keys())
            self._countries = sorted(c for c in s if isinstance(c, str) and len(c) == 3)
        return self._countries

    def blocked(self, d: dict, country: str) -> bool:
        """その国では除かれるラベルか（detect.should_geofence と同じ決まり。国の単位だけ）"""
        rule = self.geofence.get(";".join((d["cls"], d["order"], d["family"], d["genus"], d["species"])))
        if not rule or not country:
            return False
        allow = rule.get("allow")
        if allow and country not in allow:
            return True
        block = rule.get("block")
        return bool(block and country in block and not block[country])

    # ---- 候補 ----
    def candidates(self, country: str = "") -> list[dict]:
        """種リストに登録できる候補（国を渡せば、その国で除かれないものだけ）。種を先に、綱・目・科・学名の順"""
        c = (country or "").strip().upper()
        out = [dict(sci=d["sci"], common=d["common"], rank=d["rank"], cls=d["cls"], order=d["order"], family=d["family"], value=d["value"])
               for d in self.rows if not (c and d["rank"] == "species" and self.blocked(d, c))]
        if c:                                   # 属・科などの上位の区分は、その国に残る種が 1 つでもあるものだけ
            keep = {lv: {x["cls" if lv == "class" else lv] for x in self.rows if x["rank"] == "species" and not self.blocked(x, c)} for lv in ("genus", "family", "order", "class")}
            out = [x for x in out if x["rank"] == "species" or x["value"] in keep[x["rank"]]]
        seen, uniq = set(), []
        for x in out:                           # 同じ学名のラベルが 2 つあるときは 1 つに
            k = (x["rank"], x["value"])
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        uniq.sort(key=lambda x: (LEVELS.index(x["rank"]), x["cls"], x["order"], x["family"], x["sci"]))
        return uniq

    # ---- 種リストの行との対応 ----
    def level(self, value: str) -> str:
        """種リストの speciesnet の列の値が、どの段で SpeciesNet のラベルに当たるか: species / genus / family / order / class / ''（当たらない）"""
        toks = [t for t in (value or "").strip().lower().split("（")[0].split("(")[0].replace(",", " ").split() if t]
        if not toks:
            return ""
        if len(toks) >= 2:
            return "species" if " ".join(toks) in self._binomial else ""
        return next((lv for lv in ("genus", "family", "order", "class") if toks[0] in self._by_level[lv]), "")

    def suggest(self, name_sci: str) -> tuple[str, str]:
        """学名 → (speciesnet の列に入れる値, 当たる段)。その種のラベルがあれば「属 種」。なくて同じ属のラベルがあれば属（その属をこの種に集める。
        日本のカモシカ Capricornis crispus → capricornis など）。どちらもなければ「属 種」のまま（当たらない = 自動判定では付かない）"""
        toks = [t for t in (name_sci or "").strip().lower().replace(",", " ").split() if t]
        if not toks:
            return "", ""
        if len(toks) == 1:
            return toks[0], self.level(toks[0])
        full, v = " ".join(toks), f"{toks[0]} {toks[1]}"
        if full in self._binomial:              # 亜種まで書いた学名のラベルがある（家畜のヤギ Capra aegagrus hircus など）
            return full, "species"
        if v in self._binomial:
            return v, "species"
        if toks[0] in self._by_level["genus"]:
            return toks[0], "genus"
        return v, ""


def load(models_dir: str, model: str = "") -> Taxa | None:
    """ラベルの一覧と geofence を読む（一度読んだものは覚えておく。ファイルが変われば読み直す）。モデルをまだ取ってきていなければ None"""
    info = find_info(models_dir, model)
    if not info:
        return None
    try:
        key = (info, os.path.getmtime(info))
        with _lock:
            if _cache.get("key") == key:
                return _cache["taxa"]
        with open(info, encoding="utf-8") as fh:
            meta = json.load(fh)
        base = os.path.dirname(info)
        lp = os.path.join(base, str(meta.get("classifier_labels") or ""))
        if not os.path.isfile(lp):
            return None
        with open(lp, encoding="utf-8") as fh:
            labels = [ln.strip() for ln in fh if ln.strip()]
        gf = None
        gp = os.path.join(base, str(meta.get("geofence") or ""))
        if meta.get("geofence") and os.path.isfile(gp):
            with open(gp, encoding="utf-8") as fh:
                gf = json.load(fh)
        t = Taxa(labels, gf, source=str(meta.get("version") or os.path.basename(base)))
        with _lock:
            _cache.update(key=key, taxa=t)
        return t
    except (OSError, ValueError) as e:
        log.info("SpeciesNet の種の一覧を読めません（%s）: %s", info, e)
        return None
