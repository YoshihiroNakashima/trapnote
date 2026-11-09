"""近縁の種（上位の行）: 種リストの上下関係、追加学習の部分ラベル、判定のロールアップ、一致率の「上位で合う」。
イタチ科（種不明）と確定した動画の枠は、イタチ科の下位に学習する種（ニホンイタチ・ホンドテン）があれば「そのどれか」として学ぶ（損失 = -log(下位の確率の合計)）。
判定はどの種も p1 に届かないとき、使う上位の行で確率を足して届けばその行（要確認のまま）。人がイタチ科で AI がニホンイタチなら部分一致（上位で合う）"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import detect, train  # noqa: E402
from trapnote.judge import compare_codes, save as save_judgement, update_agree  # noqa: E402
from trapnote.project import Project  # noqa: E402
from trapnote.species import SpeciesList, Taxonomy, default_list, load_csv, save_csv  # noqa: E402


def _project(tmp_path, enable=("mustelidae",)):
    root = tmp_path / "N250101"
    root.mkdir(exist_ok=True)
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    rows = [dict(r) for r in p.species.rows]
    for r in rows:
        if r["code"] in enable:
            r["enabled"] = 1
    p.species.update(rows)
    return p


_N = [0]


def _item(p, station="ST1"):
    _N[0] += 1
    rel = f"{station}/a{_N[0]}.avi"
    with p.db.tx() as c:
        cur = c.execute("INSERT INTO items(rel_path, rel_key, folder, name, kind, station, status, det_state, cls_state) VALUES(?,?,?,?,?,?,?,?,?)",
                        (rel, rel.lower(), station, os.path.basename(rel), "video", station, "new", 1, 1))
        return cur.lastrowid


def test_taxonomy_default_list():
    tx = Taxonomy(default_list())
    assert tx.parent["weasel"] == "mustela" and tx.parent["mustela"] == "mustelidae" and tx.parent["marten"] == "mustelidae"
    assert tx.parent["badger"] == "mustelidae" and tx.parent["mustelidae"] == "carnivora" and tx.parent["carnivora"] == "mammal"
    assert tx.anc["weasel"] == ["mustela", "mustelidae", "carnivora", "mammal"]
    assert tx.desc["mustelidae"] == {"mustela", "weasel", "marten", "badger"}
    assert tx.parent["deer"] == "cervidae" and tx.parent["boar"] == "cetartiodactyla" and tx.parent["squirrel"] == "rodent"
    assert tx.parent["small_mammal"] == "mammal"            # 同じ Mammalia の行は SpeciesNet の対応がある哺乳類を代表に
    assert tx.parent["bird"] is None and tx.parent["mammal"] is None
    assert tx.level["mustela"] == "genus" and tx.level["mustelidae"] == "family" and tx.level["carnivora"] == "order"
    assert tx.related("mustelidae", "weasel") and tx.related("weasel", "mustelidae") and not tx.related("weasel", "marten")
    assert tx.match("weasel", {"mustelidae", "deer"}) == "mustelidae" and tx.match("deer", {"mustelidae"}) is None


def test_taxonomy_explicit_parent_and_cycle():
    rows = default_list()
    by = {r["code"]: r for r in rows}
    by["squirrel"]["parent"] = "small_mammal"                # 指定が勝つ
    by["hare"]["parent"] = "-"                               # 上位なし
    by["carnivora"]["parent"] = "weasel"                      # 輪（carnivora → weasel → … → carnivora）は指定を外す
    by["dog"]["parent"] = "nosuch"                            # ない行の指定は自動に
    tx = Taxonomy(rows)
    assert tx.parent["squirrel"] == "small_mammal" and tx.anc["squirrel"] == ["small_mammal", "mammal"]
    assert tx.parent["hare"] is None and tx.parent_auto["hare"] == "mammal"
    assert tx.parent["carnivora"] is None and "carnivora" in tx.anc["weasel"]
    assert tx.parent["dog"] == "carnivora"


def test_species_csv_parent_column(tmp_path):
    # 古い species.csv（parent 列なし）も読める（空 = 自動）。保存すると列が増える
    path = str(tmp_path / "species.csv")
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write("code,name_ja,name_en,name_sci,rank,csv_label,non_native,speciesnet,key,enabled\n")
        fh.write("weasel,ニホンイタチ,Japanese Weasel,Mustela itatsi,種,Weasel,,mustela,,1\n")
        fh.write("siberian,チョウセンイタチ,Siberian Weasel,Mustela sibirica,種,Siberian,1,mustela sibirica,,1\n")
        fh.write("mustelidae,イタチ科（種不明）,Weasel Family,Mustelidae,科,Mustelidae,,mustelidae,,1\n")
    sl = SpeciesList(path)
    assert [r["code"] for r in sl.rows] == ["weasel", "siberian", "mustelidae", "bird"]          # 古い種リストには鳥類が足される（今までどおり）
    assert all(r["parent"] == "" for r in sl.rows)
    tx = sl.taxonomy
    assert tx.parent["weasel"] == "mustelidae" and tx.parent["siberian"] == "mustelidae"       # イタチ属の行がなければ科へ
    assert sl.rollup_groups("family") == {"mustelidae": {"weasel", "siberian"}} and sl.rollup_groups("off") == {}
    d = {r["code"]: r for r in sl.to_dict()["species"]}
    assert d["weasel"]["parent_auto"] == "mustelidae" and d["weasel"]["parent_eff"] == "mustelidae" and d["mustelidae"]["n_desc"] == 2
    assert "parent_auto" not in sl.rows[0]                    # 画面に出す列は保存する行に混ぜない
    rows = [dict(r) for r in sl.rows]
    rows[1]["parent"] = "WEASEL"                              # コードは小文字に
    sl.update(rows)
    assert load_csv(path)[1]["parent"] == "weasel" and sl.taxonomy.parent["siberian"] == "weasel"
    # 種の行（ニホンイタチ）も指定すれば上位になれる。属・科の行ではないので family のロールアップの行き先にはならない
    assert sl.rollup_groups("family") == {"mustelidae": {"weasel", "siberian"}} and sl.rollup_groups("all")["weasel"] == {"siberian"}
    # 上位の行を使わなければロールアップの行き先にはならず、使わない種の行き先は「不明」
    rows = [dict(r) for r in sl.rows]
    rows[2]["enabled"] = 0
    sl.update(rows)
    assert sl.rollup_groups("family") == {} and sl.fold_target("mustelidae") == "unknown"


def test_aggregate_rollup():
    codes = ["weasel", "marten", "deer", "mustelidae", "empty"]
    groups = {"mustelidae": {"weasel", "marten", "mustela", "badger"}}
    pc = np.array([[0.40, 0.35, 0.05, 0.05, 0.15], [0.45, 0.30, 0.05, 0.05, 0.15], [0.30, 0.40, 0.10, 0.05, 0.15]], dtype=np.float32)
    assert detect.aggregate_video(pc, codes, 0.5, 0.6, 3)["species"] == []                    # 今までどおり: どの種も届かない = 要確認
    r = detect.aggregate_video(pc, codes, 0.5, 0.6, 3, groups=groups)
    assert r["species"][0][0] == "mustelidae" and r["rollup"] == "mustelidae" and abs(r["species"][0][1] - 0.8) < 0.011
    # 種が p1 に届けばまとめない
    pc2 = pc.copy()
    pc2[0] = [0.80, 0.05, 0.05, 0.0, 0.10]
    r2 = detect.aggregate_video(pc2, codes, 0.5, 0.6, 3, groups=groups)
    assert r2["species"][0][0] == "weasel" and "rollup" not in r2
    # 入れ子: 属（イタチ属 = ニホンイタチ + 属の列）が届けば科ではなく属
    codes3 = ["weasel", "mustela", "marten", "empty"]
    groups3 = {"mustela": {"weasel"}, "mustelidae": {"weasel", "mustela", "marten"}}
    pc3 = np.array([[0.45, 0.10, 0.30, 0.15]] * 3, dtype=np.float32)
    assert detect.aggregate_video(pc3, codes3, 0.5, 0.6, 3, groups=groups3)["rollup"] == "mustela"
    pc4 = np.array([[0.30, 0.10, 0.45, 0.15]] * 3, dtype=np.float32)
    assert detect.aggregate_video(pc4, codes3, 0.5, 0.6, 3, groups=groups3)["rollup"] == "mustelidae"
    # 第 2 種はまとめた行の下位以外から（シカが p2 以上 K 枚以上なら加わる。下位のテンは加えない）
    codes5 = ["weasel", "marten", "deer", "empty"]
    pc5 = np.array([[0.30, 0.25, 0.05, 0.40]] * 3 + [[0.02, 0.0, 0.48, 0.5]] * 3, dtype=np.float32)
    pc5[3:, 2] = 0.62
    pc5[3:, 3] = 0.36
    r5 = detect.aggregate_video(pc5, codes5, 0.5, 0.6, 3, "confidence", {"mustelidae": {"weasel", "marten"}})
    assert [c for c, _ in r5["species"]] == ["deer"]                                           # シカが p1 に届くのでまとめない
    pc6 = np.array([[0.30, 0.25, 0.05, 0.40]] * 3 + [[0.02, 0.0, 0.48, 0.5]] * 3, dtype=np.float32)
    r6 = detect.aggregate_video(pc6, codes5, 0.5, 0.45, 3, "confidence", {"mustelidae": {"weasel", "marten"}})
    assert [c for c, _ in r6["species"]] == ["mustelidae", "deer"] and r6["rollup"] == "mustelidae"


class _Hub:
    detector_name = "md"

    def cls_label(self):
        return "test"


def test_finish_item_rollup_is_low(tmp_path):
    p = _project(tmp_path)
    try:
        assert p.setting("species_rollup") == "family"
        assert detect.rollup_groups(p) == {"mustelidae": {"mustela", "weasel", "marten", "badger"}}
        p.update_settings(dict(species_rollup="off"))
        assert detect.rollup_groups(p) == {}
        p.update_settings(dict(species_rollup="all"))
        assert "carnivora" not in detect.rollup_groups(p)                                       # 食肉目は使っていない行
        p.update_settings(dict(species_rollup="family"))
        summary = dict(animal=True, person=False, vehicle=False, max_animal=0.9, max_person=0.0, max_vehicle=0.0, n_frames=10, animal_frames=5)
        iid = _item(p)
        cls = dict(species=[("mustelidae", 0.8)], probs={}, empty_mean=0.1, empty_frac=0.0, rollup="mustelidae")
        detect.finish_item(p, _Hub(), dict(id=iid, rel_path="ST1/a.avi"), dict(summary), cls)
        row = p.db.q1("SELECT auto_json, flag FROM items WHERE id=?", (iid,))
        assert json.loads(row["auto_json"])["species"][0]["species"] == "mustelidae" and row["flag"] == "low"
        assert [r[0] for r in p.db.q("SELECT species FROM judgements WHERE item_id=?", (iid,))] == ["mustelidae"]
        # 種まで決まったときは今までどおり要確認にしない
        iid2 = _item(p)
        detect.finish_item(p, _Hub(), dict(id=iid2, rel_path="ST1/b.avi"), dict(summary), dict(species=[("weasel", 0.9)], probs={}, empty_mean=0.0, empty_frac=0.0))
        assert p.db.q1("SELECT flag FROM items WHERE id=?", (iid2,))["flag"] is None
    finally:
        p.close()


def test_compare_codes_and_agree(tmp_path):
    tx = Taxonomy(default_list())
    assert compare_codes({"mustelidae"}, ["weasel"]) == 3                       # 上下関係を見なければ不一致
    assert compare_codes({"mustelidae"}, ["weasel"], tx) == 2                   # 見れば部分一致（上位で合う）
    assert compare_codes({"weasel"}, ["mustelidae"], tx) == 2
    assert compare_codes({"weasel"}, ["marten"], tx) == 3 and compare_codes({"weasel"}, ["weasel"], tx) == 1
    p = _project(tmp_path)
    try:
        a, b = _item(p), _item(p)
        for iid, code in ((a, "weasel"), (b, "marten")):
            p.db.x("UPDATE items SET auto_json=? WHERE id=?", (json.dumps(dict(model="m", species=[dict(species=code, confidence=0.9)])), iid))
        save_judgement(p.db, a, dict(species=["mustelidae"]), "me")
        save_judgement(p.db, b, dict(species=["weasel"]), "me")
        update_agree(p.db, [a, b])
        assert p.db.scalar("SELECT agree FROM items WHERE id=?", (a,)) == 2 and p.db.scalar("SELECT agree FROM items WHERE id=?", (b,)) == 3
        # 一致率の集計: 上位で合うは一致に数えず、別に数える
        ev = detect.evaluate(p, {})
        assert ev["n_model"] == 2 and ev["correct"] == 0 and ev["n_group"] == 1 and ev["by_species"]["mustelidae"]["group"] == 1
    finally:
        p.close()


def test_fold_disabled_to_parent(tmp_path):
    p = _project(tmp_path)
    try:
        rows = [dict(r) for r in p.species.rows]
        for r in rows:
            if r["code"] == "weasel":
                r["enabled"] = 0                             # ニホンイタチを外し、イタチ科は使う
        p.species.update(rows)
        codes, pc = train.fold_disabled(p, ["weasel", "marten", "deer", "empty"], np.array([[0.5, 0.2, 0.2, 0.1]], dtype=np.float32))
        assert codes == ["mustelidae", "marten", "deer", "empty"] and np.allclose(pc, [[0.5, 0.2, 0.2, 0.1]])
        codes, pc = train.fold_disabled(p, ["weasel", "mustelidae", "deer"], np.array([[0.4, 0.3, 0.3]], dtype=np.float32))
        assert codes == ["mustelidae", "deer"] and np.allclose(pc, [[0.7, 0.3]])
        rows = [dict(r) for r in p.species.rows]
        for r in rows:
            if r["code"] in ("mustelidae", "carnivora", "mammal"):
                r["enabled"] = 0
        p.species.update(rows)
        assert train.fold_disabled(p, ["weasel", "deer"], np.array([[0.6, 0.4]], dtype=np.float32))[0] == ["unknown", "deer"]
    finally:
        p.close()


def _it(iid, labels, station="S1", hard=False):
    return dict(id=iid, labels=labels, station=station, excluded=False, hard=hard, holdout=False)


def test_build_dataset_partial():
    tx = Taxonomy(default_list())
    items = {1: _it(1, ["weasel"] * 6), 2: _it(2, ["marten"] * 6), 3: _it(3, ["mustelidae"] * 3), 4: _it(4, ["deer"] * 6), 5: _it(5, ["mustela"] * 2)}
    opts = dict(train_min_per_class=5)
    ds = train.build_dataset(items, set(), opts, tax=tx)
    assert set(ds["classes"]) == {"weasel", "marten", "deer"} and "mustelidae" not in ds["dropped"]
    w, m = ds["classes"].index("weasel"), ds["classes"].index("marten")
    part = [smp for smp in ds["samples"] if smp[0] == 3]
    assert len(part) == 3 and all(smp[2] == tuple(sorted((w, m))) for smp in part)          # イタチ科 = ニホンイタチかホンドテン
    assert all(smp[2] == (w,) for smp in ds["samples"] if smp[0] == 5)                      # イタチ属 = ニホンイタチだけ（種リストにある属の種）
    assert ds["partial"] == {"mustelidae": dict(n=3, to=sorted(["weasel", "marten"], key=ds["classes"].index)), "mustela": dict(n=2, to=["weasel"])}
    assert ds["n_partial"] == 5
    M = train.label_mask([smp[2] for smp in ds["samples"]], len(ds["classes"]))
    assert M.sum() == 6 + 6 + 6 + 3 * 2 + 2
    # 上下関係を見なければ今までどおり（イタチ科は枠が少ないので外れる）
    ds0 = train.build_dataset(items, set(), opts)
    assert ds0["dropped"] == {"mustelidae": 3, "mustela": 2} and ds0["n_partial"] == 0 and ds0["partial"] == {}
    # 下位に学習する種がない上位の行は、今までどおり 1 つの種として学ぶ
    items2 = {1: _it(1, ["mustelidae"] * 6), 2: _it(2, ["deer"] * 6), 3: _it(3, ["weasel"] * 2)}
    ds2 = train.build_dataset(items2, set(), opts, tax=tx)
    assert set(ds2["classes"]) == {"mustelidae", "deer"} and ds2["dropped"] == {"weasel": 2} and ds2["n_partial"] == 0


def test_train_head_partial_labels():
    """部分ラベルの勾配と、学習の結果: イタチ科の枠（イタチとテンの中間の特徴）はイタチ + テンの確率が高く、どちらかに決めつけない"""
    rng = np.random.default_rng(0)
    K, D = 3, 6
    cen = {0: np.r_[3, 0, 0, 0, 0, 0], 1: np.r_[0, 3, 0, 0, 0, 0], 2: np.r_[0, 0, 0, 0, 3, 3]}          # weasel, marten, deer
    X, y = [], []
    for c, mu in cen.items():
        for _ in range(60):
            X.append(mu + rng.normal(0, 0.7, D))
            y.append(c)
    Xp = [np.r_[1.5, 1.5, 0, 0, 0, 0] + rng.normal(0, 0.7, D) for _ in range(60)]      # イタチ科（種不明）
    Xa = np.array(X + Xp, dtype=np.float32)
    M = train.label_mask(y + [(0, 1)] * 60, K)
    w = np.ones(len(Xa), dtype=np.float32)
    prm, rec = train.train_head(Xa, M, w, K, dict(train_epochs_a=60, train_lr_a=0.05, train_seed=1))
    h = train.Head(["weasel", "marten", "deer"], prm["W"], prm["b"], prm["mu"], prm["sd"])
    pp = h.predict(np.array(Xp, dtype=np.float32))
    assert (pp[:, 0] + pp[:, 1]).mean() > 0.9 and pp[:, 2].mean() < 0.1
    assert rec["history"][-1]["val_acc"] > 0.9                         # 当たり = 第 1 候補が印の中
    # 印が 1 つなら番号のラベルと同じ結果（従来の交差エントロピー）
    Xs = np.array(X, dtype=np.float32)
    a, _ = train.train_head(Xs, np.array(y), np.ones(len(Xs), dtype=np.float32), K, dict(train_epochs_a=5, train_seed=3))
    b, _ = train.train_head(Xs, train.label_mask(y, K), np.ones(len(Xs), dtype=np.float32), K, dict(train_epochs_a=5, train_seed=3))
    assert np.allclose(a["W"], b["W"], atol=1e-5) and np.allclose(a["b"], b["b"], atol=1e-5)
    # 勾配（p - q）を数値微分で確かめる: 損失 = -log(印の確率の合計)
    z = rng.normal(0, 1, (4, K))
    Mq = np.array([[1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 0]], dtype=bool)

    def loss(zz):
        p = np.exp(zz - zz.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        return float(-np.log((p * Mq).sum(1)).sum())
    p = np.exp(z - z.max(1, keepdims=True))
    p /= p.sum(1, keepdims=True)
    q = np.where(Mq, p, 0)
    q /= q.sum(1, keepdims=True)
    num = np.zeros_like(z)
    for i in range(z.shape[0]):
        for j in range(K):
            e = np.zeros_like(z)
            e[i, j] = 1e-5
            num[i, j] = (loss(z + e) - loss(z - e)) / 2e-5
    assert np.allclose(num, p - q, atol=1e-5)
    assert np.allclose(train._masked_lse(z, Mq), np.log(np.where(Mq, np.exp(z), 0).sum(1)))


def test_collect_two_species_with_group(tmp_path):
    """シカとイタチ科が写った動画: モデルの第 1 候補がニホンイタチ・ホンドテンの枠はイタチ科（部分ラベル）、シカの枠はシカ"""
    p = _project(tmp_path)
    try:
        iid = _item(p)
        rel = p.db.scalar("SELECT rel_path FROM items WHERE id=?", (iid,))
        fp = train.features_path(p, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        codes = ["deer", "weasel", "marten", "empty"]
        pc = np.array([[0.9, 0.05, 0.0, 0.05], [0.1, 0.7, 0.1, 0.1], [0.1, 0.2, 0.6, 0.1], [0.1, 0.1, 0.1, 0.7]], dtype=np.float32)
        np.savez(fp, feats=np.zeros((4, 8), dtype=np.float32), codes=np.array(codes), pc=pc, t=np.arange(4, dtype=np.float32),
                 bbox=np.tile(np.array([[0.1, 0.1, 0.2, 0.2]], dtype=np.float32), (4, 1)), conf=np.array([0.9, 0.8, 0.95, 0.7], dtype=np.float32))
        save_judgement(p.db, iid, dict(species=["deer", "mustelidae"]), "me")
        col = train.collect(p, train.train_settings(p, dict(train_frames="all")))
        assert col["items"][iid]["labels"] == ["deer", "mustelidae", "mustelidae", None]
        # 1 動画 1 枚: 種ごとの学習フレーム（イタチ科はモデルがイタチ・テンと言う枠のうち信頼度最大 = k=2）
        col1 = train.collect(p, train.train_settings(p))
        assert col1["items"][iid]["anchors"] == {"deer": 0, "mustelidae": 2} and col1["items"][iid]["labels"] == ["deer", None, "mustelidae", None]
        # 人が k=1（モデルはニホンイタチ）を選んで確定 → イタチ科の学習フレーム
        assert train.set_anchor(p, iid, 1) == {"mustelidae": 1}
        ic = train.item_crops(p, iid)
        assert ic["anchors"] == {"deer": 0, "mustelidae": 1}
    finally:
        p.close()


def test_run_train_a_partial(tmp_path):
    """方式 A を通しで: イタチ科と確定した動画は部分ラベルになり、イタチ科は種として学ばない。評価は上位で合うも数える"""
    p = _project(tmp_path)
    try:
        rng = np.random.default_rng(5)
        cen = {"weasel": np.r_[3, 0, 0, 0, 0, 0, 0, 0], "marten": np.r_[0, 3, 0, 0, 0, 0, 0, 0], "deer": np.r_[0, 0, 0, 0, 3, 3, 0, 0],
               "mustelidae": np.r_[1.5, 1.5, 0, 0, 0, 0, 0, 0]}
        base = {"weasel": [0.3, 0.3, 0.1, 0.3], "marten": [0.3, 0.3, 0.1, 0.3], "deer": [0.1, 0.1, 0.7, 0.1], "mustelidae": [0.3, 0.3, 0.1, 0.3]}
        for st in ("S1", "S2", "S3", "S4", "S5"):
            for code in ("weasel", "marten", "deer", "mustelidae"):
                for _ in range(3):
                    iid = _item(p, st)
                    rel = p.db.scalar("SELECT rel_path FROM items WHERE id=?", (iid,))
                    fp = train.features_path(p, rel)
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    feats = (cen[code] + rng.normal(0, 0.6, (2, 8))).astype(np.float32)
                    np.savez(fp, feats=feats, codes=np.array(["weasel", "marten", "deer", "empty"]), pc=np.array([base[code]] * 2, dtype=np.float32),
                             t=np.array([0.5, 1.0], dtype=np.float32), bbox=np.zeros((2, 4), dtype=np.float32), conf=np.array([0.9, 0.9], dtype=np.float32))
                    save_judgement(p.db, iid, dict(species=[code]), "me")
        opts = train.train_settings(p, dict(train_min_per_class=3, train_holdout_frac=0.4, train_frames="all", train_epochs_a=40, train_lr_a=0.05))
        r = train.run_train_a(p, opts, "pl")
        assert "mustelidae" not in r["classes"] and set(r["classes"]) == {"weasel", "marten", "deer"}
        assert r["partial"]["mustelidae"]["to"] and set(r["partial"]["mustelidae"]["to"]) == {"weasel", "marten"} and r["n_partial"] > 0
        assert r["rollup"] == "family"
        ev = r["eval"]
        # 学習前（元の確率は決め手なし）はイタチ類が要確認かロールアップのイタチ科、学習後はイタチ・テンが当たる
        assert ev["after"]["exact"] > ev["before"]["exact"]
        assert "group" in ev["after"] and ev["after"]["by_species"]["mustelidae"]["n"] > 0
        h = train.Head.load(os.path.join(p.dir, "models", "pl"), r)
        pp = h.predict(np.tile(cen["mustelidae"].astype(np.float32), (1, 1)))[0]
        i_w, i_m, i_d = (h.classes.index(c) for c in ("weasel", "marten", "deer"))
        assert pp[i_w] + pp[i_m] > 0.8 and pp[i_d] < 0.2
        with open(os.path.join(p.dir, "models", "pl", "model.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["partial"]["mustelidae"]["n"] > 0
    finally:
        p.close()


def test_finetune_partial_loss():
    """方式 B の損失（torch があるときだけ）: 部分ラベルの交差エントロピーと、finetune が部分ラベルの枠で回ること"""
    torch = pytest.importorskip("torch")
    z = torch.tensor([[2.0, 1.0, -1.0], [0.5, 0.2, 3.0]])
    M = torch.tensor([[True, True, False], [False, False, True]])
    got = train.partial_ce(z, M)
    p = torch.softmax(z, 1)
    assert torch.allclose(got, -torch.log((p * M).sum(1)), atol=1e-6)
    one = torch.tensor([[False, True, False], [True, False, False]])
    assert torch.allclose(train.partial_ce(z, one), torch.nn.functional.cross_entropy(z, torch.tensor([1, 0]), reduction="none"), atol=1e-6)


def test_finetune_runs_with_partial(tmp_path):
    torch = pytest.importorskip("torch")
    from PIL import Image

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.body = torch.nn.Linear(3, 8)
            self.head = torch.nn.Linear(8, 3)

        def forward(self, x):
            return self.head(torch.relu(self.body(x.mean(dim=(1, 2)))))
    torch.manual_seed(0)
    model = Tiny()
    paths, samples = {}, []
    colors = {0: (220, 30, 30), 1: (30, 220, 30), 2: (30, 30, 220)}
    for i in range(24):
        c = i % 4
        col = colors[c] if c < 3 else (125, 125, 30)                     # 4 番目 = 赤か緑のどちらか（部分ラベル）
        pth = str(tmp_path / f"{i}.jpg")
        Image.new("RGB", (480, 480), col).save(pth)
        paths[(i, 0)] = pth
        samples.append((i, 0, (0, 1) if c == 3 else c, 1.0))
    state, rec = train.finetune(model, "cpu", samples, paths, 3, dict(train_epochs_b=3, train_batch_b=8, train_lr_b=1e-2, train_unfreeze=1.0), samples[:8])
    assert rec["history"] and all(h["train_loss"] == h["train_loss"] for h in rec["history"])          # NaN にならない
    assert rec["history"][-1]["val_acc"] is not None


def test_run_train_b_partial(tmp_path, monkeypatch):
    """方式 B を通しで（torch があるときだけ。偽の小さな torch のモデル）: 枠の切り出し → 部分ラベル込みの学習 → 評価 → 保存"""
    pytest.importorskip("torch")
    cv2 = pytest.importorskip("cv2")
    from trapnote import scan
    monkeypatch.setenv("TRAPNOTE_FAKE_ML", "torch")
    root = tmp_path / "N240128"
    plan = []
    for si, st in enumerate(("BSN0001", "BSN0002", "BSN0003", "BSN0004")):
        (root / st).mkdir(parents=True)
        for k, code in enumerate(("deer", "boar", "cetartiodactyla", "deer", "boar")):
            path = root / st / f"IMG_{k:04d}.AVI"
            w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
            for i in range(40):
                fr = np.full((120, 160, 3), 40, np.uint8)
                x = 10 + (i * 2) % 100
                cv2.rectangle(fr, (x, 30), (x + 40, 80), (220, 220, 220 - 30 * k), -1)        # 明るい塊が右へ動く（偽の検出器が枠を返す）
                w.write(fr)
            w.release()
            plan.append((f"{st}/IMG_{k:04d}.AVI", code))
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    try:
        rows = [dict(r) for r in p.species.rows]
        for r in rows:
            if r["code"] == "cetartiodactyla":
                r["enabled"] = 1                                     # 鯨偶蹄目（種不明）を使う = シカとイノシシの上位
        p.species.update(rows)
        scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
        p.db.x("UPDATE items SET duration=4.0, fps=10.0, meta_state=1, width=160, height=120")
        hub = detect.ModelHub(dict(device="cpu", batch_size=4), str(tmp_path / "models"))
        hub.load_detector("auto")
        hub.load_classifier("speciesnet-test")
        for rel, code in plan:
            item = dict(p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE rel_path=?", (rel.replace("/", os.sep),))
                        or p.db.q1("SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE rel_path=?", (rel,)))
            detect.process_item(p, hub, item, True)
            save_judgement(p.db, item["id"], dict(species=[code]), "me")
        assert int(p.db.scalar("SELECT COUNT(*) FROM items WHERE status='verified' AND det_state=1 AND cls_state=1")) == len(plan)
        opts = train.train_settings(p, dict(train_min_per_class=2, train_min_conf=0.0, train_holdout_frac=0.5, train_frames="all", train_epochs_b=2, train_batch_b=8, train_unfreeze=1.0))
        r = train.run_train_b(p, hub, opts, "B_partial")
        assert r["kind"] == "B" and set(r["classes"]) >= {"deer", "boar"} and "cetartiodactyla" not in r["classes"]
        assert set(r["partial"]["cetartiodactyla"]["to"]) == {"deer", "boar"} and r["n_partial"] > 0
        assert r["eval"] is not None and "group" in r["eval"]["after"]
        assert os.path.isfile(os.path.join(p.dir, "models", "B_partial", "finetune.pt"))
    finally:
        p.close()
