"""方式 A の「元のモデルを混ぜる割合」（train_base_mix）の既定は 0（混ぜない）。2026-11-13 に 0.3 から変えた（確認事項 121 回目）。

変えた理由（確認事項 119・120 回目）: 0.3 では、元の SpeciesNet が判定できない種（タヌキ）を間違えやすかった。
学習に出てこなかった区分の確率は最大でも混ぜる割合なので、基準の線（0.5）より小さい割合では、その区分が答えになることもなかった。

ここで見ること:
- 新しいプロジェクトの既定は 0。学習の設定（train_settings）も 0。
- 古い既定（0.3）のままのプロジェクトは、開いたときに 0 に直る。0.3 でない値と、人が 0.3 と決めたもの（base_mix_fixed）はそのまま。
- 設定を既定と違う数値にすると「人が決めた」の印が付き、開き直しても直らない。既定に戻すと印が外れる。
- 印の名前（base_mix_fixed）は train_ で始めないので、学習の記録（model.json の settings）には入らない。
- 混ぜる割合 0 の答えは、新しい分類器の確率そのもの（学習していない区分の確率は 0）。0.3 では、学習していない区分の確率は最大でも 0.3。
- 保存してあるモデルは、記録（model.json の base_mix）の割合のまま。記録に割合のない古いモデルは 0.3。
- 学習（run_train_a）は、設定の割合をモデルの記録に書く。分類器の重みは、割合が違っても同じ"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import train  # noqa: E402
from trapnote.judge import save as save_judgement  # noqa: E402
from trapnote.project import PROJECT_DEFAULTS, PROJECT_JSON, Project  # noqa: E402

from test_static_boxes import _fake_detect  # noqa: E402


def _new_project(tmp_path, stations=("BSN0001",), per_station=0):
    root = tmp_path / "N240128"
    for st in stations:
        (root / st).mkdir(parents=True)
        for i in range(per_station):
            (root / st / f"IMG_{i:04d}.AVI").write_bytes(b"")
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    if per_station:
        from trapnote import scan
        scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    return p


def _set_stored(pdir: str, **settings) -> None:
    """project.json の settings を、アプリを通さずに書き換える（前のアプリが作ったプロジェクトのつもり）。None はその項目を消す"""
    fp = os.path.join(pdir, PROJECT_JSON)
    with open(fp, encoding="utf-8") as fh:
        d = json.load(fh)
    for k, v in settings.items():
        if v is None:
            d["settings"].pop(k, None)
        else:
            d["settings"][k] = v
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)


def _stored(pdir: str) -> dict:
    with open(os.path.join(pdir, PROJECT_JSON), encoding="utf-8") as fh:
        return json.load(fh)["settings"]


def test_default_is_no_mix(tmp_path):
    assert PROJECT_DEFAULTS["train_base_mix"] == 0.0 and PROJECT_DEFAULTS["base_mix_fixed"] is False
    assert train.TRAIN_DEFAULTS["train_base_mix"] == 0.0 and "base_mix_fixed" not in train.TRAIN_DEFAULTS
    p = _new_project(tmp_path)
    try:
        assert p.setting("train_base_mix") == 0.0 and p.setting("base_mix_fixed") is False
        s = train.train_settings(p)
        assert s["train_base_mix"] == 0.0 and "base_mix_fixed" not in s
        assert _stored(p.dir)["train_base_mix"] == 0.0
    finally:
        p.close()


def test_old_default_becomes_zero_on_open(tmp_path):
    p = _new_project(tmp_path)
    pdir = p.dir
    p.close()
    # 古い既定 0.3 のまま（印の項目そのものがない = 変える前のアプリが保存したプロジェクト）→ 開くと 0 になり、保存もされる
    _set_stored(pdir, train_base_mix=0.3, base_mix_fixed=None)
    p = Project(pdir)
    try:
        assert p.setting("train_base_mix") == 0.0 and p.setting("base_mix_fixed") is False
        assert train.train_settings(p)["train_base_mix"] == 0.0
    finally:
        p.close()
    assert _stored(pdir)["train_base_mix"] == 0.0
    # 0.3 でない値は、そのまま
    _set_stored(pdir, train_base_mix=0.2, base_mix_fixed=None)
    p = Project(pdir)
    try:
        assert p.setting("train_base_mix") == 0.2
    finally:
        p.close()
    # 人が 0.3 と決めたもの（印あり）は、そのまま
    _set_stored(pdir, train_base_mix=0.3, base_mix_fixed=True)
    p = Project(pdir)
    try:
        assert p.setting("train_base_mix") == 0.3 and train.train_settings(p)["train_base_mix"] == 0.3
    finally:
        p.close()
    assert _stored(pdir)["train_base_mix"] == 0.3


def test_choice_is_kept(tmp_path):
    p = _new_project(tmp_path)
    pdir = p.dir
    try:
        s = p.update_settings({"train_base_mix": 0.3})          # 学習の窓で 0.3 にして学習を始めたとき（server.train_start）と同じ道
        assert s["train_base_mix"] == 0.3 and s["base_mix_fixed"] is True
    finally:
        p.close()
    p = Project(pdir)                                           # 開き直しても 0.3 のまま
    try:
        assert p.setting("train_base_mix") == 0.3
        s = p.update_settings({"train_base_mix": 0.0})          # 既定に戻すと、印が外れる
        assert s["train_base_mix"] == 0.0 and s["base_mix_fixed"] is False
        s = p.update_settings({"train_min_conf": 0.4})          # ほかの設定を変えても、印は変わらない
        assert s["base_mix_fixed"] is False and s["train_base_mix"] == 0.0
        s = p.update_settings({"train_base_mix": 0.3, "base_mix_fixed": False})     # 印を名指しで渡したら、それに従う
        assert s["base_mix_fixed"] is False
    finally:
        p.close()


def test_head_apply_and_load(tmp_path):
    rng = np.random.default_rng(0)
    classes = ["deer", "boar", "empty"]
    W, b = rng.normal(0, 1, (8, 3)).astype(np.float32), np.zeros(3, np.float32)
    mu, sd = np.zeros(8, np.float32), np.ones(8, np.float32)
    feats = rng.normal(0, 1, (5, 8)).astype(np.float32)
    codes = ["deer", "fox", "boar", "empty"]                                        # fox は学習していない区分
    pc = np.tile(np.array([[0.02, 0.95, 0.02, 0.01]], np.float32), (5, 1))          # 元のモデルは、どの枠も fox と答える
    h0 = train.Head(classes, W, b, mu, sd, 0.0)
    u, out = h0.apply(feats, codes, pc)
    assert u[:3] == classes and np.allclose(out[:, :3], h0.predict(feats), atol=1e-6)        # 混ぜない: 新しい分類器の確率そのもの
    assert np.allclose(out[:, u.index("fox")], 0.0)                                 # 学習していない区分の確率は 0
    h3 = train.Head(classes, W, b, mu, sd, 0.3)
    u3, out3 = h3.apply(feats, codes, pc)
    assert np.allclose(out3.sum(axis=1), 1.0, atol=1e-5)
    assert np.all(out3[:, u3.index("fox")] <= 0.3 + 1e-6)                           # 0.3 で混ぜても、学習していない区分は最大 0.3（基準の線 0.5 に届かない）
    assert np.all(out3[:, :3].max(axis=1) <= 0.7 + 0.3 * 0.02 + 1e-5)               # 元のモデルが別の種と答えた枠では、学習した区分は 0.7 あまりで頭打ち
    # 保存と読み込み: 割合はモデルの記録（model.json）から。記録に割合がなければ 0.3（古いモデル）
    folder = tmp_path / "m"
    folder.mkdir()
    h0.save(str(folder))
    assert train.Head.load(str(folder), {"base_mix": 0.0}).base_mix == 0.0
    assert train.Head.load(str(folder), {"base_mix": 0.3}).base_mix == 0.3
    assert train.Head.load(str(folder), {}).base_mix == 0.3 and train.Head.load(str(folder), None).base_mix == 0.3


def test_training_records_the_setting(tmp_path):
    """学習は、設定の割合をモデルの記録に書く。分類器の重みは、割合が違っても同じ（学習は割合を使わない）"""
    p = _new_project(tmp_path, stations=("BSN0001", "BSN0002", "BSN0003", "BSN0004"), per_station=4)
    try:
        rows = p.db.q("SELECT id, rel_path FROM items ORDER BY rel_path")
        assert len(rows) == 16
        rng = np.random.default_rng(1)
        box = [0.3, 0.3, 0.2, 0.2]
        for n, r in enumerate(rows):
            sp = "deer" if n % 2 == 0 else "boar"
            _fake_detect(p, r["id"], [(t, [[0.1 + 0.05 * t, 0.3, 0.2, 0.2]]) for t in np.arange(0.0, 6.0, 0.5)], [(1.0, box), (3.0, box)],
                         pc=[[0.7, 0.2, 0.1], [0.6, 0.3, 0.1]] if sp == "deer" else [[0.2, 0.7, 0.1], [0.3, 0.6, 0.1]])
            fp = train.features_path(p, r["rel_path"])
            with np.load(fp) as z:
                d = {k: z[k] for k in z.files}
            d["feats"] = (rng.normal(0, 0.3, (2, 8)) + (1.0 if sp == "deer" else -1.0)).astype(np.float32)     # 種ごとに、はっきり違う特徴量
            np.savez(fp, **d)
            save_judgement(p.db, r["id"], dict(species=[sp]), "me")
        opts = train.train_settings(p, dict(train_min_per_class=2))
        assert opts["train_base_mix"] == 0.0
        r0 = train.run_train_a(p, opts, "m0")
        assert r0["base_mix"] == 0.0 and r0["settings"]["train_base_mix"] == 0.0 and "base_mix_fixed" not in r0["settings"]
        r3 = train.run_train_a(p, {**opts, "train_base_mix": 0.3}, "m3")
        assert r3["base_mix"] == 0.3 and r3["settings"]["train_base_mix"] == 0.3
        mdir = train.models_root(p)
        with np.load(os.path.join(mdir, "m0", "head.npz")) as z0, np.load(os.path.join(mdir, "m3", "head.npz")) as z3:
            assert all(np.array_equal(z0[k], z3[k]) for k in ("W", "b", "mu", "sd", "classes"))
        with open(os.path.join(mdir, "m0", "model.json"), encoding="utf-8") as fh:
            assert json.load(fh)["base_mix"] == 0.0
        assert train.Head.load(os.path.join(mdir, "m0"), train.model_info(p, "m0")).base_mix == 0.0
        assert train.Head.load(os.path.join(mdir, "m3"), train.model_info(p, "m3")).base_mix == 0.3
    finally:
        p.close()
