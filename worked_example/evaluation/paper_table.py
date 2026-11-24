"""論文用の表: 集計の結果（results_<日時>/）から、SpeciesNet と学習モデル（方式 B の 4 つと、軽い方式（方式 A）の 3 つまで）の結果を 1 つにまとめる。
軽い方式の列は 2026-11-12 に足した（前の台本は paper_table.before-A-20261112.py）。軽い方式の答えのない集計（11/12 22:59 より前の results）には使えない。
2026-11-13 に、軽い方式を 3 つ（1 回目・2 回目・3 回目の材料）に増やした（前の台本は paper_table.before-A23-20261113.py）。表での名前は「軽い方式 1 回目」のように、
同じ材料の方式 B（1 回目・2 回目・3 回目）と並ぶ名前にした（前の表の「軽い方式」は、いまの「軽い方式 1 回目」）。集計に答えのある軽い方式だけを並べる。
2026-11-13 の昼に、軽い方式の「元のモデルを混ぜる割合」を 0 にしたモデル（「軽い方式（混ぜない）1 回目」など）の列と、表 13・14 を足した
（前の台本は paper_table.before-mix0-20261113.py）。このモデルの答えのない集計（11/13 11:54 より前の results）では、前と同じ表になる
（「読むときの注意」の、軽い方式が学習した区分のほかを答えるかについての文だけは、数えた本数を入れる形に直した）。
同じ日に、「論文では、軽い方式（混ぜない）を主な結果にする」と決まったので、その 1 行を「読むときの注意」に足した（数字は変えていない）。

読むだけ: 集計の出力（videos_<gold>.csv、compare_<gold>.csv、top1_<gold>.csv、sites_<gold>.csv、detected_<gold>.csv、summary.md）と、
モデルの model.json・train_items.csv、種リスト（species.csv）、gold の project.json。TrapNote のプログラムには触らない。
gold のデータベース（data.sqlite）は、通しの指標のために読むだけで開く（種の判別に進まなかった動画の、地点・人の判定・アプリの答えを読む。
集計の出力には本数しかないため）。読んだ本数が集計の detected_<gold>.csv と同じでなければ、止まる。
数え方は compare_gold.py の先頭に書いてあるものと同じ（動画ごとの答えを、集計が出した videos_<gold>.csv から読む）。

使い方: python -I -X utf8 paper_table.py [results フォルダ] [--out=出力フォルダ]
  results フォルダを省くと、このファイルと同じフォルダのいちばん新しい results_*。
  出力フォルダを省くと、このファイルと同じフォルダの paper_tables/。前に作った表は上書きしない（同じ集計からもう一度作ると、名前に _2、_3 … が付く）。

出すもの（<日時> は、もとの results の日時）:
- 論文用の表_<日時>.md           表 1（正解率）、表 2（95% の幅）、表 3〜6（同じ動画どうしの差）、表 7（学習に使った枠の数）、表 8（検出の段階）、
                                 表 9〜12（通しの指標: 種の判別に進まなかった動画も数える再現率・適合率・F1）
- 論文用の表_<日時>_正解率.csv   表 1・2 の全部のますと、種ごとの行（本数、地点数、正解の本数、%、幅）
- 論文用の表_<日時>_差.csv       同じ動画どうしの差（差、幅、入れ替わった本数、McNemar の p）
- 論文用の表_<日時>_通しの指標.csv   表 9〜12 の全部（種ごとの再現率・適合率・F1 と幅、線を使わない数え方の値）
- 論文用の表_<日時>_種の判別に進まなかった動画.csv   評価に入る動画のうち、種の判別に進まなかった動画の一覧

確かめ: 動画ごとの答えから数え直した本数が、集計の表（compare・top1・sites の CSV と summary.md）と全部同じでなければ、何も書かずに止まる。

幅の出し方: 地点ごとの取り直し（ブートストラップ）。その行の動画がある地点を、同じ数だけ重複を許して選び直し、正解率（または差）を出し直す。
これを 20,000 回繰り返し、2.5% と 97.5% の点を幅とする。同じ地点の動画は似ているので、動画を 1 本ずつ別のものとして扱う幅（Wilson など）より正直。
乱数は行ごとに決まった種から出すので、何度作っても同じ数字になる。
"""
from __future__ import annotations

import collections
import csv
import datetime
import glob
import json
import math
import os
import re
import sqlite3
import sys
import zlib

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
MODELS_DIR = "F:/TrapNote/data/models/local"
SPECIES_CSV = "F:/Copy/gold_season/TrapNote/species.csv"
GOLDS = ["gold_season", "gold_station", "gold_species"]
SPECIES_ONLY = {"gold_species"}          # 種ごとに本数を決めて選んだ gold: 動物全体・合計は出さない
MAIN_SPECIES = ["ホンドテン", "ニホンイタチ", "ネコ", "ニホンノウサギ", "ニホンリス"]
SN_OPEN, SN_CLOSED = "SpeciesNet（そのまま）", "SpeciesNet（絞る）"
B_MODELS = [("First_200 だけ", "B_20261109_1554"), ("1 回目", "B_20261110_1911"), ("2 回目", "B_20261111_1543"), ("3 回目", "B_20261111_2231")]
# 軽い方式 = 方式 A（元の SpeciesNet の特徴量から小さな分類器を学習する。GPU なしで学習できる）。（表での名前、モデルの名前、同じ材料で学習した方式 B）
A_ALL = [("軽い方式 1 回目", "A_20261112_2255", "1 回目"), ("軽い方式 2 回目", "A_20261113_0251", "2 回目"), ("軽い方式 3 回目", "A_20261113_0255", "3 回目")]
# 軽い方式（混ぜない）= 同じ回の軽い方式と同じ材料・同じ設定で学習し、判定のときに元の SpeciesNet の確率を混ぜない（学習の窓の「元のモデルを混ぜる割合」を 0 にした）もの。
# （表での名前、モデルの名前、同じ材料で学習した方式 B、同じ回の軽い方式）。軽い方式（混ぜる割合 0.3）の gold の結果を見たあとで足した評価
A0_ALL = [("軽い方式（混ぜない）1 回目", "A_20261113_1148", "1 回目", "軽い方式 1 回目"), ("軽い方式（混ぜない）2 回目", "A_20261113_1149", "2 回目", "軽い方式 2 回目"),
          ("軽い方式（混ぜない）3 回目", "A_20261113_1150", "3 回目", "軽い方式 3 回目")]
HEAD = {SN_OPEN: "SpeciesNet（そのまま）", SN_CLOSED: "SpeciesNet（17 の選択肢に絞る）"}
RULES = [("アプリの規則", ""), ("線を使わない数え方", "線なし ")]      # （数え方、列の名前の頭に付ける字）
# 下の 7 つは、集計に答えのある軽い方式が決まってから set_models() が入れる
LIGHTS: list[tuple[str, str, str]] = []
LIGHTS0: list[tuple[str, str, str, str]] = []
MODELS: list[tuple[str, str]] = []
SYS: list[tuple[str, str]] = []            # （表での名前、videos_<gold>.csv の列の名前）
PAIRS_MD: list[tuple[str, str]] = []       # 表 3・4・11 に出す組
PAIRS: list[tuple[str, str]] = []          # CSV に出す組の全部
P6: list[tuple[str, str]] = []             # 表 6（gold_species の、学習したモデルどうしの差）に出す組


def set_models(R: str) -> None:
    """集計（R）に答えのある軽い方式だけを並べて、モデルと、比べる組を決める"""
    global LIGHTS, LIGHTS0, MODELS, SYS, PAIRS_MD, PAIRS, P6
    with open(f"{R}/videos_{GOLDS[0]}.csv", encoding="utf-8-sig") as f:
        cols = set(next(csv.reader(f)))
    LIGHTS = [a for a in A_ALL if a[1] in cols]
    assert LIGHTS and LIGHTS == A_ALL[:len(LIGHTS)], ("軽い方式の答えが、集計に順にそろっていない", [a[1] for a in LIGHTS])
    LIGHTS0 = [a for a in A0_ALL if a[1] in cols]
    assert not LIGHTS0 or (LIGHTS0 == A0_ALL and len(LIGHTS) == len(A_ALL)), ("軽い方式（混ぜない）の答えが、集計に 3 つそろっていない", [a[1] for a in LIGHTS0])
    MODELS = B_MODELS + [(lab, m) for lab, m, _ in LIGHTS] + [(a[0], a[1]) for a in LIGHTS0]
    SYS = [(SN_OPEN, SN_OPEN), (SN_CLOSED, SN_CLOSED)] + MODELS
    lab = [a[0] for a in LIGHTS]
    b_steps = [("First_200 だけ", "1 回目"), ("1 回目", "2 回目"), ("2 回目", "3 回目"), ("First_200 だけ", "2 回目"), ("First_200 だけ", "3 回目")]
    method = [(b, a) for a, _, b in LIGHTS]                                # 同じ材料の方式 B → 軽い方式
    a_steps = list(zip(lab, lab[1:])) + ([(lab[0], lab[2])] if len(lab) == 3 else [])
    lab0 = [a[0] for a in LIGHTS0]
    mix_pairs = [(a[3], a[0]) for a in LIGHTS0]                            # 同じ回の軽い方式（混ぜる割合 0.3）→ 軽い方式（混ぜない）。分類器は同じで、違うのは混ぜる割合だけ
    method0 = [(a[2], a[0]) for a in LIGHTS0]                              # 同じ材料の方式 B → 軽い方式（混ぜない）
    a0_steps = list(zip(lab0, lab0[1:])) + ([(lab0[0], lab0[2])] if len(lab0) == 3 else [])
    PAIRS_MD = [(SN_CLOSED, m) for m, _ in MODELS] + b_steps + method + a_steps + mix_pairs + method0 + a0_steps
    PAIRS = PAIRS_MD + [(SN_OPEN, SN_CLOSED), ("1 回目", "3 回目"), (lab[0], "2 回目"), (lab[0], "3 回目")]
    P6 = [("First_200 だけ", "1 回目"), ("1 回目", "2 回目"), ("2 回目", "3 回目"), ("First_200 だけ", "3 回目")] + method + mix_pairs + method0
NOTAN = {"人", "車"}
NOANS = {"要確認", "不明", ""}
EMPTY = "動物なし"
TOTAL_ROWS = {"動物（動物なしを除く）", "合計"}     # compare・top1 の CSV の終わりにある、全体の行
FIXED_JA = {"empty": "動物なし", "human": "人", "vehicle": "車", "unknown": "不明", "area": "有効範囲", "(low)": "要確認"}   # compare_gold.py と同じ
NOT_ANIMAL_CODES = {"human", "vehicle", "area"}
B = 20000
MIN_N = 10                               # これより本数の少ない行には、幅と差を出さない


def humans(v: dict) -> list[str]:
    return [h for h in v["人の判定"].split("+") if h not in NOTAN]


def correct(v: dict, col: str) -> bool:
    return v[col] in humans(v)


def mcnemar(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def model_info(m: str) -> tuple[dict, set[str]]:
    """model.json と、学習に使った地点（compare_gold.py の model_info と同じ読み方）"""
    info = json.load(open(f"{MODELS_DIR}/{m}/model.json", encoding="utf-8"))
    st: set[str] = set()
    for fn in (info.get("items_csv"), f"{MODELS_DIR}/{m}/train_items.csv"):
        if fn and os.path.exists(fn):
            for r in csv.DictReader(open(fn, encoding="utf-8-sig")):
                if r.get("Station") and (r.get("Role") or "train") == "train":
                    s, p = r["Station"], r.get("Project") or ""
                    st.add(s[len(p) + 1:] if p and s.startswith(p + ":") else s)
            break
    return info, st


class Subset:
    """表の 1 行の対象になる動画の集まり。地点ごとの取り直しの重みを持つ"""

    def __init__(self, gold: str, name: str, vids: list[dict]):
        self.gold, self.name, self.vids, self.n = gold, name, vids, len(vids)
        order: dict[str, int] = {}
        for v in vids:
            order.setdefault(v["地点"], len(order))
        self.G = len(order)
        self.idx = np.array([order[v["地点"]] for v in vids], dtype=int)
        self.N = np.bincount(self.idx, minlength=self.G).astype(float)
        self._W = None

    def ci(self, x: np.ndarray) -> tuple[float | None, float | None]:
        """x（動画ごとの 0/1、または差）の平均の、地点ごとの取り直しの 95% の幅（%）"""
        if self.G < 2 or self.n < MIN_N:
            return None, None
        if self._W is None:
            rng = np.random.default_rng([20261112, zlib.crc32(f"{self.gold}|{self.name}".encode("utf-8"))])
            self._W = rng.multinomial(self.G, np.full(self.G, 1.0 / self.G), size=B).astype(np.float64)
        est = (self._W @ np.bincount(self.idx, weights=x, minlength=self.G)) / (self._W @ self.N)
        lo, hi = np.percentile(est, [2.5, 97.5])
        return 100 * float(lo), 100 * float(hi)

    def free(self) -> None:
        self._W = None


def load_missed(gold: str, classified_ids: set[str], code_ja: dict) -> tuple[list[dict], int]:
    """評価に入る動画（確認済みで、人の判定に不明を含まず、人・車だけでもない）のうち、種の判別に進まなかった動画。
    選び方は compare_gold.py の「検出の段階」と同じ。データベースは読むだけ。返すのは（その動画の一覧、評価に入る動画の総数）"""
    con = sqlite3.connect(f"file:F:/Copy/{gold}/TrapNote/data.sqlite?mode=ro", uri=True)
    try:
        items = {r[0]: r for r in con.execute("SELECT id, rel_path, station, season, status, auto_json FROM items WHERE missing=0")}
        human: dict[int, list[str]] = collections.defaultdict(list)
        for iid, sp in con.execute("SELECT item_id, species FROM judgements ORDER BY id"):
            it = items.get(iid)
            if it and it[4] == "verified" and sp != "area" and sp not in human[iid]:
                human[iid].append(sp)
    finally:
        con.close()
    out, n_elig = [], 0
    for iid, hs_all in human.items():
        hs = [h for h in hs_all if h not in NOT_ANIMAL_CODES]
        if not hs or "unknown" in hs_all:
            continue
        n_elig += 1
        if str(iid) in classified_ids:
            continue
        it = items[iid]
        sp = (json.loads(it[5]) if it[5] else {}).get("species") or []
        first = (sp[0].get("species") if isinstance(sp[0], dict) else sp[0]) if sp else ""
        out.append({"item_id": str(iid), "rel_path": it[1], "調査回": it[3], "地点": it[2],
                    "人の判定": "+".join(code_ja.get(h, h) for h in hs_all), "アプリの答え": code_ja.get(first, first or "")})
    return out, n_elig


def e2e_for_gold(gold: str, allv: list[dict], vids: list[dict], code_ja: dict, det: dict) -> dict:
    """通しの指標: 種の判別に進まなかった動画（MegaDetector の見落としなど）も、アプリの答えのまま数える。
    再現率 = その種と確定した動画のうち、AI の第 1 種が人の判定に含まれる割合。適合率 = AI がその種と答えた動画のうち、人の判定にその種が含まれる割合。
    幅は、評価に入る動画がある地点の全部を選び直す取り直し（F1 と種ごとの平均は、同じ選び直しの中で出し直す）"""
    missed, n_elig = load_missed(gold, {v["item_id"] for v in allv}, code_ja)
    assert n_elig == len(vids) + len(missed), (gold, "評価に入る動画の数が合わない", n_elig, len(vids), len(missed))
    recs = [{"地点": v["地点"], "labels": humans(v), "ans": {(rule, s): v[pre + col] for rule, pre in RULES for s, col in SYS}} for v in vids]
    for m in missed:
        recs.append({"地点": m["地点"], "labels": humans(m), "ans": {(rule, s): m["アプリの答え"] for rule, _ in RULES for s, _c in SYS}})
    first = [r["labels"][0] for r in recs]
    n_first = collections.Counter(first)
    n_cls = collections.Counter(humans(v)[0] for v in vids)
    for c, (a, b) in det.items():
        assert n_first[c] == a and n_cls[c] == b, (gold, "検出の段階の本数が合わない", c, n_first[c], a, n_cls[c], b)
    assert set(n_first) == set(det), (gold, set(n_first) ^ set(det))
    order: dict[str, int] = {}
    for r in recs:
        order.setdefault(r["地点"], len(order))
    G = len(order)
    idx = np.array([order[r["地点"]] for r in recs], dtype=int)
    rng = np.random.default_rng([20261112, zlib.crc32(f"{gold}|通し".encode("utf-8"))])
    W = rng.multinomial(G, np.full(G, 1.0 / G), size=B).astype(np.float64)

    def st(x) -> np.ndarray:
        return np.bincount(idx, weights=np.asarray(x, float), minlength=G)

    def ci(arr: np.ndarray, n: int) -> tuple[float | None, float | None]:
        if n < MIN_N or G < 2 or np.all(np.isnan(arr)):
            return None, None
        lo, hi = np.nanpercentile(arr, [2.5, 97.5])
        return float(lo), float(hi)

    classes = [c for c in det if c != EMPTY]            # 動物の区分（集計の表の順）
    is_cls = {c: np.array([f == c for f in first], float) for c in classes + [EMPTY]}
    is_ani = np.array([f != EMPTY for f in first], float)
    out = {"n": len(recs), "n_ani": int(is_ani.sum()), "n_emp": int(len(recs) - is_ani.sum()), "G": G, "classes": classes,
           "n_cls": {c: int(is_cls[c].sum()) for c in is_cls}, "missed": missed, "res": {}}
    for rule, _ in RULES:
        for s, _c in SYS:
            ans = [r["ans"][(rule, s)] for r in recs]
            okv = np.array([a in r["labels"] for a, r in zip(ans, recs)], float)
            per, Rb_all, Fb_all = {}, {}, {}
            for c in classes + [EMPTY]:
                ans_c = np.array([a == c for a in ans], float)
                tpp = np.array([a == c and c in r["labels"] for a, r in zip(ans, recs)], float)
                n, tp, na, tpa = int(is_cls[c].sum()), int((is_cls[c] * okv).sum()), int(ans_c.sum()), int(tpp.sum())
                rec = tp / n if n else float("nan")
                prec = tpa / na if na else float("nan")
                f1 = float("nan") if n == 0 else (0.0 if (na == 0 or rec + prec == 0) else 2 * rec * prec / (rec + prec))
                with np.errstate(divide="ignore", invalid="ignore"):
                    Nb, Ab = W @ st(is_cls[c]), W @ st(ans_c)
                    Rb = (W @ st(is_cls[c] * okv)) / Nb
                    Pb = (W @ st(tpp)) / Ab
                    Fb = np.where(Nb == 0, np.nan, np.where((Ab == 0) | (Rb + Pb == 0), 0.0, 2 * Rb * Pb / (Rb + Pb)))
                per[c] = {"n": n, "tp": tp, "na": na, "tpa": tpa, "rec": rec, "prec": prec, "f1": f1,
                          "rec_ci": ci(Rb, n), "prec_ci": ci(Pb, min(n, na)), "f1_ci": ci(Fb, n)}
                Rb_all[c], Fb_all[c] = Rb, Fb
            big = [c for c in classes if per[c]["n"] >= MIN_N]
            with np.errstate(divide="ignore", invalid="ignore"):
                micro_b = (W @ st(is_ani * okv)) / (W @ st(is_ani))
                all_b = (W @ st(okv)) / (W @ st(np.ones(len(recs))))
                mrec_b = np.nanmean(np.vstack([Rb_all[c] for c in classes]), axis=0)
                mf1_b = np.nanmean(np.vstack([Fb_all[c] for c in classes]), axis=0)
                mf1_big_b = np.nanmean(np.vstack([Fb_all[c] for c in big]), axis=0) if big else np.array([np.nan])
            k_ani = int((is_ani * okv).sum())
            out["res"][(rule, s)] = {
                "per": per, "big": big,
                "micro": (k_ani, out["n_ani"], k_ani / out["n_ani"]) + ci(micro_b, out["n_ani"]),
                "all": (int(okv.sum()), len(recs), okv.sum() / len(recs)) + ci(all_b, len(recs)),
                "macro_rec": (float(np.mean([per[c]["rec"] for c in classes])),) + ci(mrec_b, MIN_N),
                "macro_f1": (float(np.mean([per[c]["f1"] for c in classes])),) + ci(mf1_b, MIN_N),
                "macro_f1_big": ((float(np.mean([per[c]["f1"] for c in big])),) + ci(mf1_big_b, MIN_N)) if big else (float("nan"), None, None),
                "boot": {"micro": micro_b, "macro_f1": mf1_b, "macro_f1_big": mf1_big_b, "all": all_b, "emp_rec": Rb_all[EMPTY]}}
    # 同じ選び直しの中での、モデルどうしの差（後 − 前）
    out["diff"] = {}
    for rule, _ in RULES:
        for a, b in PAIRS:
            ra, rb = out["res"][(rule, a)], out["res"][(rule, b)]
            d = {}
            for key, pa, pb in (("micro", ra["micro"][2], rb["micro"][2]), ("macro_f1", ra["macro_f1"][0], rb["macro_f1"][0]),
                                ("macro_f1_big", ra["macro_f1_big"][0], rb["macro_f1_big"][0]), ("all", ra["all"][2], rb["all"][2]),
                                ("emp_rec", ra["per"][EMPTY]["rec"], rb["per"][EMPTY]["rec"])):
                d[key] = (pb - pa,) + ci(rb["boot"][key] - ra["boot"][key], MIN_N)
            out["diff"][(rule, a, b)] = d
    return out


def pct_s(k: int, n: int) -> str:
    return f"{100 * k / n:.1f}%"


def sgn(x: float, nd: int = 2) -> str:
    return f"{x:+.{nd}f}".replace("-", "−")


def rng_s(lo: float | None, hi: float | None, nd: int = 1, signed: bool = False) -> str:
    if lo is None:
        return "—"
    return f"{sgn(lo, nd)}〜{sgn(hi, nd)}" if signed else f"{lo:.{nd}f}〜{hi:.{nd}f}"


def p_s(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"       # 小数第 3 位まで出す（0.125 のような端の値を、丸め方で迷わせないため）


def tbl(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows] + [""]


def read_rows(path: str) -> list[list[str]]:
    return list(csv.reader(open(path, encoding="utf-8-sig")))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--out=")]
    out_dir = next((a[6:] for a in sys.argv[1:] if a.startswith("--out=")), HERE + "/paper_tables").replace("\\", "/")
    R = (args[0] if args else sorted(glob.glob(HERE + "/results_*"))[-1]).replace("\\", "/").rstrip("/")
    stamp = os.path.basename(R).replace("results_", "")
    set_models(R)
    # 前に作った表は上書きしない: 同じ集計からもう一度作るときは、名前に _2、_3 … を付ける
    ver = 1
    while True:
        sfx = "" if ver == 1 else f"_{ver}"
        outs = {k: f"{out_dir}/論文用の表_{stamp}{sfx}{s}" for k, s in (("md", ".md"), ("acc", "_正解率.csv"), ("diff", "_差.csv"),
                                                                       ("e2e", "_通しの指標.csv"), ("missed", "_種の判別に進まなかった動画.csv"))}
        if not any(os.path.exists(p) for p in outs.values()):
            break
        ver += 1
    e2e: dict[str, dict] = {}
    name_of = {n: n for n, _ in SYS}
    col_of = dict(SYS)
    sp_rows = list(csv.DictReader(open(SPECIES_CSV, encoding="utf-8-sig")))
    en_sci = {r["name_ja"]: (r["name_en"], r["name_sci"]) for r in sp_rows}
    code_ja = {r["code"]: r["name_ja"] for r in sp_rows}
    code_ja.update(FIXED_JA)
    infos = {lab: model_info(m) for lab, m in MODELS}
    train_sets = [st for _, st in infos.values()]
    assert all(st == train_sets[0] for st in train_sets), "学習に使った地点がモデルごとに違う。この台本は、同じ場合だけを扱う"
    train_st = train_sets[0]
    set0 = infos[MODELS[0][0]][0].get("settings")
    no_mix = lambda s: {k: v for k, v in (s or {}).items() if k != "train_base_mix"}       # 混ぜる割合は、軽い方式（混ぜない）だけが違う（方式 B の学習には関係しない設定）
    assert all(no_mix(inf.get("settings")) == no_mix(set0) for inf, _ in infos.values()), "学習の細かい設定がモデルごとに違う。「表の読み方」の文を直すこと"
    assert all(inf.get("settings") == set0 for lab, (inf, _) in infos.items() if lab not in {a[0] for a in LIGHTS0}), "学習の細かい設定がモデルごとに違う。「表の読み方」の文を直すこと"
    mixes = {infos[lab][0].get("base_mix") for lab, _, _ in LIGHTS}
    assert len(mixes) == 1, ("軽い方式の、元の SpeciesNet の確率を混ぜる割合がモデルごとに違う", mixes)
    base_mix = mixes.pop()
    assert all(infos[a[0]][0].get("base_mix") == 0.0 and infos[a[0]][0]["settings"].get("train_base_mix") == 0.0 for a in LIGHTS0), "軽い方式（混ぜない）の、混ぜる割合が 0 でない"
    n_models, n_light = len(MODELS), len(LIGHTS)
    n_models_s = f"{n_models} つ" if n_models < 10 else f"{n_models} 個"       # 数の言い方（10 からは「個」）
    light_names = "・".join(lab for lab, _, _ in LIGHTS)
    light0_names = "・".join(a[0] for a in LIGHTS0)

    def used_videos(m: str) -> dict:
        """学習に使った動画（train_items.csv で、枠を 1 枚以上使ったもの）: (プロジェクト, 動画) -> (人の判定, 使った枠の番号と区分)"""
        with open(f"{MODELS_DIR}/{m}/train_items.csv", encoding="utf-8-sig", newline="") as f:
            return {(r["Project"], r["RelPath"]): (r["Human"], tuple((i, c) for i, c in enumerate(r["CropLabels"].split(";")) if c != "-"))
                    for r in csv.DictReader(f) if int(r["CropsUsed"] or 0) > 0}

    mat = {}                             # 軽い方式ごとに、同じ材料の方式 B との、学習に使った動画の違い
    for lab, m, partner in LIGHTS:
        ua, ub = used_videos(m), used_videos(dict(MODELS)[partner])
        both = set(ua) & set(ub)
        oa, ob, df = set(ua) - set(ub), set(ub) - set(ua), {k for k in both if ua[k] != ub[k]}
        multi = lambda ks, u: sum(1 for k in ks if ";" in u[k][0])          # 人の判定に種が 2 つ以上ある動画（学習の一覧では「;」で区切ってある）
        mat[lab] = {"n_a": len(ua), "n_b": len(ub), "only_a": len(oa), "only_b": len(ob), "diff": len(df),
                    "only_a_by": collections.Counter(k[0] for k in oa), "only_b_by": collections.Counter(k[0] for k in ob), "diff_by": collections.Counter(k[0] for k in df),
                    "only_a_multi": multi(oa, ua), "only_b_multi": multi(ob, ub), "diff_multi": multi(df, ua)}
    for lab0, m0, _, labp in LIGHTS0:    # 軽い方式（混ぜない）は、同じ回の軽い方式と、学習に使った動画・枠・分類器の重みが同じであること（違うのは、判定のときに混ぜる割合だけ）
        mp = dict(MODELS)[labp]
        with np.load(f"{MODELS_DIR}/{m0}/head.npz", allow_pickle=False) as z0, np.load(f"{MODELS_DIR}/{mp}/head.npz", allow_pickle=False) as zp:
            assert all(np.array_equal(z0[k], zp[k]) for k in ("W", "b", "mu", "sd", "classes")), (lab0, "分類器の重みが、同じ回の軽い方式と違う")
        assert used_videos(m0) == used_videos(mp) and infos[lab0][0]["counts"] == infos[labp][0]["counts"], (lab0, "学習に使った動画が、同じ回の軽い方式と違う")

    cells: dict[tuple, dict] = {}        # (gold, 数え方, 行, モデル) -> 正解率のます
    counts: dict[tuple, tuple] = {}      # (gold, 数え方, 数えたもの, モデル) -> (本数, 分母)
    diffs: dict[tuple, dict] = {}        # (gold, 数え方, 行, A, B) -> 差のます
    meta: dict[tuple, dict] = {}         # (gold, 行) -> 本数、地点数
    order: dict[str, list[str]] = {}     # gold -> 行の順
    info_g: dict[str, dict] = {}
    checks: list[str] = []
    if LIGHTS0:
        checks.append(f"軽い方式（混ぜない）の {len(LIGHTS0)} つは、同じ回の軽い方式と、分類器の重み（head.npz）と学習に使った動画・枠が同じ")

    for gold in GOLDS:
        allv = list(csv.DictReader(open(f"{R}/videos_{gold}.csv", encoding="utf-8-sig")))
        for _, col in SYS:
            for pre in ("", "線なし "):
                assert pre + col in allv[0], f"{gold}: 列がない: {pre + col}"
        vids = [v for v in allv if v["対象"] == "1"]
        ani = [v for v in vids if v["人の判定"] != EMPTY]
        emp = [v for v in vids if v["人の判定"] == EMPTY]
        assert all(humans(v) for v in vids)
        info_g[gold] = {"n": len(vids), "ani": len(ani), "emp": len(emp), "st": len({v["地点"] for v in vids}),
                        "seasons": sorted({v["調査回"] for v in vids}),
                        "st_train": len({v["地点"] for v in vids if v["地点"] in train_st}),
                        "n_train": sum(1 for v in vids if v["地点"] in train_st)}
        # 検出の段階: 確認済みの動画のうち、動物の枠が付いて種を判別した本数（集計の detected_<gold>.csv）
        det = {r[0]: (int(r[1]), int(r[2])) for r in read_rows(f"{R}/detected_{gold}.csv")[1:]}
        assert sum(b for sp, (a, b) in det.items() if sp != EMPTY) == len(ani) and det.get(EMPTY, (0, 0))[1] == len(emp), (gold, "detected")
        info_g[gold]["det"] = det
        s = json.load(open(f"F:/Copy/{gold}/TrapNote/project.json", encoding="utf-8")).get("settings") or {}
        info_g[gold]["rule"] = (s.get("species_p1"), s.get("species_empty_p"), s.get("animal_min_frames"))
        cmp_rows = read_rows(f"{R}/compare_{gold}.csv")
        sp_order = [r[0] for r in cmp_rows[1:] if r[0] not in TOTAL_ROWS]
        subs: list[tuple[str, list[dict]]] = []
        if gold not in SPECIES_ONLY:
            subs += [("動物", ani), (EMPTY, emp), ("合計", vids)]
            tr = [v for v in ani if v["地点"] in train_st]
            ot = [v for v in ani if v["地点"] not in train_st]
            if tr and ot:
                subs += [("動物（学習に使った地点）", tr), ("動物（それ以外の地点）", ot)]
        by_sp = collections.defaultdict(list)
        for v in vids:
            by_sp[humans(v)[0]].append(v)
        assert set(by_sp) == set(sp_order), (gold, set(by_sp) ^ set(sp_order))
        for sp in sp_order:
            if sp != EMPTY or gold in SPECIES_ONLY:
                subs.append(("種：" + sp, by_sp[sp]))
        order[gold] = [n for n, _ in subs]
        for rname, S in subs:
            sub = Subset(gold, rname, S)
            meta[(gold, rname)] = {"n": sub.n, "G": sub.G}
            for rule, pre in RULES:
                xs = {}
                for sname, col in SYS:
                    x = np.array([correct(v, pre + col) for v in S], float)
                    xs[sname] = x
                    k = int(x.sum())
                    lo, hi = sub.ci(x)
                    wl, wh = wilson(k, sub.n)
                    cells[(gold, rule, rname, sname)] = {"k": k, "n": sub.n, "pct": 100 * k / sub.n, "lo": lo, "hi": hi, "wl": wl, "wh": wh}
                if sub.n >= MIN_N and sub.G >= 2:
                    for a, b in PAIRS:
                        xa, xb = xs[a], xs[b]
                        up, down = int(((xb == 1) & (xa == 0)).sum()), int(((xb == 0) & (xa == 1)).sum())
                        lo, hi = sub.ci(xb - xa)
                        diffs[(gold, rule, rname, a, b)] = {"pa": 100 * xa.mean(), "pb": 100 * xb.mean(), "d": 100 * (xb - xa).mean(), "lo": lo, "hi": hi,
                                                           "up": up, "down": down, "p": mcnemar(up, down)}
            sub.free()
        for rule, pre in RULES:
            for sname, col in SYS:
                c = pre + col
                counts[(gold, rule, "動物の動画に「動物なし」", sname)] = (sum(1 for v in ani if v[c] == EMPTY), len(ani))
                counts[(gold, rule, "動物なしの動画に種の答え", sname)] = (sum(1 for v in emp if v[c] != EMPTY and v[c] not in NOANS), len(emp))
                counts[(gold, rule, "動物の動画で答えなし", sname)] = (sum(1 for v in ani if v[c] in NOANS), len(ani))
                counts[(gold, rule, "動物なしの動画で答えなし", sname)] = (sum(1 for v in emp if v[c] in NOANS), len(emp))

        # ---- 通しの指標（種の判別に進まなかった動画も数える）----
        e2e[gold] = e2e_for_gold(gold, allv, vids, code_ja, det)
        for rule, _ in RULES:
            for sname, _c in SYS:
                r = e2e[gold]["res"][(rule, sname)]
                assert r["micro"][0] == sum(cells[(gold, rule, "種：" + c, sname)]["k"] for c in e2e[gold]["classes"] if (gold, rule, "種：" + c, sname) in cells), (gold, rule, sname)
        checks.append(f"{gold}: 検出の段階の表（detected）と、種ごとの確認済みの本数・種を判別した本数が同じ（評価に入る動画 {e2e[gold]['n']:,} 本）")

        # ---- 確かめ: 集計の表と同じ本数か ----
        def col_index(header: list[str], sname: str, what: str) -> int | None:
            key = (col_of[sname] + what).replace(" ", "")
            hit = [i for i, h in enumerate(header) if h.replace(" ", "") == key]
            return hit[0] if hit else None

        n_chk = 0
        for rule, fn, extra in (("アプリの規則", f"compare_{gold}.csv", "答えなし"), ("線を使わない数え方", f"top1_{gold}.csv", "動物なしと答えた")):
            rows = read_rows(f"{R}/{fn}")
            hd = rows[0]
            ex_sum = collections.Counter()
            for r in rows[1:]:
                sp = r[0]
                S = ani if sp == "動物（動物なしを除く）" else (vids if sp == "合計" else by_sp[sp])
                assert int(r[1]) == len(S), (gold, fn, sp, r[1], len(S))
                for sname, col in SYS:
                    i = col_index(hd, sname, "正解")
                    assert i is not None, (fn, sname)
                    k = sum(correct(v, ("" if rule == "アプリの規則" else "線なし ") + col) for v in S)
                    assert int(r[i]) == k, (gold, fn, sp, sname, r[i], k)
                    assert r[i + 1] == f"{100 * k / len(S):.1f}", (gold, fn, sp, sname, r[i + 1])
                    n_chk += 1
                    j = col_index(hd, sname, extra)
                    if j is not None:
                        c = ("" if rule == "アプリの規則" else "線なし ") + col
                        mine = sum(1 for v in S if (v[c] in NOANS if extra == "答えなし" else v[c] == EMPTY))
                        assert int(r[j]) == mine, (gold, fn, sp, sname, extra, r[j], mine)
                        if sp not in TOTAL_ROWS:
                            ex_sum[(sname, sp == EMPTY)] += mine
                        n_chk += 1
            for (sname, is_emp), tot in ex_sum.items():
                if extra == "答えなし":
                    key = "動物なしの動画で答えなし" if is_emp else "動物の動画で答えなし"
                    assert counts[(gold, rule, key, sname)][0] == tot, (gold, key, sname, tot)
                elif not is_emp:
                    assert counts[(gold, rule, "動物の動画に「動物なし」", sname)][0] == tot, (gold, sname, tot)
        checks.append(f"{gold}: 種ごとの表（compare・top1）と {n_chk} か所が同じ")
        # summary.md の全体の行
        sm = open(f"{R}/summary.md", encoding="utf-8").read().split("\n## ")
        sec = next(s for s in sm if s.startswith(gold + "\n"))
        m1 = re.search(r"確認済み (\d+) 本、SpeciesNet の npz がある動画 (\d+) 本", sec)
        m2 = re.search(r"対象 (\d+) 本（外したもの: (.*?)）", sec)
        assert m1 and m2 and int(m2[1]) == len(vids), (gold, "summary.md の対象の本数")
        info_g[gold].update(verified=int(m1[1]), npz=int(m1[2]), dropped=m2[2])
        if gold not in SPECIES_ONLY:
            seen = collections.Counter()
            n_chk = 0
            hdr: list[str] = []                          # いま読んでいる表の見出し（列の並びは集計が決める。モデルの名前で列を探す）
            for ln in sec.split("\n"):
                cs = [c.strip() for c in ln.strip().strip("|").split("|")]
                if ln.startswith("|") and len(cs) >= 2 and cs[0] == "" and cs[1] == "本数":
                    hdr = cs
                    assert sorted(hdr[2:]) == sorted(col for _, col in SYS), (gold, "summary.md の列がこの台本のモデルと違う", hdr)
                if ln.startswith("|") and cs[0] in ("動物", EMPTY, "合計") and len(cs) == 2 + len(SYS):
                    rule = RULES[seen[cs[0]]][0]
                    seen[cs[0]] += 1
                    assert int(cs[1]) == meta[(gold, cs[0])]["n"], (gold, ln)
                    for sname, col in SYS:
                        s = cs[hdr.index(col)]
                        c = cells[(gold, rule, cs[0], sname)]
                        assert s == pct_s(c["k"], c["n"]), (gold, rule, cs[0], sname, s, pct_s(c["k"], c["n"]))
                        n_chk += 1
                if ln.startswith("| 動物がいるのに「動物なし」と答えた本数"):
                    for sname, col in SYS:
                        s = cs[hdr.index(col)]
                        assert int(s) == counts[(gold, "線を使わない数え方", "動物の動画に「動物なし」", sname)][0], (gold, ln)
                        n_chk += 1
            assert seen["動物"] == 2 and seen[EMPTY] == 2 and seen["合計"] == 2, (gold, seen)
            checks.append(f"{gold}: summary.md の全体の行と {n_chk} か所が同じ")
            st_rows = read_rows(f"{R}/sites_{gold}.csv")
            n_chk = 0
            for r in st_rows[1:]:
                rname = "動物（学習に使った地点）" if r[1] == "学習に使った地点" else "動物（それ以外の地点）"
                lab = next(lab for lab, m in MODELS if m == r[0])
                if (gold, rname) not in meta:
                    assert (int(r[2]) == 0) or (int(r[2]) == len(ani)), (gold, r)
                    continue
                assert int(r[2]) == meta[(gold, rname)]["n"] and int(r[3]) == meta[(gold, rname)]["G"], (gold, r)
                for sname, s in ((SN_OPEN, r[4]), (SN_CLOSED, r[5]), (lab, r[6])):
                    c = cells[(gold, "アプリの規則", rname, sname)]
                    assert s == f"{c['pct']:.1f}", (gold, r, sname, s, c["pct"])
                    n_chk += 1
            if n_chk:
                checks.append(f"{gold}: 地点で分けた表（sites）と {n_chk} か所が同じ")

    # ---------- 出力 ----------
    os.makedirs(out_dir, exist_ok=True)
    acc_rows = []
    for gold in GOLDS:
        for rname in order[gold]:
            sp = rname[2:] if rname.startswith("種：") else ""
            for rule, _ in RULES:
                for sname, col in SYS:
                    c = cells[(gold, rule, rname, sname)]
                    acc_rows.append([gold, rule, rname, "正解", en_sci.get(sp, ("", ""))[0], en_sci.get(sp, ("", ""))[1], c["n"], meta[(gold, rname)]["G"], sname, col,
                                     c["k"], f"{c['pct']:.4f}", "" if c["lo"] is None else f"{c['lo']:.4f}", "" if c["hi"] is None else f"{c['hi']:.4f}",
                                     f"{c['wl']:.4f}", f"{c['wh']:.4f}"])
        for (g, rule, what, sname), (k, n) in counts.items():
            if g == gold:
                acc_rows.append([gold, rule, "動物" if what.startswith("動物の") else EMPTY, what, "", "", n, "", sname, col_of[sname], k, f"{100 * k / n:.4f}" if n else "", "", "", "", ""])
    with open(outs["acc"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["gold", "数え方", "行（対象の動画）", "数えたもの", "英名", "学名", "本数", "地点数", "モデル", "モデルの名前", "数えた本数", "%",
                    "幅の下（地点ごとの取り直し）", "幅の上（地点ごとの取り直し）", "Wilson の下", "Wilson の上"])
        w.writerows(acc_rows)
    with open(outs["diff"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["gold", "数え方", "行（対象の動画）", "本数", "地点数", "A", "B", "A の正解率（%）", "B の正解率（%）", "差 B − A（ポイント）",
                    "幅の下", "幅の上", "不正解 → 正解（本）", "正解 → 不正解（本）", "McNemar の p"])
        for gold in GOLDS:
            for rname in order[gold]:
                for rule, _ in RULES:
                    for a, b in PAIRS:
                        d = diffs.get((gold, rule, rname, a, b))
                        if d:
                            w.writerow([gold, rule, rname, meta[(gold, rname)]["n"], meta[(gold, rname)]["G"], a, b, f"{d['pa']:.4f}", f"{d['pb']:.4f}", f"{d['d']:.4f}",
                                        f"{d['lo']:.4f}", f"{d['hi']:.4f}", d["up"], d["down"], f"{d['p']:.6g}"])

    def num(x, nd):
        return "" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"

    with open(outs["e2e"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["gold", "数え方", "行", "英名", "学名", "指標", "本数（分母）", "数えた本数（分子）", "モデル", "モデルの名前", "値（再現率・適合率・正答率は %、F1 は 0〜1）", "幅の下", "幅の上"])
        for gold in GOLDS:
            E = e2e[gold]
            for rule, _ in RULES:
                for sname, col in SYS:
                    r = E["res"][(rule, sname)]
                    for c in E["classes"] + [EMPTY]:
                        p = r["per"][c]
                        en, sci = en_sci.get(c, ("", ""))
                        w.writerow([gold, rule, c, en, sci, "通しの再現率", p["n"], p["tp"], sname, col, num(100 * p["rec"], 4)] + [num(None if x is None else 100 * x, 4) for x in p["rec_ci"]])
                        if gold in SPECIES_ONLY:
                            continue                    # 種ごとに本数を決めて選んだ gold では、適合率と F1 は意味を持たない
                        w.writerow([gold, rule, c, en, sci, "適合率", p["na"], p["tpa"], sname, col, num(100 * p["prec"], 4)] + [num(None if x is None else 100 * x, 4) for x in p["prec_ci"]])
                        w.writerow([gold, rule, c, en, sci, "F1", p["n"], "", sname, col, num(p["f1"], 6)] + [num(x, 6) for x in p["f1_ci"]])
                    if gold in SPECIES_ONLY:
                        continue
                    k, n, v, lo, hi = r["micro"]
                    w.writerow([gold, rule, "動物（全体）", "", "", "通しの再現率", n, k, sname, col, num(100 * v, 4), num(None if lo is None else 100 * lo, 4), num(None if hi is None else 100 * hi, 4)])
                    v, lo, hi = r["macro_rec"]
                    w.writerow([gold, rule, f"動物（{len(E['classes'])} 種の平均）", "", "", "通しの再現率", "", "", sname, col, num(100 * v, 4), num(None if lo is None else 100 * lo, 4), num(None if hi is None else 100 * hi, 4)])
                    v, lo, hi = r["macro_f1"]
                    w.writerow([gold, rule, f"動物（{len(E['classes'])} 種の平均）", "", "", "F1", "", "", sname, col, num(v, 6), num(lo, 6), num(hi, 6)])
                    v, lo, hi = r["macro_f1_big"]
                    w.writerow([gold, rule, f"動物（確認済みが {MIN_N} 本以上の {len(r['big'])} 種の平均）", "", "", "F1", "", "", sname, col, num(v, 6), num(lo, 6), num(hi, 6)])
                    k, n, v, lo, hi = r["all"]
                    w.writerow([gold, rule, "全動画（動物と動物なし）", "", "", "通しの正答率", n, k, sname, col, num(100 * v, 4), num(None if lo is None else 100 * lo, 4), num(None if hi is None else 100 * hi, 4)])
                if gold in SPECIES_ONLY:
                    continue
                for a, b in PAIRS:                       # モデルどうしの差（後 − 前。同じ選び直しの中で出した幅）
                    d = E["diff"][(rule, a, b)]
                    for key, label, what, sc, nd in (("micro", "動物（全体）", "通しの再現率の差（ポイント）", 100, 4), ("macro_f1", f"動物（{len(E['classes'])} 種の平均）", "F1 の差", 1, 6),
                                                     ("macro_f1_big", f"動物（確認済みが {MIN_N} 本以上の種の平均）", "F1 の差", 1, 6), ("emp_rec", EMPTY, "通しの再現率の差（ポイント）", 100, 4),
                                                     ("all", "全動画（動物と動物なし）", "通しの正答率の差（ポイント）", 100, 4)):
                        v, lo, hi = d[key]
                        w.writerow([gold, rule, label, "", "", what, "", "", f"{a} → {b}", f"{col_of[a]} → {col_of[b]}", num(sc * v, nd), num(None if lo is None else sc * lo, nd), num(None if hi is None else sc * hi, nd)])
    with open(outs["missed"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["gold", "item_id", "rel_path", "調査回", "地点", "人の判定", "アプリの答え"])
        for gold in GOLDS:
            for m in e2e[gold]["missed"]:
                w.writerow([gold, m["item_id"], m["rel_path"], m["調査回"], m["地点"], m["人の判定"], m["アプリの答え"]])

    H = ["", "本数"] + [HEAD.get(n, n) for n, _ in SYS]
    md = [f"# 論文用の表（{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]} の集計から）", "",
          f"作った日時：{datetime.datetime.now():%Y-%m-%d %H:%M}。もとの集計：`{R.replace('/', chr(92))}`。作った台本：`{os.path.abspath(__file__)}`（集計の出力を読むだけです）。", "",
          f"SpeciesNet（元のまま）と、{n_models_s}の学習モデル（本体を学習し直す方式 B の {len(B_MODELS)} つと、軽い方式（方式 A）の {n_light} つ"
          + (f"、軽い方式で元の SpeciesNet の確率を混ぜないようにした {len(LIGHTS0)} つ" if LIGHTS0 else "") + "）の結果を並べたものです。"
          "数字は全部、集計の出力から台本で出しています。手で写した数字はありません。", "",
          "## 表 1　正解率", ""]
    rows = [["**学習に使った材料**"] + [""] * (len(H) - 1)]
    ani_cr = {lab: sum(n for c, n in infos[lab][0]["counts"].items() if c != "empty") for lab, _ in MODELS}
    emp_cr = {lab: infos[lab][0]["counts"].get("empty", 0) for lab, _ in MODELS}
    rows.append(["動物の枠（枚）", "", "—", "—"] + [f"{ani_cr[lab]:,}" for lab, _ in MODELS])
    rows.append(["動物なしの枠（枚）", "", "—", "—"] + [f"{emp_cr[lab]:,}" for lab, _ in MODELS])

    def acc_line(gold, rule, rname, label):
        return [label, f"{meta[(gold, rname)]['n']:,}"] + [pct_s(cells[(gold, rule, rname, s)]["k"], cells[(gold, rule, rname, s)]["n"]) for s, _ in SYS]

    def cnt_line(gold, rule, what, label):
        return [label, f"{counts[(gold, rule, what, SYS[0][0])][1]:,}"] + [f"{counts[(gold, rule, what, s)][0]:,} 本" for s, _ in SYS]

    for gold in GOLDS:
        g = info_g[gold]
        if gold in SPECIES_ONLY:
            rows.append([f"**{gold}**（{g['n']:,} 本、{g['st']} 地点。種ごとの行だけ）"] + [""] * (len(H) - 1))
            for rule, short in (("アプリの規則", "アプリの規則"), ("線を使わない数え方", "線を使わない")):
                for sp in MAIN_SPECIES:
                    rows.append(acc_line(gold, rule, "種：" + sp, f"{short}　{sp}"))
            rows.append(cnt_line(gold, "線を使わない数え方", "動物の動画に「動物なし」", "線を使わない　動物がいるのに「動物なし」"))
            continue
        rows.append([f"**{gold}**（動物 {g['ani']:,} 本、動物なし {g['emp']:,} 本、{g['st']} 地点）"] + [""] * (len(H) - 1))
        for rname in ("動物", EMPTY, "合計", "動物（学習に使った地点）", "動物（それ以外の地点）"):
            if (gold, rname) in meta:
                rows.append(acc_line(gold, "アプリの規則", rname, "アプリの規則　" + rname))
        rows.append(cnt_line(gold, "アプリの規則", "動物の動画で答えなし", "アプリの規則　動物の動画で答えなし"))
        rows.append(cnt_line(gold, "アプリの規則", "動物なしの動画で答えなし", "アプリの規則　動物なしの動画で答えなし"))
        for rname in ("動物", EMPTY, "合計"):
            rows.append(acc_line(gold, "線を使わない数え方", rname, "線を使わない　" + rname))
        rows.append(cnt_line(gold, "線を使わない数え方", "動物の動画に「動物なし」", "線を使わない　動物がいるのに「動物なし」"))
        rows.append(cnt_line(gold, "線を使わない数え方", "動物なしの動画に種の答え", "線を使わない　動物なしの動画に種の答え"))
    md += tbl(H, rows)

    md += ["## 表 2　表 1 の正解率の 95% の幅（地点ごとの取り直し）", ""]
    rows = []
    for gold in GOLDS:
        rows.append([f"**{gold}**"] + [""] * (len(H) + 1 - 1))
        names = ["種：" + sp for sp in MAIN_SPECIES] if gold in SPECIES_ONLY else [n for n in ("動物", EMPTY, "合計", "動物（学習に使った地点）", "動物（それ以外の地点）") if (gold, n) in meta]
        for rule, short in (("アプリの規則", "アプリの規則"), ("線を使わない数え方", "線を使わない")):
            for rname in names:
                if rule != "アプリの規則" and "地点）" in rname:
                    continue
                rows.append([f"{short}　{rname.replace('種：', '')}", f"{meta[(gold, rname)]['n']:,}", str(meta[(gold, rname)]["G"])]
                            + [rng_s(cells[(gold, rule, rname, s)]["lo"], cells[(gold, rule, rname, s)]["hi"]) for s, _ in SYS])
    md += tbl(["", "本数", "地点数"] + [HEAD.get(n, n) for n, _ in SYS], rows)

    def diff_s(d):
        return "—" if d is None else f"{sgn(d['d'])}（{rng_s(d['lo'], d['hi'], 2, True)}）"

    for no, rname, title in ((3, "動物", "動物の正解率の差"), (4, EMPTY, "動物なしの正解率の差")):
        md += [f"## 表 {no}　{title}（同じ動画どうし。ポイント。かっこの中は 95% の幅）", ""]
        rows = []
        for a, b in PAIRS_MD:
            rows.append([f"{HEAD.get(a, a)} → {b}"] + [diff_s(diffs.get((gold, rule, rname, a, b))) for gold in GOLDS if gold not in SPECIES_ONLY for rule, _ in RULES])
        md += tbl(["比べる 2 つ（前 → 後）"] + [f"{gold}　{short}" for gold in GOLDS if gold not in SPECIES_ONLY for short in ("アプリの規則", "線を使わない")], rows)

    def sp_diff_s(d):
        return "—" if d is None else f"{sgn(d['d'], 1)}（{d['up']} / {d['down']}、{p_s(d['p'])}）"

    gs = "gold_species"
    md += ["## 表 5　gold_species：学習したモデルと SpeciesNet（17 の選択肢に絞る）の差（アプリの規則）", "",
           "ますの中は、差（ポイント）と、かっこの中に「SpeciesNet だけ不正解の本数 / モデルだけ不正解の本数、McNemar の検定の p」です。", ""]
    md += tbl(["人の判定", "本数"] + [m for m, _ in MODELS],
              [[sp, f"{meta[(gs, '種：' + sp)]['n']:,}"] + [sp_diff_s(diffs.get((gs, "アプリの規則", "種：" + sp, SN_CLOSED, m))) for m, _ in MODELS] for sp in MAIN_SPECIES])
    md += ["## 表 6　gold_species：学習したモデルどうしの差（アプリの規則）", "",
           "ますの中は、差（ポイント）と、かっこの中に「後のモデルだけ正解の本数 / 前のモデルだけ正解の本数、McNemar の検定の p」です。", ""]
    md += tbl(["人の判定", "本数"] + [f"{a} → {b}" for a, b in P6],
              [[sp, f"{meta[(gs, '種：' + sp)]['n']:,}"] + [sp_diff_s(diffs.get((gs, "アプリの規則", "種：" + sp, a, b))) for a, b in P6] for sp in MAIN_SPECIES])

    md += ["## 表 7　学習に使った枠の数（1 本の動画から、種ごとに 1 枚）", ""]
    codes = sorted({c for lab, _ in MODELS for c in infos[lab][0]["counts"]}, key=lambda c: (c == "empty", [r["code"] for r in sp_rows].index(c) if c in code_ja and c != "empty" else 999))
    rows = [[code_ja.get(c, c), en_sci.get(code_ja.get(c, c), ("", ""))[0], en_sci.get(code_ja.get(c, c), ("", ""))[1]] + [f"{infos[lab][0]['counts'].get(c, 0):,}" for lab, _ in MODELS] for c in codes]
    rows.append(["動物の枠の合計", "", ""] + [f"{ani_cr[lab]:,}" for lab, _ in MODELS])
    rows.append(["枠の合計", "", ""] + [f"{ani_cr[lab] + emp_cr[lab]:,}" for lab, _ in MODELS])
    md += tbl(["クラス", "英名", "学名"] + [f"{lab}（{m}）" for lab, m in MODELS], rows)

    rules = {info_g[g]["rule"] for g in GOLDS}
    assert len(rules) == 1, ("gold ごとにアプリの規則の設定が違う", rules)
    p1, empty_p, min_frames = rules.pop()
    md += ["## 表 8　検出の段階（確認済みの動画のうち、動物の枠が付いて種を判別した本数）", "",
           f"表 1〜6 は、右から 2 つ目の列の動画だけを数えています。動物の枠が {min_frames} フレーム以上に付いた動画だけが、種の判別に進みます。"
           "それ以外の動画は、種の判別に進まないので（アプリの答えは「動物なし」「不明」「人」などになります）、表 1〜6 には入りません。", "",
           "「動物なし」の行は、動物がいないのに MegaDetector が動物の枠を付けた動画です。表 1 の「動物なし」の正解率は、この動画での正解率です。", ""]
    rows = []
    for gold in GOLDS:
        det = info_g[gold]["det"]
        if gold in SPECIES_ONLY:
            rr = [(sp, det[sp]) for sp in MAIN_SPECIES]
        else:
            rr = [("動物", (sum(a for sp, (a, b) in det.items() if sp != EMPTY), sum(b for sp, (a, b) in det.items() if sp != EMPTY))), (EMPTY, det[EMPTY])]
        rows += [[f"{gold}　{lab}", f"{a:,}", f"{b:,}", pct_s(b, a)] for lab, (a, b) in rr]
    md += tbl(["", "確認済みの本数", "動物の枠が付いて種を判別した本数", "割合"], rows)

    def pv(val, lo=None, hi=None, f1=False):
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return "—"
        if f1:
            return f"{val:.3f}" + ("" if lo is None else f"（{lo:.3f}〜{hi:.3f}）")
        return f"{100 * val:.1f}%" + ("" if lo is None else f"（{100 * lo:.1f}〜{100 * hi:.1f}）")

    R9 = "アプリの規則"
    md += ["## 表 9　通しの指標（アプリの規則。かっこの中は 95% の幅）", "",
           "表 1〜6 と違って、種の判別に進まなかった動画（MegaDetector の見落としなど）も数えます。その動画は、アプリの答え（「動物なし」「不明」「人」など）のまま数えるので、動物の動画なら誤りになります。", ""]
    rows = []
    for gold in GOLDS:
        if gold in SPECIES_ONLY:
            continue
        E = e2e[gold]
        nc = len(E["classes"])
        rs = [E["res"][(R9, s)] for s, _ in SYS]
        rows.append([f"**{gold}**（評価に入る動画 {E['n']:,} 本、{E['G']} 地点）"] + [""] * (len(H) - 1))
        rows.append(["動物の通しの再現率", f"{E['n_ani']:,}"] + [pv(*r["micro"][2:]) for r in rs])
        rows.append(["種ごとの通しの再現率の平均", f"{nc} 種"] + [pv(*r["macro_rec"]) for r in rs])
        rows.append(["種ごとの F1 の平均（macro F1）", f"{nc} 種"] + [pv(*r["macro_f1"], f1=True) for r in rs])
        if len(rs[0]["big"]) != nc:
            rows.append([f"macro F1（確認済みが {MIN_N} 本以上の種だけ）", f"{len(rs[0]['big'])} 種"] + [pv(*r["macro_f1_big"], f1=True) for r in rs])
        rows.append(["動物なしの通しの再現率", f"{E['n_emp']:,}"] + [pv(r["per"][EMPTY]["rec"], *r["per"][EMPTY]["rec_ci"]) for r in rs])
        rows.append(["動物なしの適合率", ""] + [pv(r["per"][EMPTY]["prec"], *r["per"][EMPTY]["prec_ci"]) for r in rs])
        rows.append(["全動画での通しの正答率", f"{E['n']:,}"] + [pv(*r["all"][2:]) for r in rs])
    md += tbl(H, rows)
    md += ["## 表 10　種ごとの通しの再現率・適合率・F1（アプリの規則）", "",
           "ますの中は「通しの再現率（%） / 適合率（%） / F1」です。幅は CSV にあります。「—」は、AI がその種と答えた動画が 1 本もないことを表します。", ""]
    for gold in GOLDS:
        if gold in SPECIES_ONLY:
            continue
        E = e2e[gold]
        rows = []
        for c in E["classes"]:
            cellv = []
            for s, _ in SYS:
                p = E["res"][(R9, s)]["per"][c]
                cellv.append(f"{100 * p['rec']:.1f} / " + ("—" if math.isnan(p["prec"]) else f"{100 * p['prec']:.1f}") + f" / {p['f1']:.3f}")
            rows.append([c, f"{E['n_cls'][c]:,}"] + cellv)
        md += [f"**{gold}**", ""] + tbl(["人の判定", "確認済みの本数"] + [HEAD.get(n, n) for n, _ in SYS], rows)
    def dv(t, sc=100.0, nd=2):
        v, lo, hi = t
        return sgn(sc * v, nd) + ("" if lo is None else f"（{sgn(sc * lo, nd)}〜{sgn(sc * hi, nd)}）")

    md += ["## 表 11　通しの指標の差（アプリの規則。同じ動画どうし。かっこの中は 95% の幅）", "",
           "動物の通しの再現率の差はポイント、macro F1 の差は F1 の値の差です。幅が 0 をまたぐときは、差があるとは言い切れません。", ""]
    rows = []
    for a, b in PAIRS_MD:
        row = [f"{HEAD.get(a, a)} → {b}"]
        for gold in GOLDS:
            if gold not in SPECIES_ONLY:
                d = e2e[gold]["diff"][(R9, a, b)]
                row += [dv(d["micro"]), dv(d["macro_f1"], 1.0, 3)]
        rows.append(row)
    md += tbl(["比べる 2 つ（前 → 後）"] + [f"{gold}　{w}" for gold in GOLDS if gold not in SPECIES_ONLY for w in ("動物の通しの再現率", "macro F1")], rows)
    md += ["## 表 12　gold_species：種ごとの通しの再現率（アプリの規則。かっこの中は 95% の幅）", "",
           "確認済みの動画のうち、AI の第 1 種が人の判定に含まれる割合です。種の判別に進まなかった動画は、誤りに数えます。", ""]
    Es = e2e[gs]
    md += tbl(["人の判定", "確認済みの本数"] + [HEAD.get(n, n) for n, _ in SYS],
              [[sp, f"{Es['n_cls'][sp]:,}"] + [pv(Es["res"][(R9, s)]["per"][sp]["rec"], *Es["res"][(R9, s)]["per"][sp]["rec_ci"]) for s, _ in SYS] for sp in MAIN_SPECIES])

    gg = [g for g in GOLDS if g not in SPECIES_ONLY]
    TAN = "種：タヌキ"
    if LIGHTS0:
        def acc_change(d):
            return "—" if d is None else f"{d['pa']:.1f}% → {d['pb']:.1f}%、{sgn(d['d'])}（{rng_s(d['lo'], d['hi'], 2, True)}）"

        md += ["## 表 13　軽い方式で、元の SpeciesNet の確率を混ぜる割合を 0.3 から 0 にしたときの変化（アプリの規則。同じ分類器、同じ動画）", "",
               "ますの中は「混ぜる割合 0.3 のときの値 → 0 のときの値、差（差の 95% の幅）」です。前 = 同じ回の軽い方式、後 = 軽い方式（混ぜない）。"
               "上の 4 行は、種の判別に進んだ動画での正解率で、差はポイントです（表 1〜4 と同じ数え方）。下の 4 行は通しの指標です（表 9・11 と同じ数え方。macro F1 の差は F1 の値の差）。", ""]
        for gold in gg:
            E = e2e[gold]
            rows = []
            for rname, label in ((TAN, "タヌキの正解率"), ("動物", "動物の正解率"), (EMPTY, "動物なしの正解率"), ("合計", "合計の正解率")):
                rows.append([label, f"{meta[(gold, rname)]['n']:,}"] + [acc_change(diffs.get((gold, R9, rname, a[3], a[0]))) for a in LIGHTS0])
            for key, label, n_s in (("micro", "動物の通しの再現率", f"{E['n_ani']:,}"), ("macro_f1", "macro F1", f"{len(E['classes'])} 種"),
                                    ("emp_rec", "動物なしの通しの再現率", f"{E['n_emp']:,}"), ("all", "全動画での通しの正答率", f"{E['n']:,}")):
                row = [label, n_s]
                for a in LIGHTS0:
                    ra, rb, d = E["res"][(R9, a[3])], E["res"][(R9, a[0])], E["diff"][(R9, a[3], a[0])][key]
                    val = lambda r: {"micro": r["micro"][2], "macro_f1": r["macro_f1"][0], "emp_rec": r["per"][EMPTY]["rec"], "all": r["all"][2]}[key]
                    row.append(f"{val(ra):.3f} → {val(rb):.3f}、{dv(d, 1.0, 3)}" if key == "macro_f1" else f"{100 * val(ra):.1f}% → {100 * val(rb):.1f}%、{dv(d)}")
                rows.append(row)
            md += [f"**{gold}**", ""] + tbl(["", "本数"] + [f"{a[2]}の材料" for a in LIGHTS0], rows)
        md += ["## 表 14　タヌキ：方式 B、軽い方式、軽い方式（混ぜない）（種の判別に進んだ動画での正解率。アプリの規則。同じ動画どうし）", "",
               "差はポイントで、かっこの中は 95% の幅です。", ""]
        rows = []
        for gold in gg:
            for a in LIGHTS0:
                lab0, _, pb, pl = a
                c = lambda s: cells[(gold, R9, TAN, s)]
                d1, d2, d3 = (diffs.get((gold, R9, TAN, x, y)) for x, y in ((pb, pl), (pb, lab0), (pl, lab0)))
                rows.append([f"{gold}　{pb}の材料", f"{meta[(gold, TAN)]['n']:,}", pct_s(c(pb)["k"], c(pb)["n"]), pct_s(c(pl)["k"], c(pl)["n"]), pct_s(c(lab0)["k"], c(lab0)["n"]),
                             diff_s(d1), diff_s(d2), diff_s(d3)])
        md += tbl(["", "本数", "方式 B", "軽い方式（混ぜる割合 0.3）", "軽い方式（混ぜない）", "方式 B → 軽い方式", "方式 B → 軽い方式（混ぜない）", "軽い方式 → 軽い方式（混ぜない）"], rows)

    # 軽い方式（混ぜる割合 0.3）が、学習した区分のほかを答えた動画の数（数え方ごと。対象の動画。答えなしは数えない）
    cls_light = {code_ja.get(c, c) for c in infos[LIGHTS[0][0]][0]["classes"]}
    n_out = {rule: 0 for rule, _ in RULES}
    for gold in GOLDS:
        with open(f"{R}/videos_{gold}.csv", encoding="utf-8-sig") as f:
            for v in csv.DictReader(f):
                if v["対象"] == "1":
                    for rule, pre in RULES:
                        n_out[rule] += sum(1 for lab, _, _ in LIGHTS if v[pre + col_of[lab]] not in cls_light and v[pre + col_of[lab]] not in NOANS)
    src_order = ["Second_500_2", "Third_500", "Empty_2000", "Empty_Second_4000", "Empty_Third_4000"]
    md += ["## 表の読み方", "",
           f"- **モデル：** どれも、First_200 を開いて、SpeciesNet（元のまま）から学習したものです。細かい設定（枠の信頼度の下限、1 動画あたりの枠の数、検証用の地点の割合など）は、{n_models_s}とも同じです"
           + ("（軽い方式（混ぜない）の「元のモデルを混ぜる割合」だけが違います）。" if LIGHTS0 else "。"),
           "  - First_200 だけ・1 回目・2 回目・3 回目は、方式 B（SpeciesNet の本体の一部を学習し直す。GPU が要る）です。",
           f"  - {light_names}は、方式 A です。SpeciesNet（元のまま）が枠ごとに出す特徴量（1,280 個の数）から、17 の区分を当てる小さな分類器を学習します。SpeciesNet の本体は変えないので、GPU なしで学習できます。"
           f"判定のときは、この分類器の確率に、元の SpeciesNet の確率を {base_mix} の割合で混ぜます。"]
    if LIGHTS0:
        md.append(f"  - {light0_names}も、方式 A です。同じ回の軽い方式と同じ材料・同じ設定で学習し、判定のときに元の SpeciesNet の確率を混ぜません（学習の窓の「元のモデルを混ぜる割合」を 0 にしました）。"
                  "学習は混ぜる割合を使わないので、分類器の重みと、学習に使った動画・枠は、同じ回の軽い方式と同じです（台本が、重みのファイルと学習の一覧を比べて確かめています）。違うのは、判定のときに混ぜるかどうかだけです。")
    for lab, m in MODELS:
        inf = infos[lab][0]
        src = sorted([s["name"] for s in inf.get("sources") or []], key=lambda n: src_order.index(n) if n in src_order else 99)
        md.append(f"  - {lab}（{m}、方式 {inf.get('kind') or 'B'}）：材料は {'、'.join([inf.get('project') or 'First_200'] + src)}。")
    for lab, m, partner in LIGHTS:                       # 軽い方式と、同じ材料の方式 B の、材料の違い（学習の一覧から数える）
        cl, c1, mm = infos[lab][0]["counts"], infos[partner][0]["counts"], mat[lab]
        diff_k = sorted((c for c in set(cl) | set(c1) if cl.get(c, 0) != c1.get(c, 0)), key=lambda c: -abs(cl.get(c, 0) - c1.get(c, 0)))
        s = f"  - {lab}と、方式 B の {partner}は、同じプロジェクトを材料にしています。学習に使った動画は {mm['n_a']:,} 本と {mm['n_b']:,} 本です。"
        if not (mm["only_a"] or mm["only_b"] or mm["diff"]):
            s += "同じ動画で、使った枠の番号と区分も同じです。"
        else:
            by = lambda c, k: "、".join(f"{p} {n:,} 本" for p, n in sorted(c.items(), key=lambda x: -x[1])) + (f"。うち、人の判定に種が 2 つ以上ある動画 {k:,} 本" if k else "")
            parts = []
            if mm["only_a"]:
                parts.append(f"{lab}だけが使った動画が {mm['only_a']:,} 本（{by(mm['only_a_by'], mm['only_a_multi'])}）")
            if mm["only_b"]:
                parts.append(f"方式 B の {partner}だけが使った動画が {mm['only_b']:,} 本（{by(mm['only_b_by'], mm['only_b_multi'])}）")
            if mm["diff"]:
                parts.append(f"両方が使って、使った枠の番号か区分が違う動画が {mm['diff']:,} 本（{by(mm['diff_by'], mm['diff_multi'])}）")
            s += "、".join(parts) + "あります。そのほかの動画は、使った枠の番号と区分も同じです。"
        if not diff_k:
            s += "区分ごとの枠の数は、全部同じです。"
        elif len(diff_k) <= 3:
            s += "区分ごとの枠の数が違うのは、" + "、".join(f"{code_ja.get(c, c)}（{cl.get(c, 0):,} 枚と {c1.get(c, 0):,} 枚）" for c in diff_k) + "だけです。"
        else:
            c = diff_k[0]
            s += f"区分ごとの枠の数は、{len(diff_k)} の区分で違います（いちばん大きい違いは{code_ja.get(c, c)}の {cl.get(c, 0):,} 枚と {c1.get(c, 0):,} 枚。全部は表 7）。"
        md.append(s)
    md += ["- **SpeciesNet（そのまま）：** 種リストの行（哺乳類・齧歯目などの上位の行も含む）から答えを選びます。",
           "- **SpeciesNet（17 の選択肢に絞る）：** 学習したモデルと同じ 17 の選択肢だけを残して、答えを選びます。学習したモデルと比べるのは、こちらです。",
           "- **対象の動画：** 確認済みで、MegaDetector が動物の枠を付けて種を判別した動画です。人の判定に「不明」を含む動画は外しています。枠が付かなかった動画は、表 1〜6 に入っていません（本数は表 8 にあります）。"]
    for gold in GOLDS:
        g = info_g[gold]
        md.append(f"  - {gold}：確認済み {g['verified']:,} 本のうち、動物の枠が付いて種を判別した動画は {g['npz']:,} 本。そこから外したもの（{g['dropped']}）を除いて、対象は {g['n']:,} 本。")
    md += ["- **正解：** AI の第 1 種が、人の判定に含まれることです。",
           f"- **アプリの規則：** アプリが実際に出す答えです。どの種の確率も基準の線（{p1}）に届かないときは、「動物なし」の確率の平均が {empty_p} 以上なら「動物なし」、届かなければ「要確認」（答えなし）になります。答えなしは、不正解に数えます。",
           "- **線を使わない数え方：** 枠ごとの確率を動画で平均し、いちばん高い選択肢を答えとします。答えなしはありません。",
           f"- **幅（表 2〜4）：** 地点ごとの取り直し（ブートストラップ、{B:,} 回）で出した 95% の幅です。その行の動画がある地点を、重複を許して選び直します。差の幅が 0 をまたぐときは、差があるとは言い切れません。",
           "  - 間違いが数本しかない行では、幅の上の端が 100.0 になることがあります（表 2 の gold_species など）。",
           "  - 手順書のほかの節に書いた幅とは、取り直しの回数と乱数が違うので、小数第 2 位が少し違うことがあります。論文には、この表の数字を使ってください。",
           "- **表 5・6 の p：** 入れ替わった動画の本数からの、McNemar の検定（正確な両側の検定）です。種ごとの行は入れ替わる動画が少ないので、幅ではなく、本数とこの p で見ます。",
           f"- **gold の地点：** 学習に使った地点は {len(train_st)} 地点で、{n_models_s}のモデルとも同じです。"]
    for gold in GOLDS:
        g = info_g[gold]
        ss = g["seasons"]
        season_s = "、".join(ss) if len(ss) <= 3 else f"{len(ss)} 回（{ss[0]}〜{ss[-1]}）"
        if g["n_train"] == 0:
            site_s = f"{g['st']} 地点は、どれも学習に使っていない地点です。"
        else:
            site_s = (f"{g['st']} 地点のうち、学習に使った地点は {g['st_train']} 地点（対象の動画 {g['n_train']:,} 本）、"
                      f"それ以外は {g['st'] - g['st_train']} 地点（{g['n'] - g['n_train']:,} 本）です。")
            if (gold, "動物（学習に使った地点）") in meta:
                site_s += "表 1 の「学習に使った地点」の行は、このうち動物の動画だけを数えています。"
        md.append(f"  - {gold}：調査回 {season_s}。{site_s}")
    md += ["- **gold_species：** 本数の少ない 5 種を、種ごとに本数を決めて選んだテストです。種の割合が実際と違うので、動物全体と合計の正解率は出していません。適合率と F1 も、同じ理由で出していません。",
           "- **通しの指標（表 9〜12）：** 評価に入る動画は、確認済みで、人の判定に「不明」を含まず、人・車だけでもない動画の全部です（表 8 の「確認済みの本数」）。種の判別に進まなかった動画も入ります。",
           "  - 通しの再現率：その種と確定した動画のうち、AI の第 1 種が人の判定に含まれる割合です。MegaDetector が見落とした動画は、誤りになります。",
           "  - 適合率：AI がその種と答えた動画のうち、人の判定にその種が含まれる割合です。",
           "  - F1：再現率と適合率の調和平均です。macro F1 は、動物の区分ごとの F1 の平均です。AI が 1 本も答えなかった種の F1 は 0 とします。",
           "  - 動物なしの通しの再現率：動物なしと確定した動画の全部（枠が付かなかった動画を含む）のうち、アプリが「動物なし」と答えた割合です。表 1 の「動物なし」（枠が付いた動画だけ）とは、分母が違います。",
           "  - 幅：評価に入る動画がある地点の全部を、重複を許して選び直します。F1 と種ごとの平均は、同じ選び直しの中で出し直しています。確認済みが 10 本より少ない種には、幅を出していません。",
           "  - 線を使わない数え方での値は、CSV にあります。",
           "", "## 読むときの注意", "",
           f"- いちばん良い回や方式を、この表（gold）を見て選ぶことはしません。{n_models_s}のモデルを、そのまま並べます。",
           "- 軽い方式と比べる相手は、同じ材料で学習した、同じ回の方式 B です（表 3・4・6・11 の「" + "」「".join(f"{b} → {a}" for a, _, b in LIGHTS) + "」）。材料の量が違う回どうしでは、方式の違いだけを比べたことにはなりません。",
           "- 軽い方式の答えには、元の SpeciesNet の確率が混ざっています。そのため、選べる答えは 17 の区分より広く、元の SpeciesNet の行（哺乳類など）が答えになることがありえます。その答えは、不正解に数えます。"
           + (f"ただし、学習していない区分の確率は、最大でも混ぜる割合（{base_mix}）です。アプリの規則の基準の線（{p1}）には届かないので、アプリの規則では、学習していない区分は答えになりません。" if base_mix < float(p1) else "")
           + f"この表の動画で、軽い方式が学習した区分のほかを答えた動画は、アプリの規則で {n_out['アプリの規則']} 本、線を使わない数え方で {n_out['線を使わない数え方']} 本です（{n_light} つのモデルの合計）。"
           + ("軽い方式（混ぜない）の答えは、学習した 17 の区分だけです。" if LIGHTS0 else ""),
           "- 軽い方式のモデルは、材料のプロジェクト（" + "・".join(p for p in ("First_200", "Second_500_2", "Third_500") if any(p == (infos[lab][0].get("project") or "First_200") or p in [s["name"] for s in infos[lab][0].get("sources") or []] for lab, _, _ in LIGHTS))
           + "）の特徴量を、元の SpeciesNet で取り直してから学習しました（2026-11-12〜13）。枠は、前に付いていたものをそのまま使っています。取り直しの前後で、人の判定・枠・学習フレームの印は変わっていません。"
           "同じ材料の方式 B との、学習に使った動画の違いは、「表の読み方」のモデルの項に書いてあります。違いの理由は、次の 3 つです（流し直す前の控えと突き合わせて確かめました）。",
           "  - 枠を手で直した動画は、取り直しの前は種の判別に進んでいなかったので、方式 B の材料には入っていません。取り直しで種の判別に進んだので、軽い方式の材料には入りました（軽い方式だけが使った動画のうち、人の判定の種が 1 つのもの）。",
           "  - 人の判定に種が 2 つ以上ある動画では、どの枠をどの種の材料にするかを、プロジェクトに保存してある答え（枠ごとの第 1 候補）で決めます。"
           "この答えが、方式 B を学習したときは仮のモデル（B_20261026_2009）のもの、軽い方式を学習したときは元の SpeciesNet のものだったので、使う枠が変わりました。",
           f"  - 方式 B だけが使った First_200 の 1 本は、特徴量が古い検出（動画を差し替える前）のものだった動画です。取り直しで、いまの枠の特徴量になり、枠の信頼度が下限（{set0.get('train_min_conf')}）に届かなくなったので、軽い方式の材料から外れました。"]
    assert all(mat[lab]["only_b_by"].get("First_200", 0) == 1 and mat[lab]["only_a_by"].get("First_200", 0) == 0 for lab, _, _ in LIGHTS), "First_200 の動画の違いが、上の文と合わない。文を直すこと"
    gg = [g for g in GOLDS if g not in SPECIES_ONLY]
    tan = lambda s: " と ".join(f"{100 * e2e[g]['res'][(R9, s)]['per']['タヌキ']['rec']:.1f}%" for g in gg)

    def span_s(ds: list[dict]) -> str:
        """2 つの gold の差の幅が、0 をまたぐかどうかを文にする"""
        span = [d["lo"] <= 0 <= d["hi"] for d in ds]
        if all(span):
            return "幅は、どちらの gold でも 0 をまたぎます"
        if not any(span):
            return "幅は、どちらの gold でも 0 をまたぎません"
        return "幅が 0 をまたぐのは " + "・".join(g for g, sp in zip(gg, span) if sp) + " だけです"

    if LIGHTS0:                                          # 軽い方式（混ぜない）の答えが、17 の区分（と答えなし）だけであること（上の文の確かめ）
        cls_ja = {code_ja.get(c, c) for c in infos[LIGHTS0[0][0]][0]["classes"]}
        for gold in GOLDS:
            with open(f"{R}/videos_{gold}.csv", encoding="utf-8-sig") as f:
                for v in csv.DictReader(f):
                    if v["対象"] == "1":
                        for a in LIGHTS0:
                            assert v[col_of[a[0]]] in cls_ja or v[col_of[a[0]]] in NOANS, (gold, a[0], v[col_of[a[0]]])
        md.append("- **軽い方式（混ぜない）は、あとから足した評価です。** 軽い方式（混ぜる割合 0.3。学習したときのアプリの既定）の gold の結果で、タヌキが低いことを見たあとに足しました。"
                  "原因（元の SpeciesNet の確率を混ぜること）は、gold を使わずに確かめました（学習のときに検証用に取り分けた地点の動画。記録は `F:\\Copy\\evaluation\\holdout_mix_20261113\\結果.md`）。"
                  "そのうえで、混ぜる割合を 0 にして、gold で評価しました。gold で試した割合は 0 だけです。論文に載せるときは、0.3 の結果と 0 の結果の両方を載せ、0 の結果は gold の結果を見たあとの変更であることを書きます。")
        md.append("- **論文では、軽い方式（混ぜない）を、軽い方式の主な結果にします**（2026-11-13 に利用者が決めた。アプリの既定の「元のモデルを混ぜる割合」も、同じ日に 0 に変えた。確認事項の 121・122 回目）。"
                  "軽い方式（混ぜる割合 0.3）は、はじめの評価として並べます。表での名前は、評価した順のままにしてあります（軽い方式 1〜3 回目 = 混ぜる割合 0.3、軽い方式（混ぜない）1〜3 回目 = 混ぜる割合 0）。")
        md.append("- 軽い方式（混ぜない）と比べる相手は 2 つです。同じ回の軽い方式（違いは混ぜる割合だけ。表 13、表 3・4・6・11 の「" + "」「".join(f"{a[3]} → {a[0]}" for a in LIGHTS0)
                  + "」）と、同じ材料の方式 B（表 14、表 3・4・6・11 の「" + "」「".join(f"{a[2]} → {a[0]}" for a in LIGHTS0) + "」）。")
    for lab, partner in [(lab, partner) for lab, _, partner in LIGHTS] + [(a[0], a[2]) for a in LIGHTS0]:   # 軽い方式ごとに: 動物全体の差と動物なしの差が 0 をまたぐか、タヌキの通しの再現率
        ds = [diffs.get((g, "アプリの規則", "動物", partner, lab)) for g in gg]
        span = [d["lo"] <= 0 <= d["hi"] for d in ds]
        if all(span):
            head = f"{lab}は、動物全体の正解率では、方式 B の {partner}と差があるとは言えません（表 3 のアプリの規則の幅が、どちらの gold でも 0 をまたぎます）。"
        elif not any(span):
            head = f"{lab}は、動物全体の正解率で、方式 B の {partner}と差があります（表 3 のアプリの規則の幅が、どちらの gold でも 0 をまたぎません。差は " + " と ".join(sgn(d["d"]) for d in ds) + " ポイント）。"
        else:
            head = (f"{lab}と、方式 B の {partner}の動物全体の正解率の差は、gold によって違います（表 3 のアプリの規則。差は " + " と ".join(sgn(d["d"]) for d in ds)
                    + " ポイントで、幅が 0 をまたぐのは " + "・".join(g for g, sp in zip(gg, span) if sp) + " だけです）。")
        de = [diffs.get((g, "アプリの規則", EMPTY, partner, lab)) for g in gg]
        emp_s = f"動物なしの正解率の、方式 B の {partner}との差は " + " と ".join(sgn(d["d"]) for d in de) + f" ポイントです（表 4 のアプリの規則。{span_s(de)}）。"
        md.append(f"- {head}{emp_s}タヌキの通しの再現率は {tan(lab)} で、方式 B の {partner}は {tan(partner)} です（表 10）。")
    def tan_low(lab: str, partner: str) -> bool:
        """種を判別した動画での、タヌキの正解率の差（同じ材料の方式 B → 軽い方式）の幅が、どちらの gold でも 0 より下にあるか"""
        dd = [diffs.get((g, "アプリの規則", "種：タヌキ", partner, lab)) for g in gg]
        return all(d is not None and d["hi"] is not None and d["hi"] < 0 for d in dd)

    low = [lab for lab, _, partner in LIGHTS if tan_low(lab, partner)]
    md += [f"- 元の SpeciesNet（17 の選択肢に絞る）のタヌキの通しの再現率は {tan(SN_CLOSED)} です。"
           + ("" if not low else f"{'・'.join(low)}は、タヌキの正解率が、同じ材料の方式 B より低くなっています（種を判別した動画での差の幅が、どちらの gold でも 0 より下にあります。数字は「差」の CSV）。"
              + ("軽い方式の答えには元の SpeciesNet の確率が混ざるので、その誤りが残ったと考えられます（混ぜない場合の値は、gold では出していません）。" if not LIGHTS0 else
                 "原因は、軽い方式の答えに元の SpeciesNet の確率が混ざることです（gold を使わずに確かめました。上の「あとから足した評価」の項）。"))]
    if LIGHTS0:
        def span3(ds: list[dict]) -> str:
            """差の幅が 0 より上か、下か、またぐかを、gold ごとに文にする"""
            w = ["0 より上" if d["lo"] > 0 else ("0 より下" if d["hi"] < 0 else "0 をまたぐ") for d in ds]
            return f"幅は、どちらの gold でも {w[0]}" if len(set(w)) == 1 else "幅は、" + "、".join(f"{g} で {x}" for g, x in zip(gg, w))

        for a in LIGHTS0:                                # 軽い方式（混ぜない）ごとに: 混ぜる割合 0.3 との差（タヌキ、動物、動物なし、合計）と、方式 B とのタヌキの差
            lab0, _, pb, pl = a
            parts = []
            for rname, label in ((TAN, "タヌキ"), ("動物", "動物全体"), (EMPTY, "動物なし"), ("合計", "合計")):
                ds = [diffs.get((g, R9, rname, pl, lab0)) for g in gg]
                parts.append(f"{label}は " + " と ".join(sgn(d["d"]) for d in ds) + f" ポイント（{span3(ds)}）")
            db = [diffs.get((g, R9, TAN, pb, lab0)) for g in gg]
            md.append(f"- {lab0}の正解率を、同じ回の軽い方式（混ぜる割合 {base_mix}）と比べると（表 13。前が gold_season、後が gold_station）：" + "、".join(parts) + "。"
                      f"タヌキの正解率の、方式 B の {pb}との差は " + " と ".join(sgn(d["d"]) for d in db) + f" ポイントです（表 14。{span3(db)}）。")
    md += ["- 方式 B の動物全体の正解率は、2 回目で伸び、3 回目は伸びていません（表 3 の「2 回目 → 3 回目」の幅が 0 をまたぎます）。"]
    lsteps = list(zip([a[0] for a in LIGHTS], [a[0] for a in LIGHTS][1:]))
    if lsteps:
        md.append("- 軽い方式の動物全体の正解率の、回どうしの差（表 3 のアプリの規則）：" + "。".join(
            f"「{a} → {b}」は " + " と ".join(sgn(d["d"]) for d in ds) + f" ポイント（{span_s(ds)}）"
            for a, b in lsteps for ds in [[diffs.get((g, "アプリの規則", "動物", a, b)) for g in gg]]) + "。")
    md += ["- 線を使わない数え方では、方式 B の 1 回目の動物の正解率が、First_200 だけのモデルより低くなっています（表 3。gold_station では幅が 0 をまたぎません）。この数え方での「1 回目 → 2 回目」の伸びには、その分が含まれます。",
           "- 種ごとの数字は、動物の材料がほぼ同じ 2 つのモデル（方式 B の First_200 だけと 1 回目）でも大きく違います（表 6）。種ごとの数字の回どうしの上下は、データの量の効果としては読めません。",
           "- 学習は、どのモデルも 1 回ずつです。同じ材料で学習をやり直したときの揺れは、測っていません。",
           "- gold_species の人の判定は、学習したモデルの答えが出る前に確定しました。ただし、多くの動画は、SpeciesNet（元のまま）の答えが出たあとに確定しています。偏りがあるとすれば、SpeciesNet に有利な向きです。確定のしかたは手順書に書いてあり、論文の方法にも書く必要があります。",
           "", "## ファイル", "",
           f"- `{os.path.basename(outs['acc'])}`：表 1・2 の全部のますと、gold_season・gold_station の種ごとの行です（本数、地点数、数えた本数、%、幅）。Wilson の幅（動画を 1 本ずつ別のものとして扱う幅）も並べてあります。正解率が 100% のますは、取り直しの幅が 100〜100 になるので、Wilson の幅を見てください。",
           f"- `{os.path.basename(outs['diff'])}`：同じ動画どうしの差です（表 3〜6" + ("と表 13・14 の正解率の差" if LIGHTS0 else "") + f" の全部と、合計、種ごと、SpeciesNet（そのまま）→（絞る）、方式 B の 1 回目 → 3 回目、{LIGHTS[0][0]} → 方式 B の 2 回目、{LIGHTS[0][0]} → 方式 B の 3 回目）。",
           f"- `{os.path.basename(outs['e2e'])}`：通しの指標です（表 9〜12" + ("と表 13 の通しの指標" if LIGHTS0 else "") + " の全部。モデルどうしの差、種ごとの再現率・適合率・F1 と幅、線を使わない数え方の値）。",
           f"- `{os.path.basename(outs['missed'])}`：評価に入る動画のうち、種の判別に進まなかった動画の一覧です（地点、人の判定、アプリの答え）。gold のデータベースから読みました。",
           "- 確かめたこと：動画ごとの答えから数え直した本数が、集計の表と同じであること（" + "。".join(checks) + "）。", ""]
    with open(outs["md"], "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md))
    for c in checks:
        print("確かめ:", c)
    for p in outs.values():
        print("書いた:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
