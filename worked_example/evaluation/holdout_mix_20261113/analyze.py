# holdout_mix.py が書いた動画ごとの答え（holdout_videos_stage*.csv）を数える。読むだけ（結果は analyze_result.json とこの画面）。
# 正解 = 答え（第 1 種）が人の判定に含まれる（gold の集計と同じ）。差の幅 = 地点（名前からプロジェクト名を外したもの）を単位にした並べ直し 20,000 回の 95% の幅。
import collections, csv, json, os, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
JA = {r["code"]: r["name_ja"] for r in csv.DictReader(open("F:/Copy/First_200/TrapNote/species.csv", encoding="utf-8-sig"))}
JA.update({"empty": "動物なし", "(low)": "要確認", "unknown": "不明", "__animal__": "動物（動物なしを除く）", "__all__": "合計"})
MIXES = ["0.3", "0.2", "0.1", "0.0"]
B = 20000


def boot(rows, ok_a, ok_b, seed=1):
    """正解率の差（b − a、ポイント）と、地点を単位にした並べ直しの 95% の幅"""
    st = sorted({r["phys"] for r in rows})
    ix = {s: i for i, s in enumerate(st)}
    n = np.zeros(len(st)); a = np.zeros(len(st)); b = np.zeros(len(st))
    for r, x, y in zip(rows, ok_a, ok_b):
        i = ix[r["phys"]]
        n[i] += 1; a[i] += x; b[i] += y
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(st), size=(B, len(st)))
    N = n[pick].sum(axis=1)
    d = 100 * (b[pick].sum(axis=1) - a[pick].sum(axis=1)) / np.maximum(N, 1)
    d = d[N > 0]
    return round(100 * (b.sum() - a.sum()) / n.sum(), 2), round(float(np.percentile(d, 2.5)), 2), round(float(np.percentile(d, 97.5)), 2), len(st)


res = {}
for stage in (1, 2, 3):
    rows = list(csv.DictReader(open(f"{HERE}/holdout_videos_stage{stage}.csv", encoding="utf-8-sig")))
    for r in rows:
        r["hs"] = r["human"].split(";")
    groups = collections.OrderedDict()
    sp = [c for c, _ in collections.Counter(r["group"] for r in rows if r["group"] != "empty").most_common()]
    for c in sp:
        groups[c] = [r for r in rows if r["group"] == c]
    groups["__animal__"] = [r for r in rows if r["group"] != "empty"]
    groups["empty"] = [r for r in rows if r["group"] == "empty"]
    groups["__all__"] = rows
    out = dict(n=len(rows), groups={})
    print(f"\n===== 段階 {stage}: 検証用の動画 {len(rows)} 本（動物 {len(groups['__animal__'])}、動物なし {len(groups['empty'])}）")
    print("| 人の判定 | 本数 | SpeciesNet（絞る） | 混ぜる 0.3（アプリ） | 0.2 | 0.1 | 混ぜない 0 | 差（0 − 0.3） | 線なし 0.3 | 線なし 0 | 元の確率（正しい種） | 新しい分類器の確率（正しい種） |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for g, rs in groups.items():
        if not rs:
            continue
        ok = {k: [int(r[k] in r["hs"]) for r in rs] for k in ["sn_closed", "sn_asis", "sn_closed_top"] + [f"app_{b}" for b in MIXES] + [f"top_{b}" for b in MIXES]}
        acc = {k: round(100 * sum(v) / len(rs), 1) for k, v in ok.items()}
        cnt = {k: sum(v) for k, v in ok.items()}
        d = boot(rs, ok["app_0.3"], ok["app_0.0"])
        dt = boot(rs, ok["top_0.3"], ok["top_0.0"])
        single = [r for r in rs if r.get("sn_true_closed") not in (None, "")]
        m = lambda k: round(float(np.mean([float(r[k]) for r in single])), 3) if single else None
        up = sum(1 for x, y in zip(ok["app_0.3"], ok["app_0.0"]) if not x and y)
        down = sum(1 for x, y in zip(ok["app_0.3"], ok["app_0.0"]) if x and not y)
        out["groups"][g] = dict(n=len(rs), acc=acc, cnt=cnt, diff_app=d, diff_top=dt, sn_true_closed=m("sn_true_closed"), sn_true_asis=m("sn_true_asis"), head_true=m("head_true"),
                                head_top_true=m("head_top_true"), sn_listed=m("sn_listed"), up=up, down=down,
                                ans_03=dict(collections.Counter(r["app_0.3"] for r in rs).most_common()), ans_00=dict(collections.Counter(r["app_0.0"] for r in rs).most_common()),
                                ans_sn=dict(collections.Counter(r["sn_closed"] for r in rs).most_common()), ans_sn_asis=dict(collections.Counter(r["sn_asis"] for r in rs).most_common()))
        print(f"| {JA.get(g, g)} | {len(rs)} | {acc['sn_closed']} | {acc['app_0.3']} | {acc['app_0.2']} | {acc['app_0.1']} | {acc['app_0.0']} | {d[0]:+.2f} [{d[1]:+.2f}〜{d[2]:+.2f}] ↑{up} ↓{down} | {acc['top_0.3']} | {acc['top_0.0']} | {m('sn_true_closed')} | {m('head_true')} |")
    g = out["groups"].get("raccoon_dog")
    if g:
        print("タヌキの動画の答え  混ぜる 0.3:", {JA.get(k, k): v for k, v in g["ans_03"].items()})
        print("                    混ぜない 0:", {JA.get(k, k): v for k, v in g["ans_00"].items()})
        print("                    SpeciesNet（絞る）:", {JA.get(k, k): v for k, v in g["ans_sn"].items()})
        print("                    SpeciesNet（そのまま）:", {JA.get(k, k): v for k, v in g["ans_sn_asis"].items()})
    res[stage] = out
json.dump(res, open(f"{HERE}/analyze_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
