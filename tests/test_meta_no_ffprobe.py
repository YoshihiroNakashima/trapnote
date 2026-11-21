"""ffprobe がない PC でのメタデータの取得（確認事項 148 回目）。
インストーラで入れた環境には imageio-ffmpeg の ffmpeg しかなく、ffprobe がない。そのときも、
  - `ffmpeg -i` の表示から読む値が正しいこと（AVI の creation_time は間に空白がある。前は時刻が 00:00:00 になっていた）
  - MetaJob がヘッダを読む速い経路を使い、突き合わせは丸めのぶんだけを許すこと、合っていればヘッダの値を書くこと
  - 違う値が出る型は、今までどおりの経路の値になること
動画は ffmpeg で作る（ffmpeg がなければ飛ばす。ffprobe は要らない）。実行: python -m pytest tests -q"""
from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import fastprobe, media, scan  # noqa: E402
from trapnote.jobs import MetaJob  # noqa: E402
from trapnote.project import Project  # noqa: E402

AVI_TEXT = """Input #0, avi, from 'IMG_0001.AVI':
  Metadata:
    creation_time   : 2018-07-14 13:00:22
    software        : CarDV-TURNKEY
  Duration: 00:00:20.03, start: 0.000000, bitrate: 14713 kb/s
  Stream #0:0: Video: mjpeg (Baseline) (MJPG / 0x47504A4D), yuvj422p(pc, bt470bg/unknown/unknown), 1280x720, 14595 kb/s, 29.97 fps, 30 tbr, 30 tbn
  Stream #0:1: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 16000 Hz, mono, s16, 256 kb/s
At least one output file must be specified
"""
MP4_TEXT = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'IMG_0001.MP4':
  Metadata:
    major_brand     : mp42
    minor_version   : 0
    compatible_brands: isomavc1mp42
    creation_time   : 2026-07-22T11:25:51.000000Z
  Duration: 00:00:04.57, start: 0.000000, bitrate: 10410 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080, 10291 kb/s, 30 fps, 30 tbr, 100k tbn (default)
      Metadata:
        creation_time   : 2026-07-22T11:25:52.000000Z
        handler_name    : Handler
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 92 kb/s (default)
      Metadata:
        creation_time   : 2026-07-22T11:25:53.000000Z
"""


def test_parse_ffmpeg_info():
    a = media.parse_ffmpeg_info(AVI_TEXT)
    assert a == dict(duration=20.03, vcodec="mjpeg", width=1280, height=720, fps=29.97, acodec="pcm_s16le", creation_time="2018-07-14 13:00:22")
    assert media.parse_dt_any(a["creation_time"]) == "2018/07/14 13:00:22"            # 前は「2018-07-14」までしか取らず、2018/07/14 00:00:00 になっていた
    m = media.parse_ffmpeg_info(MP4_TEXT)
    assert (m["duration"], m["vcodec"], m["width"], m["height"], m["fps"], m["acodec"]) == (4.57, "h264", 1920, 1080, 30.0, "aac")
    assert m["creation_time"] == "2026-07-22T11:25:51.000000Z"                        # 全体の creation_time が先
    # 全体に日時がなければ、映像のストリームの creation_time（音声のものは使わない）
    no_head = MP4_TEXT.replace("    creation_time   : 2026-07-22T11:25:51.000000Z\n", "")
    assert media.parse_ffmpeg_info(no_head)["creation_time"] == "2026-07-22T11:25:52.000000Z"
    only_audio = no_head.replace("        creation_time   : 2026-07-22T11:25:52.000000Z\n", "")
    assert media.parse_ffmpeg_info(only_audio)["creation_time"] is None
    # date（AVI の INFO の ICRD）も、ffprobe の経路と同じ順で使う
    assert media.parse_ffmpeg_info(AVI_TEXT.replace("creation_time   : 2018-07-14 13:00:22", "date            : 2019-08-07"))["creation_time"] == "2019-08-07"
    # 読めない出力では、どれも None（例外を出さない）
    assert set(media.parse_ffmpeg_info("x.avi: Invalid data found when processing input").values()) == {None}


def test_differences_loose():
    fast = dict(duration=20.166465, width=1296, height=720, fps=30.00030000300003, n_frames=605, vcodec="h264", acodec="aac", container="mp4",
                datetime="2026/09/07 03:40:12", datetime_source="mtime")
    shown = dict(fast, duration=20.17, fps=30.0, n_frames=605)                        # `ffmpeg -i` の表示から読んだ値（丸めてある）
    assert fastprobe.differences(fast, shown) == ["duration", "fps"]                  # ffprobe が相手のときの比べ方では、違うとみなす
    assert fastprobe.differences(fast, shown, loose=True) == []
    assert fastprobe.differences(fast, dict(shown, n_frames=606), loose=True) == []   # 丸めた長さ × 丸めた fps から出うる差
    assert fastprobe.differences(fast, dict(shown, duration=20.18), loose=True) == ["duration"]
    assert fastprobe.differences(fast, dict(shown, fps=29.97), loose=True) == ["fps"]
    assert fastprobe.differences(fast, dict(shown, n_frames=620), loose=True) == []   # 音声が映像より長い動画: 見積もり（長さ × fps）のほうが多い。1 秒ぶんまで許す
    assert fastprobe.differences(fast, dict(shown, n_frames=700), loose=True) == ["n_frames"]
    assert fastprobe.differences(fast, dict(shown, n_frames=600), loose=True) == ["n_frames"]      # ヘッダの値のほうが多いのは、丸めのぶんまで
    # 大きさ・codec・日時は、loose でも完全一致
    assert fastprobe.differences(fast, dict(shown, width=1280, acodec=None, datetime="2026/09/07 00:00:00"), loose=True) == ["width", "acodec", "datetime"]


# ---------- ここから下は ffmpeg で動画を作る ----------
@pytest.fixture(scope="module")
def ffmpeg():
    exe, _ = media.find_ffmpeg("")
    if not exe:
        pytest.skip("ffmpeg がない")
    return exe


@pytest.fixture(scope="module")
def vids(ffmpeg, tmp_path_factory):
    d = tmp_path_factory.mktemp("noffprobe")
    v = ["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30000/1001"]               # 29.97 fps（表示は丸めた値になる）
    a = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    want = {"a.avi": v + a + ["-t", "1.3", "-c:v", "mjpeg", "-q:v", "5", "-c:a", "pcm_s16le"],
            "b.avi": v + ["-t", "1.3", "-c:v", "mjpeg", "-q:v", "5"],
            "x.mkv": v + a + ["-t", "1", "-c:v", "mjpeg", "-q:v", "5", "-c:a", "pcm_s16le"]}
    out = {}
    for name, args in want.items():
        p = str(d / name)
        r = subprocess.run([ffmpeg, "-y", "-v", "error", "-nostdin"] + args + [p], capture_output=True)
        assert r.returncode == 0 and os.path.getsize(p) > 0, r.stderr.decode("utf-8", "replace")[-300:]
        out[name] = p
    # ヘッダの JUNK の頭を IDIT（カメラが書く撮影日時）にした AVI
    data = open(out["b.avi"], "rb").read()
    i = data.find(b"JUNK", 12)
    n = struct.unpack_from("<I", data, i + 4)[0]
    text = b"2018/07/14 13:00:22\0"
    idit = b"IDIT" + struct.pack("<I", len(text)) + text + b"JUNK" + struct.pack("<I", n - len(text) - 8)
    out["idit.avi"] = str(d / "idit.avi")
    with open(out["idit.avi"], "wb") as fh:
        fh.write(data[:i] + idit + data[i + len(idit):])
    return out


@pytest.fixture()
def no_ffprobe(ffmpeg, monkeypatch):
    """ffmpeg だけがある PC（ffprobe は見つからない）"""
    monkeypatch.setattr(media, "find_ffmpeg", lambda setting="": (ffmpeg, None))
    monkeypatch.delenv("TRAPNOTE_FAST_PROBE", raising=False)
    return ffmpeg


def test_avi_datetime_without_ffprobe(ffmpeg, vids):
    """撮影日時つきの AVI: `ffmpeg -i` の経路でも、時刻まで読む。ヘッダを読む経路と同じ日時になる"""
    slow = media.probe_video(vids["idit.avi"], ffmpeg, None)
    assert (slow["datetime"], slow["datetime_source"]) == ("2018/07/14 13:00:22", "metadata")
    fast = fastprobe.probe_fast(vids["idit.avi"])
    assert fast is not None and fastprobe.differences(fast, slow, loose=True) == []
    assert abs(fast["fps"] - 30000 / 1001) < 1e-9 and slow["fps"] == 29.97             # 表示は丸めてあり、ヘッダの値は丸めていない


class _Ctx:
    def __init__(self, project):
        self.project = project
        self.settings = {"ffmpeg_path": ""}


def _project(tmp_path, files: dict):
    root = tmp_path / "season"
    for rel, src in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if src:
            shutil.copyfile(src, f)
        else:
            f.write_bytes(b"")
    p = Project(str(root / "TrapNote"), root=str(root), create=True)
    scan.register(p, scan.walk_media(str(root), p.setting("skip_names")))
    return p


def _spy(monkeypatch):
    calls = dict(slow=[], fast=[])
    real_probe, real_fast = media.probe, fastprobe.probe_fast

    def probe(path, *a, **kw):
        calls["slow"].append(os.path.basename(path))
        return real_probe(path, *a, **kw)

    def fast(path, *a, **kw):
        calls["fast"].append(os.path.basename(path))
        return real_fast(path, *a, **kw)
    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(fastprobe, "probe_fast", fast)
    return calls


def test_metajob_fast_without_ffprobe(no_ffprobe, vids, tmp_path, monkeypatch, caplog):
    files = {f"ST01/IMG_{i:04d}.AVI": vids["a.avi"] for i in range(6)}
    files.update({f"ST02/IMG_{i:04d}.AVI": vids["idit.avi"] for i in range(3)})
    files.update({"ST02/A.MKV": vids["x.mkv"], "ST02/EMPTY.AVI": None})
    p = _project(tmp_path, files)
    try:
        calls = _spy(monkeypatch)
        job = MetaJob()
        with caplog.at_level(logging.INFO, logger="trapnote.jobs"):
            job.run(_Ctx(p))
        assert (job.total, job.done, job.failed) == (len(files), len(files), 0)
        assert len(calls["fast"]) == len(files)                                        # 全部、まずヘッダを読む
        # `ffmpeg -i` にかけるのは、型ごとの最初の 2 本と、ヘッダからは言い切れないもの（MKV、空のファイル）だけ
        assert sorted(calls["slow"]) == sorted(["IMG_0000.AVI", "IMG_0001.AVI", "IMG_0000.AVI", "IMG_0001.AVI", "A.MKV", "EMPTY.AVI"])
        assert any("突き合わせの相手: ffmpeg -i の表示" in r.getMessage() for r in caplog.records)
        for r in p.db.q("SELECT * FROM items WHERE name LIKE 'IMG_%' ORDER BY id"):
            fast = fastprobe.probe_fast(p.abs_path(r["rel_path"]))
            assert r["meta_state"] == 1
            for k in fastprobe.FIELDS:                                                 # 突き合わせた 2 本も含めて、ヘッダから読んだ値（丸めていない値）
                assert r[k] == fast[k], (r["rel_path"], k)
        st2 = p.db.q("SELECT datetime, datetime_source FROM items WHERE folder='ST02' AND name LIKE 'IMG_%'")
        assert {(r["datetime"], r["datetime_source"]) for r in st2} == {("2018/07/14 13:00:22", "metadata")}
        mkv = p.db.q1("SELECT * FROM items WHERE name='A.MKV'")                        # 言い切れないものは、今までどおり `ffmpeg -i` の値
        assert mkv["meta_state"] == 1 and mkv["vcodec"] == "mjpeg" and mkv["width"] == 160
        job2 = MetaJob()
        job2.run(_Ctx(p))
        assert job2.total == 0
    finally:
        p.close()


def test_metajob_bad_signature_without_ffprobe(no_ffprobe, vids, tmp_path, monkeypatch, caplog):
    """ヘッダから読んだ値が `ffmpeg -i` の表示と合わない型: 既に書いた分も含めて、今までどおりの経路の値にする"""
    p = _project(tmp_path, {f"ST01/IMG_{i:04d}.AVI": vids["a.avi"] for i in range(10)})
    try:
        calls = _spy(monkeypatch)
        spied = fastprobe.probe_fast

        def wrong(path, *a, **kw):                    # 3 本目から、長さが 0.5 秒長い値を返す（丸めのぶんより大きい違い）
            m = spied(path, *a, **kw)
            if m and int(os.path.basename(path)[4:8]) >= 2:
                m["duration"] += 0.5
            return m
        monkeypatch.setattr(fastprobe, "probe_fast", wrong)
        job = MetaJob()
        job.CHUNK, job.SPOT = 4, 3
        with caplog.at_level(logging.WARNING, logger="trapnote.jobs"):
            job.run(_Ctx(p))
        assert (job.done, job.failed) == (10, 0)
        rows = p.db.q("SELECT * FROM items ORDER BY id")
        real = media.probe_video(p.abs_path(rows[0]["rel_path"]), no_ffprobe, None)
        assert all(r["meta_state"] == 1 and abs(r["duration"] - real["duration"]) <= 0.006 for r in rows)      # 0.5 秒長い値は残らない
        assert sorted(set(calls["slow"])) == sorted(r["name"] for r in rows)
        assert len([r for r in caplog.records if "ffprobe と違う" in r.getMessage()]) == 1
    finally:
        p.close()


def test_metajob_kill_switch_without_ffprobe(no_ffprobe, vids, tmp_path, monkeypatch):
    p = _project(tmp_path, {f"ST01/IMG_{i:04d}.AVI": vids["a.avi"] for i in range(4)})
    try:
        calls = _spy(monkeypatch)
        monkeypatch.setenv("TRAPNOTE_FAST_PROBE", "0")
        job = MetaJob()
        job.run(_Ctx(p))
        assert calls["fast"] == [] and len(calls["slow"]) == 4 and (job.done, job.failed) == (4, 0)
        assert p.db.scalar("SELECT COUNT(*) FROM items WHERE meta_state=1 AND fps=29.97") == 4      # `ffmpeg -i` の表示の値（丸めてある）
    finally:
        p.close()
