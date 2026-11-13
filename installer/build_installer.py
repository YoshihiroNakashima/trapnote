"""TrapNote のインストーラ（dist/TrapNote-Setup-<版>-<日付>.exe）を作る。

使い方（TrapNote のフォルダで。conda 環境 trapnote の Python で）:
    python installer/build_installer.py                 プログラムのフォルダ（この台本の 1 つ上）から作る
    python installer/build_installer.py --src <フォルダ> --out <出す先のフォルダ>

すること:
  1. 配るファイルを zip にまとめる（build/payload.zip）。入れるのは、動かすのに要るものだけ:
     app.py、trapnote/*.py、static/*、resources/*、env/*、*.bat、README.md、LICENSE。
     入れないもの: data（設定・ログ・モデル）、tests、bench、installer、確認事項.md、reports、research_notes、dist、build、__pycache__
  2. アイコン（installer/trapnote.ico）がなければ作る（Pillow）
  3. Windows に入っている C# のコンパイラ（.NET Framework 4 の csc.exe）で、installer/TrapNoteSetup.cs を exe にする。zip は exe の中に入る
できた exe は、Python も torch も含まない小さなファイル（1 MB ほど）。入れる先の PC で、conda 環境を作る（インターネットが要る）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
TOP_FILES = ("app.py", "README.md", "LICENSE")
DIRS = {"trapnote": (".py",), "static": None, "resources": None, "env": None}       # フォルダ → 入れる拡張子（None = 全部）
SKIP_NAMES = {"__pycache__", ".pytest_cache"}


def payload_files(src: str) -> list[str]:
    out = [f for f in TOP_FILES if os.path.isfile(os.path.join(src, f))]
    out += sorted(f for f in os.listdir(src) if f.lower().endswith(".bat"))
    for d, exts in DIRS.items():
        base = os.path.join(src, d)
        for root, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_NAMES]
            for n in sorted(names):
                if n.endswith(".pyc") or (exts and not n.lower().endswith(exts)):
                    continue
                out.append(os.path.relpath(os.path.join(root, n), src).replace("\\", "/"))
    return out


def version_of(src: str) -> tuple[str, str]:
    """(版, プログラムの日時)。日時は、アプリの build と同じ決め方（trapnote/*.py、app.py、static/*、resources/* のいちばん新しい更新時刻）"""
    text = open(os.path.join(src, "trapnote", "__init__.py"), encoding="utf-8").read()
    ver = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M).group(1)
    files = [os.path.join(src, "app.py")]
    for d in ("trapnote", "static", "resources"):
        files += [os.path.join(src, d, n) for n in os.listdir(os.path.join(src, d)) if os.path.isfile(os.path.join(src, d, n)) and (d != "trapnote" or n.endswith(".py"))]
    t = max(os.path.getmtime(f) for f in files)
    return ver, time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def make_icon(path: str) -> None:
    """青い角丸の四角に白い T（画面のタブのアイコンと同じ形）"""
    from PIL import Image, ImageDraw
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=48, fill=(59, 91, 219, 255))
    k = s / 32.0                                    # もとの形は 32×32 の中の M9 11h14v3h-5.5v9h-3v-9H9z
    pts = [(9, 11), (23, 11), (23, 14), (17.5, 14), (17.5, 23), (14.5, 23), (14.5, 14), (9, 14)]
    d.polygon([(x * k, y * k) for x, y in pts], fill=(255, 255, 255, 255))
    im.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.dirname(HERE))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    src = os.path.abspath(a.src)
    out_dir = os.path.abspath(a.out or os.path.join(src, "dist"))
    build_dir = os.path.join(out_dir, "build")
    os.makedirs(build_dir, exist_ok=True)
    if not os.path.isfile(CSC):
        print("C# のコンパイラが見つかりません:", CSC)
        return 1
    ver, stamp = version_of(src)
    files = payload_files(src)
    zpath = os.path.join(build_dir, "payload.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(os.path.join(src, rel), rel)          # 日時はファイルのものが入る（入れた先でも、プログラムの日時 = build が同じになる）
        z.writestr("VERSION.txt", f"{ver}  {stamp}\n")
    ico = os.path.join(HERE, "trapnote.ico")
    if not os.path.isfile(ico):
        make_icon(ico)
    exe = os.path.join(out_dir, f"TrapNote-Setup-{ver}-{stamp[:10].replace('-', '')}.exe")
    cmd = [CSC, "/nologo", "/target:winexe", "/optimize+", "/codepage:65001", "/platform:anycpu", f"/out:{exe}",
           f"/win32icon:{ico}", f"/win32manifest:{os.path.join(HERE, 'app.manifest')}", f"/resource:{zpath},payload.zip",
           "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
           "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll", os.path.join(HERE, "TrapNoteSetup.cs")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="cp932", errors="replace")
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        return 1
    if r.stdout.strip():
        print(r.stdout.strip())
    data = open(exe, "rb").read()
    print(f"プログラム {ver}（{stamp}）、{len(files)} ファイル、zip {os.path.getsize(zpath)} バイト")
    print(f"できた: {exe}  {len(data)} バイト  SHA-256 {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
