"""裏の処理の列: 実行中のメタデータ取得（kind = meta）は、横断のカタログ作成・取り込みに順番を譲る（yield_current）"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote.jobs import Job, Lane  # noqa: E402


class _Ctx:
    project = None

    def db(self):
        return None


class _Slow(Job):
    def __init__(self, kind):
        super().__init__(kind, {}, kind)
        self.started_ev = threading.Event()

    def run(self, ctx):
        self.started_ev.set()
        for _ in range(200):
            if self.stop_flag.is_set():
                return
            time.sleep(0.02)


def test_yield_current():
    lane = Lane("test", _Ctx())
    try:
        meta = _Slow("meta")
        lane.submit(meta)
        assert meta.started_ev.wait(2)
        assert not lane.yield_current(("scan",))            # 種類が違えば譲らない
        assert lane.yield_current(("meta",)) and meta.stop_flag.is_set()
        nxt = _Slow("cross_catalog")
        lane.submit(nxt, front=True)
        assert nxt.started_ev.wait(2)                        # 止めたのですぐ次が始まる
        assert meta.state == "stopped" and "譲りました" in meta.message
        assert not lane.yield_current(("meta",))             # 実行中が meta でなければ何もしない
    finally:
        lane.close()
