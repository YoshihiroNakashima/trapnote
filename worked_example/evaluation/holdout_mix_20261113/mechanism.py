# タヌキの動画で、なぜ「元の SpeciesNet を 3 割混ぜる」と答えが変わるのかを、枠ごとの確率で見る（検証用の地点の動画。gold は使わない）。
# holdout_mix.py が写しのプロジェクトに保存した「検証用の地点を除いて学習した分類器」（models/S<段階>_holdout）を使う。読むだけ（結果は mechanism_stage<段階>.json と画面）。
import collections, json, os, sys
import holdout_mix as HM
import numpy as np
from trapnote import train
from trapnote.detect import project_settings
from trapnote.project import Project

stage = int(sys.argv[1]) if len(sys.argv) > 1 else 3
SPECIES = sys.argv[2:] or ["raccoon_dog", "weasel", "boar", "raccoon"]
real_name, src_names = HM.STAGES[stage]
P = Project(HM.scratch("First_200"))
sources = [HM.scratch(n) for n in src_names]
opts = train.train_settings(P, {})
opts["holdout_stations"] = None
opts["holdout_batches"] = []
s = project_settings(P)
p1 = float(s["species_p1"])
col = train.collect_all(P, sources, opts)
out = {}
try:
    items = col["items"]
    folder = f"{P.dir}/models/S{stage}_holdout"
    info = json.load(open(folder + "/model.json", encoding="utf-8"))
    head = train.Head.load(folder, info)
    assert head.base_mix == 0.3
    holdout = set(info["holdout_stations"])
    classes = frozenset(head.classes)

    def top3(pc, j):
        c = np.sort(pc[:, j])[::-1]
        c = c[c >= p1][:3]
        return float(c.mean()) if len(c) else 0.0

    for sp in SPECIES:
        js = head.classes.index(sp)
        crops = collections.defaultdict(list)
        vids = []
        for iid, it in items.items():
            if it["station"] not in holdout or it["feats"] is None:
                continue
            hs = sorted(h for h in it["human"] if h not in HM.NOT_ANIMAL)
            if hs != [sp]:
                continue
            codes, pc = it["codes"], it["pc"]
            ph = head.predict(it["feats"])
            cm, pm = head.apply(it["feats"], codes, pc)                 # 混ぜる 0.3（アプリ）
            cm, pm = train.fold_disabled(it["proj"], cm, pm)
            h0 = train.Head(head.classes, head.W, head.b, head.mu, head.sd, 0.0)
            c0, p0 = h0.apply(it["feats"], codes, pc)                   # 混ぜない
            c0, p0 = train.fold_disabled(it["proj"], c0, p0)
            sn = pc[:, codes.index(sp)] if sp in codes else np.zeros(len(pc), dtype=np.float32)
            crops["head"] += [float(x) for x in ph[:, js]]
            crops["sn"] += [float(x) for x in sn]
            crops["mixed"] += [float(x) for x in pm[:, cm.index(sp)]]
            a3, a0 = HM.CG.first(pm, cm, s), HM.CG.first(p0, c0, s)
            # 検算: 「混ぜない」の答えを、apply を通さずに、新しい分類器の確率だけから直接出したものと比べる
            assert HM.CG.first(ph, list(head.classes), s) == a0, (it["rel_path"], a0)
            # 仕組みの確かめ: 第 1 種を「p1 以上の枠の枚数がいちばん多い種」で決める規則（species_pick = count）なら、混ぜたままでどうなるか
            s_cnt = {**s, "species_pick": "count"}
            v_cnt = (HM.CG.first(pm, cm, s_cnt), HM.CG.first(p0, c0, s_cnt))
            v = dict(rel=it["rel_path"], n=len(pc), a3=a3, a0=a0, a3_count=v_cnt[0], a0_count=v_cnt[1],
                     sp_n3=int((pm[:, cm.index(sp)] >= p1).sum()), sp_top3=round(top3(pm, cm.index(sp)), 3),
                     sp_n0=int((p0[:, c0.index(sp)] >= p1).sum()), sp_top0=round(top3(p0, c0.index(sp)), 3))
            if a3 in cm and a3 != sp:
                jw = cm.index(a3)
                v.update(w_n3=int((pm[:, jw] >= p1).sum()), w_top3=round(top3(pm, jw), 3),
                         w_sn=round(float(pc[:, codes.index(a3)].max()) if a3 in codes else 0.0, 3),                # 元の SpeciesNet がその種に付けた確率（枠の最大）
                         w_head=round(float(ph[:, head.classes.index(a3)].max()) if a3 in head.classes else 0.0, 3))  # 新しい分類器がその種に付けた確率（枠の最大）
            vids.append(v)
        q = lambda k: dict(n=len(crops[k]), mean=round(float(np.mean(crops[k])), 3), median=round(float(np.median(crops[k])), 3), p95=round(float(np.percentile(crops[k], 95)), 3),
                           max=round(float(np.max(crops[k])), 3), ge_p1=round(float(np.mean(np.asarray(crops[k]) >= p1)), 3))
        flip = [v for v in vids if v["a3"] != sp and v["a0"] == sp]            # 混ぜると間違い、混ぜないと正解
        d = dict(n_videos=len(vids), ok_03=sum(v["a3"] == sp for v in vids), ok_00=sum(v["a0"] == sp for v in vids),
                 ok_03_count=sum(v["a3_count"] == sp for v in vids), ok_00_count=sum(v["a0_count"] == sp for v in vids), crops_head=q("head"), crops_sn=q("sn"), crops_mixed=q("mixed"),
                 n_flip=len(flip),
                 flip_sp_more_crops=sum(1 for v in flip if v.get("w_n3") is not None and v["sp_n3"] > v["w_n3"]),      # p1 以上の枠はタヌキのほうが多いのに、上位 3 枚の平均で負けた
                 flip_sp_same_crops=sum(1 for v in flip if v.get("w_n3") is not None and v["sp_n3"] == v["w_n3"] and v["sp_n3"] > 0),
                 flip_sp_fewer_crops=sum(1 for v in flip if v.get("w_n3") is not None and 0 < v["sp_n3"] < v["w_n3"]),
                 flip_sp_no_crop=sum(1 for v in flip if v["sp_n3"] == 0),                                             # 混ぜるとタヌキが p1 に届く枠が 1 枚もなくなった
                 flip_no_answer=sum(1 for v in flip if v["a3"] in ("(low)", "empty")),
                 flip_winner=dict(collections.Counter(v["a3"] for v in flip).most_common()),
                 flip_sp_top3_max=max([v["sp_top3"] for v in flip], default=None), flip_w_top3_mean=round(float(np.mean([v["w_top3"] for v in flip if "w_top3" in v])), 3) if flip else None,
                 flip_w_sn_mean=round(float(np.mean([v["w_sn"] for v in flip if "w_sn" in v])), 3) if flip else None,
                 flip_w_head_mean=round(float(np.mean([v["w_head"] for v in flip if "w_head" in v])), 3) if flip else None,
                 sp_top3_max_all=max(v["sp_top3"] for v in vids), sp_top0_median=round(float(np.median([v["sp_top0"] for v in vids])), 3), sp_top3_median=round(float(np.median([v["sp_top3"] for v in vids])), 3))
        fw = [v for v in flip if "w_n3" in v]
        d.update(flip_sp_n3_median=float(np.median([v["sp_n3"] for v in fw])) if fw else None, flip_w_n3_median=float(np.median([v["w_n3"] for v in fw])) if fw else None,
                 flip_w_n3_dist={str(k): n for k, n in sorted(collections.Counter(v["w_n3"] for v in fw).items())},
                 flip_sp_top3_median=round(float(np.median([v["sp_top3"] for v in fw])), 3) if fw else None, flip_w_top3_median=round(float(np.median([v["w_top3"] for v in fw])), 3) if fw else None)
        print(sp, json.dumps(d, ensure_ascii=False))
        d["flips"] = flip
        out[sp] = d
        for v in flip[:6]:
            print("    例", v)
finally:
    train.close_sources(col["projects"][1:])
    P.close()
json.dump(out, open(f"{HM.HERE}/mechanism_stage{stage}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
