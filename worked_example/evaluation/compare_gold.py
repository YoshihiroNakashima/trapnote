"""gold の評価の集計: SpeciesNet（元のまま）と学習したモデルを、同じ選択肢で比べる。

TrapNote のデータベース（data.sqlite）と特徴量のファイル（cache/features/*.npz）は読むだけで、書き込まない。
結果は results_<日時>/ に、学習したモデルの動画ごとの答えの控えは answers/ に書く（どちらもこのファイルと同じフォルダの中）。
学習したモデルの枠ごとの出力（動物なしの確率の平均など）の控えは answers_detail/ に書く（2 つ目の表「基準の線を使わない数え方」に使う）。

使い方: 「集計する.bat」をダブルクリック（または python -I compare_gold.py）。
gold で学習したモデルの「判定の付け直し」をしたら、そのたびに流す。いまの答えを answers/ に控えてから、控えた全部のモデルで集計する
（別のモデルで付け直すと、前のモデルの答えはデータベースから消えるため）。
答えは、集計のはじめに全部の gold の分をまとめて控える（2026-11-12 に直した。集計には 1 分ほどかかり、その間に次の付け直しが始まると、
あとの gold の答えが途中までしか控えられなかったため）。集計を始めたときに処理の途中だった gold と、2 つ以上のモデルの答えが混ざっている gold は、
summary.md と画面に「注意」として出す。次の付け直しは、集計が終わってから始める。

数え方:
- 対象: 確認済みで、枠があって種を判別した動画（SpeciesNet の npz がある）。人の判定に「不明」を含む動画は外す。
  そのうえで、人の判定（人・車を除く）が全部そのモデルの選択肢に入る動画だけを数える（入らない本数は summary.md に書く）。
- 正解: AI の第 1 種が人の判定に含まれる（アプリの一致率と同じ）。答えなし（どの種も p1 に届かない = 要確認、または不明）は正解に数えず、本数を別に出す。
- SpeciesNet の答えは、npz の枠ごとの確率から、アプリと同じ規則（trapnote.detect.aggregate_video と、プロジェクトの p1・p2・K・決め方・動物なしの規則）で出し直す。
  そのまま: 種リストの行（哺乳類・齧歯目などの上位の行も含む）から選ぶ。種リストで使わない行は「不明」にまとめる（アプリの付け直しと同じ）。
  絞る: 学習したモデルの選択肢（model.json の classes）の列だけを残し、枠ごとに割り直してから選ぶ（論文の主な比較）。
- 学習したモデルの答えは、付け直したときの判定（auto_json の第 1 種。人・車は除く）。
- 基準の線を使わない数え方（2 つ目の表。2026-11-11 に足した）: 対象は同じ。枠ごとの確率を動画で平均し、いちばん高い選択肢を答えとする（人・車の列は除く）。
  p1 と動物なしの線（species_empty_p）を使わないので、答えなしはない。アプリの規則は「自信がなければ答えを保留する」作りで、動物なしの正解率が
  モデルごとの確率の出方と線の位置で大きく動くため、線に左右されない数え方を並べる。
  SpeciesNet は npz の枠ごとの確率から出す（そのまま = 種リストの行にまとめたもの、絞る = 選択肢の列だけを残して枠ごとに割り直したもの）。
  学習したモデルは answers_detail/ の控え（付け直したときの動画ごとの確率の平均）から出す。控えのないモデルは出せない（そのモデルで gold を付け直して集計すると控えられる）。
- 上位の分類で答えた: SpeciesNet がそのままで、人の種を含む上位の行（哺乳類・齧歯目など）で答えたもの。
- 地点: 学習したモデルの学習に使った地点（model.json の items_csv で Role = train の Station）と、それ以外の地点に分けた正解率も出す。
  材料に加えたプロジェクトの地点は items_csv に「<プロジェクト名>:<地点>」と書いてあるので、プロジェクト名を外して、同じ地点として数える。
- gold_species（2026-11-12 に足した）: 本数の少ない種（ホンドテン・ニホンイタチ・ネコ・ニホンノウサギ・ニホンリス）の追加のテスト。
  とっておいた調査回（N250124）の残りから、種ごとに本数を決めて選んだもの。種の割合が実際と違うので、動物全体・合計の正解率は summary.md に出さず、
  種ごとの行だけを出す（地点で分けた表も出さない）。数え方と対象の決め方は、ほかの gold と同じ。
- 検出の段階（2026-11-12 に足した）: 上の表は、動物の枠が付いて種を判別した動画だけを数える。確認済みの動画（人の判定に不明を含むもの、人・車だけのものは除く）の
  うち、枠が付いて種を判別した本数を、人の判定（第 1 種）ごとに detected_<gold>.csv に出す（gold_species では summary.md にも出す）。
"""
from __future__ import annotations

import collections
import csv
import datetime
import json
import os
import sqlite3
import sys

import numpy as np

TRAPNOTE = "F:/TrapNote"
sys.path.insert(0, TRAPNOTE)
from trapnote.detect import _parse_label, aggregate_video  # noqa: E402
from trapnote.paths import cache_name  # noqa: E402
from trapnote.project import PROJECT_DEFAULTS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS = {"gold_season": "F:/Copy/gold_season/TrapNote", "gold_station": "F:/Copy/gold_station/TrapNote",
            "gold_species": "F:/Copy/gold_species/TrapNote"}
# 種ごとに本数を決めて選んだ gold: 動物全体・合計の正解率と、地点で分けた表は summary.md に出さない（種ごとの行だけを出す）
SPECIES_ONLY = {"gold_species": "少ない種の追加のテスト（N250124 の残りから、ホンドテンとニホンイタチは全部、ネコ・ニホンノウサギ・ニホンリスは 150 本ずつ）。"
                                "種ごとに本数を決めて選んだので、動物全体・合計の正解率は出さない。種ごとの行を見る"}
MODELS_DIR = os.path.join(TRAPNOTE, "data", "models", "local")
REF_MODEL = "B_20261109_1554"      # 学習したモデルの答えがまだないとき、「絞る」の選択肢と地点の分け方に使うモデル
SKIP_MODELS = {"B_20261026_2009": "gold を確定したときに AI の提案として見えていたモデルなので比べない（答えの控えだけ残す）"}
FIX_TIME = datetime.datetime(2026, 11, 9, 20, 36)    # SpeciesNet のラベルの対応を直した時刻（確認事項 93 回目）。これより前の npz は古い対応
SN_DIR = os.path.join(TRAPNOTE, "data", "models", "kagglehub", "models", "google", "speciesnet", "pyTorch", "v4.0.3a", "1")
FIXED_JA = {"empty": "動物なし", "human": "人", "vehicle": "車", "unknown": "不明", "area": "有効範囲", "(low)": "要確認"}
NOT_ANIMAL = {"human", "vehicle", "area"}
NO_ANSWER = {"(low)", "unknown"}
LEVELS = ["cls", "order", "family", "genus"]


# ---------- 読み込み ----------
def write_csv(path: str, header: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def load_project(name: str, root: str) -> dict:
    rows = list(csv.DictReader(open(os.path.join(root, "species.csv"), encoding="utf-8-sig")))
    for r in rows:
        r["enabled"] = r.get("enabled") == "1"
    proj = json.load(open(os.path.join(root, "project.json"), encoding="utf-8"))
    s = {**PROJECT_DEFAULTS, **(proj.get("settings") or {})}
    con = sqlite3.connect("file:" + os.path.join(root, "data.sqlite").replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    items = {r["id"]: dict(r) for r in con.execute("SELECT id, rel_path, station, season, status, auto_json FROM items WHERE missing=0")}
    human: dict[int, list[str]] = collections.defaultdict(list)
    for j in con.execute("SELECT item_id, species FROM judgements ORDER BY id"):
        it = items.get(j["item_id"])
        if it and it["status"] == "verified" and j["species"] != "area" and j["species"] not in human[j["item_id"]]:
            human[j["item_id"]].append(j["species"])
    con.close()
    ja = {r["code"]: r["name_ja"] for r in rows}
    ja.update(FIXED_JA)
    order = {}
    for r in rows:
        order.setdefault(r["code"], len(order))
    for c in FIXED_JA:
        order.setdefault(c, len(order))
    return dict(name=name, root=root, rows=rows, s=s, items=items, human=human, ja=ja, order=order,
                enabled={r["code"] for r in rows if r["enabled"]} | (set(FIXED_JA) - {"(low)"}))


def model_info(m: str) -> tuple[dict, set[str]]:
    """model.json と、学習に使った地点（items_csv の Role = train）"""
    mj = os.path.join(MODELS_DIR, m, "model.json")
    info = json.load(open(mj, encoding="utf-8")) if os.path.exists(mj) else {}
    st: set[str] = set()
    for fn in (info.get("items_csv"), os.path.join(MODELS_DIR, m, "train_items.csv")):
        if fn and os.path.exists(fn):
            for r in csv.DictReader(open(fn, encoding="utf-8-sig")):
                if r.get("Station") and (r.get("Role") or "train") == "train":
                    # 材料に加えたプロジェクトの地点は「<プロジェクト名>:<地点>」と書いてある。gold の地点と比べるので、プロジェクト名を外す
                    s, p = r["Station"], r.get("Project") or ""
                    st.add(s[len(p) + 1:] if p and s.startswith(p + ":") else s)
            break
    return info, st


def taxonomy_paths(rows: list[dict]) -> dict:
    """種コード → (当たった階級, その名前, 綱〜属の名前)。SpeciesNet のラベル表から引く（上位の分類で答えたかの判定に使う）"""
    fn = next((f for f in os.listdir(SN_DIR) if ".labels." in f and f.endswith(".txt")), None)
    if fn is None:
        return {}
    tax = [_parse_label(x.strip()) for x in open(os.path.join(SN_DIR, fn), encoding="utf-8") if x.strip()]
    out = {}
    for r in rows:
        sn = (r.get("speciesnet") or "").strip().lower().split("（")[0].split("(")[0].strip()
        toks = [t for t in sn.replace(",", " ").split() if t]
        if not toks:
            continue
        if len(toks) >= 2:
            d = next((x for x in tax if x["genus"] == toks[0] and x["species"] == toks[1]), None)
            if d:
                out[r["code"]] = ("species", None, {k: d[k] for k in LEVELS})
            continue
        for lv in ("genus", "family", "order", "cls"):
            d = next((x for x in tax if x[lv] == toks[0]), None)
            if d:
                out[r["code"]] = (lv, toks[0], {k: d[k] for k in LEVELS[:LEVELS.index(lv) + 1]})
                break
    return out


def is_ancestor(a: str, h: str, paths: dict) -> bool:
    """a（上位の行）が人の判定 h を含むか（例: 哺乳類 ⊃ ニホンジカ、齧歯目 ⊃ ニホンリス）"""
    if a == h or a not in paths or h not in paths:
        return False
    lv, name, _ = paths[a]
    if lv == "species" or name is None:
        return False
    hlv = paths[h][0]
    hidx = LEVELS.index(hlv) if hlv in LEVELS else len(LEVELS)
    return LEVELS.index(lv) < hidx and paths[h][2].get(lv) == name


# ---------- 学習したモデルの答えの控え ----------
def local_model_of(tag: str | None) -> str | None:
    """auto_json の model（「元 + 学習したモデル + 検出」）から学習したモデルの名前。SpeciesNet（元 + 検出）や検出だけなら None"""
    parts = [p.strip() for p in (tag or "").split(" + ")]
    return parts[1] if len(parts) >= 3 else None


def running_job(root: str) -> str | None:
    """その gold で、判定を書き換える処理（付け直し reclassify・自動判定 detect）がいま走っていれば、その種類と始めた時刻。なければ None。
    データベースは読むだけ。種類ごとに、いちばん新しい処理だけを見る（アプリが途中で閉じられて「実行中」のまま残った古い行を拾わないため）"""
    try:
        con = sqlite3.connect("file:" + os.path.join(root, "data.sqlite").replace("\\", "/") + "?mode=ro", uri=True)
        try:
            for kind in ("reclassify", "detect"):
                r = con.execute("SELECT state, started_at FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
                if r and r[0] == "running":
                    return f"{kind}、{r[1]} に開始"
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return None


def snapshot(P: dict, ans_dir: str) -> dict:
    by_model: dict[str, dict] = collections.defaultdict(dict)
    for iid, it in P["items"].items():
        if not it["auto_json"]:
            continue
        a = json.loads(it["auto_json"])
        m = local_model_of(a.get("model"))
        if not m:
            continue
        sp = a.get("species") or []
        by_model[m][iid] = dict(item_id=str(iid), rel_path=it["rel_path"], species=";".join(x["species"] for x in sp),
                                confidence=";".join(str(x.get("confidence")) for x in sp), low=str(int(bool(a.get("low")))),
                                at=a.get("at", ""), model_tag=a.get("model", ""))
    done = {}
    head = ["item_id", "rel_path", "species", "confidence", "low", "at", "model_tag"]
    for m, new in by_model.items():
        fn = os.path.join(ans_dir, f"{P['name']}__{m}.csv")
        old = {}
        if os.path.exists(fn):
            old = {int(r["item_id"]): r for r in csv.DictReader(open(fn, encoding="utf-8-sig"))}
        old.update(new)                                    # 同じ動画はいまの答えで上書き。途中までの付け直しでも前の分は残る
        write_csv(fn, head, [[r[k] for k in head] for _, r in sorted(old.items())])
        done[m] = (len(new), len(old))
    return done


def snapshot_detail(P: dict, det_dir: str) -> dict:
    """学習したモデルの枠ごとの出力（items.cls_json: 動物なしの確率の平均、枠ごとの第 1 候補とその確率など）も控える。
    別のモデルで付け直すとデータベースから消えるので残す。2 つ目の表（基準の線を使わない数え方）は、この控えから出す。
    1 行 = 1 本の動画（JSON）。同じ動画はいまの出力で上書きする"""
    con = sqlite3.connect(f"file:{P['root']}/data.sqlite?mode=ro", uri=True)
    by_model: dict[str, dict] = collections.defaultdict(dict)
    for iid, rel, cj in con.execute("SELECT id, rel_path, cls_json FROM items WHERE missing=0 AND cls_json IS NOT NULL"):
        try:
            c = json.loads(cj)
        except ValueError:
            continue
        parts = [p.strip() for p in (c.get("model") or "").split(" + ")]      # cls_json の model は「元 + 学習したモデル」
        if len(parts) < 2 or not parts[1]:
            continue
        by_model[parts[1]][int(iid)] = dict(item_id=int(iid), rel_path=rel, **c)
    con.close()
    done = {}
    os.makedirs(det_dir, exist_ok=True)
    for m, new in by_model.items():
        fn = os.path.join(det_dir, f"{P['name']}__{m}.jsonl")
        old = {}
        if os.path.exists(fn):
            for line in open(fn, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    old[int(r["item_id"])] = r
        old.update(new)
        with open(fn, "w", encoding="utf-8", newline="\n") as fh:
            for _, r in sorted(old.items()):
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        done[m] = (len(new), len(old))
    return done


def load_detail_top1(P: dict, det_dir: str) -> dict[str, dict[int, str]]:
    """学習したモデルの、基準の線を使わない答え: 控えてある動画ごとの確率の平均（cls_json の probs）がいちばん高い選択肢。
    控えのないモデルは入らない（そのモデルで gold を付け直してから集計すると控えられる）"""
    out: dict[str, dict[int, str]] = {}
    pre = P["name"] + "__"
    if not os.path.isdir(det_dir):
        return out
    for fn in sorted(os.listdir(det_dir)):
        if not (fn.startswith(pre) and fn.endswith(".jsonl")):
            continue
        d: dict[int, str] = {}
        for line in open(os.path.join(det_dir, fn), encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            pr = {k: v for k, v in (r.get("probs") or {}).items() if k not in NOT_ANIMAL}
            if pr:
                d[int(r["item_id"])] = max(pr.items(), key=lambda x: x[1])[0]
        out[fn[len(pre):-len(".jsonl")]] = d
    return out


def load_answers(P: dict, ans_dir: str) -> dict[str, dict[int, list[str]]]:
    out = {}
    pre = P["name"] + "__"
    for fn in sorted(os.listdir(ans_dir)):
        if fn.startswith(pre) and fn.endswith(".csv"):
            m = fn[len(pre):-4]
            out[m] = {int(r["item_id"]): [c for c in r["species"].split(";") if c]
                      for r in csv.DictReader(open(os.path.join(ans_dir, fn), encoding="utf-8-sig"))}
    return out


# ---------- SpeciesNet の答え（npz から出し直す） ----------
def first(pc: np.ndarray, codes: list[str], s: dict) -> str:
    cls = aggregate_video(pc, codes, float(s["species_p1"]), float(s["species_p2"]), int(s["species_k"]), str(s.get("species_pick") or "confidence"))
    if cls["species"]:
        return cls["species"][0][0]
    pe = float(s.get("species_empty_p") or 0)
    if pe > 0 and float(cls.get("empty_mean") or 0) >= pe:
        return "empty"
    return "(low)"


def top1(pc: np.ndarray, codes: list[str]) -> str:
    """基準の線を使わない答え: 枠ごとの確率を動画で平均し、いちばん高い選択肢（人・車の列は除く。アプリでも人・車は検出の方で決める）"""
    mean = pc.mean(axis=0)
    idx = [j for j, c in enumerate(codes) if c not in NOT_ANIMAL]
    if not idx:
        return "(low)"
    return codes[max(idx, key=lambda j: float(mean[j]))]


def fold(codes: list[str], pc: np.ndarray, enabled: set[str]) -> tuple[list[str], np.ndarray]:
    """種リストで使わない列を「不明」にまとめる（train.fold_disabled と同じ）"""
    if all(c in enabled for c in codes):
        return codes, pc
    keys, cols = [], {}
    for j, c in enumerate(codes):
        k = c if c in enabled else "unknown"
        if k not in cols:
            cols[k] = []
            keys.append(k)
        cols[k].append(j)
    new = np.zeros((len(pc), len(keys)), dtype=np.float32)
    for i, k in enumerate(keys):
        new[:, i] = pc[:, cols[k]].sum(axis=1)
    return keys, new


def restrict(codes: list[str], pc: np.ndarray, keep: frozenset) -> tuple[list[str], np.ndarray]:
    idx = [j for j, c in enumerate(codes) if c in keep]
    pc2 = pc[:, idx]
    ssum = pc2.sum(axis=1, keepdims=True)
    return [codes[j] for j in idx], np.where(ssum > 1e-6, pc2 / np.maximum(ssum, 1e-6), pc2)


def speciesnet_answers(P: dict, class_sets: list[frozenset]) -> tuple[dict, dict, collections.Counter, dict, dict]:
    """返り値: アプリの規則での答え（そのまま、絞る）、数、基準の線を使わない答え（そのまま、絞る）"""
    fdir = os.path.join(P["root"], "cache", "features")
    open_: dict[int, str] = {}
    closed: dict[frozenset, dict[int, str]] = {cs: {} for cs in class_sets}
    open_top: dict[int, str] = {}
    closed_top: dict[frozenset, dict[int, str]] = {cs: {} for cs in class_sets}
    stat: collections.Counter = collections.Counter()
    for iid, it in P["items"].items():
        fp = os.path.join(fdir, cache_name(it["rel_path"]) + ".npz")
        if not os.path.isfile(fp):
            continue
        with np.load(fp, allow_pickle=False) as z:
            model = str(z["model"]) if "model" in z else ""
            if " + " in model:                  # 学習したモデルで「検出と種判別」を流したもの: SpeciesNet の確率ではない
                stat["npz_retrained"] += 1
                continue
            codes = [str(c) for c in z["codes"]]
            pc = z["pc"].astype(np.float32)
        if len(pc) == 0 or pc.ndim != 2 or pc.shape[1] == 0:
            stat["npz_empty"] += 1
            continue
        stat["npz"] += 1
        if datetime.datetime.fromtimestamp(os.path.getmtime(fp)) < FIX_TIME:
            stat["npz_old_map"] += 1
        c1, p1 = fold(codes, pc, P["enabled"])
        open_[iid] = first(p1, c1, P["s"])
        open_top[iid] = top1(p1, c1)
        for cs in class_sets:
            c2, p2 = restrict(codes, pc, cs)
            closed[cs][iid] = first(p2, c2, P["s"]) if c2 else "(low)"
            closed_top[cs][iid] = top1(p2, c2) if c2 else "(low)"
    # 検算: いまの判定が SpeciesNet のものなら、出し直した答えと比べる
    for iid, a in open_.items():
        aj = P["items"][iid]["auto_json"]
        if not aj:
            continue
        d = json.loads(aj)
        tag = d.get("model") or ""
        if local_model_of(tag) or " + " not in tag:
            continue
        sp = [x["species"] for x in d.get("species", []) if x["species"] not in ("human", "vehicle")]
        app = sp[0] if sp else "(low)"
        app = app if app in P["enabled"] or app == "(low)" else "unknown"
        if a == "(low)" and app == "(low)" or a == app:
            stat["check_same"] += 1
        else:
            stat["check_diff"] += 1
    return open_, closed, stat, open_top, closed_top


def first_of(lst: list[str] | None) -> str:
    for c in lst or []:
        if c not in ("human", "vehicle"):
            return c
    return "(low)"


# ---------- 集計 ----------
def pct(a: int, n: int) -> str:
    return f"{100 * a / n:.1f}" if n else ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.datetime.now()
    out_dir = os.path.join(HERE, "results_" + now.strftime("%Y%m%d_%H%M%S"))
    ans_dir = os.path.join(HERE, "answers")
    det_dir = os.path.join(HERE, "answers_detail")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(ans_dir, exist_ok=True)
    md = [f"# gold の評価の集計（{now:%Y-%m-%d %H:%M}）", "",
          "SpeciesNet（元のまま）と学習したモデルを、人の判定（確認済み）と比べた。数え方はこのフォルダの上の compare_gold.py の先頭に書いてある。", ""]
    ref_info, _ = model_info(REF_MODEL)
    # 先に、全部の gold のいまの答えを控える（集計の途中で次の付け直しが始まっても、あとの gold の控えが途中までのものにならないように）
    loaded: dict[str, tuple] = {}
    for name, root in PROJECTS.items():
        if not os.path.isfile(os.path.join(root, "data.sqlite")):
            continue
        busy = running_job(root)
        P = load_project(name, root)
        loaded[name] = (P, snapshot(P, ans_dir), snapshot_detail(P, det_dir), busy)
        print(f"{datetime.datetime.now():%H:%M:%S} 答えを控えた: {name}（" + ("、".join(f"{m} {n} 本" for m, (n, _) in loaded[name][1].items()) or "学習したモデルの答えなし")
              + "）" + (f" 注意: 処理の途中（{busy}）" if busy else ""), flush=True)
    for name, root in PROJECTS.items():
        if name not in loaded:
            md += [f"## {name}", "", f"- プロジェクトが見つからないので飛ばした（{root}）", ""]
            continue
        species_only = name in SPECIES_ONLY
        P, snap, _, busy = loaded[name]
        answers = load_answers(P, ans_dir)
        models = [m for m in answers if m not in SKIP_MODELS]
        minfo = {m: model_info(m) for m in models}
        groups: dict[frozenset, list[str]] = collections.OrderedDict()
        groups[frozenset(ref_info.get("classes") or [])] = []
        for m in models:
            groups.setdefault(frozenset(minfo[m][0].get("classes") or []), []).append(m)
        groups = collections.OrderedDict((cs, ms) for cs, ms in groups.items() if cs)
        sn_open, sn_closed, stat, sn_open_top, sn_closed_top = speciesnet_answers(P, list(groups))
        det_top = load_detail_top1(P, det_dir)
        paths = taxonomy_paths(P["rows"])
        ja, order = P["ja"], P["order"]
        key = lambda c: (order.get(c, 999), c)
        n_ver = sum(1 for it in P["items"].values() if it["status"] == "verified")
        md += [f"## {name}", "",
               f"- 確認済み {n_ver} 本、SpeciesNet の npz がある動画 {stat['npz']} 本"
               + (f"（学習したモデルで「検出と種判別」を流した npz {stat['npz_retrained']} 本は使っていない）" if stat["npz_retrained"] else "")]
        if species_only:
            md += [f"- **{SPECIES_ONLY[name]}**"]
        if stat["npz"] == 0:
            md += ["- **SpeciesNet をまだ流していないので、比べられない。** 自動判定で SpeciesNet（元のまま）を流してから、もう一度集計する。", ""]
        if stat["npz_old_map"]:
            md += [f"- **注意: npz のうち {stat['npz_old_map']} 本は、ラベルの対応を直す前（{FIX_TIME:%m/%d %H:%M} より前）のもの。** "
                   "SpeciesNet を流し直してから集計し直す（そのままの数字は直す前の対応のもの）。"]
        if stat["check_same"] or stat["check_diff"]:
            md += [f"- 検算: いまの判定（SpeciesNet）と、npz から出し直した答えが同じ {stat['check_same']} 本、違う {stat['check_diff']} 本"
                   + ("（違うのは、種リストを変える前に付けた判定と、保存した確率の丸め（float16）でしきい値ぎりぎりになったもの）" if stat["check_diff"] else "")]
        for m, (n_new, n_all) in snap.items():
            md += [f"- 学習したモデル {m} の答えを控えた（いまの判定 {n_new} 本、控え全体 {n_all} 本）" + (f"。{SKIP_MODELS[m]}" if m in SKIP_MODELS else "")]
        if busy:
            md += [f"- **注意: 集計を始めたとき、この gold は処理の途中だった（{busy}）。** 途中までの答えを控えた。処理が終わってから、もう一度集計する。"]
        if len(snap) > 1:
            md += ["- **注意: いまの判定に、2 つ以上の学習モデルの答えが混ざっている（" + "、".join(f"{m} {n} 本" for m, (n, _) in snap.items())
                   + "）。** 付け直しの途中か、途中で止まったもの。付け直しが終わってから、もう一度集計する。"]
        if not models:
            md += [f"- 学習したモデルの答えはまだない（gold で付け直したら、もう一度集計する）。「絞る」の選択肢は {REF_MODEL} のもの"]
        md += [""]
        # 検出の段階: 確認済みの動画（人の判定に不明を含むもの、人・車だけのものは除く）のうち、動物の枠が付いて種を判別した本数を、人の判定（第 1 種）ごとに出す。
        # 下の表は枠が付いた動画だけを数えるので、枠が付かなかった動画（検出の取りこぼし）はここで別に見る
        if stat["npz"]:
            dn, dk = collections.Counter(), collections.Counter()
            for iid, hs_all in P["human"].items():
                hs = [h for h in hs_all if h not in NOT_ANIMAL]
                if not hs or "unknown" in hs_all:
                    continue
                dn[hs[0]] += 1
                dk[hs[0]] += iid in sn_open
            if dn:
                drows = [[ja.get(h, h), dn[h], dk[h], pct(dk[h], dn[h])] for h in sorted(dn, key=key)]
                write_csv(os.path.join(out_dir, f"detected_{name}.csv"), ["人の判定", "確認済みの本数", "枠が付いて種を判別した本数", "%"], drows)
                if species_only:
                    md += ["確認済みの動画のうち、MegaDetector が動物の枠を付けて種を判別した本数（下の表は、この動画だけを数える）:", "",
                           "| 人の判定 | 確認済み | 枠が付いて種を判別した | 割合 |", "|---|---|---|---|"]
                    md += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]}% |" for r in drows]
                    md += [""]
        videos_cols = []
        for gi, (cs, ms) in enumerate(groups.items()):
            animals = cs - {"empty"}
            tag = "" if gi == 0 else "__" + (ms[0] if ms else f"set{gi}")
            # 対象
            excl = collections.Counter()
            target = []
            for iid in sn_open:
                hs_all = P["human"].get(iid, [])
                hs = [h for h in hs_all if h not in NOT_ANIMAL]
                if not hs_all:
                    excl["人の判定なし"] += 1
                elif "unknown" in hs_all:
                    excl["不明を含む"] += 1
                elif not hs:
                    excl["人・車だけ"] += 1
                elif not all(h in cs for h in hs):
                    excl["選択肢にない種を含む"] += 1
                else:
                    target.append((iid, hs))
            if not target:
                continue
            ans = {"SN_closed": {iid: sn_closed[cs][iid] for iid, _ in target}}
            for m in ms:
                ans[m] = {iid: first_of(answers[m].get(iid)) if iid in answers[m] else None for iid, _ in target}
            cols = [("SN_closed", "SpeciesNet（絞る）")] + [(m, m) for m in ms]
            # 種ごとの表
            per = collections.defaultdict(lambda: collections.Counter())
            for iid, hs in target:
                h = hs[0]
                for grp in (h, "__animal__" if h in animals else "__empty__", "__all__"):
                    per[grp]["n"] += 1
                    per[grp]["open_ok"] += sn_open[iid] in hs
                    for c, _ in cols:
                        a = ans[c][iid]
                        per[grp][c + "_ok"] += a in hs
                        per[grp][c + "_none"] += a is None or a in NO_ANSWER
            head = ["人の判定", "本数", "SpeciesNet（そのまま）正解", "%"]
            for c, lab in cols:
                head += [f"{lab} 正解", "%", f"{lab} 答えなし"]
            rows = []
            for grp, label in [(h, ja.get(h, h)) for h in sorted({hs[0] for _, hs in target if hs[0] in animals}, key=key)] + [
                    ("__animal__", "動物（動物なしを除く）"), ("__empty__", "動物なし"), ("__all__", "合計")]:
                d = per.get(grp)
                if not d:
                    continue
                row = [label, d["n"], d["open_ok"], pct(d["open_ok"], d["n"])]
                for c, _ in cols:
                    row += [d[c + "_ok"], pct(d[c + "_ok"], d["n"]), d[c + "_none"]]
                rows.append(row)
            write_csv(os.path.join(out_dir, f"compare_{name}{tag}.csv"), head, rows)
            # summary.md の表に出す行: ふつうは 動物・動物なし・合計。種ごとに本数を決めて選んだ gold は、種ごとの行（と、確定で動物なしになった動画）
            if species_only:
                md_rows = [(h, ja.get(h, h)) for h in sorted({hs[0] for _, hs in target if hs[0] in animals}, key=key)] + [("__empty__", "動物なし")]
            else:
                md_rows = [("__animal__", "動物"), ("__empty__", "動物なし"), ("__all__", "合計")]
            md += [f"### 同じ選択肢で比べる（選択肢 {len(cs)}: {'・'.join(ja.get(c, c) for c in sorted(cs, key=key))}）", "",
                   f"対象 {len(target)} 本（外したもの: " + ("、".join(f"{k} {v}" for k, v in excl.most_common()) or "なし") + "）", "",
                   "| | 本数 | SpeciesNet（そのまま） | " + " | ".join(lab for _, lab in cols) + " |",
                   "|---|---|---|" + "---|" * len(cols)]
            for grp, label in md_rows:
                d = per.get(grp)
                if d:
                    md += [f"| {label} | {d['n']} | {pct(d['open_ok'], d['n'])}% | " + " | ".join(f"{pct(d[c + '_ok'], d['n'])}%" for c, _ in cols) + " |"]
            md += ["", f"種ごとの表: compare_{name}{tag}.csv"]
            for m in ms:
                miss = sum(1 for iid, _ in target if ans[m][iid] is None)
                if miss:
                    md += [f"- **{m} の答えがない動画 {miss} 本**（付け直しが途中で止まった、など。答えなしに数えた）"]
            # 2 つ目の表: 基準の線を使わない数え方（対象は同じ）
            tcols = [("SN_open", "SpeciesNet（そのまま）"), ("SN_closed", "SpeciesNet（絞る）")] + [(m, m) for m in ms]
            tans = {"SN_open": {iid: sn_open_top[iid] for iid, _ in target}, "SN_closed": {iid: sn_closed_top[cs][iid] for iid, _ in target}}
            for m in ms:
                if m in det_top:
                    tans[m] = {iid: det_top[m].get(iid) for iid, _ in target}
            tper = collections.defaultdict(lambda: collections.Counter())
            for iid, hs in target:
                h = hs[0]
                for grp in (h, "__animal__" if h in animals else "__empty__", "__all__"):
                    tper[grp]["n"] += 1
                    for c, _ in tcols:
                        if c in tans:
                            a = tans[c][iid]
                            tper[grp][c + "_ok"] += a in hs
                            tper[grp][c + "_empty"] += a == "empty"
            thead = ["人の判定", "本数"]
            for c, lab in tcols:
                if c in tans:
                    thead += [f"{lab} 正解", "%", f"{lab} 動物なしと答えた"]
            trows = []
            for grp, label in [(h, ja.get(h, h)) for h in sorted({hs[0] for _, hs in target if hs[0] in animals}, key=key)] + [
                    ("__animal__", "動物（動物なしを除く）"), ("__empty__", "動物なし"), ("__all__", "合計")]:
                d = tper.get(grp)
                if not d:
                    continue
                row = [label, d["n"]]
                for c, _ in tcols:
                    if c in tans:
                        row += [d[c + "_ok"], pct(d[c + "_ok"], d["n"]), d[c + "_empty"]]
                trows.append(row)
            write_csv(os.path.join(out_dir, f"top1_{name}{tag}.csv"), thead, trows)
            md += ["", "基準の線を使わない数え方（対象は同じ。枠ごとの確率を動画で平均し、いちばん高い選択肢を答えとする。答えなしはない）:", "",
                   "| | 本数 | " + " | ".join(lab for _, lab in tcols) + " |",
                   "|---|---|" + "---|" * len(tcols)]
            for grp, label in md_rows:
                d = tper.get(grp)
                if d:
                    md += [f"| {label} | {d['n']} | " + " | ".join((f"{pct(d[c + '_ok'], d['n'])}%" if c in tans else "—") for c, _ in tcols) + " |"]
            d = tper.get("__animal__")
            if d:
                md += [f"| 動物がいるのに「動物なし」と答えた本数 | {d['n']} | " + " | ".join((str(d[c + "_empty"]) if c in tans else "—") for c, _ in tcols) + " |"]
            md += ["", f"種ごとの表: top1_{name}{tag}.csv"]
            for m in ms:
                if m not in tans:
                    md += [f"- {m} は、枠ごとの出力の控えがないので、この数え方では出せない（gold をこのモデルで付け直してから集計すると出る）"]
                else:
                    miss = sum(1 for iid, _ in target if tans[m][iid] is None)
                    if miss:
                        md += [f"- **{m} の枠ごとの出力の控えがない動画 {miss} 本**（この数え方では不正解に数えた）"]
            # 地点（種ごとに本数を決めて選んだ gold では出さない）
            site_models = [] if species_only else (ms or [REF_MODEL])
            srows = []
            for m in site_models:
                st = minfo[m][1] if m in minfo else model_info(m)[1]
                if not st:
                    continue
                for lab, cond in (("学習に使った地点", True), ("それ以外の地点", False)):
                    sub = [(iid, hs) for iid, hs in target if hs[0] in animals and (P["items"][iid]["station"] in st) == cond]
                    n = len(sub)
                    r = [m, lab, n, len({P["items"][iid]["station"] for iid, _ in sub}),
                         pct(sum(sn_open[iid] in hs for iid, hs in sub), n), pct(sum(ans["SN_closed"][iid] in hs for iid, hs in sub), n)]
                    r.append(pct(sum((ans[m][iid] in hs) for iid, hs in sub), n) if m in ans else "")
                    srows.append(r)
            if srows:
                write_csv(os.path.join(out_dir, f"sites_{name}{tag}.csv"),
                          ["学習したモデル", "地点", "動物の本数", "地点数", "SpeciesNet（そのまま）%", "SpeciesNet（絞る）%", "学習したモデル %"], srows)
                md += ["", "地点で分けた正解率（動物）:", "", "| 学習したモデル | 地点 | 本数 | 地点数 | SpeciesNet（そのまま） | SpeciesNet（絞る） | 学習したモデル |", "|---|---|---|---|---|---|---|"]
                md += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]}% | {r[5]}% | {(r[6] + '%') if r[6] else '—'} |" for r in srows]
            # SpeciesNet のありのままの答え（最初の組だけ）
            if gi == 0:
                op = collections.defaultdict(collections.Counter)
                for iid, hs in target:
                    if hs[0] not in animals:
                        continue
                    a_ = sn_open[iid]
                    k = "ok" if a_ in hs else ("coarse" if any(is_ancestor(a_, h, paths) for h in hs) else ("none" if a_ in NO_ANSWER else "wrong"))
                    for grp in (hs[0], "__animal__"):
                        op[grp][k] += 1
                        op[grp]["n"] += 1
                orows = []
                for grp, label in [(h, ja.get(h, h)) for h in sorted({hs[0] for _, hs in target if hs[0] in animals}, key=key)] + [("__animal__", "動物（合計）")]:
                    d = op.get(grp)
                    if d:
                        orows.append([label, d["n"], d["ok"], d["coarse"], d["none"], d["wrong"],
                                      pct(d["ok"], d["n"]), pct(d["coarse"], d["n"]), pct(d["none"], d["n"]), pct(d["wrong"], d["n"])])
                write_csv(os.path.join(out_dir, f"speciesnet_open_{name}.csv"),
                          ["人の判定", "本数", "一致", "上位の分類で答えた（中身は合う）", "答えなし（要確認・不明）", "違う", "一致 %", "上位 %", "答えなし %", "違う %"], orows)
                d = op["__animal__"]
                if species_only:
                    md += ["", f"SpeciesNet のありのままの答えの内訳（一致・上位の分類で答えた・答えなし・違う）は、種ごとに speciesnet_open_{name}.csv にある。"]
                else:
                    md += ["", "SpeciesNet のありのままの答え（動物）: "
                           f"一致 {pct(d['ok'], d['n'])}%、上位の分類で答えた {pct(d['coarse'], d['n'])}%、答えなし {pct(d['none'], d['n'])}%、違う {pct(d['wrong'], d['n'])}%"
                           f"（{d['n']} 本。種ごと: speciesnet_open_{name}.csv）"]
            # 行列
            for c, lab in [("SN_open", "SpeciesNet_open")] + [(c, ("SpeciesNet_closed" if c == "SN_closed" else c)) for c, _ in cols]:
                mat = collections.defaultdict(collections.Counter)
                for iid, hs in target:
                    a_ = sn_open[iid] if c == "SN_open" else ans[c][iid]
                    mat[hs[0]][a_ if a_ is not None else "(なし)"] += 1
                if gi > 0 and c == "SN_open":
                    continue
                acols = sorted({a_ for r in mat.values() for a_ in r} | set(mat), key=key)
                write_csv(os.path.join(out_dir, f"matrix_{name}{tag}_{lab}.csv"), ["人 \\ AI"] + [ja.get(a_, a_) for a_ in acols] + ["本数"],
                          [[ja.get(h, h)] + [mat[h].get(a_, 0) for a_ in acols] + [sum(mat[h].values())] for h in sorted(mat, key=key)])
            videos_cols.append((cs, ms, target, ans, tans, {i for i, _ in target}))
            md += [""]
        # 動画ごと（うしろの「線なし」の列は、基準の線を使わない数え方での答え）
        vhead = ["item_id", "rel_path", "調査回", "地点", "人の判定", "SpeciesNet（そのまま）"]
        for gi, (cs, ms, target, ans, tans, tset) in enumerate(videos_cols):
            sfx = "" if gi == 0 else f"（組 {gi + 1}）"
            vhead += [f"対象{sfx}", f"SpeciesNet（絞る）{sfx}"] + [f"{m}" for m in ms]
        vhead += ["線なし SpeciesNet（そのまま）"]
        for gi, (cs, ms, target, ans, tans, tset) in enumerate(videos_cols):
            sfx = "" if gi == 0 else f"（組 {gi + 1}）"
            vhead += [f"線なし SpeciesNet（絞る）{sfx}"] + [f"線なし {m}" for m in ms if m in tans]
        vrows = []
        for iid in sorted(sn_open):
            it = P["items"][iid]
            hs_all = P["human"].get(iid, [])
            r = [iid, it["rel_path"], it["season"], it["station"], "+".join(ja.get(h, h) for h in hs_all), ja.get(sn_open[iid], sn_open[iid])]
            for cs, ms, target, ans, tans, tset in videos_cols:
                inside = iid in tset
                v = ans["SN_closed"].get(iid) if inside else None
                r += [1 if inside else 0, ja.get(v, v) if v else ""]
                for m in ms:
                    a_ = ans[m].get(iid) if inside else None
                    r.append(ja.get(a_, a_) if a_ else "")
            r.append(ja.get(sn_open_top[iid], sn_open_top[iid]))
            for cs, ms, target, ans, tans, tset in videos_cols:
                inside = iid in tset
                v = tans["SN_closed"].get(iid) if inside else None
                r.append(ja.get(v, v) if v else "")
                for m in ms:
                    if m in tans:
                        a_ = tans[m].get(iid) if inside else None
                        r.append(ja.get(a_, a_) if a_ else "")
            vrows.append(r)
        if vrows:
            write_csv(os.path.join(out_dir, f"videos_{name}.csv"), vhead, vrows)
    md += ["## ファイル", "",
           "- compare_<gold>.csv: 同じ選択肢で比べた種ごとの正解（論文の主な表）",
           "- top1_<gold>.csv: 基準の線を使わない数え方での種ごとの正解（2 つ目の表）",
           "- speciesnet_open_<gold>.csv: SpeciesNet のありのままの答えを、一致・上位の分類・答えなし・違うに分けたもの（補足の表）",
           "- sites_<gold>.csv: 学習に使った地点とそれ以外の地点に分けた正解率",
           "- detected_<gold>.csv: 確認済みの動画のうち、動物の枠が付いて種を判別した本数（人の判定ごと。上の表はこの動画だけを数える）",
           "- matrix_<gold>_<モデル>.csv: 人の判定（第 1 種）× AI の第 1 種の本数",
           "- videos_<gold>.csv: 動画ごとの人の判定と各モデルの答え（うしろの「線なし」の列は、基準の線を使わない数え方での答え）",
           f"- 学習したモデルの答えの控え: {ans_dir}",
           f"- 学習したモデルの枠ごとの出力の控え（動物なしの確率の平均など。2 つ目の表に使う）: {det_dir}"]
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    print("\n結果:", out_dir)
    warn = [name for name, (_, snap, _, busy) in loaded.items() if busy or len(snap) > 1]
    if warn:
        print("\n注意: 付け直しの途中の gold があった（" + "、".join(warn) + "）。付け直しが終わってから、もう一度流す。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
