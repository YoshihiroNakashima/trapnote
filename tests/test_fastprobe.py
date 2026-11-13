"""メタデータの取得の速い経路（fastprobe）: 動画のヘッダを直接読んだ値が ffprobe（media.probe_video）と同じこと、言い切れないものは None を返すこと。
MetaJob: 署名（ファイルの「型」）ごとに ffprobe と突き合わせ、違いが出たら既に書いた分も含めて ffprobe で取り直すこと、環境変数で切れること、「止める」が効くこと。
動画は ffmpeg で作る（ffmpeg がなければ飛ばす）。実行: python -m pytest tests -q"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import fastprobe, media, scan  # noqa: E402
from trapnote.jobs import MetaJob  # noqa: E402
from trapnote.project import Project  # noqa: E402

V = ["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30"]
A = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
T = ["-t", "1"]
MJPEG = ["-c:v", "mjpeg", "-q:v", "5"]
H264 = [["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"], ["-c:v", "libopenh264", "-pix_fmt", "yuv420p"], ["-c:v", "h264_nvenc", "-pix_fmt", "yuv420p"]]
HEVC = [["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-x265-params", "log-level=error"],
        ["-c:v", "hevc_nvenc", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]]
AAC = ["-c:a", "aac", "-b:a", "64k"]
# 名前 → (入力、映像の符号化の候補、残りの引数)。値が出て ffprobe と同じになるはずのもの
SAME = {
    "avi_mjpeg_pcm.avi": (V + A + T, [MJPEG], ["-c:a", "pcm_s16le"]),
    "avi_mjpeg_noaudio.avi": (V + T, [MJPEG], []),
    "avi_mpeg4.avi": (V + A + T, [["-c:v", "mpeg4"]], ["-c:a", "pcm_s16le"]),
    "avi_h264.avi": (V + T, H264, []),
    "mov_mjpeg_pcm.mov": (V + A + T, [MJPEG], ["-c:a", "pcm_s16le"]),
    "mp4_h264_aac.mp4": (V + A + T, H264, AAC),                                    # moov は最後
    "mp4_h264_aac_faststart.mp4": (V + A + T, H264, AAC + ["-movflags", "+faststart"]),
    "mp4_h264_noaudio.mp4": (V + T, H264, []),
    "mov_h264.mov": (V + A + T, H264, AAC),
    "mp4_creation_time.mp4": (V + A + T, H264, AAC + ["-metadata", "creation_time=2020-05-06T07:08:09Z"]),
    "mp4_hevc.mp4": (V + A + T, HEVC, AAC),
}
# None を返すはずのもの（入れ物が対象外）
OTHER = {
    "x.mkv": (V + A + T, [MJPEG], ["-c:a", "pcm_s16le"]),
    "x.webm": (V + A + T, [["-c:v", "libvpx", "-b:v", "200k"], ["-c:v", "libvpx-vp9", "-b:v", "200k"]], ["-c:a", "libvorbis"]),
}


@pytest.fixture(scope="module")
def ff():
    ffmpeg, ffprobe = media.find_ffmpeg("")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg / ffprobe がない")
    return ffmpeg, ffprobe


@pytest.fixture(scope="module")
def vids(ff, tmp_path_factory):
    """試験用の動画を作る。名前 → パス（作れなかったもの = 符号化器がない は入らない）"""
    d = tmp_path_factory.mktemp("fastprobe")
    out = {}
    for name, (src, encs, rest) in {**SAME, **OTHER}.items():
        p = str(d / name)
        for enc in encs:
            r = subprocess.run([ff[0], "-y", "-v", "error", "-nostdin"] + src + enc + rest + [p], capture_output=True)
            if r.returncode == 0 and os.path.isfile(p) and os.path.getsize(p) > 0:
                out[name] = p
                break
    return out


def _same(fast: dict, real: dict) -> None:
    assert fast is not None
    assert fastprobe.differences(fast, real) == []
    for k in fastprobe.FIELDS:                       # 許す誤差の中ではなく、完全に同じ値
        assert fast[k] == real[k], k


@pytest.mark.parametrize("name", list(SAME))
def test_same_as_ffprobe(name, ff, vids):
    if name not in vids:
        pytest.skip("この ffmpeg では作れない")
    real = media.probe_video(vids[name], ff[0], ff[1])
    assert real["vcodec"] and real["duration"] and real["n_frames"]
    _same(fastprobe.probe_fast(vids[name]), real)
    assert fastprobe.explain(vids[name]) == ""


def test_values_and_signature(ff, vids):
    m = fastprobe.probe_fast(vids["avi_mjpeg_pcm.avi"])
    assert (m["vcodec"], m["acodec"], m["container"], m["width"], m["height"], m["n_frames"], m["fps"], m["duration"]) == ("mjpeg", "pcm_s16le", "avi", 160, 120, 30, 30.0, 1.0)
    assert m["datetime_source"] == "mtime" and m["datetime"] == media.mtime_dt(vids["avi_mjpeg_pcm.avi"])
    m2 = fastprobe.probe_fast(vids["avi_mjpeg_noaudio.avi"])
    assert m2["acodec"] is None and m2["sig"] != m["sig"] and hash(m["sig"]) is not None      # 音のあり・なしで署名が違う
    if "mp4_h264_aac.mp4" in vids:
        a, b = fastprobe.probe_fast(vids["mp4_h264_aac.mp4"]), fastprobe.probe_fast(vids["mp4_h264_aac_faststart.mp4"])
        assert (a["vcodec"], a["acodec"]) == ("h264", "aac") and a["sig"] != b["sig"]         # moov の位置で署名が違う
        c = fastprobe.probe_fast(vids["mp4_creation_time.mp4"])
        assert (c["datetime"], c["datetime_source"]) == ("2020/05/06 07:08:09", "metadata") and c["sig"] != a["sig"]
        # 「末尾 Z は UTC」の設定でも ffprobe の経路と同じ（この PC の時差で直した時刻）
        _same(fastprobe.probe_fast(vids["mp4_creation_time.mp4"], True), media.probe_video(vids["mp4_creation_time.mp4"], ff[0], ff[1], True))


def test_none_when_unsure(ff, vids, tmp_path):
    for name in OTHER:
        if name in vids:                                                          # MKV / WebM は対象外
            assert fastprobe.probe_fast(vids[name]) is None and fastprobe.explain(vids[name])
            assert media.probe_video(vids[name], ff[0], ff[1])["vcodec"]
    avi = vids["avi_mjpeg_pcm.avi"]
    data = open(avi, "rb").read()
    cases = {"cut.avi": data[:len(data) * 6 // 10], "head.avi": data[:200], "empty.avi": b"", "empty.mp4": b"", "garbage.mov": os.urandom(4096),
             "avi_named.mp4": data}                                              # 拡張子と中身が合わない
    if "mp4_h264_aac.mp4" in vids:
        mp4 = open(vids["mp4_h264_aac.mp4"], "rb").read()
        cases.update({"cut.mp4": mp4[:len(mp4) * 6 // 10], "mp4_named.avi": mp4})
    for name, b in cases.items():
        p = tmp_path / name
        p.write_bytes(b)
        assert fastprobe.probe_fast(str(p)) is None, name                          # 例外も出さない
    assert fastprobe.probe_fast(str(tmp_path / "nothing.avi")) is None             # ファイルがない


def test_avi_dates_and_tail(ff, vids, tmp_path, monkeypatch):
    """AVI の日時（IDIT → creation_time、INFO の ICRD → date）と、movi のあと（索引のほかに INFO があるか）"""
    import struct
    data = open(vids["avi_mjpeg_noaudio.avi"], "rb").read()

    def write(name, b):
        p = tmp_path / name
        p.write_bytes(b)
        return str(p)

    def riff(b):                                     # RIFF の大きさをファイルの大きさに合わせる
        return b[:4] + struct.pack("<I", len(b) - 8) + b[8:]
    # ヘッダの JUNK の頭を IDIT にする（カメラが書く形）
    i = data.find(b"JUNK", 12)
    n = struct.unpack_from("<I", data, i + 4)[0]
    for k, (text, want) in enumerate([(b"Mon Mar 10 15:04:43 2003\n\0", "2003/03/10 15:04:43"), (b"2018/07/14 13:00:22\0", "2018/07/14 13:00:22"), (b"2018:07:14 13:00:22\0", None)]):
        idit = b"IDIT" + struct.pack("<I", len(text)) + text + b"JUNK" + struct.pack("<I", n - len(text) - 8)
        p = write(f"idit{k}.avi", data[:i] + idit + data[i + len(idit):])
        m = fastprobe.probe_fast(p)
        _same(m, media.probe_video(p, ff[0], ff[1]))
        assert (m["datetime"], m["datetime_source"]) == ((want, "metadata") if want else (media.mtime_dt(p), "mtime"))
    # movi のあと: 索引だけなら読まずに済む（署名は =idx1）。INFO が付いていれば読み、その中に日付があれば ffprobe に任せる
    plain = fastprobe.probe_fast(vids["avi_mjpeg_noaudio.avi"])
    assert plain["sig"][-1] == "=idx1"
    info = lambda body: b"LIST" + struct.pack("<I", 4 + len(body)) + b"INFO" + body      # noqa: E731
    p = write("tail_soft.avi", riff(data + info(b"ISFT" + struct.pack("<I", 4) + b"abc\0")))
    m = fastprobe.probe_fast(p)
    _same(m, media.probe_video(p, ff[0], ff[1]))
    assert m["sig"][-1] == "idx1,LIST" and m["sig"] != plain["sig"]
    p = write("tail_date.avi", riff(data + info(b"ICRD" + struct.pack("<I", 12) + b"2019-08-07\0\0")))      # 大きさが 16 の倍数でも、フレーム数と合わないので読む
    assert fastprobe.probe_fast(p) is None and "日付" in fastprobe.explain(p)
    assert media.probe_video(p, ff[0], ff[1])["datetime"] == "2019/08/07 00:00:00"     # ffprobe はこの日付を使う（だから ffprobe に回す）
    # 必ず読む設定でも値は同じ
    monkeypatch.setattr(fastprobe, "AVI_TAIL_ALWAYS", True)
    m = fastprobe.probe_fast(vids["avi_mjpeg_noaudio.avi"])
    assert m["sig"][-1] == "idx1" and {k: m[k] for k in fastprobe.FIELDS} == {k: plain[k] for k in fastprobe.FIELDS}


def test_helpers():
    assert fastprobe._av_reduce(600 * 1000000, 20066466) == (100000000, 3344411, True)
    assert fastprobe._av_reduce(6001 * 1000000, 200031334)[2] is False              # 分子が int に収まらない: 近い分数に
    assert fastprobe._idit(b"Mon Mar 10 15:04:43 2003\n\0") == "2003-03-10 15:04:43"
    assert fastprobe._idit(b"2018/07/14 13:00:22\0") == "2018-07-14 13:00:22"
    assert fastprobe._idit(b"2018:07:14 13:00:22\0") is None
    a = dict(duration=20.0, width=1296, height=720, fps=30.0, n_frames=600, vcodec="mjpeg", acodec=None, container="avi", datetime="2020/01/01 00:00:00", datetime_source="mtime")
    assert fastprobe.differences(a, dict(a, duration=20.0005, fps=30.0 + 1e-12)) == []
    assert fastprobe.differences(a, dict(a, duration=20.002, width=1280, acodec="aac")) == ["duration", "width", "acodec"]


# ---------- MetaJob ----------
class _Ctx:
    def __init__(self, project):
        self.project = project
        self.settings = {"ffmpeg_path": ""}


def _project(tmp_path, files: dict):
    """files: 相対パス → 写す元のパス（None なら空のファイル）"""
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
    """media.probe（ffprobe）と fastprobe.probe_fast が呼ばれたファイル名を覚える"""
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


def _check_rows(p, ff):
    """items の値が、どの動画も ffprobe の値と同じ"""
    rows = p.db.q("SELECT * FROM items WHERE kind='video' ORDER BY id")
    for r in rows:
        assert r["meta_state"] == 1, r["rel_path"]
        real = media.probe_video(p.abs_path(r["rel_path"]), ff[0], ff[1])
        for k in fastprobe.FIELDS:
            assert r[k] == real[k], (r["rel_path"], k)
    return rows


def test_metajob_fast(ff, vids, tmp_path, monkeypatch):
    monkeypatch.delenv("TRAPNOTE_FAST_PROBE", raising=False)
    avi, mkv = vids["avi_mjpeg_pcm.avi"], vids["x.mkv"]
    files = {f"ST01/IMG_{i:04d}.AVI": avi for i in range(6)}
    files.update({"ST02/A.MKV": mkv, "ST02/EMPTY.AVI": None})
    if "mp4_h264_aac.mp4" in vids:
        files.update({f"ST02/IMG_{i:04d}.MP4": vids["mp4_h264_aac.mp4"] for i in range(4)})
    p = _project(tmp_path, files)
    try:
        calls = _spy(monkeypatch)
        job = MetaJob()
        job.run(_Ctx(p))
        n_mp4 = sum(1 for f in files if f.endswith(".MP4"))
        assert (job.total, job.done, job.failed) == (len(files), len(files), 0) and job.message == f"{len(files)} 件取得、0 件失敗"
        assert len(calls["fast"]) == len(files)
        # ffprobe にかけるのは、署名ごとの最初の 2 本と、ヘッダからは言い切れないもの（MKV、空のファイル）だけ
        assert sorted(calls["slow"]) == sorted(["IMG_0000.AVI", "IMG_0001.AVI", "A.MKV", "EMPTY.AVI"] + (["IMG_0000.MP4", "IMG_0001.MP4"] if n_mp4 else []))
        rows = _check_rows(p, ff)
        assert {r["vcodec"] for r in rows} == {"mjpeg", None} | ({"h264"} if n_mp4 else set())
        # 取り終わったものは対象にならない
        job2 = MetaJob()
        job2.run(_Ctx(p))
        assert job2.total == 0
    finally:
        p.close()


def test_metajob_bad_signature(ff, vids, tmp_path, monkeypatch, caplog):
    """速い経路が違う値を出す型: 突き合わせで気づき、既に書いた分も含めて ffprobe の値にする"""
    monkeypatch.delenv("TRAPNOTE_FAST_PROBE", raising=False)
    avi = vids["avi_mjpeg_pcm.avi"]
    p = _project(tmp_path, {f"ST01/IMG_{i:04d}.AVI": avi for i in range(12)})
    try:
        calls = _spy(monkeypatch)
        spied = fastprobe.probe_fast

        def wrong(path, *a, **kw):                    # 3 本目から、幅が違う値を返す
            m = spied(path, *a, **kw)
            if m and int(os.path.basename(path)[4:8]) >= 2:
                m["width"] += 2
            return m
        monkeypatch.setattr(fastprobe, "probe_fast", wrong)
        seen = []
        real_tx = p.db.tx

        def tx():                                     # 書き込みのたびに、その時点の幅を覚える（違う値が一度は書かれ、あとで直ることを見る）
            seen.append({r["name"]: r["width"] for r in p.db.q("SELECT name, width FROM items WHERE meta_state=1")})
            return real_tx()
        monkeypatch.setattr(p.db, "tx", tx)
        job = MetaJob()
        job.CHUNK, job.SPOT = 4, 3                    # 4 本ずつ進め、3 本ごとに 1 本を突き合わせる
        with caplog.at_level(logging.WARNING, logger="trapnote.jobs"):
            job.run(_Ctx(p))
        monkeypatch.setattr(p.db, "tx", real_tx)
        assert (job.done, job.failed) == (12, 0)
        assert any(w.get("IMG_0002.AVI") == 162 for w in seen)                      # 最初の 2 本は合っていたので、3・4 本目は違う値のまま一度書かれた
        rows = _check_rows(p, ff)                                                   # 最後は全部 ffprobe の値
        assert all(r["width"] == 160 for r in rows)
        assert sorted(calls["slow"]) == sorted(r["name"] for r in rows)             # 全部を ffprobe で取った（3・4 本目は取り直し）
        warns = [r for r in caplog.records if "ffprobe と違う" in r.getMessage()]
        assert len(warns) == 1 and "width" in warns[0].getMessage()                 # 警告は型ごとに 1 回、違った項目の名前つき
    finally:
        p.close()


def test_metajob_kill_switch(ff, vids, tmp_path, monkeypatch):
    avi = vids["avi_mjpeg_pcm.avi"]
    p = _project(tmp_path, {f"ST01/IMG_{i:04d}.AVI": avi for i in range(5)})
    try:
        calls = _spy(monkeypatch)
        monkeypatch.setenv("TRAPNOTE_FAST_PROBE", "0")
        job = MetaJob()
        job.run(_Ctx(p))
        assert calls["fast"] == [] and len(calls["slow"]) == 5 and (job.done, job.failed) == (5, 0)     # 今までどおり全部 ffprobe
        _check_rows(p, ff)
    finally:
        p.close()


def test_metajob_stop(ff, vids, tmp_path, monkeypatch):
    monkeypatch.delenv("TRAPNOTE_FAST_PROBE", raising=False)
    avi = vids["avi_mjpeg_pcm.avi"]
    p = _project(tmp_path, {f"ST01/IMG_{i:04d}.AVI": avi for i in range(12)})
    try:
        job = MetaJob()
        job.stop_flag.set()                           # 始まる前に止める: 何もしない
        job.run(_Ctx(p))
        assert job.done == 0 and p.db.scalar("SELECT COUNT(*) FROM items WHERE meta_state=1") == 0
        # 途中で止める: 2 つ目のかたまりを読んでいる間に「止める」。そのかたまりまでは書き、残りは未取得のまま
        job = MetaJob()
        job.CHUNK = 4
        real_fast = fastprobe.probe_fast

        def fast(path, *a, **kw):
            if os.path.basename(path) == "IMG_0005.AVI":
                job.stop_flag.set()
            return real_fast(path, *a, **kw)
        monkeypatch.setattr(fastprobe, "probe_fast", fast)
        job.run(_Ctx(p))
        assert job.done == 8 and job.failed == 0
        left = [r["name"] for r in p.db.q("SELECT name FROM items WHERE meta_state=0 ORDER BY id")]
        assert left == [f"IMG_{i:04d}.AVI" for i in range(8, 12)]
        # 次の回は残りだけ（ヘッダを読むスレッドのディスクの優先度を下げる設定でも同じ）
        monkeypatch.setattr(fastprobe, "probe_fast", real_fast)
        job = MetaJob()
        job.FAST_LOW_IO = True
        job.run(_Ctx(p))
        assert (job.total, job.done) == (4, 4)
        _check_rows(p, ff)
        import threading
        got = []
        t = threading.Thread(target=lambda: got.append(fastprobe.low_io_thread()))       # 別のスレッドで（試験のスレッドの優先度は変えない）
        t.start()
        t.join()
        assert got == [sys.platform.startswith("win")]
    finally:
        p.close()
