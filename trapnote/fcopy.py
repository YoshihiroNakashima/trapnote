"""速いファイルのコピー。Windows では CopyFileExW（OS のコピー。大きな読み書きの単位と先読みで、Python の 1 MB ずつの読み書きより NAS からの転送が速い。途中で止められる）、
ほかは shutil（Linux の sendfile など）。複数のファイルを並列に写す copy_many も"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

IS_WIN = sys.platform.startswith("win")


class CopyCancelled(Exception):
    pass


if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROGRESS = ctypes.WINFUNCTYPE(wt.DWORD, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong, wt.DWORD, wt.DWORD, wt.HANDLE, wt.HANDLE, wt.LPVOID)
    _k32.CopyFileExW.argtypes = (wt.LPCWSTR, wt.LPCWSTR, _PROGRESS, wt.LPVOID, ctypes.POINTER(wt.BOOL), wt.DWORD)
    _k32.CopyFileExW.restype = wt.BOOL
    _ERROR_REQUEST_ABORTED = 1235

    def _wpath(p: str) -> str:
        p = os.path.abspath(p).replace("/", "\\")
        if len(p) > 240 and not p.startswith("\\\\?\\"):
            p = ("\\\\?\\UNC\\" + p[2:]) if p.startswith("\\\\") else ("\\\\?\\" + p)
        return p

    def copy_file(src: str, dst: str, stop=None) -> int:
        """src を dst に写す（上書き）。stop() が真になったら途中でやめて CopyCancelled（dst は消す）。返り値はバイト数"""
        cancel = wt.BOOL(0)

        def cb(total, transferred, stream_size, stream_transferred, stream_no, reason, hsrc, hdst, data):
            return 1 if (stop and stop()) else 0          # 1 = PROGRESS_CANCEL
        ok = _k32.CopyFileExW(_wpath(src), _wpath(dst), _PROGRESS(cb), None, ctypes.byref(cancel), 0)
        if not ok:
            err = ctypes.get_last_error()
            try:
                os.remove(dst)
            except OSError:
                pass
            if err == _ERROR_REQUEST_ABORTED:
                raise CopyCancelled()
            raise OSError(err, f"{ctypes.FormatError(err).strip()}: {src} -> {dst}")
        try:
            return os.path.getsize(dst)
        except OSError:
            return 0
else:
    def copy_file(src: str, dst: str, stop=None) -> int:
        if stop and stop():
            raise CopyCancelled()
        shutil.copy2(src, dst)
        return os.path.getsize(dst)


def copy_to(src: str, dst: str, stop=None) -> int:
    """<dst>.part に写してから名前を変える（途中で止めても中途半端なファイルを残さない）。フォルダは作る"""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".part"
    try:
        n = copy_file(src, tmp, stop)
        os.replace(tmp, dst)
        return n
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class Throughput:
    """写した量と速さ（MB/s）"""

    def __init__(self):
        self.t0 = time.time()
        self.bytes = 0
        self.lock = threading.Lock()

    def add(self, n: int) -> None:
        with self.lock:
            self.bytes += n

    @property
    def seconds(self) -> float:
        return max(1e-6, time.time() - self.t0)

    @property
    def mb_s(self) -> float:
        return self.bytes / 1e6 / self.seconds

    def text(self) -> str:
        gb = self.bytes / 1e9
        return (f"{gb:.1f} GB" if gb >= 0.1 else f"{self.bytes / 1e6:.0f} MB") + f"・{self.mb_s:.0f} MB/s"


def copy_many(tasks, do_one, on_done, workers: int = 3, stop=None) -> bool:
    """tasks（反復子）を workers 本ずつ並列に do_one(task) で処理し、終わったものから順に on_done(task, result_or_exception) を呼ぶ（呼び出し元のスレッドで）。
    stop() が真になったら新しい仕事は入れず、走っているものは do_one 側（copy_file の stop）で止まるのを待つ。返り値 = 止めたか"""
    workers = max(1, int(workers or 1))
    stopped = False
    it = iter(tasks)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="copy") as ex:
        pending: dict = {}
        exhausted = False
        while True:
            while not stopped and not exhausted and len(pending) < workers * 2:
                try:
                    task = next(it)
                except StopIteration:
                    exhausted = True
                    break
                pending[ex.submit(do_one, task)] = task
            if not pending:
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                task = pending.pop(fut)
                try:
                    res = fut.result()
                except BaseException as e:          # CopyCancelled や OSError は on_done に渡す
                    res = e
                on_done(task, res)
            if stop and stop():
                stopped = True
    return stopped
