"""横断プロジェクト: 学習の手順を支える部分。
連続動画の間引き（地点・調査回の単位でも）、評価用の組（gold）の地点・調査回を母集団から除く（exclude_batches）、
組の評価（既存の判定・AI の判定 vs 目視）、組を検証用にした学習（holdout_batches）、まとめて書き出す CSV（1 行 = 1 動画 × 1 種、Season 列）。
実行: python -m pytest tests -q"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross, train  # noqa: E402
from trapnote.judge import save as judge_save, update_agree  # noqa: E402
from trapnote.project import Project  # noqa: E402

SEASONS = {"N240128": ("2024/01/28", ["BSN0812", "BSN0590", "BSN0620"]), "N240415": ("2024/04/15", ["BSN0812", "BSN0590", "BSN0700"])}
LABELS = ["Deer", "Boar", "Deer", "Empty", "Deer", "Boar", "RaccoonDog", "Deer"]


def make_nas(tmp_path):
    """2 調査回 × 3 地点 × 8 本。同じ地点の動画は 2 分おき（連続動画）。1 本は 2 種（Deer と Boar の 2 行）"""
    root = tmp_path / "nas" / "PRM_N"
    rows = []
    for se, (date, stations) in SEASONS.items():
        for st in stations:
            for i in range(8):
                name = f"IMG_{i:04d}.AVI"
                d = root / se / st
                d.mkdir(parents=True, exist_ok=True)
                (d / name).write_bytes(b"")
                rows.append((se, st, name, LABELS[i], f"{date} 13:{21 + 2 * i:02d}:00"))
    rows.append(("N240128", "BSN0812", "IMG_0001.AVI", "Deer", "2024/01/28 13:23:00"))        # 2 種目
    with open(root / "labels.csv", "w", encoding="utf-8", newline="") as fh:
        fh.write("Season,Station,File,Species,DateTime\n")
        for r in rows:
            fh.write(",".join(r) + "\n")
    return str(root), rows


def make_cross(tmp_path):
    place = tmp_path / "Studies" / "gold"
    place.mkdir(parents=True)
    p = Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")
    cross.add_sources_under(p, str(tmp_path / "nas" / "PRM_N"))
    cross.build_catalog(p)
    return p


def test_thin_in_units(tmp_path):
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        f = {"station": "BSN0812"}
        r0 = cross.catalog_sample(p, f, dict(unit="station", n=1, seed=1))
        assert r0["n"] == 16 and r0["n_pool"] == 16 and r0["n_pool_thin"] == 16
        # 2 分おきの 8 本を 5 分で間引くと 0, 6, 12 分の 3 本（調査回ごと）→ 地点 1 つで 6 本
        r = cross.catalog_sample(p, f, dict(unit="station", n=1, seed=1, gap_min=5))
        assert r["n"] == 6 and r["n_pool"] == 16 and r["n_pool_thin"] == 6 and r["units"] == [("BSN0812", 6)] and r["units_pool"] == [("BSN0812", 16)]
        names = {x[0] for x in p.db.q(f"SELECT name FROM catalog WHERE id IN ({','.join('?' * 6)})", r["ids"])}
        assert names == {"IMG_0000.AVI", "IMG_0003.AVI", "IMG_0006.AVI"}
        # 間引いてから各単位の上限・合計の上限
        r = cross.catalog_sample(p, {}, dict(unit="station", n=4, seed=1, gap_min=5, per_unit=4, total=10))
        assert r["n"] == 10 and all(n <= 4 for _, n in r["units"]) and r["n_pool_thin"] < r["n_pool"]
        r = cross.catalog_sample(p, {}, dict(unit="season", n=1, seed=1, gap_min=5))
        assert r["n"] == 9 and r["n_pool"] == 24
        # 動画・種ごとの単位は前からの通り（母数も返る）
        r = cross.catalog_sample(p, {}, dict(unit="video", n=100, seed=1, gap_min=5))
        assert r["n"] == 18 and r["n_pool"] == 48 and r["n_pool_thin"] == 18
        r = cross.catalog_sample(p, {}, dict(unit="species", n=100, seed=1, gap_min=5))
        assert r["n_pool_thin"] == 18 and sum(r["available"].values()) >= 18
    finally:
        p.close()


def _import(p, f, params, batch):
    r = cross.catalog_sample(p, f, params)
    cross.catalog_import(p, r["ids"], batch, dict(filters=f, params=params, counts=r["counts"], unit=r.get("unit"), units=r.get("units") or []))
    return r


def test_exclude_batches(tmp_path):
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        # gold = 地点 1 か所（BSN0812。両調査回）→ その地点が母集団から外れる
        _import(p, {"station": "BSN0812"}, dict(unit="station", n=1, seed=1), "gold")
        bu = cross.batch_units(p, ["gold"])
        assert bu["stations"] == {"BSN0812"} and not bu["seasons"] and not bu["cells"]
        c = cross.catalog_counts(p, {"exclude_batches": ["gold"]})
        assert c["total"] == 32 and c["excluded"] == dict(batches=["gold"], stations=1, seasons=0, cells=0)
        assert cross.catalog_counts(p, {})["total"] == 48
        # gold_s = 調査回 1 回（N240415。取り込み済みの BSN0812 は選ばれない）→ その調査回が丸ごと外れる
        _import(p, {"season": "N240415"}, dict(unit="season", n=1, seed=1), "gold_s")
        assert cross.batch_units(p, ["gold_s"])["seasons"] == {"N240415"}
        c = cross.catalog_counts(p, {"exclude_batches": ["gold", "gold_s"]})
        assert c["total"] == 16 and set(c["stations"]) == {"BSN0590", "BSN0620"} and c["seasons"] == ["N240128"]
        # 種ごとに選んだ組 → 動画のある 地点 × 調査回 の組だけが外れる
        r = _import(p, {"exclude_batches": ["gold", "gold_s"]}, dict(unit="species", n=1, seed=1), "round1")
        bu = cross.batch_units(p, ["round1"])
        assert not bu["stations"] and not bu["seasons"] and bu["cells"] and all(c.startswith("N240128/") for c in bu["cells"])
        c = cross.catalog_counts(p, {"exclude_batches": ["round1"]})
        assert c["total"] == 48 - 8 * len(bu["cells"])
        # ランダム選択の母集団からも外れる（取り込み済みも外れる）
        r = cross.catalog_sample(p, {"exclude_batches": ["gold", "gold_s"]}, dict(unit="station", n=10, seed=1))
        assert r["n_units_available"] == 2 and r["n"] == 16 - r["n"] * 0 - int(p.db.scalar("SELECT COUNT(*) FROM items WHERE batch='round1'"))
        # 組の一覧と評価用の印
        info = {b["batch"]: b for b in cross.batches_info(p)}
        assert info["gold"]["n"] == 16 and info["gold"]["imports"][0]["unit"] == "station" and info["gold"]["imports"][0]["n_units"] == 1 and not info["gold"]["eval"]
        assert cross.set_eval_batches(p, ["gold", "gold_s", "nosuch"]) == ["gold", "gold_s"]
        assert cross.eval_batches(p) == ["gold", "gold_s"] and {b["batch"]: b["eval"] for b in cross.batches_info(p)}["gold"]
        assert train.plan_counts(p)["eval_batches"] == ["gold", "gold_s"]
    finally:
        p.close()


def _set_auto(p, iid: int, codes: list[str], model: str = "speciesnet") -> None:
    p.db.x("UPDATE items SET det_state=1, cls_state=1, auto_json=? WHERE id=?", (json.dumps(dict(species=[dict(species=c, confidence=0.9) for c in codes], model=model)), iid))


def test_batch_report(tmp_path):
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        _import(p, {"station": "BSN0812", "season": "N240128"}, dict(unit="station", n=1, seed=1), "gold")
        items = {r["name"]: dict(r) for r in p.db.q("SELECT i.id, i.name, c.species FROM items i JOIN catalog c ON c.item_id=i.id WHERE i.batch='gold'")}
        assert len(items) == 8
        # 目視: 0 = Deer（合う）、1 = Deer+Boar（合う）、2 = Deer → Boar に直す（既存は誤り）、3 = Empty（合う）、4 = Deer → Boar（誤り）。5〜7 は未確認
        truth = {"IMG_0000.AVI": ["deer"], "IMG_0001.AVI": ["deer", "boar"], "IMG_0002.AVI": ["boar"], "IMG_0003.AVI": ["empty"], "IMG_0004.AVI": ["boar"]}
        for name, codes in truth.items():
            judge_save(p.db, items[name]["id"], dict(species=codes), "tester")
        # AI: 0 deer（合う）、1 deer（部分一致）、2 boar（合う）、3 なし、4 deer（不一致）
        _set_auto(p, items["IMG_0000.AVI"]["id"], ["deer"]); _set_auto(p, items["IMG_0001.AVI"]["id"], ["deer"]); _set_auto(p, items["IMG_0002.AVI"]["id"], ["boar"]); _set_auto(p, items["IMG_0004.AVI"]["id"], ["deer"])
        update_agree(p.db, [it["id"] for it in items.values()])
        r = cross.batch_report(p, "gold")
        assert r["n"] == 8 and r["n_verified"] == 5 and r["n_existing"] == 5 and r["n_ai"] == 4 and r["models"] == {"speciesnet": 4}
        ex = r["existing"]
        assert ex["n"] == 5 and ex["exact"] == 3 and ex["partial"] == 3 and ex["first"] == 3 and ex["low"] == 0
        # 2 種の動画（deer + boar）はコード順の第 1 種 boar の行に入る（既存も boar;deer なので列は boar）
        assert ex["by_species"]["boar"] == dict(n=3, exact=1, first=1, low=0) and ex["matrix"]["boar"] == {"boar": 1, "deer": 2}
        assert ex["by_species"]["deer"] == dict(n=1, exact=1, first=1, low=0) and ex["matrix"]["empty"] == {"empty": 1}
        ai = r["ai"]
        assert ai["n"] == 5 and ai["exact"] == 2 and ai["partial"] == 3 and ai["first"] == 3 and ai["low"] == 1
        assert ai["matrix"]["empty"] == {"(low)": 1} and ai["matrix"]["boar"] == {"boar": 1, "deer": 2} and "(low)" in ai["cols"] and ai["cols"][-1] == "(low)"
        assert cross.batch_report(p, "nosuch")["n"] == 0
        # 一覧の数字
        info = {b["batch"]: b for b in cross.batches_info(p)}
        assert info["gold"]["n_verified"] == 5 and info["gold"]["n_ai"] == 4 and info["gold"]["n_disagree"] == 1 and info["gold"]["n_partial"] == 1
    finally:
        p.close()


def test_export_merged_csv(tmp_path):
    root, rows = make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        out = os.path.join(p.dir, "exports", "merged.csv")
        r = cross.export_catalog(p, out, {}, long=True)
        assert r["n"] == 48 and r["n_rows"] == 49                                   # 2 種の動画は 2 行
        with open(out, encoding="utf-8-sig") as fh:
            lines = [ln.rstrip("\n").split(",") for ln in fh]
        assert lines[0][:5] == ["Season", "Station", "File", "DateTime", "Species"]
        two = [ln for ln in lines[1:] if ln[0] == "N240128" and ln[1] == "BSN0812" and ln[2] == "IMG_0001.AVI"]
        assert len(two) == 2 and {ln[4] for ln in two} == {"Deer", "Boar"} and all(ln[3] == "2024/01/28 13:23:00" for ln in two)
        assert {ln[0] for ln in lines[1:]} == set(SEASONS)
        empty = [ln for ln in lines[1:] if ln[2] == "IMG_0003.AVI"]
        assert empty and all(ln[4] == "Empty" for ln in empty)
        r = cross.export_catalog(p, out, {"status": "verified"}, long=True)
        assert r["n"] == 0
        r = cross.export_catalog(p, out, {}, long=False)
        assert r["n"] == 48 and r["n_rows"] == 48
    finally:
        p.close()


def _features(p, items: dict[int, str], dim: int = 8) -> None:
    """特徴量: 種ごとに分かれた乱数（deer は +、boar は -）。枠 2 つ"""
    rng = np.random.default_rng(0)
    for iid, code in items.items():
        rel = p.db.scalar("SELECT rel_path FROM items WHERE id=?", (iid,))
        fp = train.features_path(p, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        center = np.full(dim, 2.0 if code == "deer" else -2.0, dtype=np.float32)
        feats = (center + rng.normal(0, 0.5, (2, dim))).astype(np.float32)
        pc = np.array([[0.5, 0.5], [0.5, 0.5]], dtype=np.float32)           # 元の判定は決め手なし
        np.savez(fp, feats=feats, codes=np.array(["deer", "boar"]), pc=pc, t=np.array([0.5, 1.0], dtype=np.float32), bbox=np.zeros((2, 4), dtype=np.float32), conf=np.array([0.9, 0.9], dtype=np.float32))


def test_train_holdout_batches(tmp_path):
    """gold を検証用の組にすると、gold は学習に使われず（保存するモデルにも入らない）、gold での学習前・後の一致率が出る"""
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        _import(p, {"station": "BSN0812"}, dict(unit="station", n=1, seed=1), "gold")
        _import(p, {"exclude_batches": ["gold"]}, dict(unit="all"), "round1")
        rows = p.db.q("SELECT i.id, i.batch, c.species FROM items i JOIN catalog c ON c.item_id=i.id")
        truth = {}
        for r in rows:
            code = (r["species"] or "").split(";")[0]
            if code in ("deer", "boar"):
                judge_save(p.db, r["id"], dict(species=[code]), "tester")
                truth[r["id"]] = code
        _features(p, truth)
        p.db.x("UPDATE items SET det_state=1, cls_state=1")
        n_gold = int(p.db.scalar("SELECT COUNT(*) FROM items WHERE batch='gold' AND status='verified'"))
        assert n_gold and train.plan_counts(p)["batches"][0]["batch"] == "gold"
        opts = train.train_settings(p, dict(train_min_per_class=2, train_holdout_frac=0.5, train_epochs=30))
        opts["holdout_batches"] = ["gold"]
        r = train.run_train_a(p, opts, "m1")
        assert r["holdout_batches"] == ["gold"] and r["holdout_stations"] == [] and r["n_holdout"] == n_gold and r["final_without_holdout"]
        assert r["eval"]["n_items"] == n_gold and r["eval"]["batches"] == ["gold"] and r["eval"]["after"]["n"] == n_gold
        assert r["n_items"] == len(truth) - n_gold                                   # gold は学習に入っていない
        assert r["eval"]["after"]["exact"] >= r["eval"]["before"]["exact"]
        # 組を選ばなければ従来どおり地点の割合で
        opts["holdout_batches"] = []
        r2 = train.run_train_a(p, opts, "m2")
        assert r2["holdout_batches"] == [] and r2["holdout_stations"] and not r2["final_without_holdout"] and r2["n_items"] == len(truth)
    finally:
        p.close()
