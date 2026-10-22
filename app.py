"""TrapNote: カメラトラップのデータ管理アプリ

  python app.py                  サーバーを立ててブラウザに画面を開く
  python app.py --no-browser     画面を開かない（URL を表示する）
  python app.py --port 8766      ポートを変える
  python app.py --data-dir DIR   設定・状態・ログの置き場所を変える（試験用）
  python app.py --make-shortcut  デスクトップに起動用のショートカットを作る（Windows）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from trapnote import APP_ID, APP_NAME, VERSION, BUILD, BUILD_TIME    # noqa: E402

log = logging.getLogger("app")

BROWSERS_WIN = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]
BROWSERS_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def setup_logging() -> None:
    from trapnote.config import DATA_DIR, LOG_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:   # pythonw.exe では標準出力がない。モデルの読み込みなどが print / tqdm で落ちないようにファイルへ
        try:
            f = open(DATA_DIR / "console.log", "a", encoding="utf-8", errors="replace", buffering=1)
            if sys.stdout is None:
                sys.stdout = f
            if sys.stderr is None:
                sys.stderr = f
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):   # cp932 のコンソールでも日本語で落ちないように
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for name in ("aiohttp.access", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def running_version(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8"))
            if d.get("app") != APP_ID:
                return None
            return f"{d.get('version') or '?'}+{d.get('build') or '0'}"
    except Exception:
        return None


def stop_instance(port: int, wait_sec: float = 15.0) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception as e:
        log.warning("古いサーバーに終了を頼めません: %s", e)
        return False
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if port_free(port):
            return True
        time.sleep(0.3)
    return port_free(port)


def find_port(start: int) -> int:
    for p in range(start, start + 50):
        if port_free(p):
            return p
    raise SystemExit("空いているポートがありません")


def open_browser(url: str) -> str:
    """Edge か Chrome のアプリモード（専用の窓）で開く。なければ既定のブラウザ"""
    cands = BROWSERS_WIN if sys.platform.startswith("win") else (BROWSERS_MAC if sys.platform == "darwin" else [])
    for exe in cands:
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1500,920", "--new-window"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return os.path.basename(exe)
            except Exception as e:
                log.warning("%s を起動できません: %s", exe, e)
    webbrowser.open(url)
    return "default"


def make_shortcut() -> int:
    if not sys.platform.startswith("win"):
        print("ショートカットの作成は Windows だけです")
        return 1
    pyw = sys.executable[:-len("python.exe")] + "pythonw.exe" if sys.executable.lower().endswith("python.exe") else sys.executable
    if not os.path.isfile(pyw):
        pyw = sys.executable
    app = os.path.join(APP_DIR, "app.py")
    ps = (
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
        '$d = [Environment]::GetFolderPath("Desktop"); '
        '$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut((Join-Path $d "{APP_NAME}.lnk")); '
        f'$s.TargetPath = "{pyw}"; '
        f'$s.Arguments = \'"{app}"\'; '
        f'$s.WorkingDirectory = "{APP_DIR}"; '
        f'$s.IconLocation = "{sys.executable},0"; '
        f'$s.Description = "{APP_NAME}"; '
        '$s.Save(); Write-Output (Join-Path $d "' + APP_NAME + '.lnk")'
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("ショートカットを作れません:", r.stderr.strip())
        return 1
    print("ショートカットを作りました:", r.stdout.strip())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, help="待ち受けポート（既定は設定の 8765）")
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを開かない")
    ap.add_argument("--make-shortcut", action="store_true", help="デスクトップに起動用のショートカットを作る")
    ap.add_argument("--data-dir", help="設定・状態・ログを置くフォルダ（既定は TrapNote/data）")
    ap.add_argument("--project", help="起動時に開くプロジェクト（メディアのルートか TrapNote フォルダ）")
    a = ap.parse_args()
    if a.data_dir:
        os.environ["TRAPNOTE_DATA_DIR"] = os.path.abspath(a.data_dir)
    setup_logging()
    log.info("%s %s（%s 更新）を起動（%s）", APP_NAME, VERSION, BUILD_TIME, sys.executable)
    if a.make_shortcut:
        return make_shortcut()

    from trapnote.server import Session, serve
    session = Session()
    port = a.port or int(session.settings["port"])
    if not port_free(port):
        ver = running_version(port)
        if ver == f"{VERSION}+{BUILD}":
            log.info("起動済みのサーバーがあるので、画面だけ開きます")
            if not a.no_browser:
                open_browser(f"http://127.0.0.1:{port}/")
            return 0
        if ver is not None:
            log.info("起動済みのサーバーは古い版（%s）なので止めて、%s+%s で起動し直します", ver, VERSION, BUILD)
            if not stop_instance(port):
                port = find_port(port + 1)
        else:
            port = find_port(port + 1)
    url = f"http://127.0.0.1:{port}/"

    def restore():
        p = a.project or session.state.last_project
        if p and os.path.isdir(p):
            try:
                session.open_project(p)
                log.info("前回のプロジェクトを開きました: %s", p)
            except Exception as e:
                log.warning("前回のプロジェクトを開けません: %s", e)

    session.io.submit(restore)

    def on_ready(p):
        if not a.no_browser:
            log.info("画面を開きます: %s（%s）", url, open_browser(url))
        else:
            print(url, flush=True)

    asyncio.run(serve(session, "127.0.0.1", port, on_ready))
    return 0


if __name__ == "__main__":
    sys.exit(main())
