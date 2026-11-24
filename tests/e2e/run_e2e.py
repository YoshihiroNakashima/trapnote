"""TrapNote を、入れたままの環境で、はじめから終わりまで動かして確かめる（どの OS でも同じ手順。確認事項 160 回目）。

画面（ブラウザ）が使うのと同じ HTTP の API で、次の順に動かす:
  1. 小さな調査回のフォルダ（地点のフォルダと、動画・静止画）を作る。フォルダの名前には、空白と、ASCII でない文字を入れる
  2. サーバーを起動する（python app.py --no-browser）
  3. 英語の画面の設定でプロジェクトを作り、地点のフォルダを選び、種を登録する
  4. 自動判定（MegaDetector と SpeciesNet）
  5. サムネイルと、再生用の動画（ffmpeg を使う）
  6. 目視の確認（判定の保存）
  7. 追加学習（軽量な分類器 = 方式 A）と、そのモデルでの判定のやり直し。本物のモデルでは、微調整（方式 B。1 エポックだけ）も行う
  8. 書き出し（CSV、MegaDetector の JSON、YOLO 形式）
  9. 再現用のスクリプトを書き出す。同じ動画の写しで 2 つ目のプロジェクトを作り、サーバーを終了する
 10. 画面なしで、スクリプトを実行する: 判定のスクリプトを 2 つ目のプロジェクトにかけて、画面での自動判定（手順 4）と同じ結果になるかを見る。
     モデルのスクリプトで、追加学習をやり直して、記録と同じになるかを見る（方式 A。方式 B は、最後まで動くかだけを見る）
 11. （--start-check を付けたとき）利用者と同じ起動のしかた（ブラウザを開く。Windows では、デスクトップのアイコンと同じ pythonw.exe）で起動して、
     サーバーが答えるか、ブラウザの窓が開いたかを見る

使い方（TrapNote の Python の環境で実行する）:
  python tests/e2e/run_e2e.py --work DIR --fake
      偽のモデル（TRAPNOTE_FAKE_ML=1）で動かす。torch もモデルも要らない。流れだけを確かめる
  python tests/e2e/run_e2e.py --work DIR --image "Ursus americanus=bear.jpg" --image "Bos taurus=cattle.jpg" --image empty=blank.jpg
      本物のモデルで動かす。静止画から短い動画を作り、判定が、付けた名前のとおりになるかも見る。名前は、学名か、empty / human / vehicle。
      学名は 2 つ以上（追加学習に 2 種が要る）。モデルは、はじめに取ってくる（インターネットが要る）。
      写真の一部だけを使うときは、FILE のあとに #crop=幅:高さ:左:上 を付ける（小さく写った人を、大きくするため）
  --app DIR   確かめる TrapNote のフォルダ（app.py のある所）。省けば、この台本のあるリポジトリ。インストーラで入れたフォルダを確かめるときに使う
  --start-check   手順 11 も行う
  --skip-finetune 微調整（方式 B）を省く。GPU がなく、メモリーの少ない計算機（GitHub の Mac）では、微調整に数十分かかるため

出す文は英語（GitHub Actions の記録に出るため）。失敗があれば、終了コード 1。pytest の試験ではない（名前が test_ で始まらない）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(HERE))

STATIONS = ["ST01", "ST02", "ST03", "ST04", "ST05", "ST06"]
T = dict(model=1800, classify=1500, train=900, finetune=2400)       # 待つ秒数の上限（本物のモデルを CPU で動かしても、ふつうは、この数分の一で終わる）
FIXED = ("empty", "human", "vehicle")              # 種リストの固定項目（どのプロジェクトにもある）
SURVEY = "Survey 2026 調査 été"        # 空白、日本語、アクセント付きの文字を入れた名前（調査 été）
COMPARE = ("Kind", "DetAnimal", "DetPerson", "DetVehicle", "DetModel", "AutoJudgement")     # 画面とスクリプトで同じになるはずの列
problems: list[str] = []
n_checks = 0


T0 = time.time()


def say(text: str = "") -> None:
    if text.startswith("Step "):
        text += f"   [{time.time() - T0:.0f} s]"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, "replace").decode(enc), flush=True)


def check(ok: bool, what: str, detail: str = "") -> bool:
    global n_checks
    n_checks += 1
    say(f"  [{'ok' if ok else 'FAIL'}] {what}" + (f" ({detail})" if detail else ""))
    if not ok:
        problems.append(what + (f": {detail}" if detail else ""))
    return bool(ok)


class Api:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"

    def raw(self, method: str, path: str, body=None, timeout: float = 300):
        data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def call(self, method: str, path: str, body=None, timeout: float = 300) -> dict:
        try:
            _st, _ct, data = self.raw(method, path, body, timeout)
            return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(text).get("error") or text
            except Exception:
                msg = text
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {msg[:400]}") from None

    def get(self, path: str, **kw) -> dict:
        return self.call("GET", path, None, **kw)

    def post(self, path: str, body=None, **kw) -> dict:
        return self.call("POST", path, body, **kw)

    def wait_idle(self, timeout: float, label: str) -> dict:
        """裏の処理（走査、メタデータ、検出、学習、書き出し）が終わるまで待つ"""
        t0, last, t_say = time.time(), "", 0.0
        while time.time() - t0 < timeout:
            j = self.get("/api/jobs")
            busy = False
            for lane in ("bg", "ml", "cross", "archive"):
                st = j.get(lane) or {}
                if st.get("current") or st.get("queued"):
                    busy = True
                    cur = st.get("current") or {}
                    line = f"    ... {label}: {cur.get('kind', '')} {cur.get('done', 0)}/{cur.get('total', 0)} {cur.get('phase', '') or ''}".rstrip()
                    if line != last and time.time() - t_say > 15:
                        say(line)
                        last, t_say = line, time.time()
            if not busy:
                return j
            time.sleep(1.0)
        raise RuntimeError(f"{label}: not finished after {timeout:.0f} s")

    def last_job(self, lane: str, kind: str) -> dict:
        for r in (self.get("/api/jobs").get(lane) or {}).get("recent") or []:
            if r.get("kind") == kind:
                return r
        return {}

    def job_ok(self, lane: str, kind: str, what: str, detail: str = "") -> bool:
        """終わった処理が、失敗していないか（失敗なら、サーバーが返した理由を出す）"""
        job = self.last_job(lane, kind)
        ok = job.get("state") == "done" and not job.get("failed")
        err = str(job.get("error") or "")
        return check(ok, what, detail if ok else f"state {job.get('state')}" + (": " + err[-700:] if err else ""))


def ffmpeg_exe() -> str:
    from trapnote import media
    exe, _probe = media.find_ffmpeg("")
    if not exe:
        raise RuntimeError("ffmpeg was not found (the package imageio-ffmpeg should provide it)")
    return exe


def run_ffmpeg(args: list[str]) -> None:
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + (r.stderr or b"").decode("utf-8", "replace")[-400:])


H264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
MJPEG = ["-c:v", "mjpeg", "-q:v", "4", "-pix_fmt", "yuvj420p"]


def make_media(root: str, images: list[tuple[str, str, str]], fake: bool) -> dict[str, str]:
    """調査回のフォルダを作る。返り値: 相対パス → 付ける判定（学名、empty / human / vehicle。偽のモデルでは A と B）。
    動画は、MP4（H.264）と AVI（MJPEG）を半分ずつ。静止画も 1 枚"""
    expected: dict[str, str] = {}
    for st in STATIONS:
        os.makedirs(os.path.join(root, st), exist_ok=True)
    if fake:
        for i, st in enumerate(STATIONS):
            for k, src in enumerate(("testsrc=size=320x240:rate=10:duration=2", "testsrc2=size=320x240:rate=10:duration=2")):
                avi = (i + k) % 2 == 1
                rel = f"{st}/IMG_{k + 1:04d}.{'avi' if avi else 'mp4'}"
                run_ffmpeg(["-f", "lavfi", "-i", src, *(MJPEG if avi else H264), os.path.join(root, rel)])
                expected[rel] = ("A", "B")[k]
        rel = f"{STATIONS[0]}/IMG_0003.jpg"
        run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=1:duration=1", "-frames:v", "1", os.path.join(root, rel)])
        expected[rel] = "A"
        return expected
    n = 0
    for name, img, crop in images:
        stations = STATIONS if name not in FIXED else (STATIONS[:4] if name == "empty" else STATIONS[:2])
        tag = "".join(ch for ch in name.title() if ch.isalnum())[:12]
        for i, st in enumerate(stations):
            avi = (i + n) % 2 == 1
            vf = crop + "scale=1280:-2" + (",hflip" if i % 2 else "") + f",eq=brightness={(i - 2) * 0.03:.2f}"       # 地点ごとに、少しだけ違う映像にする
            rel = f"{st}/{tag}_{i + 1:02d}.{'avi' if avi else 'mp4'}"
            run_ffmpeg(["-loop", "1", "-i", img, "-t", "2", "-r", "5", "-vf", vf, *(MJPEG if avi else H264), os.path.join(root, rel)])
            expected[rel] = name
        n += 1
    name, img, crop = next((x for x in images if x[0] not in FIXED), images[0])
    rel = f"{STATIONS[0]}/STILL_01.jpg"
    run_ffmpeg(["-i", img, "-vf", crop + "scale=1280:-2", "-frames:v", "1", os.path.join(root, rel)])
    expected[rel] = name
    return expected


def start_server(port: int, data_dir: str, fake: bool, log_path: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")     # 止まったときに、どこで止まっているかを記録に出せるようにする（dump_stacks）
    if fake:
        env["TRAPNOTE_FAKE_ML"] = "1"
    else:
        env.pop("TRAPNOTE_FAKE_ML", None)
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen([sys.executable, os.path.join(APP_DIR, "app.py"), "--no-browser", "--port", str(port), "--data-dir", data_dir],
                            cwd=APP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)


def dump_stacks(proc: subprocess.Popen) -> None:
    """サーバーが止まって見えるときに、各スレッドがどこにいるかを server.log に書かせて、終わらせる（Windows では、終わらせるだけ）"""
    if proc.poll() is not None:
        return
    try:
        if sys.platform.startswith("win"):
            proc.kill()
        else:
            import signal
            proc.send_signal(signal.SIGABRT)          # PYTHONFAULTHANDLER=1 なので、全部のスレッドの位置が出る
        proc.wait(timeout=30)
    except Exception:
        pass


def wait_ping(api: Api, proc: subprocess.Popen, timeout: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"the server stopped at start-up (exit code {proc.returncode})")
        try:
            return api.get("/api/ping", timeout=3)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("the server did not answer")


def prepare(api: Api, item_id: int, body: dict, timeout: float = 180) -> dict | None:
    """再生の準備ができるまで待つ（変換が要るときは、サーバーが裏で ffmpeg を動かす）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        res = api.post(f"/api/items/{item_id}/prepare", body)
        if res.get("ready"):
            return res
        time.sleep(0.5)
    return None


def run_script(path: str, extra: list[str], fake: bool, data_dir: str, timeout: float) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TRAPNOTE_DATA_DIR=data_dir)
    if fake:
        env["TRAPNOTE_FAKE_ML"] = "1"
    else:
        env.pop("TRAPNOTE_FAKE_ML", None)
    r = subprocess.run([sys.executable, path, "--app", APP_DIR, *extra], capture_output=True, env=env, timeout=timeout)
    return r.returncode, ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")


def read_csv(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {row["Path"]: row for row in csv.DictReader(fh)}


def register_species(api: Api, fake: bool, names: list[str]) -> dict[str, str]:
    """種を登録して、付ける判定の名前 → 種のコード を返す"""
    code_of = {k: k for k in FIXED}
    if fake:
        api.post("/api/species/add_many", {"defaults": True})
        code_of.update(A="boar", B="deer")
        return code_of
    cand = api.get("/api/species/candidates?country=")
    rows = cand.get("rows") or []
    check(bool(cand.get("available")) and len(rows) > 100, "the list of species known to SpeciesNet is available", f"{len(rows)} candidates")
    want = {}
    for sci in names:
        hit = [x for x in rows if x.get("rank") == "species" and sci.lower() in ((x.get("sci") or "").lower(), (x.get("common") or "").lower())]
        if check(bool(hit), f"SpeciesNet knows {sci}", f"{hit[0].get('sci')} / {hit[0].get('common')}" if hit else ""):
            want[sci] = hit[0]
    api.post("/api/species/add_many", {"values": [x["value"] for x in want.values()]})
    for row in api.get("/api/species").get("species") or []:
        for sci, x in want.items():
            if (row.get("speciesnet") or "").lower() == str(x["value"]).lower() or (row.get("name_sci") or "").lower() == (x.get("sci") or "").lower():
                code_of[sci] = row["code"]
    check(all(sci in code_of for sci in names), "the species are registered from the candidates", ", ".join(f"{k} = {v}" for k, v in code_of.items() if k not in FIXED))
    return code_of


def first_auto(row: dict) -> str:
    """自動判定の第 1 候補（動物なしは empty）"""
    auto = row.get("auto") or []
    return (auto[0].get("species") if auto else None) or "empty"


def browser_processes(url: str) -> list[str]:
    """コマンドラインに url が入っているプロセスの名前（アプリモードで開いたブラウザの窓）"""
    try:
        if sys.platform.startswith("win"):
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*--app=" + url + "*' } | "
                  "ForEach-Object { $_.Name }")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
            return sorted({ln.strip() for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.strip() and "powershell" not in ln.lower()})   # 調べている powershell 自身も当たるので除く
        r = subprocess.run(["ps", "-axo", "command="], capture_output=True, timeout=60)
        hits = [ln for ln in r.stdout.decode("utf-8", "replace").splitlines() if "--app=" + url in ln]
        return sorted({os.path.basename(ln.split(" --")[0].strip()) for ln in hits})
    except Exception as e:
        say(f"    could not list the processes: {type(e).__name__}: {e}")
        return []


def start_check(work: str, port: int) -> None:
    """利用者と同じ起動のしかたで起動する（ブラウザを開く）。Windows では pythonw.exe（窓なし。標準出力がない）"""
    import app as trapnote_app                      # ブラウザの候補の一覧だけを使う（app.py は、読み込んだだけでは何も起動しない）
    exe = sys.executable
    if sys.platform.startswith("win"):
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = pyw if os.path.isfile(pyw) else exe
    cands = trapnote_app.BROWSERS_WIN if sys.platform.startswith("win") else (trapnote_app.BROWSERS_MAC if sys.platform == "darwin" else [])
    have = [c for c in cands if c and os.path.isfile(c)]
    say(f"  started with: {exe}")
    say("  browser for the application window: " + (have[0] if have else "none of Edge / Chrome / Chromium at the usual place (the default browser is used)"))
    url = f"http://127.0.0.1:{port}/"
    data_dir = os.path.join(work, "appdata start")
    env = {k: v for k, v in os.environ.items() if k not in ("TRAPNOTE_FAKE_ML", "TRAPNOTE_DATA_DIR")}
    proc = subprocess.Popen([exe, os.path.join(APP_DIR, "app.py"), "--port", str(port), "--data-dir", data_dir], cwd=APP_DIR, env=env)
    api = Api(port)
    try:
        ping = wait_ping(api, proc)
        check(ping.get("app") == "trapnote", "the application starts in the way a user starts it", f"version {ping.get('version')}")
        names: list[str] = []
        t0 = time.time()
        while have and not names and time.time() - t0 < 30:
            names = browser_processes(url)
            time.sleep(1.0)
        if have:
            check(bool(names), "the application window is opened in the browser", ", ".join(names))
        log = os.path.join(data_dir, "app.log")
        if os.path.isfile(log):
            with open(log, encoding="utf-8", errors="replace") as fh:
                for ln in fh.read().splitlines()[-4:]:
                    say("    log: " + ln)
    except Exception as e:
        check(False, "the application starts in the way a user starts it", f"{type(e).__name__}: {e}"[:300])
    finally:
        try:
            api.post("/api/quit", timeout=5)
        except Exception:
            pass
        try:
            proc.wait(timeout=60)
        except Exception:
            proc.kill()
        check(proc.poll() is not None, "the application quits when asked")
        try:                                    # 開いたブラウザの窓を閉じる（このポートのアプリの窓だけ）
            if sys.platform.startswith("win"):
                ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*--app=" + url + "*' } | "
                      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
            else:
                subprocess.run(["pkill", "-f", "--", "--app=" + url], capture_output=True, timeout=60)
        except Exception:
            pass


def main() -> int:
    global APP_DIR
    ap = argparse.ArgumentParser(description="End-to-end check of TrapNote through its HTTP API")
    ap.add_argument("--work", required=True, help="empty working folder (media, project and settings are created here)")
    ap.add_argument("--app", default=APP_DIR, help="TrapNote application folder (the one that holds app.py)")
    ap.add_argument("--fake", action="store_true", help="use the stand-in models (no torch, no model download)")
    ap.add_argument("--image", action="append", default=[], metavar="NAME=FILE[#crop=W:H:X:Y]",
                    help="photograph and the judgement expected for it: a scientific name, or empty / human / vehicle (repeat). "
                         "#crop= uses only a part of the photograph")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--device", default="cpu", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--start-check", action="store_true", help="also start the application as a user does (opens a browser window)")
    ap.add_argument("--skip-finetune", action="store_true", help="leave out fine-tuning (it takes tens of minutes on a CPU with little memory)")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):              # 記録（GitHub Actions など）は UTF-8。Windows の既定の文字コードでは、フォルダの名前の文字を出せない
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    APP_DIR = os.path.abspath(a.app)
    if not os.path.isfile(os.path.join(APP_DIR, "app.py")):
        ap.error(f"app.py was not found in: {APP_DIR}")
    sys.path.insert(0, APP_DIR)
    images = []
    for s in a.image:
        name, _, f = s.partition("=")
        f, _, crop = f.partition("#crop=")           # 写真の一部だけを使う（小さく写ったものを大きくする）: FILE#crop=幅:高さ:左:上
        if not name.strip() or not os.path.isfile(f):
            ap.error(f"--image needs 'NAME=existing file': {s}")
        images.append((name.strip(), os.path.abspath(f), f"crop={crop}," if crop else ""))
    species = [n for n, _, _ in images if n not in FIXED]
    if not a.fake and len(species) < 2:
        ap.error("without --fake, give two or more --image arguments with scientific names")

    work = os.path.abspath(a.work)
    root = os.path.join(work, "media", SURVEY)
    root2 = os.path.join(work, "media copy", SURVEY)
    data_dir = os.path.join(work, "appdata")
    os.makedirs(root, exist_ok=True)
    os.environ["TRAPNOTE_DATA_DIR"] = data_dir            # この台本が trapnote を読み込むときも、設定やログを work の中に置く
    from trapnote import VERSION
    say(f"TrapNote end-to-end check ({'stand-in models' if a.fake else 'real models'}, device {a.device})")
    say(f"System: {platform.platform()} {platform.machine()}, Python {platform.python_version()} ({sys.executable})")
    say(f"TrapNote {VERSION} in {APP_DIR}")
    t_start = time.time()

    say("Step 1. Create a small survey folder")
    expected = make_media(root, images, a.fake)
    shutil.copytree(root, root2)
    say(f"  {len(expected)} files in {len(STATIONS)} station folders under {root}")
    say(f"  ffmpeg: {ffmpeg_exe()}")
    try:
        import tkinter
        say(f"  file dialogs: tkinter {tkinter.TkVersion} is available")
    except Exception as e:
        say(f"  file dialogs: tkinter is not available ({type(e).__name__}); on macOS the system dialog is used instead, elsewhere paths are typed in")

    say("Step 2. Start the server")
    api = Api(a.port)
    proc = start_server(a.port, data_dir, a.fake, os.path.join(work, "server.log"))
    scripts: list[tuple[str, str, list[str]]] = []
    app_csv = os.path.join(work, "classification_by_app.csv")
    script_csv = os.path.join(work, "classification_by_script.csv")
    env: dict = {}
    try:
        ping = wait_ping(api, proc)
        check(ping.get("app") == "trapnote" and ping.get("version") == VERSION, "the server answers", f"version {ping.get('version')}, edition {ping.get('edition')}")
        st, ct, body = api.raw("GET", "/")
        check(st == 200 and b"<html" in body.lower(), "the interface page is served")
        st, ct, body = api.raw("GET", "/static/app.js")
        check(st == 200 and len(body) > 1000, "the interface scripts are served")

        say("Step 3. Create a project (English interface), choose the station folder and register species")
        api.post("/api/settings", {"language": "en", "device": a.device})
        r = api.post("/api/project/open", {"path": root, "create": True})
        proj_dir = (r.get("project") or {}).get("dir") or ""
        check(bool(proj_dir) and os.path.isdir(proj_dir), "the project is created", proj_dir)
        api.wait_idle(600, "scan")
        info = api.get("/api/project/station")
        check(info.get("n_items") == len(expected), "all files are registered", f"{info.get('n_items')} of {len(expected)}")
        api.post("/api/project/station/pick", {"station_level": 0, "season_level": None})
        rows = api.get("/api/items?limit=1000&sort=path")["rows"]
        check(sorted({str(x["station"]) for x in rows}) == STATIONS, "stations are read from the folder names", ", ".join(sorted({str(x["station"]) for x in rows})))
        check(sorted(x["rel_path"] for x in rows) == sorted(expected), "the files are listed under their own names")
        check([x["id"] for x in rows] == sorted(x["id"] for x in rows), "files are registered in name order")
        check(all(x.get("meta_state") == 1 for x in rows), "date, duration and size were read from every file")
        check(len(api.get("/api/species").get("species") or []) == 0, "a project made in the English interface starts with an empty species list")
        say("  getting the list of species from SpeciesNet (the model is downloaded at first use)")
        t0 = time.time()
        api.post("/api/species/model")
        api.wait_idle(T["model"], "SpeciesNet model")
        api.job_ok("ml", "species_model", "the SpeciesNet model is downloaded and loaded", f"{time.time() - t0:.0f} s")
        code_of = register_species(api, a.fake, species)
        status = api.get("/api/species/status")
        say(f"  species in the list: {status.get('n_species')}, used: {status.get('n_enabled')}")

        say("Step 4. Automated classification (MegaDetector and SpeciesNet)")
        t0 = time.time()
        api.post("/api/detect/start", {"device": a.device})
        api.wait_idle(T["classify"], "classification")
        job = api.last_job("ml", "detect")
        api.job_ok("ml", "detect", "classification finished", f"{job.get('done')} files in {time.time() - t0:.0f} s, counts {job.get('counts')}")
        rows = api.get("/api/items?limit=1000&sort=path")["rows"]
        check(all(x.get("det_state") == 1 for x in rows), "every file has a detection result")
        env = api.get("/api/detect/env")
        loaded = env.get("loaded") or {}
        say(f"  detector: {loaded.get('detector')}, classifier: {loaded.get('classifier')}, device: {loaded.get('device_name') or loaded.get('device')}")
        say(f"  torch: {env.get('torch_info')}")
        counts: dict[str, int] = {}
        for x in rows:
            counts[first_auto(x)] = counts.get(first_auto(x), 0) + 1
        say("  first species of the automated classification: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        sig1 = json.dumps([(x["rel_path"], first_auto(x)) for x in rows])
        sig2 = json.dumps([(x["rel_path"], first_auto(x), int(x.get("n_boxes") or 0)) for x in rows])
        say(f"  fingerprint of the results (file and first species): {hashlib.sha1(sig1.encode()).hexdigest()[:12]}; "
            f"with the number of boxes: {hashlib.sha1(sig2.encode()).hexdigest()[:12]}")
        if not a.fake:
            for group in dict.fromkeys(n for n, _, _ in images):
                mine = [x for x in rows if expected[x["rel_path"]] == group]
                if group in ("human", "vehicle"):       # 人と車は、候補のどこかに入っていればよい（動かない写真から作った動画では、第 1 候補が「動物なし」になることがある。OS の間で同じかは、上の照合用の値で見る）
                    right = [x for x in mine if group in [j.get("species") for j in (x.get("auto") or [])]]
                else:
                    right = [x for x in mine if first_auto(x) == code_of.get(group)]
                for x in mine:
                    if x not in right:
                        say(f"    {x['rel_path']}: expected {code_of.get(group)}, got {[j.get('species') for j in (x.get('auto') or [])]} ({x.get('n_boxes')} boxes)")
                check(len(right) >= 0.8 * len(mine), f"{group}: the automated classification is as expected", f"{len(right)} of {len(mine)} files")
        r = api.post("/api/export/csv", {})
        shutil.copyfile(r["path"], app_csv)
        r = api.post("/api/export/script", {})
        if check(os.path.isfile(r.get("path") or ""), "the classification script is written"):
            first = os.path.join(work, "reproduce_classification_first.py")
            shutil.copyfile(r["path"], first)
            scripts.append(("classification", first, ["--project", root2, "--device", a.device, "--csv", script_csv]))

        say("Step 5. Thumbnail and playback (ffmpeg)")
        vids = [x for x in rows if x["kind"] == "video"]
        for x in [v for v in vids if v["rel_path"].lower().endswith(".avi")][:1] + [v for v in vids if v["rel_path"].lower().endswith(".mp4")][:1]:
            st, ct, body = api.raw("GET", f"/api/items/{x['id']}/thumb")
            check(st == 200 and body[:2] == b"\xff\xd8", f"thumbnail of {x['rel_path']}", f"{len(body)} bytes")
            ready = prepare(api, x["id"], {})
            how = "played directly" if (ready or {}).get("direct") else ("read as MJPEG by the interface" if (ready or {}).get("mjpeg") else "converted to MP4")
            if check(ready is not None, f"{x['rel_path']} is ready for playback", how if ready else ""):
                st, ct, body = api.raw("GET", f"/api/items/{x['id']}/{'raw' if ready.get('mjpeg') else 'video'}")
                check(st in (200, 206) and len(body) > 1000, f"video data of {x['rel_path']} is served", f"{len(body)} bytes")
            if (ready or {}).get("mjpeg"):          # MJPEG の AVI は、画面がそのまま読む。ffmpeg での MP4 への変換（ほかの形式の AVI で使う経路）も確かめる
                conv = prepare(api, x["id"], {"no_mjpeg": True})
                if check(conv is not None and not conv.get("direct"), f"{x['rel_path']} is converted to MP4 with ffmpeg"):
                    st, ct, body = api.raw("GET", f"/api/items/{x['id']}/video")
                    check(st in (200, 206) and body[4:8] == b"ftyp", "the converted video is served as MP4", f"{len(body)} bytes")
        still = [x for x in rows if x["kind"] != "video"]
        if still:
            st, ct, body = api.raw("GET", f"/api/items/{still[0]['id']}/image")
            check(st == 200 and len(body) > 300, f"still image {still[0]['rel_path']} is served")
            st, ct, body = api.raw("GET", f"/api/items/{still[0]['id']}/thumb")
            check(st == 200 and body[:2] == b"\xff\xd8", f"thumbnail of {still[0]['rel_path']}")

        say("Step 6. Manual verification (labels saved through the interface API)")
        n_saved = 0
        for x in rows:
            code = code_of.get(expected[x["rel_path"]])
            if code:
                api.post(f"/api/items/{x['id']}/save", {"species": [code], "status": "verified"})
                n_saved += 1
        plan = api.get("/api/train/plan")
        check(plan.get("n_verified") == n_saved == len(rows), "all labels are saved", f"{plan.get('n_verified')} verified, {plan.get('n_ready')} usable for training")

        say("Step 7. Retraining (lightweight classifier) and classification with the retrained model")
        t0 = time.time()
        api.post("/api/train/start", {"kind": "A", "name": "e2e_A", "train_min_per_class": 2, "device": a.device})
        api.wait_idle(T["train"], "retraining")
        api.job_ok("ml", "train", "retraining finished", f"{time.time() - t0:.0f} s")
        models = [m.get("name") for m in api.get("/api/train/models").get("models") or []]
        if check("e2e_A" in models, "the retrained model is saved"):
            rec = api.get("/api/train/result?name=e2e_A")
            say(f"  classes: {rec.get('classes')}, training videos: {rec.get('n_items')}, validation stations: {rec.get('holdout_stations')}")
            if not a.fake:
                check({code_of.get(s) for s in species} <= set(rec.get("classes") or []), "the retrained model has a class for every species")
            api.post("/api/train/use", {"classifier": "local:e2e_A", "reclassify": True, "device": a.device})
            api.wait_idle(T["train"], "classification with the retrained model")
            api.job_ok("ml", "reclassify", "classification with the retrained model finished")
            rows2 = api.get("/api/items?limit=1000&sort=path")["rows"]
            used = sum(1 for x in rows2 if "e2e_A" in str(x.get("auto_model") or ""))
            with_boxes = sum(1 for x in rows2 if x.get("n_boxes"))
            check(used >= with_boxes > 0, "files with detections are classified again with the retrained model", f"{used} of {with_boxes}")
            if not a.fake:
                mine = [x for x in rows2 if expected[x["rel_path"]] in species]
                right = [x for x in mine if first_auto(x) == code_of.get(expected[x["rel_path"]])]
                check(len(right) >= 0.8 * len(mine), "the retrained model classifies the animals as verified", f"{len(right)} of {len(mine)} files")

        if a.skip_finetune and not a.fake:
            say("Step 7b. Fine-tuning of SpeciesNet: left out (--skip-finetune)")
        if not a.fake and not a.skip_finetune:
            say("Step 7b. Fine-tuning of SpeciesNet (one epoch) and classification with the fine-tuned model")
            t0 = time.time()
            api.post("/api/train/start", {"kind": "B", "name": "e2e_B", "train_min_per_class": 2, "train_epochs_b": 1, "train_batch_b": 4, "device": a.device})
            api.wait_idle(T["finetune"], "fine-tuning")
            api.job_ok("ml", "train", "fine-tuning finished", f"{time.time() - t0:.0f} s")
            models = [m.get("name") for m in api.get("/api/train/models").get("models") or []]
            if check("e2e_B" in models, "the fine-tuned model is saved"):
                rec = api.get("/api/train/result?name=e2e_B")
                say(f"  classes: {rec.get('classes')}, training videos: {rec.get('n_items')}, crops: {rec.get('n_crops')}, validation stations: {rec.get('holdout_stations')}")
                t0 = time.time()
                api.post("/api/train/use", {"classifier": "local:e2e_B", "reclassify": True, "device": a.device})
                api.wait_idle(T["classify"], "classification with the fine-tuned model")
                api.job_ok("ml", "reclassify", "classification with the fine-tuned model finished", f"{time.time() - t0:.0f} s")
                rows3 = api.get("/api/items?limit=1000&sort=path")["rows"]
                used = sum(1 for x in rows3 if "e2e_B" in str(x.get("auto_model") or ""))
                mine = [x for x in rows3 if expected[x["rel_path"]] in species]
                right = [x for x in mine if first_auto(x) == code_of.get(expected[x["rel_path"]])]
                check(used > 0, "files are classified again with the fine-tuned model", f"{used} files")
                check(len(right) >= 0.5 * len(mine), "the fine-tuned model classifies the animals as verified", f"{len(right)} of {len(mine)} files")

        say("Step 8. Export")
        r = api.post("/api/export/csv", {})
        n_rows = len(read_csv(r["path"])) if os.path.isfile(r.get("path") or "") else 0
        check(n_rows == len(rows), "CSV export", f"{n_rows} rows")
        r = api.post("/api/export/md_json", {})
        ok = os.path.isfile(r.get("path") or "")
        n_img = len(json.load(open(r["path"], encoding="utf-8")).get("images") or []) if ok else 0
        check(ok and n_img > 0, "MegaDetector JSON export", f"{n_img} files")
        api.post("/api/yolo/export", {})
        api.wait_idle(900, "YOLO export")
        api.job_ok("bg", "yolo_export", "YOLO export finished")
        ydir = os.path.join(proj_dir, "exports", "yolo")
        n_txt = sum(1 for _d, _s, fs in os.walk(ydir) for f in fs if f.endswith(".txt") and f != "classes.txt")
        n_jpg = sum(1 for _d, _s, fs in os.walk(ydir) for f in fs if f.lower().endswith(".jpg"))
        check(n_jpg > 0 and n_txt >= n_jpg, "YOLO export", f"{n_jpg} images, {n_txt} label files")

        say("Step 9. Reproducibility script of the retrained model, and a second project on a copy of the videos")
        if "e2e_A" in models:
            r = api.post("/api/export/script", {"model": "e2e_A"})
            if check(os.path.isfile(r.get("path") or ""), "the model script is written"):
                scripts.append(("model", r["path"], ["--device", a.device, "--strict"]))
        if "e2e_B" in models:
            r = api.post("/api/export/script", {"model": "e2e_B"})
            if check(os.path.isfile(r.get("path") or ""), "the script of the fine-tuned model is written"):
                scripts.append(("finetune", r["path"], ["--device", a.device]))
        r = api.post("/api/project/open", {"path": root2, "create": True})
        api.wait_idle(600, "scan of the second project")
        api.post("/api/project/station/pick", {"station_level": 0, "season_level": None})
        register_species(api, a.fake, species)
        n2 = api.get("/api/project/station").get("n_items")
        check(n2 == len(expected), "the second project is ready (not classified yet)", f"{n2} files")
    except Exception as e:
        check(False, "the run was interrupted", f"{type(e).__name__}: {e}"[:600])
        if "not finished after" in str(e):
            try:
                say("  state of the jobs: " + json.dumps(api.get("/api/jobs", timeout=10), ensure_ascii=False)[:1500])
            except Exception as e2:
                say(f"  the server does not answer: {type(e2).__name__}: {e2}"[:300])
            dump_stacks(proc)
    finally:
        try:
            api.post("/api/quit", timeout=5)
        except Exception:
            pass
        try:
            proc.wait(timeout=60)
        except Exception:
            proc.kill()
        check(proc.poll() is not None, "the server quits when asked")

    say("Step 10. Run the scripts without the interface")
    for kind, path, extra in scripts:
        t0 = time.time()
        try:
            code, out = run_script(path, extra, a.fake, data_dir, T["finetune"] if kind == "finetune" else T["classify"])
        except Exception as e:
            check(False, f"{os.path.basename(path)} runs without the interface", f"{type(e).__name__}: {e}"[:300])
            continue
        for ln in [ln for ln in out.splitlines() if ln.strip()][-16:]:
            say("    | " + ln)
        check(code == 0, f"{os.path.basename(path)} runs without the interface", f"exit code {code}, {time.time() - t0:.0f} s")
        if kind == "model":
            check("identical to the record" in out, "the retrained classifier is reproduced exactly")
        elif kind == "finetune":
            check("Comparison with the record" in out, "fine-tuning is repeated by the script and compared with the record")
        elif os.path.isfile(script_csv) and os.path.isfile(app_csv):
            by_app, by_script = read_csv(app_csv), read_csv(script_csv)
            check(sorted(by_app) == sorted(by_script) == sorted(expected), "the script processed the same files", f"{len(by_script)} files")
            diff = [p for p in by_app if p in by_script and any(by_app[p].get(c) != by_script[p].get(c) for c in COMPARE)]
            for p in diff[:8]:
                say(f"    {p}: app {[by_app[p].get(c) for c in COMPARE]}, script {[by_script[p].get(c) for c in COMPARE]}")
            check(not diff, "the script gives the same detections and species as the interface", f"{len(by_app) - len(diff)} of {len(by_app)} files identical")
            conf = [abs(float(by_app[p].get("DetMaxConf") or 0) - float(by_script[p].get("DetMaxConf") or 0)) for p in by_app if p in by_script]
            say(f"    largest difference in detection confidence: {max(conf) if conf else 0:.4f}")
        else:
            check(False, "the script writes the results as CSV")

    if a.start_check:
        say("Step 11. Start the application as a user does")
        start_check(work, a.port + 1)

    say()
    ti = env.get("torch_info") or {}
    say(f"SUMMARY | {platform.system()} {platform.release()} {platform.machine()} | Python {platform.python_version()} | TrapNote {VERSION} | "
        f"{'stand-in models' if a.fake else 'torch ' + str(ti.get('version')) + ', device ' + str((env.get('loaded') or {}).get('device'))} | "
        f"{n_checks - len(problems)} of {n_checks} checks passed | {time.time() - t_start:.0f} s")
    if problems:
        say(f"RESULT: {len(problems)} problem(s)")
        for p in problems:
            say("  - " + p)
        log = os.path.join(work, "server.log")
        if os.path.isfile(log):
            say("--- last lines of the server log ---")
            with open(log, encoding="utf-8", errors="replace") as fh:
                lines = [ln for ln in fh.read().splitlines() if "%|" not in ln and ln.strip()]       # 進み具合の棒（tqdm）は除く
                for ln in lines[-160:]:
                    say("    " + ln[:400])
        return 1
    say("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
