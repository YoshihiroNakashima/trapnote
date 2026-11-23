"""再現用のスクリプト（trapnote/repro.py）。画面で行った自動の処理を、同じ設定でやり直せる Python のスクリプトとして書き出す。

ここで見ること:
- モデルのスクリプトを書き出して、別のプロセスで実行すると、同じ分類器（方式 A は乱数の種が固定なので、重みまで同じ）ができ、記録と「同じ」と出る。
- 学習のあとで確認済みの動画が増えても、スクリプトは学習のときの一覧（train_items.csv）の動画だけを使うので、結果は変わらない。
- 学習のあとで人の判定が変わっていれば、その数を知らせ、--strict なら終了コード 2 を返す。
- モデルのないスクリプト（検出と種判別だけ）も動き、--csv で CSV が書ける。
- スクリプトの間だけ当てた設定は、終わると元に戻る。「自動」の設定は、数値に直して書く。
- 記録どうしの比較（compare_records）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import repro, train  # noqa: E402
from trapnote.judge import save as save_judgement  # noqa: E402
from trapnote.project import Project  # noqa: E402

from test_static_boxes import _fake_detect  # noqa: E402


def _add_videos(p, root, station: str, n: int, start: int = 0):
    from trapnote import scan
    os.makedirs(os.path.join(root, station), exist_ok=True)
    for i in range(start, start + n):
        open(os.path.join(root, station, f"IMG_{i:04d}.AVI"), "wb").close()
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))


def _label(p, rows, rng, offset: int = 0):
    box = [0.3, 0.3, 0.2, 0.2]
    for n, r in enumerate(rows):
        sp = "deer" if (n + offset) % 2 == 0 else "boar"
        _fake_detect(p, r["id"], [(t, [[0.1 + 0.05 * t, 0.3, 0.2, 0.2]]) for t in np.arange(0.0, 6.0, 0.5)], [(1.0, box), (3.0, box)],
                     pc=[[0.7, 0.2, 0.1], [0.6, 0.3, 0.1]] if sp == "deer" else [[0.2, 0.7, 0.1], [0.3, 0.6, 0.1]])
        fp = train.features_path(p, r["rel_path"])
        with np.load(fp) as z:
            d = {k: z[k] for k in z.files}
        d["feats"] = (rng.normal(0, 0.6, (2, 8)) + (1.0 if sp == "deer" else -1.0)).astype(np.float32)
        np.savez(fp, **d)
        save_judgement(p.db, r["id"], dict(species=[sp]), "me")


def _project(tmp_path):
    """4 地点 × 6 本の確認済みの動画（特徴量つき）と、方式 A のモデル m0"""
    root = tmp_path / "N240128"
    root.mkdir()
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    for st in ("BSN0001", "BSN0002", "BSN0003", "BSN0004"):
        _add_videos(p, root, st, 6)
    rows = p.db.q("SELECT id, rel_path FROM items ORDER BY rel_path")
    assert len(rows) == 24
    _label(p, rows, np.random.default_rng(1))
    rec = train.run_train_a(p, train.train_settings(p, dict(train_min_per_class=2)), "m0")
    assert rec["eval"] and rec["eval"]["n_items"] > 0 and os.path.isfile(os.path.join(p.dir, "models", "m0", "train_items.csv"))
    return p, str(root)


def _run(script: str, tmp_path, *args):
    env = dict(os.environ, TRAPNOTE_DATA_DIR=str(tmp_path / "appdata"), TRAPNOTE_FAKE_ML="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
    return r.returncode, r.stdout + r.stderr


def _head(pdir: str, name: str) -> dict:
    with np.load(os.path.join(pdir, "models", name, "head.npz")) as z:
        return {k: z[k] for k in z.files}


def test_model_script_reproduces_the_classifier(tmp_path):
    p, _ = _project(tmp_path)
    pdir = p.dir
    try:
        r = repro.export_script(p, None, "m0")
        assert r["model"] == "m0" and r["path"].endswith("exports/scripts/reproduce_m0.py") and os.path.basename(r["path"]) in r["command"]
        text = open(r["path"], encoding="utf-8").read()
        compile(text, r["path"], "exec")                                   # 実行できる Python であること
        assert "TRAINING = " in text and "'train_min_per_class': 2" in text and "models/m0/train_items.csv" in text
        assert "'frame_interval_sec': 0.5" in text and "'md_model': 'MDv1000-spruce'" in text     # 「自動」は、数値と名前に直して書く
        before = dict(p.data["settings"])
    finally:
        p.close()
    code, out = _run(r["path"], tmp_path)
    assert code == 0, out
    assert "Result: identical to the record." in out and "models/m0_repro" in out, out
    a, b = _head(pdir, "m0"), _head(pdir, "m0_repro")
    assert all(np.array_equal(a[k], b[k]) for k in ("W", "b", "mu", "sd", "classes"))
    p = Project(pdir)
    try:
        assert p.data["settings"] == before                                 # スクリプトの間だけ当てた設定は、元に戻る
        assert p.setting("frame_interval_sec") == "auto"
    finally:
        p.close()
    code, out = _run(r["path"], tmp_path)                                   # もう一度実行しても、前のものは消さずに別の名前にする
    assert code == 0 and "models/m0_repro_2" in out, out


def test_videos_verified_later_do_not_change_the_result(tmp_path):
    p, root = _project(tmp_path)
    pdir = p.dir
    try:
        r = repro.export_script(p, None, "m0")
        _add_videos(p, root, "BSN0005", 6)                                  # 学習のあとで、確認済みの動画が 1 地点ぶん増えた
        new = p.db.q("SELECT id, rel_path FROM items WHERE station='BSN0005' ORDER BY rel_path")
        assert len(new) == 6
        _label(p, new, np.random.default_rng(5), offset=1)
        again = train.run_train_a(p, train.train_settings(p, dict(train_min_per_class=2)), "m_all")
        assert again["n_items"] > train.model_info(p, "m0")["n_items"]     # ふつうに学習し直すと、材料が増える
    finally:
        p.close()
    code, out = _run(r["path"], tmp_path)
    assert code == 0 and "Result: identical to the record." in out, out
    a, b = _head(pdir, "m0"), _head(pdir, "m0_repro")
    assert all(np.array_equal(a[k], b[k]) for k in ("W", "b", "mu", "sd", "classes"))


def test_changed_labels_are_reported(tmp_path):
    p, _ = _project(tmp_path)
    try:
        r = repro.export_script(p, None, "m0")
        rows = p.db.q("SELECT id FROM items WHERE station IN ('BSN0001','BSN0002','BSN0003','BSN0004') ORDER BY rel_path LIMIT 8")
        for x in rows:                                                      # 学習のあとで、8 本の判定を書き換えた
            cur = p.db.q("SELECT species FROM judgements WHERE item_id=?", (x["id"],))[0]["species"]
            save_judgement(p.db, x["id"], dict(species=["boar" if cur == "deer" else "deer"]), "me")
    finally:
        p.close()
    code, out = _run(r["path"], tmp_path, "--strict")
    assert "verified labels of 8 item(s) have changed" in out, out
    assert code == 2 and "Result: differs from the record" in out, out
    code, out = _run(r["path"], tmp_path)                                   # --strict がなければ、違っていても終了コードは 0
    assert code == 0, out


def test_classification_script_and_csv(tmp_path):
    p, _ = _project(tmp_path)
    pdir = p.dir
    try:
        r = repro.export_script(p, None)
        assert r["model"] is None and r["classifier"] == "speciesnet" and r["path"].endswith("reproduce_classification.py")
        text = open(r["path"], encoding="utf-8").read()
        compile(text, r["path"], "exec")
        assert "TRAINING" not in text and "--strict" not in text
        r2 = repro.export_script(p, None, classifier="none", path=str(tmp_path / "out" / "detect_only"))     # Lite（検出だけ）。場所と名前を決めて書く
        assert r2["classifier"] == "none" and r2["path"].endswith("out/detect_only.py")
        p.update_settings(dict(frame_interval_sec=0.25, frame_interval_fixed=True, md_model="MDv5a"))        # 人が決めた値は、そのまま書く
        r3 = repro.export_script(p, None, path=str(tmp_path / "out"))
        t3 = open(r3["path"], encoding="utf-8").read()
        assert "'frame_interval_sec': 0.25" in t3 and "'md_model': 'MDv5a'" in t3 and r3["notes"] == {}
    finally:
        p.close()
    csv_path = str(tmp_path / "result.csv")
    code, out = _run(r["path"], tmp_path, "--csv", csv_path)
    assert code == 0 and "0 of 24 items to process" in out, out            # 検出済みの対象は、やり直さない
    assert os.path.isfile(csv_path) and "CSV written" in out
    with open(csv_path, encoding="utf-8-sig") as fh:
        assert fh.readline().startswith("Season,Station,File")
    code, out = _run(r["path"], tmp_path, "--project", str(tmp_path / "nowhere"))
    assert code != 0 and "Folder not found" in out


def test_shared_or_missing_model_is_refused(tmp_path):
    p, _ = _project(tmp_path)
    try:
        try:
            repro.export_script(p, None, "nope")
            raise AssertionError("no error")
        except FileNotFoundError:
            pass
    finally:
        p.close()


def test_compare_records():
    a = dict(kind="A", n_items=10, n_crops=10, classes=["deer", "boar"],
             eval=dict(n_items=4, before=dict(n=4, exact=2, first=2), after=dict(n=4, exact=4, first=4)), fit_all=dict(after=dict(n=10, exact=10, first=10)))
    assert repro.compare_records(a, json.loads(json.dumps(a)))["identical"]
    b = json.loads(json.dumps(a))
    b["eval"]["after"]["exact"] = 3
    res = repro.compare_records(a, b)
    assert not res["identical"] and [r[0] for r in res["rows"] if not r[3]] == ["agreement after training"]
    c = dict(a, classes=["deer", "hare"])
    assert not repro.compare_records(a, c)["identical"]
    assert repro.compare_records(dict(a, eval=None, fit_all=None), dict(a, eval=None, fit_all=None))["identical"]
