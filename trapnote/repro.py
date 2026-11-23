"""再現用のスクリプト（2026-11-23）。

画面で行った自動の処理（検出と種判別、追加学習）を、あとから同じ設定でやり直せる Python のスクリプトとして書き出す。
画面の操作は記録に残らないので、同じ結果を第三者が再現できるように、設定と手順を 1 つの実行できるファイルにまとめる。

- export_script(): プロジェクトの設定（と、追加学習のモデルの記録 model.json）から、スクリプトを書く
- Runner: 画面なしで、アプリと同じ処理（jobs.py の DetectJob、train.py の run_train_a / run_train_b、export.py の CSV）を動かす。
  書き出したスクリプトは、この Runner を順に呼ぶだけ
- compare_records(): やり直した学習の結果を、記録と比べる

人の作業（目視の確認）はスクリプトにできないので、確定した判定（data.sqlite）を入力として使う。
学習は「そのときの確認済み全部」から集めるので、やり直すときは、学習のときの一覧（models/<名前>/train_items.csv）にある対象だけを使う。

スクリプトと、スクリプトが出す文は英語（査読者や、ほかの国の利用者が読むため）。
"""
from __future__ import annotations

import csv
import json
import logging
import os
import platform
import sys
import threading
import time
from collections import Counter

from . import APP_NAME, BUILD_TIME, VERSION
from .project import PROJECT_DEFAULTS

log = logging.getLogger(__name__)

# 検出と種判別の結果を決める設定（project.json の settings から書き出す）
DETECT_KEYS = ("md_model", "speciesnet_model", "country", "frame_first_sec", "frame_first_fps", "frame_interval_sec",
               "animal_threshold", "animal_min_frames", "person_threshold", "person_min_frames",
               "species_p1", "species_p2", "species_k", "species_pick", "species_empty_p",
               "max_crops", "crop_min_gap_sec", "crop_per_frame", "detect_image_size", "save_features")
SCRIPT_DIR = ("exports", "scripts")


def _print(text: str) -> None:
    """コンソールの文字コードで出せない文字（日本語のファイル名など）があっても止まらないように出す"""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, "replace").decode(enc), flush=True)


# ====================================================================== 画面なしで動かす
class Runner:
    """画面なしで、アプリと同じ処理を動かす（ジョブが使う ctx の形: project / db() / model_hub() / settings）。

    設定（use_settings）は、このスクリプトの間だけプロジェクトに当てて、close() で元に戻す"""

    def __init__(self, project_dir: str, device: str | None = None, batch_size: int | None = None, out=None):
        from .config import Settings
        from .paths import norm
        from .project import Project, find_project_for_root, is_project_dir
        self.out = out or _print
        self.settings = Settings()
        if device in ("auto", "cuda", "mps", "cpu"):          # 保存はしない（この実行だけ）
            self.settings.data["device"] = device
        if batch_size:
            self.settings.data["batch_size"] = max(1, min(64, int(batch_size)))
        path = norm(project_dir)
        if os.path.isfile(path) and os.path.basename(path).lower() == "project.json":
            path = os.path.dirname(path)
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Folder not found: {path}")
        pdir = path if is_project_dir(path) else find_project_for_root(path)
        if not pdir:
            raise FileNotFoundError(f"No TrapNote project in: {path} (give the TrapNote folder or the media folder with --project)")
        self.project = Project(pdir)
        if not self.project.root:
            self.project.close()
            raise FileNotFoundError(f"The media folder of the project was not found on this computer: {pdir} (open the project once in TrapNote to set it)")
        self._hub = None
        self._saved: dict | None = None
        self.out(f"Project: {self.project.name}  ({self.project.dir})")

    # ---------- ジョブから使う ----------
    def db(self):
        return self.project.db

    def models_dir(self) -> str:
        from . import train
        from .config import DATA_DIR
        d = self.settings.get("models_dir") or str(DATA_DIR / "models")
        os.makedirs(d, exist_ok=True)
        train.SHARED_DIR = os.path.join(d, "local")
        return d

    def model_hub(self):
        if self._hub is None:
            from . import detect
            self._hub = detect.ModelHub(self.settings, self.models_dir())
        return self._hub

    # ---------- 設定 ----------
    def use_settings(self, changes: dict | None) -> dict:
        """記録した設定を、この実行の間だけプロジェクトに当てる。違っていた項目を返す（{項目: (いまの値, 記録の値)}）"""
        s = self.project.data.setdefault("settings", {})
        if self._saved is None:
            self._saved = dict(s)
        diff = {}
        for k, v in (changes or {}).items():
            if k not in PROJECT_DEFAULTS:
                continue
            if s.get(k, PROJECT_DEFAULTS.get(k)) != v:
                diff[k] = (s.get(k, PROJECT_DEFAULTS.get(k)), v)
            s[k] = v
        if diff:
            self.out("Settings applied for this run (the project keeps its own values):")
            for k, (a, b) in diff.items():
                self.out(f"  {k}: {a!r} -> {b!r}")
        return diff

    def close(self) -> None:
        p = self.project
        if p is None:
            return
        try:
            if self._saved is not None:
                p.data["settings"] = self._saved
                p.save()
        finally:
            if self._hub is not None:
                try:
                    self._hub.unload()
                except Exception:
                    pass
            p.close()
            self.project = None

    # ---------- ジョブを 1 つ動かす ----------
    def _run_job(self, job, label: str, summary=None):
        stop = threading.Event()

        def report():
            last = ""
            while not stop.wait(10.0):
                line = f"  ... {job.done}/{job.total} {job.current or ''}".rstrip()
                if line != last:
                    self.out(line)
                    last = line
        t0 = time.time()
        job.state, job.started = "running", t0
        th = threading.Thread(target=report, daemon=True)
        th.start()
        try:
            job.run(self)
        finally:
            stop.set()
        job.finished = time.time()
        self.out(f"{label}: done in {job.finished - t0:.0f} s. {summary(job) if summary else job.message}")
        return job

    # ---------- 手順 1: 検出と種判別 ----------
    def detect(self, classifier: str | None = None, include_verified: bool = True, redo: bool = False) -> dict:
        """まだ検出していない対象に、MegaDetector と分類器を当てる（画面の「自動判定」と同じ処理）。
        classifier: speciesnet / none / local:<名前>（省けばプロジェクトの設定）"""
        from . import detect
        from .jobs import DetectJob
        p = self.project
        n_all = int(p.db.scalar("SELECT COUNT(*) FROM items WHERE missing=0 AND meta_state<>2") or 0)
        n_todo = n_all if redo else int(p.db.scalar("SELECT COUNT(*) FROM items WHERE missing=0 AND meta_state<>2 AND det_state=0") or 0)
        self.out(f"Step 1. Detection and classification: {n_todo} of {n_all} items to process")
        if not n_todo:
            return dict(n=0)
        env = detect.env_status(self.settings, self.models_dir())
        only_detect = classifier == "none"
        if not (env["ok"] or (only_detect and env["torch"] and env["megadetector"])):
            raise RuntimeError("torch / megadetector / speciesnet are not installed in this Python environment (run this script with the Python of TrapNote)")
        if redo:
            detect.clear_detections(p.db, None)
        opts = dict(redo=bool(redo), include_verified=bool(include_verified), use_existing=False)
        if classifier:
            opts["classifier"] = str(classifier)
        def summary(j):
            c = j.counts
            return (f"{j.done} items (animal {c['animal']}, person {c['person']}, vehicle {c['vehicle']}, empty {c['empty']}, "
                    f"needs review {c['low']}), {j.failed} failed")
        job = self._run_job(DetectJob(None, opts), "Detection and classification", summary)
        return dict(n=job.done, failed=job.failed, counts=dict(job.counts), message=job.message)

    # ---------- 手順 2: 追加学習 ----------
    def check_items(self, items_csv: str | None) -> dict | None:
        """学習のときの一覧（train_items.csv）と、いまのプロジェクトを比べる。返り値: only（プロジェクトの名前 → 相対パスの集まり）と、
        見つからない対象・人の判定が変わった対象の数"""
        from . import train
        from .judge import human_codes
        p = self.project
        if not items_csv:
            return None
        path = items_csv if os.path.isabs(items_csv) else os.path.join(p.dir, items_csv)
        if not os.path.isfile(path):
            self.out(f"  The list of training items was not found ({path}); all videos verified now are used instead.")
            return None
        only: dict[str, set] = {}
        human_then: dict[str, set] = {}
        me = train.source_label(p)
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                proj, rel = row.get("Project") or me, row.get("RelPath") or ""
                if not rel:
                    continue
                only.setdefault(proj, set()).add(rel)
                if proj == me:
                    human_then[rel] = {x for x in (row.get("Human") or "").split(";") if x}
        n_missing = n_changed = 0
        rels = sorted(human_then)
        found: dict[str, tuple] = {}
        for i in range(0, len(rels), 500):
            part = rels[i:i + 500]
            qs = ",".join("?" * len(part))
            for r in p.db.q(f"SELECT id, rel_path, status FROM items WHERE missing=0 AND rel_path IN ({qs})", part):
                found[r["rel_path"]] = (r["id"], r["status"])
        ids = [v[0] for v in found.values()]
        js: dict[int, list] = {i: [] for i in ids}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            qs = ",".join("?" * len(part))
            for j in p.db.q(f"SELECT item_id, species, origin FROM judgements WHERE item_id IN ({qs})", part):
                js[j["item_id"]].append(dict(species=j["species"], origin=j["origin"]))
        for rel, then in human_then.items():
            if rel not in found:
                n_missing += 1
                continue
            iid, status = found[rel]
            now = human_codes(status, js[iid]) if status == "verified" else set()
            if now != then:
                n_changed += 1
        n_listed = sum(len(v) for v in only.values())
        self.out(f"  Items listed when the model was trained: {n_listed}"
                 + (f" ({len(only) - (1 if me in only else 0)} source project(s) besides this one)" if len(only) > 1 or me not in only else ""))
        if n_missing or n_changed:
            self.out(f"  NOTE: {n_missing} listed item(s) are no longer in the project and the verified labels of {n_changed} item(s) have changed since. "
                     "The result can differ from the record.")
        return dict(only=only, n_listed=n_listed, n_missing=n_missing, n_changed=n_changed, path=path)

    def train(self, name: str, kind: str = "A", settings: dict | None = None, holdout_stations=None, holdout_batches=None,
              sources=None, init_from: str | None = None, items_csv: str | None = None) -> dict:
        """記録した設定で学習し直す（画面の「追加学習」と同じ処理）。返り値は学習の記録（model.json の中身）。
        items_csv = 学習のときの一覧。あれば、その対象だけを材料にする"""
        from . import detect, train
        p = self.project
        kind = "B" if kind == "B" else "A"
        self.out(f"Step 2. Retraining (method {kind})")
        name = train.safe_model_name(name)
        base, k = name, 1
        while os.path.isdir(os.path.join(train.models_root(p), name)):       # 前にやり直したものは消さず、別の名前にする
            k += 1
            name = f"{base}_{k}"
        self.models_dir()
        chk = self.check_items(items_csv)
        opts = train.train_settings(p, settings or {})
        opts["holdout_stations"] = [str(x) for x in (holdout_stations or [])] or None
        opts["holdout_batches"] = [str(x) for x in (holdout_batches or []) if str(x)]
        if chk:
            opts["only_items"] = chk["only"]
        srcs = [str(x) for x in (sources or []) if str(x)] or None

        state = dict(phase="", last=0.0)

        def prog(phase, a, b, msg):
            now = time.time()
            if phase != state["phase"] or now - state["last"] > 10:
                state.update(phase=phase, last=now)
                self.out(f"  ... {phase} {int(a)}/{int(b)} {msg or ''}".rstrip())
        t0 = time.time()
        if kind == "B":
            env = detect.env_status(self.settings, self.models_dir())
            if not env["ok"]:
                raise RuntimeError("torch / speciesnet are not installed in this Python environment (method B needs them)")
            hub = self.model_hub()
            s = detect.project_settings(p)
            hub.load_classifier(s.get("speciesnet_model"), None, None)
            init = train.safe_model_name(init_from) if init_from else None
            hub.set_local(p, f"local:{init}" if init else "speciesnet", s.get("speciesnet_model"))
            rec = train.run_train_b(p, hub, opts, name, prog, None, init_from=init, sources=srcs)
        else:
            rec = train.run_train_a(p, opts, name, prog, None, sources=srcs)
        self.out(f"Retraining: done in {time.time() - t0:.0f} s. Model saved as models/{name} "
                 f"({rec.get('n_items')} videos, {rec.get('n_crops')} crops, {len(rec.get('classes') or [])} classes)")
        rec = dict(rec, name=name)
        if chk:
            rec["_check"] = dict(n_listed=chk["n_listed"], n_missing=chk["n_missing"], n_changed=chk["n_changed"])
        return rec

    # ---------- 手順 3: 記録と比べる ----------
    def compare(self, recorded: dict | None, new: dict) -> bool:
        if not recorded:
            return True
        res = compare_records(recorded, new)
        self.out("Step 3. Comparison with the record")
        w = max(len(r[0]) for r in res["rows"])
        self.out(f"  {'':{w}}  {'recorded':>12}  {'this run':>12}")
        for label, a, b, same in res["rows"]:
            self.out(f"  {label:{w}}  {str(a):>12}  {str(b):>12}  {'' if same else '<- differs'}")
        if res["identical"]:
            self.out("Result: identical to the record.")
        elif (new.get("kind") or recorded.get("kind")) == "B":
            self.out("Result: differs from the record. Fine-tuning (method B) is not deterministic, so small differences are expected.")
        else:
            self.out("Result: differs from the record (see the notes above: changed labels, missing items or a different version of the models).")
        return res["identical"]

    # ---------- CSV ----------
    def export_csv(self, path: str, verified_only: bool = False) -> dict:
        from . import export as export_mod
        from .paths import norm
        r = export_mod.export_csv(self.project, norm(os.path.abspath(path)), dict(status="verified") if verified_only else {}, "en", True)
        self.out(f"CSV written: {r.get('path')} ({r.get('rows')} rows)")
        return r


def _ev(d: dict | None, k: str):
    return (d or {}).get(k) if isinstance(d, dict) else None


def compare_records(recorded: dict, new: dict) -> dict:
    """学習の記録（model.json）どうしを比べる。rows = [(項目, 記録, 今回, 同じか)]"""
    rows = []

    def add(label, a, b):
        rows.append((label, a, b, a == b))
    add("training videos", recorded.get("n_items"), new.get("n_items"))
    add("training crops", recorded.get("n_crops"), new.get("n_crops"))
    add("classes", len(recorded.get("classes") or []), len(new.get("classes") or []))
    if sorted(recorded.get("classes") or []) != sorted(new.get("classes") or []):
        rows.append(("class names", "", "", False))
    ra, na = recorded.get("eval"), new.get("eval")
    if ra or na:
        add("validation videos", _ev(ra, "n_items"), _ev(na, "n_items"))
        for when, lab in (("before", "before training"), ("after", "after training")):
            a, b = _ev(ra, when), _ev(na, when)
            add(f"agreement {lab}", f"{_ev(a, 'exact')}/{_ev(a, 'n')}", f"{_ev(b, 'exact')}/{_ev(b, 'n')}")
            add(f"first species correct {lab}", f"{_ev(a, 'first')}/{_ev(a, 'n')}", f"{_ev(b, 'first')}/{_ev(b, 'n')}")
    fa, fb = _ev(recorded.get("fit_all"), "after"), _ev(new.get("fit_all"), "after")
    if fa or fb:
        add("agreement on all videos", f"{_ev(fa, 'exact')}/{_ev(fa, 'n')}", f"{_ev(fb, 'exact')}/{_ev(fb, 'n')}")
    return dict(rows=rows, identical=all(r[3] for r in rows))


# ====================================================================== スクリプトを書き出す
def _recorded(info: dict) -> dict:
    """model.json から、比べるのに使う数だけを抜き出す（スクリプトに書き込む）"""
    def ev(d):
        return dict(n=d.get("n"), exact=d.get("exact"), first=d.get("first")) if isinstance(d, dict) else None
    out = dict(kind=info.get("kind"), trained_at=info.get("trained_at"), n_items=info.get("n_items"), n_crops=info.get("n_crops"),
               classes=list(info.get("classes") or []))
    e = info.get("eval")
    out["eval"] = dict(n_items=e.get("n_items"), before=ev(e.get("before")), after=ev(e.get("after"))) if isinstance(e, dict) else None
    f = info.get("fit_all")
    out["fit_all"] = dict(after=ev(f.get("after"))) if isinstance(f, dict) else None
    return out


def _used_detector(project) -> str | None:
    """検出済みの対象の記録（det_json の model）から、実際に使った MegaDetector を調べる（いちばん多いもの）"""
    cnt: Counter = Counter()
    for r in project.db.q("SELECT det_json FROM items WHERE det_state=1 AND det_json IS NOT NULL ORDER BY id DESC LIMIT 2000"):
        try:
            m = str((json.loads(r["det_json"] or "{}") or {}).get("model") or "")
        except Exception:
            continue
        m = m.replace("(edited)", "").strip()
        if m:
            cnt[m] += 1
    return cnt.most_common(1)[0][0] if cnt else None


def detection_spec(project, app_settings, classifier: str | None = None) -> tuple[dict, dict]:
    """いまのプロジェクトの検出と種判別の設定。「自動」の項目は、この計算機での値に直して書く（別の計算機でも同じ値で動くように）。
    返り値: (設定, 直した項目の説明)"""
    from . import detect
    s = detect.project_settings(project)
    out = {k: s.get(k) for k in DETECT_KEYS}
    notes = {}
    hub = detect.ModelHub.instance
    device = hub.device if hub is not None and hub.device else detect.probe_device(app_settings.get("device", "auto") if app_settings is not None else "auto")[0]
    if out.get("md_model") in (None, "", "auto"):
        used = _used_detector(project)
        from .detect import MD_MODELS
        md = used if used in MD_MODELS else ("MDv1000-redwood" if device == "cuda" else "MDv1000-spruce")
        notes["md_model"] = f"'auto' resolved to {md} (" + ("the detector recorded in the project" if used in MD_MODELS else f"device at export: {device}") + ")"
        out["md_model"] = md
    try:
        fixed = float(out.get("frame_interval_sec")) > 0
    except (TypeError, ValueError):
        fixed = False
    if not fixed:
        iv = detect.frame_interval(s, device)
        notes["frame_interval_sec"] = f"'auto' resolved to {iv} s (device at export: {device}; auto means 0.2 s with a GPU and 0.5 s without)"
        out["frame_interval_sec"] = iv
    cls = classifier or s.get("classifier") or "speciesnet"
    return dict(settings=out, classifier=cls), notes


def _py(obj, level: int = 0) -> str:
    """Python の値として読める形に書く（辞書は 1 行に 1 項目。短い辞書と列は 1 行）"""
    pad, end = "    " * (level + 1), "    " * level
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        one = "{" + ", ".join(f"{k!r}: {v!r}" for k, v in obj.items()) + "}"
        if level > 0 and len(one) <= 88 and not any(isinstance(v, (dict, list, tuple)) for v in obj.values()):
            return one
        return "{\n" + "".join(f"{pad}{k!r}: {_py(v, level + 1)},\n" for k, v in obj.items()) + end + "}"
    if isinstance(obj, (list, tuple)):
        one = repr(list(obj))
        if not obj or len(one) <= 88:
            return one
        return "[\n" + "".join(f"{pad}{_py(v, level + 1)},\n" for v in obj) + end + "]"
    return repr(obj)


# はじめの説明（docstring）は、r を付けた文字列にする。Windows の場所（C:\\Users\\… など）をそのまま書くので、
# r がないと、\\U や \\x が文字の指定と読まれて、スクリプトが SyntaxError で動かない（確認事項 155 回目）
_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""{app} reproducibility script

Generated by {app} {version} (build {build}) on {now}.
Project : {pname}
Purpose : {purpose}

This script re-runs the automated steps with the settings recorded below, without the
graphical interface. Manual verification cannot be scripted: the verified labels stored
in the project (data.sqlite) are the input of this script.

How to run (use the Python environment of {app}, and close {app} first):

    "{python}" "{script}"

Options:
    --project DIR      project folder (the TrapNote folder or the media folder), if it has moved
    --app DIR          {app} application folder (the one that holds app.py), if it has moved
    --device X         auto / cuda / mps / cpu (default: the setting of the application)
{train_opts}    --skip-detect      skip Step 1 and use the stored detections as they are
    --csv FILE         also write the results of the project to a CSV file
"""
import argparse
import os
import sys

APP_DIR = {app_dir!r}
PROJECT_DIR = {project_dir!r}

GENERATED = {generated}

# Step 1. Detection (MegaDetector) and species classification.
# Only items that have not been processed yet are processed; stored results are kept.
DETECTION = {detection}
{training_block}

def main() -> int:
    ap = argparse.ArgumentParser(description="Re-run the automated steps of a {app} project")
    ap.add_argument("--project", default=PROJECT_DIR)
    ap.add_argument("--app", default=APP_DIR)
    ap.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"])
{train_args}    ap.add_argument("--skip-detect", action="store_true")
    ap.add_argument("--csv")
    args = ap.parse_args()

    if not os.path.isfile(os.path.join(args.app, "trapnote", "repro.py")):
        sys.exit("The {app} application was not found in: " + args.app + " (give its folder with --app)")
    sys.path.insert(0, args.app)
    from trapnote import repro

    try:
        run = repro.Runner(args.project, device=args.device)
    except (FileNotFoundError, RuntimeError) as e:
        sys.exit(str(e))
    try:
        run.use_settings(DETECTION["settings"])
        if not args.skip_detect:
            run.detect(classifier=DETECTION["classifier"])
{train_steps}        if args.csv:
            run.export_csv(args.csv)
    finally:
        run.close()
    return {ret}


if __name__ == "__main__":
    sys.exit(main())
'''

_TRAIN_BLOCK = '''
# Step 2. Retraining. The training material is restricted to the videos listed when the
# model was trained ("items"), so videos verified later do not change the result.
TRAINING = {training}

# Step 3. Results recorded when the model was trained (compared with this run).
RECORDED = {recorded}
'''

_TRAIN_OPTS = '''    --name NAME        name of the re-trained model (default: {default_name})
    --source NAME=DIR  folder of a source project used as training material, if it has moved
    --strict           exit with status 2 when the result differs from the record
'''

_TRAIN_ARGS = '''    ap.add_argument("--name", default={default_name!r})
    ap.add_argument("--source", action="append", default=[], metavar="NAME=DIR")
    ap.add_argument("--strict", action="store_true")
'''

_TRAIN_STEPS = '''        moved = dict(x.split("=", 1) for x in args.source if "=" in x)
        sources = [moved.get(s["name"], s["dir"]) for s in TRAINING["sources"]]
        model = run.train(name=args.name, kind=TRAINING["kind"], settings=TRAINING["settings"],
                          holdout_stations=TRAINING["holdout_stations"], holdout_batches=TRAINING["holdout_batches"],
                          sources=sources, init_from=TRAINING["init_from"], items_csv=TRAINING["items"])
        same = run.compare(RECORDED, model)
'''


def export_script(project, app_settings=None, model: str | None = None, path: str | None = None, classifier: str | None = None) -> dict:
    """再現用のスクリプトを書く。model があれば、そのモデルの学習をやり直す手順も入れる（なければ、検出と種判別だけ）。
    path を省けば <プロジェクト>/exports/scripts/ の下。返り値: path, command（実行の命令）, model"""
    from . import train
    from .config import APP_DIR
    from .db import now_iso
    from .paths import norm
    info = None
    if model:
        name = train.safe_model_name(model)
        folder = os.path.join(train.models_root(project), name)
        if not os.path.isfile(os.path.join(folder, "model.json")):
            if train.model_folder(project, name):
                raise ValueError(f"共有のモデル（{name}）は、学習したプロジェクトを開いてから書き出してください")
            raise FileNotFoundError(f"追加学習のモデルがありません: {name}")
        with open(os.path.join(folder, "model.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        model = name
    det, notes = detection_spec(project, app_settings, "speciesnet" if info else classifier)
    if info and isinstance(info.get("species_rule"), dict):       # 学習のときの集約の規則（評価に使う）
        rule = info["species_rule"]
        for k_rec, k_set in (("p1", "species_p1"), ("p2", "species_p2"), ("k", "species_k")):
            if rule.get(k_rec) is not None:
                det["settings"][k_set] = rule[k_rec]
    generated = dict(app=APP_NAME, version=VERSION, build=BUILD_TIME, at=now_iso(), platform=platform.platform(), python=platform.python_version(),
                     project=project.name, notes=notes)
    fname = f"reproduce_{model}.py" if model else "reproduce_classification.py"
    if path:
        path = norm(path)
        if os.path.isdir(path):
            path = os.path.join(path, fname)
        elif not path.lower().endswith(".py"):
            path += ".py"
    else:
        path = os.path.join(project.dir, *SCRIPT_DIR, fname)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def doc(s) -> str:            # はじめの説明に入れる文字: 説明を閉じる """ にならないようにする
        return str(s).replace('"""', "'''")
    kw = dict(app=APP_NAME, version=VERSION, build=BUILD_TIME or "?", now=generated["at"], pname=doc(project.name), python=doc(sys.executable.replace("pythonw.exe", "python.exe")),
              script=doc(os.path.normpath(path)), app_dir=os.path.normpath(str(APP_DIR)), project_dir=os.path.normpath(project.dir),
              generated=_py(generated), detection=_py(det))
    if info:
        items_csv = os.path.join("models", model, "train_items.csv")
        training = dict(model=model, kind=info.get("kind") or "A", settings={k: v for k, v in (info.get("settings") or {}).items() if k in train.TRAIN_DEFAULTS},
                        holdout_stations=list(info.get("holdout_stations") or []), holdout_batches=list(info.get("holdout_batches") or []),
                        sources=[dict(name=x.get("name"), dir=x.get("dir")) for x in (info.get("sources") or [])],
                        init_from=info.get("init_from") or None,
                        items=items_csv.replace("\\", "/") if os.path.isfile(os.path.join(project.dir, items_csv)) else None)
        default_name = f"{model}_repro"
        kw.update(purpose=f'reproduce the retrained model "{model}" (method {training["kind"]}, trained {str(info.get("trained_at") or "")[:16].replace("T", " ")})',
                  training_block=_TRAIN_BLOCK.format(training=_py(training), recorded=_py(_recorded(info))),
                  train_opts=_TRAIN_OPTS.format(default_name=default_name), train_args=_TRAIN_ARGS.format(default_name=default_name),
                  train_steps=_TRAIN_STEPS, ret="2 if (args.strict and not same) else 0")
    else:
        kw.update(purpose=f'reproduce the automated classification of the project (classifier: {det["classifier"]})',
                  training_block="", train_opts="", train_args="", train_steps="", ret="0")
    text = _TEMPLATE.format(**kw)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    command = f'"{kw["python"]}" "{os.path.normpath(path)}"'
    return dict(path=norm(path), command=command, model=model, classifier=det["classifier"], notes=notes,
                resolved={k: det["settings"].get(k) for k in notes})          # 「自動」から直した値（画面に出す）
