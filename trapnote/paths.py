"""パスの正規化と相対パス（areacheck の paths.py と同じ規則）"""
from __future__ import annotations

import os
import re

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".avi", ".mp4", ".mov", ".mkv", ".m4v", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp", ".webm"}
# ブラウザがそのまま再生できる可能性のある入れ物（中身の codec は media.probe で確かめる）
DIRECT_CONTAINERS = {".mp4", ".m4v", ".mov", ".webm"}


def norm(p) -> str:
    """絶対パスにして区切りを / にそろえる。ドライブ文字は大文字"""
    s = os.path.abspath(str(p)).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        s = s[0].upper() + s[1:]
    return s


def key(p) -> str:
    """比較用（Windows では大文字小文字を区別しない）。Windows では区切りが \\ になるので、「中にあるか」は is_under で見る（key 同士の startswith は / と \\ が混ざって当たらない）"""
    return os.path.normcase(norm(p))


def is_under(p, base) -> bool:
    """p が base の中にあるか（base 自体は含まない）。区切りを / にそろえてから比べる"""
    kp = key(p).replace("\\", "/")
    kb = key(base).replace("\\", "/").rstrip("/")
    return kp.startswith(kb + "/")


def kind_of(p) -> str | None:
    ext = os.path.splitext(str(p))[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def rel_of(path: str, root: str) -> str:
    """ルートからの相対パス（/ 区切り）。ルートの外ならそのまま絶対パス（areacheck の staytime.rel_of と同じ）"""
    p, r = norm(path), norm(root).rstrip("/")
    if key(p).startswith(key(r + "/")):
        return p[len(r) + 1:]
    return p


def abs_of(rel: str, root: str) -> str:
    """相対パスを絶対パスに戻す。絶対パスのままのもの（ルートの外）はそのまま"""
    if os.path.isabs(rel) or rel.startswith("//") or (len(rel) >= 2 and rel[1] == ":"):
        return norm(rel)
    return norm(os.path.join(root, rel))


def rel_key(rel: str) -> str:
    """相対パスの比較用（Windows の大文字小文字の違いを吸収）"""
    return rel.replace("\\", "/").strip("/").lower()


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(s: str) -> str:
    return _BAD.sub("_", s).strip(" .") or "_"


def cache_name(rel: str) -> str:
    """キャッシュ（再生用 MP4、サムネイル）のファイル名の基本。相対パスを _ でつなぐ。長すぎればハッシュで短くする"""
    import hashlib
    parts = [safe_name(x) for x in rel.replace("\\", "/").split("/") if x]
    stem = "_".join(parts)
    stem = os.path.splitext(stem)[0] if parts else "_"
    if len(stem) > 120:
        stem = stem[:100] + "_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
    return stem


IO_PRIORITY_VERY_LOW, IO_PRIORITY_NORMAL = 0, 2       # Windows のスレッドのディスクの優先度（0 = とても低い、1 = 低い、2 = ふつう）


def background_thread(on: bool = True) -> bool:
    """この（呼び出した）スレッドのディスクの読み書きの優先度を「とても低い」にする（on=False で「ふつう」に戻す）。
    下げている間は、画面の再生・サムネイル・枠の画像など、ふつうの優先度の読み出しが先に通る（裏の処理が動画を読み続けていると HDD が詰まり、
    再生する動画の読み出しが何秒も待たされるため）。CPU の優先度は変えない（Python のスレッドは、GIL を持ったまま後回しにされると、画面の応答まで止めるため）。
    Windows 以外では何もしない。返り値 = 設定できたか。

    使い方: 裏の処理のスレッドが、仕事 1 つ（動画 1 本）ごとに background_thread(foreground_active()) と呼ぶ = 人が画面で動画を見ている間だけ下げる。
    いつも下げたままにはしない。測ると（確認事項 133 回目。HDD の動画を 3 本並列で写す）、「とても低い」のままでは、人が見ていなくても写す速さが半分になった
    （131 → 65 MB/s）。検出のフレームの読み出しも 1 割遅くなった（1 フレーム 14.8 → 16.5 ミリ秒）。下げている間の画面側の読み出しは、
    中央 218 → 40 ミリ秒、最大 5.3 → 0.07 秒。「低い」（1）では、画面側の待ちがほとんど変わらなかったので、使わない。

    確認事項 133 回目に作り直した。前は SetThreadPriority(GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN) を、型を決めずに呼んでいて、
    64 ビットの Windows ではハンドルが 32 ビットに切られて「無効なハンドル」で失敗し、何も下がっていなかった"""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        level = ctypes.c_ulong(IO_PRIORITY_VERY_LOW if on else IO_PRIORITY_NORMAL)
        st = ctypes.WinDLL("ntdll").NtSetInformationThread(wintypes.HANDLE(-2), 22, ctypes.byref(level), ctypes.sizeof(level))     # -2 = このスレッド、22 = ThreadIoPriority
        return st == 0
    except Exception:
        return False


def thread_io_priority() -> int | None:
    """この（呼び出した）スレッドのディスクの優先度（0 = とても低い、1 = 低い、2 = ふつう）。Windows 以外・読めないときは None（確かめと試験用）"""
    import sys
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        v = ctypes.c_ulong(0)
        st = ctypes.WinDLL("ntdll").NtQueryInformationThread(wintypes.HANDLE(-2), 22, ctypes.byref(v), ctypes.sizeof(v), None)
        return int(v.value) if st == 0 else None
    except Exception:
        return None


# ---- 画面側の読み出しが続いているか（裏の処理が、人が見ている間だけ優先度を下げるための目印） ----
FOREGROUND_SEC = 30.0            # 画面側の最後の読み出しから、この秒数の間は「人が見ている」とみなす
_FOREGROUND_RE = re.compile(r"^/api/items/\d+(?:/(?:video|raw|image|thumb|crop/[^/]+))?$")
_fg_last = 0.0


def is_foreground_path(method: str, path: str) -> bool:
    """画面が人のために読むもの（動画を開く、再生の読み出し、静止画、サムネイル、枠の画像）の要求か。一覧や処理の進み具合の問い合わせは数えない"""
    return method == "GET" and bool(_FOREGROUND_RE.match(path or ""))


def note_foreground_read() -> None:
    """画面側の読み出しがあった、と記す（server の入口で、is_foreground_path の要求が来るたびに呼ぶ）"""
    global _fg_last
    import time
    _fg_last = time.monotonic()


def foreground_active(within: float | None = None) -> bool:
    """画面側の読み出しが、この within 秒（既定 FOREGROUND_SEC）の間にあったか"""
    import time
    return _fg_last > 0 and time.monotonic() - _fg_last < (FOREGROUND_SEC if within is None else within)


def lower_process_io(popen) -> bool:
    """起動した子プロセス（ffprobe など）のディスクの優先度を「とても低い」にする（NtSetInformationProcess の ProcessIoPriority = 33、0 = VeryLow。psutil の ionice と同じ）。
    裏の処理（メタデータの取得など）が大量にファイルを読むとき、画面の再生の読み出しを先に通すため。Windows 以外では何もしない"""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        h = getattr(popen, "_handle", None)
        if h is None:
            return False
        prio = ctypes.c_int(0)
        st = ctypes.windll.ntdll.NtSetInformationProcess(ctypes.c_void_p(int(h)), 33, ctypes.byref(prio), ctypes.sizeof(prio))
        return st == 0
    except Exception:
        return False
