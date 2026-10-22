"""OS の「開く」「保存」の窓（tkinter）を別プロセスで出して、選ばれた絶対パスを返す（areacheck の dialogs.py と同じ）。
Mac で tkinter がなければ osascript で代わりに出す"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

SCRIPT = r'''
import sys, os
import tkinter as tk
from tkinter import filedialog
kind = sys.argv[1]
initial = sys.argv[2] if len(sys.argv) > 2 else ""
name = sys.argv[3] if len(sys.argv) > 3 else ""
root = tk.Tk()
root.withdraw()
try:
    root.overrideredirect(True)
    root.attributes("-topmost", True)
except Exception:
    pass
root.update_idletasks()
kw = {}
if initial and os.path.isdir(initial):
    kw["initialdir"] = initial
elif initial and os.path.isdir(os.path.dirname(initial)):
    kw["initialdir"] = os.path.dirname(initial)
if kind == "folder":
    p = filedialog.askdirectory(title="Folder", mustexist=True, **kw)
elif kind == "csv":
    p = filedialog.askopenfilename(title="CSV", filetypes=[("CSV", "*.csv *.txt *.tsv"), ("All", "*.*")], **kw)
elif kind == "timelapse":
    p = filedialog.askopenfilename(title="Timelapse (.tdb / .ddb)", filetypes=[("Timelapse", "*.tdb *.ddb"), ("All", "*.*")], **kw)
elif kind == "json":
    p = filedialog.askopenfilename(title="JSON", filetypes=[("JSON", "*.json"), ("All", "*.*")], **kw)
elif kind == "exe":
    p = filedialog.askopenfilename(title="ffmpeg", filetypes=[("Program", "*.exe *"), ("All", "*.*")], **kw)
elif kind == "savecsv":
    p = filedialog.asksaveasfilename(title="CSV", defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=name or "export.csv", **kw)
elif kind == "savejson":
    p = filedialog.asksaveasfilename(title="JSON", defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile=name or "judgements.json", **kw)
else:
    p = filedialog.askdirectory(title="Folder", **kw)
try:
    root.quit()
    root.destroy()
except Exception:
    pass
sys.stdout.buffer.write((p or "").encode("utf-8"))
sys.stdout.flush()
os._exit(0)
'''

OSASCRIPT = {
    "folder": 'POSIX path of (choose folder with prompt "Folder")',
    "csv": 'POSIX path of (choose file with prompt "CSV" of type {"public.comma-separated-values-text", "public.plain-text"})',
    "json": 'POSIX path of (choose file with prompt "JSON")',
    "timelapse": 'POSIX path of (choose file with prompt "Timelapse")',
    "exe": 'POSIX path of (choose file with prompt "ffmpeg")',
    "savecsv": 'POSIX path of (choose file name with prompt "CSV" default name "export.csv")',
    "savejson": 'POSIX path of (choose file name with prompt "JSON" default name "judgements.json")',
}


async def pick(kind: str, initial: str | None = None, name: str | None = None) -> str | None:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", SCRIPT, kind, initial or "", name or "",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out, err = await proc.communicate()
        if proc.returncode == 0:
            p = out.decode("utf-8", "replace").strip()
            return p or None
        log.warning("ダイアログの失敗: %s", err.decode("utf-8", "replace")[-300:])
    except Exception as e:
        log.warning("ダイアログを開けません: %s", e)
    if sys.platform == "darwin" and kind in OSASCRIPT:
        proc = await asyncio.create_subprocess_exec("osascript", "-e", OSASCRIPT[kind],
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if proc.returncode == 0:
            p = out.decode("utf-8", "replace").strip().rstrip("/")
            return p or None
        return None
    raise RuntimeError("ファイル選択の窓を開けませんでした。パスを入力欄に貼り付けてください")
