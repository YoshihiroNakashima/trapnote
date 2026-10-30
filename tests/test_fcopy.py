"""速いコピー（fcopy）: 並列に写す、途中で止める（.part を残さない）、写した量と速さ。export_subset / copy_videos がこれを使う"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import fcopy  # noqa: E402


def _files(tmp_path, n=30, size=200_000):
    src = tmp_path / "src"
    src.mkdir()
    out = []
    for i in range(n):
        f = src / f"sub{i % 3}" / f"V{i:03d}.AVI"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(os.urandom(size))
        out.append(str(f))
    return out


def test_copy_many_parallel(tmp_path):
    files = _files(tmp_path)
    dst_dir = tmp_path / "dst"
    tp = fcopy.Throughput()
    done = []

    def do_one(f):
        d = os.path.join(str(dst_dir), os.path.relpath(f, str(tmp_path / "src")))
        tp.add(fcopy.copy_to(f, d, None))
        return d
    stopped = fcopy.copy_many(files, do_one, lambda f, r: done.append((f, r)), workers=3)
    assert not stopped and len(done) == len(files) and all(not isinstance(r, BaseException) for _, r in done)
    assert tp.bytes == 30 * 200_000 and tp.mb_s > 0 and "MB" in tp.text()
    for f, d in done:
        assert os.path.getsize(d) == os.path.getsize(f) and open(d, "rb").read(64) == open(f, "rb").read(64)
    assert not any(x.endswith(".part") for _, _, fs in os.walk(dst_dir) for x in fs)


def test_copy_many_stop(tmp_path):
    files = _files(tmp_path, n=40)
    dst_dir = tmp_path / "dst"
    done = []
    stop = lambda: len(done) >= 6

    def do_one(f):
        d = os.path.join(str(dst_dir), os.path.relpath(f, str(tmp_path / "src")))
        return fcopy.copy_to(f, d, stop)
    stopped = fcopy.copy_many(files, do_one, lambda f, r: done.append(r), workers=3, stop=stop)
    assert stopped
    ok = [r for r in done if not isinstance(r, BaseException)]
    cancelled = [r for r in done if isinstance(r, fcopy.CopyCancelled)]
    assert 6 <= len(ok) < 40 and len(ok) + len(cancelled) == len(done)
    assert not any(x.endswith(".part") for _, _, fs in os.walk(dst_dir) for x in fs)
    copied = [x for _, _, fs in os.walk(dst_dir) for x in fs]
    assert len(copied) == len(ok)


def test_copy_many_error_passed_to_on_done(tmp_path):
    files = _files(tmp_path, n=3)
    res = []
    fcopy.copy_many(files + ["/nonexistent/x.avi"], lambda f: fcopy.copy_to(f, str(tmp_path / "dst" / os.path.basename(f)), None), lambda f, r: res.append(r), workers=2)
    assert sum(isinstance(r, Exception) for r in res) == 1 and len(res) == 4
