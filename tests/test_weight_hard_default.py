"""「食い違いの重み」（train_weight_hard）の既定は 1（重みを付けない）。2026-11-13 に 2 から変えた（確認事項 127 回目）。

変えた理由: 論文の事例の学習は、重み 1 で行った。配るアプリの既定を、評価した設定と同じにする。

ここで見ること:
- 新しいプロジェクトの既定は 1。学習の設定（train_settings）も 1。
- 前に作ったプロジェクトに保存してある値（2 など）は、開いてもそのまま（自動では直さない）。
- 既定のままなら、人と AI が食い違った動画の枠も重みは 1。設定で 2 にすれば、その枠だけ 2 になる。
- 設定に項目がないときも、重みは既定（1）"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import train  # noqa: E402
from trapnote.project import PROJECT_DEFAULTS, PROJECT_JSON, Project  # noqa: E402


def _new_project(tmp_path):
    root = tmp_path / "N240128"
    (root / "BSN0001").mkdir(parents=True)
    return Project(str(root / "TrapNote"), root=str(root), create=True)


def _stored(pdir: str) -> dict:
    with open(os.path.join(pdir, PROJECT_JSON), encoding="utf-8") as fh:
        return json.load(fh)["settings"]


def _items() -> dict:
    """build_dataset に渡す形の動画 4 本（2 種 × 2 本。種ごとに 1 本は、人と AI が食い違った動画）"""
    out = {}
    for n, (sp, hard) in enumerate([("deer", False), ("deer", True), ("boar", False), ("boar", True)], 1):
        out[n] = dict(id=n, station=f"BSN{n:04d}", excluded=False, holdout=False, hard=hard, labels=[sp])
    return out


def test_default_is_one(tmp_path):
    assert PROJECT_DEFAULTS["train_weight_hard"] == 1.0
    assert train.TRAIN_DEFAULTS["train_weight_hard"] == 1.0
    p = _new_project(tmp_path)
    try:
        assert p.setting("train_weight_hard") == 1.0
        assert train.train_settings(p)["train_weight_hard"] == 1.0
        assert _stored(p.dir)["train_weight_hard"] == 1.0
    finally:
        p.close()


def test_stored_value_is_kept(tmp_path):
    p = _new_project(tmp_path)
    pdir = p.dir
    p.close()
    fp = os.path.join(pdir, PROJECT_JSON)
    with open(fp, encoding="utf-8") as fh:
        d = json.load(fh)
    d["settings"]["train_weight_hard"] = 2.0          # 既定を変える前のアプリが作ったプロジェクトのつもり
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    p = Project(pdir)
    try:
        assert p.setting("train_weight_hard") == 2.0 and train.train_settings(p)["train_weight_hard"] == 2.0
    finally:
        p.close()
    assert _stored(pdir)["train_weight_hard"] == 2.0


def test_weights_in_dataset():
    items = _items()
    base = dict(train_min_per_class=1)
    ds = train.build_dataset(items, set(), {**train.TRAIN_DEFAULTS, **base}, use_marks=False)
    assert sorted(ds["classes"]) == ["boar", "deer"] and len(ds["samples"]) == 4
    assert {s[3] for s in ds["samples"]} == {1.0}                      # 既定: どの枠も重み 1
    ds0 = train.build_dataset(items, set(), base, use_marks=False)     # 設定に項目がなくても、既定の 1
    assert {s[3] for s in ds0["samples"]} == {1.0}
    ds2 = train.build_dataset(items, set(), {**base, "train_weight_hard": 2.0}, use_marks=False)
    assert {s[0]: s[3] for s in ds2["samples"]} == {1: 1.0, 2: 2.0, 3: 1.0, 4: 2.0}      # 設定で 2 にすると、食い違った動画の枠だけ 2
