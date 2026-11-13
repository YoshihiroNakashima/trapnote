"""裏の処理のスレッドのディスクの優先度（確認事項 133 回目）:
paths.background_thread が実際に下げること（前は Windows の関数が失敗して、何も下がっていなかった）、
写すスレッドが、人が画面で動画を見ている間だけ「とても低い」で動くこと（見ていない間は「ふつう」）、
画面側の読み出しの目印（is_foreground_path / note_foreground_read / foreground_active）、サーバーの入口がその目印を付けること"""
from __future__ import annotations

import asyncio
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import fcopy, paths, scan  # noqa: E402
from trapnote.project import Project  # noqa: E402

WIN = sys.platform.startswith("win")


def _in_thread(fn):
    out = {}

    def body():
        out["v"] = fn()

    t = threading.Thread(target=body)
    t.start()
    t.join()
    return out["v"]


@pytest.mark.skipif(not WIN, reason="Windows だけ")
def test_background_thread_lowers_io_priority():
    def body():
        before = paths.thread_io_priority()
        on = paths.background_thread()
        low = paths.thread_io_priority()
        off = paths.background_thread(False)
        return before, on, low, off, paths.thread_io_priority()

    before, on, low, off, after = _in_thread(body)
    assert before == paths.IO_PRIORITY_NORMAL                    # 新しいスレッドは、ふつうの優先度
    assert on is True and low == paths.IO_PRIORITY_VERY_LOW      # 実際に下がる（前は、返り値が False で、何も変わらなかった）
    assert off is True and after == paths.IO_PRIORITY_NORMAL     # 戻せる


@pytest.mark.skipif(WIN, reason="Windows 以外")
def test_background_thread_noop_elsewhere():
    assert paths.background_thread() is False and paths.thread_io_priority() is None


def _copy(tmp_path, name):
    src = tmp_path / ("src_" + name)
    src.mkdir()
    tasks = []
    for i in range(6):
        f = src / f"{i}.bin"
        f.write_bytes(bytes([i]) * 1000)
        tasks.append((str(f), str(tmp_path / ("dst_" + name) / f"{i}.bin")))
    seen = []

    def do_one(t):
        fcopy.copy_to(t[0], t[1])
        return paths.thread_io_priority()

    stopped = fcopy.copy_many(tasks, do_one, lambda t, res: seen.append(res), workers=3)
    assert not stopped and len(seen) == 6 and all(os.path.getsize(d) == 1000 for _s, d in tasks)
    return set(seen)


def test_copy_many_lowers_only_while_viewing(tmp_path, monkeypatch):
    # 人が見ていない（画面側の読み出しがない）: 写すスレッドは、ふつうの優先度のまま（下げたままだと、写す速さが半分になるため）
    monkeypatch.setattr(paths, "_fg_last", 0.0)
    assert _copy(tmp_path, "idle") == ({paths.IO_PRIORITY_NORMAL} if WIN else {None})
    # 人が見ている（画面側の読み出しがあった直後）: 写すスレッドは「とても低い」
    paths.note_foreground_read()
    assert _copy(tmp_path, "viewing") == ({paths.IO_PRIORITY_VERY_LOW} if WIN else {None})
    # 見終わって 30 秒より後: 同じスレッドの集まりでなくても、また「ふつう」に戻る
    monkeypatch.setattr(paths, "_fg_last", paths._fg_last - paths.FOREGROUND_SEC - 1)
    assert _copy(tmp_path, "after") == ({paths.IO_PRIORITY_NORMAL} if WIN else {None})
    assert paths.thread_io_priority() in (None, paths.IO_PRIORITY_NORMAL)       # 呼んだ側のスレッドは変わらない


def test_foreground_marks(monkeypatch):
    ok = ["/api/items/12", "/api/items/12/video", "/api/items/12/raw", "/api/items/12/image", "/api/items/12/thumb", "/api/items/12/crop/3"]
    ng = ["/api/items", "/api/items/count", "/api/items/12/index", "/api/items/12/neighbor", "/api/items/12/detections", "/api/items/12/crops", "/api/jobs", "/api/state", "/static/app.js", "/"]
    assert all(paths.is_foreground_path("GET", p) for p in ok)
    assert not any(paths.is_foreground_path("GET", p) for p in ng)
    assert not paths.is_foreground_path("POST", "/api/items/12/save") and not paths.is_foreground_path("POST", "/api/items/12")
    monkeypatch.setattr(paths, "_fg_last", 0.0)
    assert paths.foreground_active() is False             # 起動してから一度も読んでいない
    paths.note_foreground_read()
    assert paths.foreground_active() is True and paths.foreground_active(0) is False
    monkeypatch.setattr(paths, "_fg_last", paths._fg_last - paths.FOREGROUND_SEC - 1)
    assert paths.foreground_active() is False             # 30 秒たてば、見ていない扱いに戻る


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey")
def test_server_marks_foreground_reads(tmp_path, monkeypatch):
    """サーバーの入口: 動画を開く・サムネイルなどの要求で目印が付き、一覧や処理の進み具合の問い合わせでは付かない"""
    from aiohttp.test_utils import TestClient, TestServer
    from trapnote import config
    from trapnote.server import Session, make_app
    root = tmp_path / "S"
    (root / "ST01").mkdir(parents=True)
    (root / "ST01" / "IMG_0001.AVI").write_bytes(b"x" * 100)
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    iid = p.db.q1("SELECT id FROM items")["id"]
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    s = Session()
    s.project = p

    async def run():
        async with TestClient(TestServer(make_app(s))) as c:
            monkeypatch.setattr(paths, "_fg_last", 0.0)
            assert (await c.get("/api/jobs")).status == 200
            assert (await c.get("/api/items", params={"limit": "5"})).status == 200
            assert paths.foreground_active() is False
            assert (await c.get(f"/api/items/{iid}")).status == 200          # 動画を開いた
            assert paths.foreground_active() is True
            monkeypatch.setattr(paths, "_fg_last", 0.0)
            await c.get(f"/api/items/{iid}/raw")                            # 再生の読み出し（中身は見ない）
            assert paths.foreground_active() is True

    try:
        asyncio.run(run())
    finally:
        s.close()
