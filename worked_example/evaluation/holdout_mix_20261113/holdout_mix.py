# 軽い方式（方式 A）のタヌキが低い理由を、gold を使わずに調べる。
#
# やること（段階 1〜3 = 軽い方式の 1〜3 回目の材料ごと）:
#  1. プロジェクトの写し（proj/。prep_copies.py で作った）を、アプリの Project で開く。特徴量だけは元の場所から読む（読むだけ）。
#  2. アプリの学習の関数（trapnote.train.collect_all・_run_train_a）をそのまま呼ぶ。
#     a) アプリと同じ設定 → 保存される分類器が、本物のモデルの head.npz と同じか（写しでの計算が、本物の学習と同じかの検算）。
#     b) 「検証用の地点は保存するモデルにも使わない」をオン → 検証用の地点を除いて学習した分類器が保存される。その検証の記録が本物の model.json の記録と同じか。
#  3. b) の分類器で、検証用の地点の動画（その分類器の学習に使っていない）の答えを、混ぜる割合（base_mix）を変えて出す。
#     分類器は同じで、変えるのは混ぜる割合だけ。数え方は gold の集計（compare_gold.py）と同じ関数を使う。
#
# 元のプロジェクト・アプリの data フォルダには書かない（TRAPNOTE_DATA_DIR をこのフォルダの中にしてから読み込む）。
import collections, csv, json, os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
os.environ["TRAPNOTE_DATA_DIR"] = HERE + "/data"
os.makedirs(HERE + "/data", exist_ok=True)
sys.path.insert(0, "F:/TrapNote")
sys.path.insert(0, "F:/Copy/evaluation")
import numpy as np
from trapnote import train
from trapnote.detect import project_settings
from trapnote.paths import cache_name, key
from trapnote.project import Project
import compare_gold as CG

REAL = {}          # 写しのプロジェクトのフォルダ（key）→ 元のプロジェクトのフォルダ


def scratch(name: str) -> str:
    p = f"{HERE}/proj/{name}/TrapNote"
    REAL[key(p)] = f"F:/Copy/{name}/TrapNote"
    return p


def features_path(project, rel_path: str) -> str:
    return os.path.join(REAL[key(project.dir)], "cache", "features", cache_name(rel_path) + ".npz")


train.features_path = features_path                     # 特徴量は元の場所から読む（読むだけ）
train.SHARED_DIR = HERE + "/data/models/local"
REAL_MODELS = "F:/Copy/First_200/TrapNote/models"
STAGES = {1: ("A_20261112_2255", ["Empty_2000"]),
          2: ("A_20261113_0251", ["Empty_Second_4000", "Empty_2000", "Second_500_2"]),
          3: ("A_20261113_0255", ["Empty_Third_4000", "Empty_Second_4000", "Empty_2000", "Second_500_2", "Third_500"])}
MIXES = [0.3, 0.2, 0.1, 0.0]
NOT_ANIMAL = CG.NOT_ANIMAL


def phys(st: str) -> str:
    """地点の名前から、材料に加えたプロジェクトの名前（「<名前>:」）を外す"""
    return st.split(":", 1)[1] if ":" in st else st


def totals(e: dict) -> dict:
    return {k: e[k] for k in ("n", "exact", "partial", "first", "low")}


def colsum(e: dict) -> dict:
    """検証の表（人の種 → AI の第 1 種）を、AI の第 1 種ごとに足したもの。
    種が 2 つ以上の動画を「人の種」のどの行に数えるかは、アプリでは起動のたびに変わる（set の並び順）ので、行に左右されない数で比べる"""
    c = collections.Counter()
    for row in e["matrix"].values():
        for a, n in row.items():
            c[a] += n
    return dict(c)


def same_eval(e1: dict, e2: dict) -> bool:
    return all(totals(e1[t]) == totals(e2[t]) and colsum(e1[t]) == colsum(e2[t]) for t in ("before", "after")) and e1["stations"] == e2["stations"] and e1["n_items"] == e2["n_items"]


def answers(it: dict, head, beta, s: dict, classes: frozenset):
    """動画 1 本の答え（アプリの規則、線を使わない数え方）。beta = None なら SpeciesNet を選択肢に絞ったもの、"asis" ならそのまま"""
    codes, pc = it["codes"], it["pc"]
    if beta is None:
        c2, p2 = CG.restrict(codes, pc, classes)
        return (CG.first(p2, c2, s), CG.top1(p2, c2)) if c2 else ("(low)", "(low)")
    if beta == "asis":
        c2, p2 = train.fold_disabled(it["proj"], codes, pc)
        return CG.first(p2, c2, s), CG.top1(p2, c2)
    h = train.Head(head.classes, head.W, head.b, head.mu, head.sd, beta)
    c2, p2 = h.apply(it["feats"], codes, pc)
    c2, p2 = train.fold_disabled(it["proj"], c2, p2)     # アプリの付け直し（reclassify_item）と同じ順
    return CG.first(p2, c2, s), CG.top1(p2, c2)


def run_stage(stage: int) -> dict:
    real_name, src_names = STAGES[stage]
    real = json.load(open(f"{REAL_MODELS}/{real_name}/model.json", encoding="utf-8"))
    assert [x["name"] for x in real["sources"]] == src_names, (real["sources"], src_names)
    t0 = time.time()
    P = Project(scratch("First_200"))
    sources = [scratch(n) for n in src_names]
    opts = train.train_settings(P, {})
    opts["holdout_stations"] = None
    opts["holdout_batches"] = []
    assert {k: opts[k] for k in train.TRAIN_DEFAULTS} == real["settings"], "学習の設定が本物のモデルと違う"
    s = project_settings(P)
    col = train.collect_all(P, sources, opts)
    out = dict(stage=stage, real=real_name, n_items=len(col["items"]))
    try:
        items = col["items"]
        # a) アプリと同じ設定で学習 → 保存された分類器を本物と比べる
        ra = train._run_train_a(P, dict(opts), f"S{stage}_full", None, None, s, time.time(), col)
        za = np.load(f"{P.dir}/models/S{stage}_full/head.npz", allow_pickle=False)
        zr = np.load(f"{REAL_MODELS}/{real_name}/head.npz", allow_pickle=False)
        same_head = all(np.array_equal(za[k], zr[k]) for k in ("W", "b", "mu", "sd", "classes"))
        same_eval_a = same_eval(ra["eval"], real["eval"])
        out["same_eval_rowwise"] = bool(ra["eval"] == real["eval"])      # 行（人の種）まで同じか。種が 2 つ以上の動画の行は起動のたびに変わるので、違っていてもよい
        same_counts = (ra["n_items"], ra["n_crops"], ra["counts"], ra["holdout_stations"]) == (real["n_items"], real["n_crops"], real["counts"], real["holdout_stations"])
        out.update(same_head=bool(same_head), same_eval=bool(same_eval_a), same_counts=bool(same_counts), n_train_items=ra["n_items"], n_crops=ra["n_crops"])
        # b) 検証用の地点を除いて学習した分類器を保存する（学習は a) の検証のときと同じもの）
        rb = train._run_train_a(P, {**opts, "train_final_without_holdout": True}, f"S{stage}_holdout", None, None, s, time.time(), col)
        assert rb["final_without_holdout"] is True
        out.update(same_eval_b=bool(same_eval(rb["eval"], real["eval"])), n_holdout=rb["n_holdout"], n_fit_items=rb["n_items"], n_fit_crops=rb["n_crops"])
        head = train.Head.load(f"{P.dir}/models/S{stage}_holdout", rb)
        classes = frozenset(head.classes)
        holdout = set(rb["holdout_stations"])
        ev_ids = [iid for iid, it in items.items() if it["station"] in holdout]
        assert len(ev_ids) == rb["n_holdout"] == real["n_holdout"], (len(ev_ids), rb["n_holdout"], real["n_holdout"])
        # 検証用の地点と同じ名前の地点（別のプロジェクト）が、学習の側にあるか
        tr_phys = {phys(it["station"]) for iid, it in items.items() if it["station"] not in holdout and it["station"]}
        ho_phys = {phys(x) for x in holdout}
        out.update(n_holdout_stations=len(holdout), n_holdout_phys=len(ho_phys), n_holdout_phys_also_train=len(ho_phys & tr_phys))
        # アプリの検証と同じ数え方（evaluate_items）で、混ぜる割合ごと
        app_eval = {}
        for b in MIXES:
            h = train.Head(head.classes, head.W, head.b, head.mu, head.sd, b)
            e = train.evaluate_items(items, ev_ids, lambda it: h.apply(it["feats"], it["codes"], it["pc"]), s)
            app_eval[str(b)] = dict(totals(e), first_answers=colsum(e))
        assert {k: v for k, v in app_eval["0.3"].items() if k != "first_answers"} == totals(real["eval"]["after"]) and app_eval["0.3"]["first_answers"] == colsum(real["eval"]["after"])
        out["app_eval"] = app_eval
        # gold の集計と同じ数え方で、動画ごとの答え
        rows = []
        for iid in ev_ids:
            it = items[iid]
            hs_all = sorted(it["human"])
            hs = [h for h in hs_all if h not in NOT_ANIMAL]
            if "unknown" in hs_all or not hs or not all(h in classes for h in hs):
                continue
            r = dict(stage=stage, project=train.source_label(it["proj"]), rel_path=it["rel_path"], station=it["station"], phys=phys(it["station"]), human=";".join(hs),
                     group="empty" if hs == ["empty"] else hs[0], n_crops=len(it["pc"]), has_feats=int(it["feats"] is not None))
            a, t = answers(it, head, None, s, classes)
            r["sn_closed"], r["sn_closed_top"] = a, t
            a, t = answers(it, head, "asis", s, classes)
            r["sn_asis"], r["sn_asis_top"] = a, t
            for b in MIXES:
                a, t = answers(it, head, b, s, classes)
                r[f"app_{b}"], r[f"top_{b}"] = a, t
            # 仕組みを見るための数字（人の種が 1 つの動画だけ）: 元の SpeciesNet と新しい分類器が、正しい種に付けた確率（枠の平均）
            if len(hs) == 1 and it["feats"] is not None:
                true = hs[0]
                codes, pc = it["codes"], it["pc"]
                c2, p2 = CG.restrict(codes, pc, classes)
                ph = head.predict(it["feats"])
                jt = head.classes.index(true)
                r["sn_true_asis"] = round(float(pc[:, codes.index(true)].mean()), 4) if true in codes else 0.0
                r["sn_true_closed"] = round(float(p2[:, c2.index(true)].mean()), 4) if true in c2 else 0.0
                r["head_true"] = round(float(ph[:, jt].mean()), 4)
                r["head_top_true"] = round(float((np.argmax(ph, axis=1) == jt).mean()), 4)
                m = pc.mean(axis=0)
                jj = int(np.argmax(m))
                r["sn_top_code"], r["sn_top_mean"] = codes[jj], round(float(m[jj]), 4)
                r["sn_listed"] = round(float(sum(m[j] for j, c in enumerate(codes) if c in classes)), 4)      # 元の確率のうち、選択肢の列に乗っている分
            rows.append(r)
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(f"{HERE}/holdout_videos_stage{stage}.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        out.update(n_target=len(rows), classes=list(head.classes), elapsed=round(time.time() - t0, 1))
    finally:
        train.close_sources(col["projects"][1:])
        P.close()
    return out


if __name__ == "__main__":
    stages = [int(x) for x in sys.argv[1:]] or [1, 2, 3]
    res = {}
    for st in stages:
        r = run_stage(st)
        res[st] = r
        print(json.dumps({k: v for k, v in r.items() if k not in ("classes",)}, ensure_ascii=False), flush=True)
        json.dump(r, open(f"{HERE}/holdout_stage{st}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
