"""動画のヘッダを直接読んで、ffprobe と同じメタデータを出す（別プロセスを起こさない近道。メタデータの取得のジョブ用）。
対応するのは AVI（RIFF。映像は MJPEG / H.264 / MPEG-4）と MP4 / MOV / M4V（ISO BMFF。映像は H.264 / HEVC / MJPEG）で、映像 1 本・音声 0〜1 本のもの。
読むのはファイルの先頭（ヘッダと最初のフレームの頭）と、MP4 の moov（最後にあることが多い）だけ。AVI の movi のあと（索引 idx1）は、大きさの計算で中身が言い切れないときだけ読む。
値の出し方は ffmpeg の libavformat（avidec.c / mov.c / demux.c）と ffprobe の表示に合わせてある。映像の大きさは復号器がフレームの中の見出し
（JPEG の SOF、H.264 / HEVC の SPS、MPEG-4 の VOL）から決めるので、入れ物に書いてある値がそれと同じになることも確かめる。
ffprobe と同じ値になると言い切れないとき（知らない入れ物・codec、変わった構造、途中で切れたファイル、例外）は None を返す = 「このファイルは ffprobe で」。
返す dict には sig（ファイルの「型」の署名）が入る。MetaJob は署名ごとに最初の数本を ffprobe と突き合わせ、違えばその署名のファイルを全部 ffprobe に回す
（知らないカメラの形式で、違う値を黙って書かないため）"""
from __future__ import annotations

import operator
import os
import re
import struct
from datetime import datetime, timedelta

from . import media

HEAD = 64 * 1024                 # MP4 / MOV で最初にまとめて読む量（ftyp と、先頭にある moov が入る）
AVI_HEAD = 8 * 1024              # AVI で最初に読む量（ヘッダと最初のフレームの頭が入る。NAS では読む量がそのまま時間になるので小さく）
AVI_TAIL_ALWAYS = False          # True: AVI の movi のあとを必ず読む（大きさの計算で済ませない。冷えた HDD では時間が約 2 倍になる）
MOOV_MAX = 16 * 1024 * 1024      # moov がこれより大きければ ffprobe に任せる（何時間もある動画。丸ごとメモリに読むので上限を置く）
INT_MAX = 2 ** 31 - 1
EPOCH_1904 = 2082844800          # 1904-01-01 から 1970-01-01 までの秒（MP4 の時刻の起点）
FIELDS = ("duration", "width", "height", "fps", "n_frames", "vcodec", "acodec", "container", "datetime", "datetime_source")


class _Unsure(Exception):
    """ffprobe と同じ値になると言い切れない（理由つき。probe_fast は None を返す）"""


def _need(ok, why: str) -> None:
    if not ok:
        raise _Unsure(why)


# ---------- 公開の関数 ----------
def probe_fast(path: str, z_is_utc: bool = False) -> dict | None:
    """media.probe_video と同じキー・同じ値（duration, width, height, fps, n_frames, vcodec, acodec, container, datetime, datetime_source）と sig。
    言い切れないときは None（呼ぶ側が ffprobe に回す）。例外は出さない"""
    try:
        return _probe(path, z_is_utc)
    except Exception:
        return None


def explain(path: str, z_is_utc: bool = False) -> str:
    """probe_fast が None を返す理由（調べるとき用）。値が出るなら空文字"""
    try:
        _probe(path, z_is_utc)
        return ""
    except _Unsure as e:
        return str(e)
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def low_io_thread() -> bool:
    """呼び出したスレッドのディスクの優先度だけを「とても低い」にする（NtSetInformationThread の ThreadIoPriority = 22、0 = VeryLow）。
    CPU の優先度は変えない（Python のスレッドは、GIL を持ったまま後回しにされると画面の応答まで止めるため）。
    ThreadPoolExecutor の initializer に渡す。Windows 以外では何もしない。返り値 = 設定できたか"""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        prio = ctypes.c_ulong(0)
        return ctypes.windll.ntdll.NtSetInformationThread(ctypes.c_void_p(-2), 22, ctypes.byref(prio), ctypes.sizeof(prio)) == 0      # -2 = このスレッド
    except Exception:
        return False


def differences(fast: dict, real: dict) -> list[str]:
    """速い経路の結果と ffprobe の結果（media.probe）で違う項目の名前。長さは 0.001 秒、fps は相対 1e-9 までを同じとみなす。ほかは完全一致"""
    out = []
    for k in FIELDS:
        a, b = fast.get(k), real.get(k)
        if a is None or b is None:
            same = a is None and b is None
        elif k == "duration":
            same = abs(a - b) <= 0.001
        elif k == "fps":
            same = abs(a - b) <= 1e-9 * max(abs(a), abs(b))
        else:
            same = a == b
        if not same:
            out.append(k)
    return out


# ---------- 読み出し ----------
class _Reader:
    """ファイルの一部を読む。先頭のかたまり（head）は持ち続け、その外は要るところだけ読む
    （HDD ではヘッドの移動の回数が時間を決めるので、読む場所を増やさない）"""
    __slots__ = ("f", "size", "head", "xoff", "xbuf")

    def __init__(self, f, size: int, head: int):
        self.f, self.size = f, size
        self.head = self._read(min(head, size))
        self.xoff, self.xbuf = 0, b""

    def _read(self, n: int) -> bytes:
        b = self.f.read(n) or b""
        while len(b) < n:                              # 1 回で読み切れないことがあれば続きを読む
            c = self.f.read(n - len(b))
            if not c:
                break
            b += c
        return b

    def get(self, pos: int, n: int, ahead: int = 0) -> bytes:
        """pos から n バイト（ファイルの終わりを越える分は返らない）。持っていなければ pos から max(n, ahead) バイト読んで覚える"""
        if pos + n <= len(self.head):
            return self.head[pos:pos + n]
        o = pos - self.xoff
        if o >= 0 and o + n <= len(self.xbuf):
            return self.xbuf[o:o + n]
        if pos >= self.size:
            return b""
        self.f.seek(pos)
        b = self._read(min(max(n, ahead), self.size - pos))
        self.xoff, self.xbuf = pos, b
        return b[:n]


def _opener(path: str, flags: int) -> int:
    """OS の先読みをさせずに開く（Windows の O_RANDOM）。読むのは 1〜2 か所だけなのに先頭を読むと続きを先読みされ、冷えた HDD では 1 本あたりの時間が 1.5 倍になる"""
    return os.open(path, flags | getattr(os, "O_RANDOM", 0))


def _probe(path: str, z_is_utc: bool) -> dict:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    _need(ext in ("avi", "mp4", "m4v", "mov"), "対象外の拡張子")
    with open(path, "rb", buffering=0, opener=_opener) as f:
        size = os.fstat(f.fileno()).st_size
        _need(size >= 16, "ファイルが小さすぎる")
        r = _Reader(f, size, AVI_HEAD if ext == "avi" else HEAD)
        h = r.head
        if ext == "avi":
            _need(h[:4] == b"RIFF" and h[8:12] == b"AVI ", "RIFF AVI ではない")
            v, ct, sig = _avi(r)
        else:
            _need(h[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"), "MP4 / MOV ではない")
            v, ct, sig = _mov(r)
    out: dict = dict(duration=None, width=None, height=None, fps=None, n_frames=None, vcodec=None, acodec=None,
                     container=ext, datetime=None, datetime_source=None)
    out.update(v)
    # ここから下は media.probe_video の後半と同じ
    if out["n_frames"] is None and out["duration"] and out["fps"]:
        out["n_frames"] = int(round(out["duration"] * out["fps"]))
    if ct:
        dt = media.parse_dt_any(str(ct), z_is_utc)
        if dt and not dt.startswith("1970/") and not dt.startswith("1904/"):
            out["datetime"], out["datetime_source"] = dt, "metadata"
    if not out["datetime"]:
        out["datetime"], out["datetime_source"] = media.mtime_dt(path), "mtime"
    out["sig"] = (ext,) + sig
    return out


# ---------- ffmpeg と同じ計算 ----------
def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _av_reduce(num: int, den: int, mx: int = INT_MAX) -> tuple[int, int, bool]:
    """libavutil の av_reduce（分数を約分し、分子・分母が mx を超えるなら連分数で近い分数にする）。返り値 (分子, 分母, 正確か)。num, den は 0 以上"""
    a0n, a0d, a1n, a1d = 0, 1, 1, 0
    g = _gcd(num, den)
    if g:
        num //= g
        den //= g
    if num <= mx and den <= mx:
        a1n, a1d = num, den
        den = 0
    while den:
        x = num // den
        nxt = num - den * x
        a2n, a2d = x * a1n + a0n, x * a1d + a0d
        if a2n > mx or a2d > mx:
            if a1n:
                x = (mx - a0n) // a1n
            if a1d:
                x = min(x, (mx - a0d) // a1d)
            if den * (2 * x * a1d + a0d) > num * a1d:
                a1n, a1d = x * a1n + a0n, x * a1d + a0d
            break
        a0n, a0d, a1n, a1d = a1n, a1d, a2n, a2d
        num, den = den, nxt
    return a1n, a1d, den == 0


def _rescale(a: int, b: int, c: int) -> int:
    """av_rescale（a * b / c を四捨五入。a, b >= 0、c > 0）"""
    return (a * b + c // 2) // c


def _sec(us: int) -> float:
    """ffprobe の表示（マイクロ秒を %f で 6 桁の秒に）を読んだ float"""
    return float(f"{us // 1000000}.{us % 1000000:06d}")


# ---------- ビット列（H.264 / HEVC の SPS） ----------
class _Bits:
    __slots__ = ("v", "n", "p")

    def __init__(self, data: bytes):
        self.v, self.n, self.p = int.from_bytes(data, "big"), len(data) * 8, 0

    def u(self, k: int) -> int:
        _need(self.p + k <= self.n, "映像の見出し（SPS / VOL）が短い")
        self.p += k
        return (self.v >> (self.n - self.p)) & ((1 << k) - 1)

    def ue(self) -> int:
        z = 0
        while self.u(1) == 0:
            z += 1
            _need(z <= 32, "SPS を読めない")
        return (1 << z) - 1 + (self.u(z) if z else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


_sps_cache: dict[bytes, tuple] = {}       # 同じカメラの動画は SPS が同じなので、結果を覚えておく


def _remember(key: bytes, val: tuple) -> tuple:
    if len(_sps_cache) > 256:
        _sps_cache.clear()
    _sps_cache[key] = val
    return val


def _rbsp(nal: bytes) -> bytes:
    return nal.replace(b"\x00\x00\x03", b"\x00\x00")       # 00 00 03 の 03 は区切りと紛れないための詰め物


def _dims_ok(w: int, h: int) -> bool:
    """復号器が開ける大きさか（libavutil の av_image_check_size2。開けないと ffprobe は何も返さない）"""
    return w > 0 and h > 0 and (8 * w + 1024) * (h + 128) < INT_MAX


def _nal(data: bytes, want) -> bytes | None:
    """開始符号（00 00 01）で区切られた並びから、最初のバイトが want に合う最初のかたまり（次の開始符号の手前まで）"""
    i = data.find(b"\x00\x00\x01")
    while 0 <= i < len(data) - 3:
        if want(data[i + 3]):
            j = data.find(b"\x00\x00\x01", i + 3)
            return data[i + 3:j if j >= 0 else len(data)]
        i = data.find(b"\x00\x00\x01", i + 3)
    return None


def _h264_fits(nal: bytes | None, w: int, h: int) -> bool:
    """入れ物の大きさ (w, h) が、そのまま復号器の出す大きさになるか: SPS の大きさと同じか、切り落としの違いだけで SPS の大きさに収まる（h264_slice.c の init_dimensions）"""
    _need(nal is not None, "SPS がない")
    sw, sh, crop_tl = _h264_sps(nal[:512])
    return (w, h) == (sw, sh) or (not crop_tl and w <= sw and h <= sh and (w + 15) // 16 == (sw + 15) // 16 and (h + 15) // 16 == (sh + 15) // 16)


def _h264_sps(nal: bytes) -> tuple:
    """H.264 の SPS（頭の 1 バイトつき）から (幅, 高さ, 左か上の切り落としがあるか)。幅・高さは切り落とし（crop）のあとの大きさ"""
    c = _sps_cache.get(nal)
    if c is not None:
        return c
    _need(len(nal) >= 4 and (nal[0] & 0x9F) == 7, "SPS がおかしい")
    b = _Bits(_rbsp(nal[1:]))
    profile = b.u(8)
    b.u(16)
    b.ue()
    chroma = 1
    _need(profile not in (134, 135, 139), "H.264: 扱っていない profile")       # 規格では下の並びに入るが、ffmpeg（h264_ps.c）は入れていない
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 144):
        chroma = b.ue()
        if chroma == 3:
            b.u(1)
        b.ue()
        b.ue()
        b.u(1)
        if b.u(1):                                     # 量子化の表（大きさを読むために読み飛ばす）
            for i in range(8 if chroma != 3 else 12):
                if b.u(1):
                    last = nxt = 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + b.se() + 256) % 256
                        last = nxt if nxt else last
    b.ue()
    poc = b.ue()
    if poc == 0:
        b.ue()
    elif poc == 1:
        b.u(1)
        b.se()
        b.se()
        for _ in range(b.ue()):
            b.se()
    b.ue()
    b.u(1)
    mbw = b.ue() + 1
    mbh = b.ue() + 1
    frame_only = b.u(1)
    if not frame_only:
        b.u(1)
    b.u(1)
    cl = cr = ct = cb = 0
    if b.u(1):
        cl, cr, ct, cb = b.ue(), b.ue(), b.ue(), b.ue()
    w = 16 * mbw - (cl + cr) * (2 if chroma in (1, 2) else 1)
    h = 16 * mbh * (2 - frame_only) - (ct + cb) * (2 - frame_only) * (2 if chroma == 1 else 1)
    _need(w > 0 and h > 0, "SPS の大きさがおかしい")
    return _remember(nal, (w, h, bool(cl or ct)))


def _mpeg4_vol(data: bytes) -> tuple | None:
    """MPEG-4 の VOL（開始符号 00 00 01 2x）から (幅, 高さ)。なければ None（mpeg4videodec.c の decode_vol_header と同じ順に読む）"""
    v = _nal(data, lambda c: 0x20 <= c <= 0x2F)
    if v is None:
        return None
    b = _Bits(v[1:40])
    b.u(1)
    _need(b.u(8) not in (14, 15), "MPEG-4 の studio profile")
    if b.u(1):
        b.u(7)
    if b.u(4) == 15:
        b.u(16)
    if b.u(1):
        b.u(3)
        if b.u(1):
            b.u(79)
    _need(b.u(2) == 0, "MPEG-4: 四角でない形")
    b.u(1)
    res = b.u(16)
    _need(res > 0, "MPEG-4 の VOL がおかしい")
    b.u(1)
    if b.u(1):
        b.u(max(1, (res - 1).bit_length()))
    b.u(1)
    w = b.u(13)
    b.u(1)
    h = b.u(13)
    return w, h


def _hevc_dims(hvcc: bytes) -> tuple:
    """hvcC の最初の SPS から (幅, 高さ)。conformance window で切り落としたあとの大きさ"""
    c = _sps_cache.get(hvcc)
    if c is not None:
        return c
    _need(len(hvcc) >= 23 and hvcc[0] == 1, "hvcC がおかしい")
    pos, nal = 23, None
    for _ in range(hvcc[22]):
        _need(pos + 3 <= len(hvcc), "hvcC が短い")
        typ, cnt = hvcc[pos] & 0x3F, struct.unpack_from(">H", hvcc, pos + 1)[0]
        pos += 3
        for _ in range(cnt):
            _need(pos + 2 <= len(hvcc), "hvcC が短い")
            n = struct.unpack_from(">H", hvcc, pos)[0]
            if typ == 33 and nal is None:
                nal = hvcc[pos + 2:pos + 2 + n]
                _need(len(nal) == n, "hvcC が短い")
            pos += 2 + n
    _need(nal is not None and len(nal) > 4, "hvcC に SPS がない")
    b = _Bits(_rbsp(nal[2:]))
    b.u(4)
    sub = b.u(3)
    b.u(1)
    b.u(88)
    b.u(8)
    present = [(b.u(1), b.u(1)) for _ in range(sub)]
    if sub:
        b.u(2 * (8 - sub))
    for pp, lp in present:
        if pp:
            b.u(88)
        if lp:
            b.u(8)
    b.ue()
    chroma = b.ue()
    if chroma == 3 and b.u(1):
        chroma = 0
    w, h = b.ue(), b.ue()
    if b.u(1):
        left, right, top, bottom = b.ue(), b.ue(), b.ue(), b.ue()
        _need(left == 0, "HEVC: 左の切り落としがある")             # ffmpeg は左の切り落としを丸めることがある
        w -= (2 if chroma in (1, 2) else 1) * (left + right)
        h -= (2 if chroma == 1 else 1) * (top + bottom)
    _need(w > 0 and h > 0, "SPS の大きさがおかしい")
    return _remember(hvcc, (w, h))


def _jpeg_dims(r: _Reader, pos: int, limit: int) -> tuple:
    """pos から始まる JPEG の SOF（フレームの見出し）から (幅, 高さ)。MJPEG の大きさは復号器がここから決める"""
    limit = min(limit, 256 * 1024)
    b = r.get(pos, min(limit, 1024), 4096)             # SOF はふつう最初の数百バイトの中にある
    _need(b[:2] == b"\xff\xd8", "JPEG の頭がない")
    p = 2
    for _ in range(200):
        if p + 9 > len(b):
            _need(len(b) < limit, "SOF が見つからない")
            b = r.get(pos, min(limit, max(p + 9, len(b) * 4)))
            _need(p + 9 <= len(b), "SOF が見つからない")
        _need(b[p] == 0xFF, "JPEG のマーカーがおかしい")
        m = b[p + 1]
        if m == 0xFF:                                  # 詰め物
            p += 1
            continue
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack_from(">HH", b, p + 5)
            return w, h
        _need(m not in (0xD9, 0xDA, 0x01) and not 0xD0 <= m <= 0xD7, "SOF が見つからない")
        p += 2 + struct.unpack_from(">H", b, p + 2)[0]
    raise _Unsure("SOF が見つからない")


# ---------- AVI ----------
# biCompression（fourcc。大文字にそろえたもの）→ ffprobe の codec_name。ここにないものは ffprobe に任せる
_AVI_VIDEO = {b"MJPG": "mjpeg", b"H264": "h264", b"X264": "h264", b"AVC1": "h264",
              b"FMP4": "mpeg4", b"XVID": "mpeg4", b"DIVX": "mpeg4", b"DX50": "mpeg4", b"MP4V": "mpeg4"}
_AVI_PCM = {8: "pcm_u8", 16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}
_AVI_AUDIO = {0x0002: "adpcm_ms", 0x0006: "pcm_alaw", 0x0007: "pcm_mulaw", 0x0011: "adpcm_ima_wav", 0x0055: "mp3"}
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# avidec.c の sscanf("%*3s%*[ ]%3s%*[ ]%2d%*[ ]%8s%*[ ]%4d") と同じ読み方（"Mon Mar 10 15:04:43 2003"）
_IDIT_RE = re.compile(r"[ \t\n\v\f\r]*[^ \t\n\v\f\r]{1,3} +[ \t\n\v\f\r]*([^ \t\n\v\f\r]{1,3}) +[ \t\n\v\f\r]*(\d{1,2}) +"
                      r"[ \t\n\v\f\r]*([^ \t\n\v\f\r]{1,8}) +[ \t\n\v\f\r]*(\d{1,4})")


def _cstr(b: bytes) -> str:
    """NUL までの文字列。ASCII でなければ ffprobe の出力と同じになると言い切れない"""
    b = b.split(b"\0", 1)[0]
    _need(all(32 <= c < 127 or c in (9, 10, 13) for c in b), "日時の文字列が ASCII でない")
    return b.decode("ascii")


def _idit(data: bytes) -> str | None:
    """IDIT の文字列 → ffprobe の tags の creation_time（avidec.c の avi_metadata_creation_time と同じ）"""
    s = _cstr(data[:63])
    m = _IDIT_RE.match(s)
    if m:
        mon = m.group(1).lower()
        if mon not in _MONTHS:
            return None
        return f"{int(m.group(4)):04d}-{_MONTHS.index(mon) + 1:02d}-{int(m.group(2)):02d} {m.group(3)}"
    _need("+" not in s and "-" not in s, "IDIT の書式が読めない")      # 符号つきの数は sscanf と読み方が違うかもしれない
    if len(s) > 7 and s[4] == "/" and s[7] == "/":
        return s[:4] + "-" + s[5:7] + "-" + s[8:]
    return None


def _riff_info(r: _Reader, start: int, end: int) -> tuple:
    """LIST INFO の中の日付（ICRD。ffprobe の tags の date になる）と、読み終わった位置"""
    pos, date = start, None
    for _ in range(300):
        if pos > end - 8:
            break
        hd = r.get(pos, 8, 512)
        _need(len(hd) == 8, "INFO が途中で切れている")
        code, n = hd[:4], struct.unpack_from("<I", hd, 4)[0]
        _need(n <= end - pos - 8, "INFO の項目が大きすぎる")
        n += n & 1
        if code.upper() == b"ICRD" or code.lower() == b"date":
            _need(date is None, "INFO に日付が複数ある")
            date = _cstr(r.get(pos + 8, n))
        pos += 8 + n
    _need(pos in (end, end + 1), "INFO の終わりがずれている")
    return date, pos


def _avi(r: _Reader) -> tuple:
    size = r.size
    riff_end = struct.unpack_from("<I", r.head, 4)[0] + 8
    _need(riff_end <= size, "AVI が途中で切れている")                                       # ffmpeg は切れた分だけ長さを縮めて見積もる
    pos, list_end = 12, 0
    streams: list[dict] = []
    cur: dict | None = None
    tags: list[bytes] = []
    idit = date = movi = movi_end = None
    n_idit = n_info = 0
    for _ in range(400):                               # avidec.c の avi_read_header と同じく、LIST の中に入りながらチャンクを順に読む
        hd = r.get(pos, 12, 4096)
        _need(len(hd) >= 8, "movi が見つからない")
        tag, n = hd[:4], struct.unpack_from("<I", hd, 4)[0]
        if tag == b"LIST":
            _need(len(hd) == 12, "movi が見つからない")
            lt = hd[8:12]
            list_end = pos + 8 + n
            tags.append(lt)
            if lt == b"movi":
                _need(n >= 4, "movi が空")
                movi, movi_end = pos + 12, pos + 8 + n + (n & 1)
                break
            _need(lt != b"ncdt", "Nikon のタグがある")
            if lt == b"INFO":
                n_info += 1
                _need(n_info == 1 and n >= 4, "INFO が複数ある")
                date, pos = _riff_info(r, pos + 12, pos + 8 + n)
            else:
                pos += 12
            continue
        tags.append(tag)
        body = pos + 8
        if tag == b"strh":
            b = r.get(body, 48)
            _need(n >= 48 and len(b) == 48, "strh が短い")
            typ = b[:4]
            scale, rate, _start, length = struct.unpack_from("<4I", b, 20)
            ssize = struct.unpack_from("<I", b, 44)[0]
            _need(typ in (b"vids", b"auds"), "映像・音声以外のストリームがある")
            _need(0 < scale <= INT_MAX and 0 < rate <= INT_MAX and ssize <= INT_MAX, "strh の scale / rate がおかしい")
            _need(b[4:8] != b"Axan", "Xan の音声")
            cur = dict(typ=typ, scale=scale, rate=rate, length=length, ssize=0 if typ == b"vids" else ssize, strf=False)
            streams.append(cur)
            pos = body + n
        elif tag == b"strf":
            _need(cur is not None and not cur["strf"] and n > 0 and body + n <= list_end, "strf の位置がおかしい")
            cur["strf"] = True
            if cur["typ"] == b"vids":
                b = r.get(body, 40)
                _need(n >= 40 and len(b) == 40, "strf（映像）が短い")
                w, h = struct.unpack_from("<ii", b, 4)
                _need(h != 0 and _dims_ok(w, abs(h)), "映像の大きさがおかしい")
                cur["w"], cur["h"], cur["comp"], cur["extra"] = w, abs(h), b[16:20], (body + 40, n - 40)
            else:
                b = r.get(body, 16)
                _need((n == 16 or n >= 18) and len(b) == 16, "strf（音声）が短い")
                cur["fmt"], cur["ch"], srate = struct.unpack_from("<HHI", b, 0)
                cur["bits"] = struct.unpack_from("<H", b, 14)[0]
                _need(0 < srate <= INT_MAX and 1 <= cur["ch"] <= 8, "音声のサンプリング周波数・チャンネル数がおかしい")      # 復号器が開けないと ffprobe は何も返さない
            pos = body + n + (n & 1)
        elif tag == b"IDIT":
            n_idit += 1
            _need(n_idit == 1, "IDIT が複数ある")
            n += n & 1
            idit = _idit(r.get(body, min(n, 63)))
            pos = body + n
        elif tag in (b"avih", b"indx", b"vprp"):       # 値には使わない。avidec.c はこの 3 つでは詰め物（奇数のときの 1 バイト）を飛ばさない
            _need(tag != b"avih" or n >= 40, "avih が短い")
            pos = body + n
        else:                                          # JUNK、strn、dmlh など: 飛ばす
            _need(tag not in (b"strd", b"amvh"), "strd / amvh がある")
            _need(n <= 1000000 or tag == b"idx1", "ヘッダの中に大きすぎるチャンクがある")
            pos = body + n + (n & 1)
    _need(movi is not None, "movi が見つからない")
    vs = [s for s in streams if s["typ"] == b"vids"]
    au = [s for s in streams if s["typ"] == b"auds"]
    _need(len(vs) == 1 and len(au) <= 1 and all(s["strf"] for s in streams), "映像 1 本・音声 0〜1 本でない")
    v = vs[0]
    vcodec = _AVI_VIDEO.get(v["comp"].upper())
    _need(vcodec, "知らない映像の codec")
    _need(v["length"] > 0, "フレーム数が 0")
    acodec = None
    if au:
        a = au[0]
        acodec = _AVI_PCM.get(a["bits"]) if a["fmt"] == 1 else _AVI_AUDIO.get(a["fmt"])
        _need(acodec and (a["fmt"] not in (0x02, 0x11) or (a["bits"] == 4 and a["ch"] <= 2)), "知らない音声の codec")
    # 映像の大きさは復号器が最初のフレームの中の見出しから決める。ヘッダ（strf）の値がそれと同じになることを確かめる
    fpos, fn = _avi_first_frame(r, movi, movi_end, streams.index(v))
    if vcodec == "mjpeg":
        _need(_jpeg_dims(r, fpos, fn) == (v["w"], v["h"]), "MJPEG のフレームの大きさがヘッダと違う")
    elif vcodec == "h264":
        d = r.get(fpos, min(fn, 2048), 4096)
        _need(v["extra"][1] == 0 and (d[:3] == b"\0\0\1" or d[:4] == b"\0\0\0\1"), "H.264: フレームが開始符号で始まらない")
        _need(_h264_fits(_nal(d, lambda c: c & 0x9F == 7), v["w"], v["h"]), "H.264: ヘッダの大きさが SPS と合わない")
    else:
        vols = [_mpeg4_vol(r.get(v["extra"][0], min(v["extra"][1], 4096))) if v["extra"][1] > 0 else None, _mpeg4_vol(r.get(fpos, min(fn, 2048), 4096))]
        _need(any(vols) and all(x in (None, (v["w"], v["h"])) for x in vols), "MPEG-4: ヘッダの大きさが VOL と違う")
    # movi のあと: ふつうは索引 idx1 だけ。その大きさが「映像のフレーム数 + 音声のチャンク数」ちょうどなら、読まずに idx1 だけと言える
    # （ファイルの最後を読みに行くと、HDD ではヘッドの移動が 1 回増えて時間が約 2 倍になる）。計算が合わないものは実際に読む
    tail = None
    k, rem = divmod(size - movi_end - 8, 16)
    if not AVI_TAIL_ALWAYS and riff_end == size and rem == 0 and b"indx" not in tags:
        if not au and k == v["length"]:
            tail = "=idx1"
        elif au and k > v["length"] and au[0]["length"] > 0 and au[0]["length"] % (k - v["length"]) == 0:
            tail = f"=idx1/{au[0]['length'] // (k - v['length'])}"         # 音声 1 チャンクの長さ。カメラごとに決まった値なので署名に入れる（違うものは別の型として突き合わせられる）
    if tail is None:
        tail = _avi_tail(r, movi_end)
    # 長さ: 時間を持つのは sample_size が 0 のストリーム（映像と、可変長の音声）だけ。その最大（demux.c の update_stream_timings）
    durs = []
    for s in streams:
        if s["ssize"] == 0:
            g = _gcd(s["scale"], s["rate"])
            durs.append((_rescale(s["length"], s["scale"] // g * 1000000, s["rate"] // g), s["typ"]))
    us, src = max(durs)
    _need(us > 0, "長さが 0")
    g = _gcd(v["scale"], v["rate"])
    out = dict(duration=_sec(us), width=v["w"], height=v["h"], fps=float(v["rate"] // g) / float(v["scale"] // g), n_frames=v["length"],
               vcodec=vcodec, acodec=acodec)
    # 署名にはファイルごとに変わる値（長さ、フレーム数、カメラが実測して書く音声の rate など）を入れない
    sig = (b",".join(tags).decode("latin1"), v["comp"].decode("latin1"), v["w"], v["h"],
           (au[0]["fmt"], au[0]["bits"], au[0]["ssize"] == 0) if au else None,
           "v" if src == b"vids" else "a", ("idit" if idit else "") + ("+date" if date else ""), tail)
    return out, idit or date, sig


def _avi_first_frame(r: _Reader, movi: int, movi_end: int, index: int) -> tuple:
    """movi の中の最初の映像フレームの (位置, 大きさ)"""
    want = b"%02d" % index
    pos = movi
    for _ in range(64):
        hd = r.get(pos, 12, 4096)
        _need(len(hd) >= 8 and pos < movi_end, "最初のフレームが見つからない")
        tag, n = hd[:4], struct.unpack_from("<I", hd, 4)[0]
        if tag == b"LIST":                             # LIST rec の中に入る
            pos += 12
            continue
        if tag[:2] == want and tag[2:] in (b"dc", b"db") and n > 0:
            return pos + 8, n
        pos += 8 + n + (n & 1)
    raise _Unsure("最初のフレームが見つからない")


def _avi_tail(r: _Reader, movi_end: int) -> str:
    """movi のあとを avidec.c の avi_load_index と同じ順に見る（索引 idx1 のほかに INFO があれば、その中の日付が tags の date になる）。
    INFO に日付があれば言い切れない。返り値は署名に入れる並び"""
    pos, seen, loaded = movi_end, [], False
    for _ in range(64):
        hd = r.get(pos, 12, 4096) if pos + 8 <= r.size else b""
        if len(hd) < 8:
            return ",".join(seen)
        tag, n = hd[:4], struct.unpack_from("<I", hd, 4)[0]
        if tag == b"idx1" and n >= 16 and pos + 8 + n <= r.size:
            loaded = True
        elif tag == b"LIST":
            if hd[8:12] == b"INFO":
                _need(_riff_info(r, pos + 12, pos + 8 + n)[0] is None, "movi のあとの INFO に日付がある")
        elif loaded:
            return ",".join(seen)
        if len(seen) < 8:
            seen.append(tag.decode("latin1"))
        pos += 8 + n + (n & 1)
    raise _Unsure("movi のあとのチャンクが多すぎる")


# ---------- MP4 / MOV ----------
# mov.c が中身を解釈する atom（mov_default_parse_table）。ここにあるのに下の _OK にないものが出てきたら ffprobe に任せる。ここにないものは mov.c も読み飛ばす
_MOV_KNOWN = frozenset(x.encode("latin1") for x in (
    "ACLR APRG AALP ARES avss av1C chpl co64 colr ctts dinf DpxE dref edts elst enda fiel adrm ftyp glbl hdlr ilst jp2h mdat mdhd mdia meta minf moof moov mvex mvhd "
    "alac avcC pasp clap sbas sidx stbl stco stps strf stsc stsd stss stsz stts stz2 sdtp tkhd tfdt tfhd trak traf tref tmcd chap trex trun udta wave esds dac3 dec3 "
    "ddts wide wfex cmov chan chnl dvc1 sgpd sbgp hvcC vvcC uuid free ---- sinf frma senc saiz saio pssh schm schi tenc dfLa st3d sv3d vexu hfov dOps dmlp SmDm CoLL vpcC "
    "mdcv clli dvcC dvvC dvwC kind SA3D SAND iloc pcmC pitm evcC idat imir iref ispe irot iprp iinf amve lhvC lvcC apvC iacb hoov").split()) | {b"SMI "}
_META = (b"udta", b"meta")                                          # 文字の情報（タイトルなど）。中身は _check_meta と _mov の最後で調べる
_OK = {                                                             # 入れ物ごとの、ここで扱う atom と、あっても値を変えない atom
    "moov": frozenset((b"mvhd", b"trak", b"free", b"uuid") + _META),
    "trak": frozenset((b"tkhd", b"edts", b"mdia", b"tref", b"free", b"uuid") + _META),
    "tref": frozenset(),                                            # chap（章のトラック）や tmcd（タイムコード）への参照があれば扱わない
    "edts": frozenset((b"elst", b"free")),
    "mdia": frozenset((b"mdhd", b"hdlr", b"minf", b"free", b"uuid")),
    "minf": frozenset((b"stbl", b"hdlr", b"dinf", b"free", b"uuid")),
    "dinf": frozenset((b"dref", b"free")),
    "udta": frozenset((b"meta", b"free", b"uuid")),
    "meta": frozenset((b"hdlr", b"ilst", b"free", b"uuid")),
    "ilst": frozenset((b"----", b"free")),
    "stbl": frozenset((b"stsd", b"stts", b"ctts", b"stsc", b"stsz", b"stz2", b"stco", b"co64", b"stss", b"stps", b"sdtp", b"sgpd", b"sbgp", b"free", b"uuid")),
    "映像の stsd": frozenset((b"avcC", b"hvcC", b"pasp", b"colr", b"fiel", b"free", b"uuid")),
    "音声の stsd": frozenset((b"esds", b"wave", b"chan", b"free", b"uuid")),
    "wave": frozenset((b"esds", b"frma", b"free")),
}
_MOV_VIDEO = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc", b"jpeg": "mjpeg", b"mjpa": "mjpeg"}
_MOV_PCM = {b"sowt": "pcm_s16le", b"twos": "pcm_s16be", b"ulaw": "pcm_mulaw", b"alaw": "pcm_alaw"}


def _atoms(b: bytes, start: int, end: int, where: str, metas: list | None = None) -> dict:
    """b[start:end] に並ぶ atom を {種類: [(中身の始まり, 終わり), ...]} にする。udta / meta の範囲は metas に足す。
    大きさがおかしいもの、mov.c が解釈するのにここで扱わないものがあれば _Unsure"""
    out: dict[bytes, list] = {}
    ok = _OK[where]
    pos = start
    while pos + 8 <= end:
        n, typ = struct.unpack_from(">I4s", b, pos)
        hl = 8
        if n == 1:
            _need(pos + 16 <= end, "atom の大きさがおかしい")
            n, hl = struct.unpack_from(">Q", b, pos + 8)[0], 16
        elif n == 0:
            n = end - pos
        _need(hl <= n <= end - pos, "atom の大きさがおかしい")
        if typ not in ok and typ in _MOV_KNOWN:
            raise _Unsure(f"{where} の中に扱っていない atom（{typ.decode('latin1')}）")
        _need(typ != b"uuid" or n - hl >= 16, "uuid が短い")
        out.setdefault(typ, []).append((pos + hl, pos + n))
        if metas is not None and typ in _META:
            metas.append((typ, pos + hl, pos + n))
        pos += n
    return out


def _check_meta(b: bytes, typ: bytes, s: int, e: int) -> None:
    """udta / meta の中身が文字の情報だけであること。mov.c はこの中でも、知っている atom（tkhd、stsd など）があればトラックの情報として解釈してしまう"""
    if typ == b"udta":
        for x in _atoms(b, s, e, "udta").get(b"meta", []):
            _check_meta(b, b"meta", *x)
        return
    i = b.find(b"hdlr", s, e)                          # mov.c（mov_read_meta）は 4 バイトごとに hdlr を探し、その atom から先を解釈する
    while i >= 0 and (i - s) % 4:
        i = b.find(b"hdlr", i + 1, e)
    if i < 0:
        return
    _need(i - s >= 4, "meta がおかしい")
    d = _atoms(b, i - 4, e, "meta")
    _need(all(xe - xs >= 12 and b[xs + 8:xs + 12] not in (b"vide", b"soun", b"m1a ", b"subp", b"clcp") for xs, xe in d.get(b"hdlr", [])), "meta の hdlr がおかしい")
    for x in d.get(b"ilst", []):
        _atoms(b, *x, "ilst")
    for xs, xe in d.get(b"keys", []):                  # 項目の名前の表（QuickTime / Android）: 大きさが合っていること（mov.c の mov_read_keys）
        _need(xe - xs >= 8, "keys がおかしい")
        p = xs + 8
        for _ in range(struct.unpack_from(">I", b, xs + 4)[0]):
            _need(p + 8 <= xe and 8 <= struct.unpack_from(">I", b, p)[0] <= xe - p, "keys がおかしい")
            p += struct.unpack_from(">I", b, p)[0]


def _one(d: dict, typ: bytes, required: bool = True):
    v = d.get(typ) or ()
    if len(v) > 1 or (required and not v):
        raise _Unsure(f"{typ.decode('latin1')} が{'複数ある' if v else 'ない'}")
    return v[0] if v else None


def _mov_time(b: bytes, pos: int) -> str | None:
    """mvhd / mdhd の作成時刻 → ffprobe の tags の creation_time（mov.c の mov_metadata_creation_time と同じ）。0 なら None"""
    if b[pos] == 1:
        t = struct.unpack_from(">q", b, pos + 4)[0]
        _need(t == 0 or EPOCH_1904 <= t < EPOCH_1904 + 4102444800, "作成時刻が範囲の外")
    else:
        t = struct.unpack_from(">I", b, pos + 4)[0]
        if 0 < t < EPOCH_1904:                          # 1970 年起点で書くカメラがある
            t += EPOCH_1904
    if not t:
        return None
    return (datetime(1970, 1, 1) + timedelta(seconds=t - EPOCH_1904)).strftime("%Y-%m-%dT%H:%M:%S") + ".000000Z"


def _esds_codec(b: bytes) -> str:
    """mp4a の esds（中身）→ codec_name（isom.c の ff_mp4_read_dec_config_descr と同じ）。aac（AAC-LC）と mp3 だけ"""
    def descr(p):
        tag, n = b[p], 0
        p += 1
        for _ in range(4):
            c = b[p]
            p += 1
            n = (n << 7) | (c & 0x7F)
            if not c & 0x80:
                break
        return tag, n, p
    end = len(b)
    _need(end >= 24, "esds が短い")
    b += bytes(16)                                     # 途中で切れた esds で範囲の外を読まないための詰め物
    tag, _n, p = descr(4)
    if tag == 0x03:
        flags = b[p + 2]
        p += 3 + (2 if flags & 0x80 else 0)
        if flags & 0x40:
            p += 1 + b[p]
        if flags & 0x20:
            p += 2
    else:
        p += 2
    _need(p + 20 <= len(b), "esds が短い")
    tag, _n, p = descr(p)
    _need(tag == 0x04, "esds に DecoderConfig がない")
    obj = b[p]
    p += 13
    if obj in (0x69, 0x6B):
        return "mp3"
    _need(obj in (0x40, 0x66, 0x67, 0x68) and p + 20 <= len(b), "知らない音声の種類（esds）")
    tag, n, p = descr(p)
    _need(tag == 0x05 and 2 <= n <= end - p and b[p] >> 3 == 2, "AAC-LC ではない")       # ほかの種類（mp3on4、ALS など）は codec_name が変わりうる
    rate_i, chan = ((b[p] & 7) << 1) | (b[p + 1] >> 7), (b[p + 1] >> 3) & 15
    _need(rate_i <= 12 and 1 <= chan <= 7 and not b[p + 1] & 7, "AAC の設定がふつうでない")      # 復号器がこの設定を読めないと、ffprobe は何も返さない
    return "aac"


def _mov_trak(b: bytes, s: int, e: int, movie_ts: int, r: _Reader, metas: list) -> dict:
    """trak 1 つ → 種類（v / a）、codec、ffmpeg の st->start_time・st->duration・nb_frames にあたる値など"""
    tr = _atoms(b, s, e, "trak", metas)
    _one(tr, b"tkhd", False)
    for x in tr.get(b"tref", []):
        _atoms(b, *x, "tref")
    md = _atoms(b, *_one(tr, b"mdia"), "mdia")
    mi = _atoms(b, *_one(md, b"minf"), "minf")
    st = _atoms(b, *_one(mi, b"stbl"), "stbl")
    hs, he = _one(md, b"hdlr")
    _need(he - hs >= 24, "hdlr が短い")
    kind = {b"vide": "v", b"soun": "a"}.get(b[hs + 8:hs + 12])
    _need(kind and b[hs + 4:hs + 12] != b"mhlrMPEG", "映像・音声以外のトラックがある")
    for xs, xe in mi.get(b"hdlr", []):                 # minf の中の hdlr（データの出どころ）が種類を変えないこと
        _need(xe - xs >= 12 and b[xs + 8:xs + 12] not in (b"vide", b"soun", b"m1a ", b"subp", b"clcp"), "minf の hdlr がおかしい")
    di = _one(mi, b"dinf", False)
    dr = _one(_atoms(b, *di, "dinf"), b"dref", False) if di else None
    if dr:                                             # データの置き場: 項目が 1 つ以上で、外のファイルを指す参照（alias）でないこと
        _need(dr[1] - dr[0] >= 8 and struct.unpack_from(">I", b, dr[0] + 4)[0] >= 1, "dref がおかしい")
        p = dr[0] + 8
        for _ in range(struct.unpack_from(">I", b, dr[0] + 4)[0]):
            _need(p + 12 <= dr[1], "dref がおかしい")
            n, typ = struct.unpack_from(">I4s", b, p)
            _need(12 <= n <= dr[1] - p and not (typ == b"alis" and n > 150), "dref がおかしい、または外のファイルを参照している")
            p += n
    # mdhd: 時間の単位と長さ
    ms, me = _one(md, b"mdhd")
    ver = b[ms]
    _need(ver <= 1 and me - ms >= (36 if ver else 24), "mdhd がおかしい")
    ctime = _mov_time(b, ms)
    ts, dur = struct.unpack_from(">IQ" if ver else ">II", b, ms + (20 if ver else 12))
    if dur == (2 ** 64 - 1 if ver else 2 ** 32 - 1):
        dur = 0
    _need(0 < ts <= INT_MAX and dur < 2 ** 62, "mdhd の時間の単位がおかしい")
    # stts: フレーム数と時間の合計（fps と長さの元）
    ss, se = _one(st, b"stts")
    n = struct.unpack_from(">I", b, ss + 4)[0]
    _need(n > 0 and ss + 8 + 8 * n <= se, "stts がおかしい")
    tt = struct.unpack_from(f">{2 * n}I", b, ss + 8)
    counts, deltas = tt[0::2], tt[1::2]
    _need(max(deltas) <= INT_MAX, "stts の間隔が大きすぎる")
    frames, total = sum(counts), sum(map(operator.mul, counts, deltas))
    _need(0 < frames <= INT_MAX and total > 0, "stts の合計が 0")
    dur = min(dur, total)
    # ctts: 最初のフレームの表示のずれ（編集リストがない映像の始まりの時刻になる）
    off0 = 0
    cs = _one(st, b"ctts", False)
    if cs:
        cn = struct.unpack_from(">I", b, cs[0] + 4)[0]
        _need(cs[0] + 8 + 8 * cn <= cs[1], "ctts がおかしい")
        for i in range(cn):
            c, o = struct.unpack_from(">ii", b, cs[0] + 8 + 8 * i)
            if c > 0:
                off0 = o
                break
        _need(off0 >= 0 and kind == "v", "ctts の最初が負、または音声に ctts がある")
    # 索引が最後まで作れること（mov.c の sanity_checks と mov_build_index。途中でやめると編集リストが使われず、長さの出し方が変わる）
    _need(b"stz2" not in st and len(st.get(b"stco", [])) + len(st.get(b"co64", [])) == 1, "stsz / stco がおかしい")
    sz, sc = _one(st, b"stsz"), _one(st, b"stsc")
    co = (st.get(b"stco") or st[b"co64"])[0]
    cw = 8 if b"co64" in st else 4
    _need(sz[1] - sz[0] >= 12 and co[1] - co[0] >= 8 and sc[1] - sc[0] >= 8, "索引がおかしい")
    size1, nsamp = struct.unpack_from(">II", b, sz[0] + 4)
    nchunk, nsc = struct.unpack_from(">I", b, co[0] + 4)[0], struct.unpack_from(">I", b, sc[0] + 4)[0]
    _need(0 < nsamp <= 100000000 and nchunk > 0 and nsc > 0 and sc[0] + 8 + 12 * nsc <= sc[1] and co[0] + 8 + cw * nchunk <= co[1], "索引がおかしい")
    if size1:
        _need(size1 <= 0xFFFFF, "サンプルの大きさがおかしい")
    else:
        _need(sz[0] + 12 + 4 * nsamp <= sz[1] and max(struct.unpack_from(f">{nsamp}I", b, sz[0] + 12)) <= 0x3FFFFFFF, "サンプルの大きさがおかしい")
    sv = struct.unpack_from(f">{3 * nsc}I", b, sc[0] + 8)
    first, per = sv[0::3], sv[1::3]
    _need(first[0] == 1 and all(map(operator.lt, first, first[1:])) and first[-1] <= nchunk and 0 < min(per) and max(per) <= INT_MAX and set(sv[2::3]) == {1},
          "stsc がおかしい")
    _need(sum(map(operator.mul, map(operator.sub, first[1:] + (nchunk + 1,), first), per)) == nsamp, "stsc と stsz のサンプル数が合わない")
    for k in (b"stss", b"stps"):                       # 表が atom に収まっていること（はみ出すとファイルの終わりまで読みに行って失敗する）
        for xs, xe in st.get(k, []):
            _need(xe - xs >= 8 and xs + 8 + 4 * struct.unpack_from(">I", b, xs + 4)[0] <= xe, f"{k.decode()} がおかしい")
    for k in (b"sgpd", b"sbgp"):                       # mov.c が中身を使うのは rap / sync の組だけ（映像の鍵フレームの扱い）。それ以外（音声の roll など）は読み飛ばされる
        for xs, xe in st.get(k, []):
            _need(xe - xs >= 8 and b[xs + 4:xs + 8] not in (b"rap ", b"sync"), f"{k.decode()}（rap / sync）がある")
    # 編集リスト: 始まりの時刻 = 先頭の空白、長さ = min(長さ, 区間の合計)（mov.c の mov_fix_index）
    start, shape = (off0 if kind == "v" else 0), ""
    ed = _one(tr, b"edts", False)
    el = _one(_atoms(b, *ed, "edts"), b"elst", False) if ed else None
    if el:
        ev, en = b[el[0]], struct.unpack_from(">I", b, el[0] + 4)[0]
        _need(ev <= 1 and el[1] - el[0] - 8 == en * (20 if ev else 12), "elst がおかしい")
        ents = [struct.unpack_from(">Qq" if ev else ">Ii", b, el[0] + 8 + i * (20 if ev else 12)) for i in range(en)]
        if ents:
            times = [t for _d, t in ents]
            _need(len(ents) <= 2 and times[-1] >= 0 and (len(ents) == 1 or times[0] == -1) and all(d < 2 ** 62 for d, _t in ents),
                  "編集リストが 1 区間（と先頭の空白）でない")
            ds = [_rescale(d, ts, movie_ts) for d, _t in ents]
            start = ds[0] if len(ents) == 2 else 0
            dur = min(dur, sum(ds))
            shape = ("e" if len(ents) == 2 else "") + ("m" if times[-1] else "z")
    _need(dur > 0, "長さが 0")
    # stsd: codec と大きさ
    ds_, de_ = _one(st, b"stsd")
    _need(de_ - ds_ >= 24 and struct.unpack_from(">I", b, ds_ + 4)[0] == 1, "stsd の項目が 1 つでない")
    es = ds_ + 8
    en_, fmt = struct.unpack_from(">I4s", b, es)
    ee = es + en_
    _need(en_ >= 36 and ee <= de_, "stsd がおかしい")
    t = dict(kind=kind, ts=ts, dur=dur, start=start, frames=frames, total=total, fmt=fmt, shape=shape, ctime=ctime, ctts=bool(cs), vfr=len(set(deltas)) > 1)
    if kind == "v":
        t["codec"] = _MOV_VIDEO.get(fmt)
        _need(t["codec"] and en_ >= 86, "知らない映像の codec")
        w, h = struct.unpack_from(">HH", b, es + 32)
        _need(_dims_ok(w, h) and not b[es + 51:es + 82].startswith(b"Planar Y'CbCr 8-bit 4:2:0") and (b[es + 83] & 0x1F) not in (1, 2, 4, 8),
              "映像の見出しがおかしい")                # 色の表つき（8 ビット以下）は見出しの長さが変わる
        ch = _atoms(b, es + 86, ee, "映像の stsd")
        _need(len(ch.get(b"avcC", [])) + len(ch.get(b"hvcC", [])) == (0 if t["codec"] == "mjpeg" else 1) and all(xe - xs >= 2 for xs, xe in ch.get(b"fiel", [])),
              "映像の見出しの中身がおかしい")          # 復号器の設定（avcC / hvcC）は 1 つだけ（2 つあると ffmpeg は最初のものを使う）
        if t["codec"] == "h264":                       # 大きさは復号器が SPS から決める（入れ物の値が SPS の大きさに収まっていれば入れ物の値）
            c = b[slice(*_one(ch, b"avcC"))]
            _need(len(c) >= 8 and c[0] == 1 and c[5] & 0x1F and 8 + struct.unpack_from(">H", c, 6)[0] <= len(c), "avcC に SPS がない")
            _need(_h264_fits(c[8:8 + struct.unpack_from(">H", c, 6)[0]], w, h), "H.264: 入れ物の大きさが SPS と合わない")
        elif t["codec"] == "hevc":
            _need(_hevc_dims(b[slice(*_one(ch, b"hvcC"))]) == (w, h), "HEVC: 入れ物の大きさが SPS と違う")
        else:
            first_chunk = struct.unpack_from(">Q" if cw == 8 else ">I", b, co[0] + 8)[0]
            _need(_jpeg_dims(r, first_chunk, r.size - first_chunk) == (w, h), "MJPEG のフレームの大きさがヘッダと違う")
        t["w"], t["h"] = w, h
    else:
        ver, nch, bits = struct.unpack_from(">H", b, es + 16)[0], struct.unpack_from(">H", b, es + 24)[0], struct.unpack_from(">H", b, es + 26)[0]
        _need((ver == 0 or (ver == 1 and b[ds_] == 0 and en_ >= 52)) and nch <= 8, "音声の見出しの版・チャンネル数がおかしい")
        ch = _atoms(b, es + (52 if ver else 36), ee, "音声の stsd")
        if fmt == b"mp4a":
            x = _one(ch, b"esds", False)
            _need(not (x and b"wave" in ch), "esds と wave の両方がある")
            t["codec"] = _esds_codec(b[slice(*(x or _one(_atoms(b, *_one(ch, b"wave"), "wave"), b"esds")))])
        else:
            t["codec"] = _MOV_PCM.get(fmt)
            _need(t["codec"] and b"wave" not in ch and b"esds" not in ch and (bits == 16 or fmt in (b"ulaw", b"alaw")) and nch >= 1, "知らない音声の codec")
            # まとめ読みの索引（mov_build_index）が作れない形だと、編集リストが効かず長さの出し方が変わる
            _need(ver == 0 or struct.unpack_from(">I", b, es + 36)[0] <= 1, "PCM の音声の 1 パケットのサンプル数が 1 でない")
    return t


def _mov(r: _Reader) -> tuple:
    size = r.size
    pos, moov, mdat, brand, n_ftyp = 0, None, None, "", 0
    for _ in range(200):                               # いちばん外側の atom を最後まで順に見る（mov.c も、読めるファイルでは最後まで見る）
        if pos == size:
            break
        hd = r.get(pos, 16, HEAD if (moov is None and mdat is not None) else 0)      # mdat の次はたぶん moov: 見出しと一緒に中身も読んでおく
        _need(len(hd) >= 8, "ファイルの終わりに端数がある")
        n, typ = struct.unpack_from(">I4s", hd, 0)
        hl = 8
        if n == 1:
            _need(len(hd) == 16, "atom の大きさがおかしい")
            n, hl = struct.unpack_from(">Q", hd, 8)[0], 16
        elif n == 0:
            n = size - pos
        _need(hl <= n <= size - pos, "atom が途中で切れている")
        if typ == b"ftyp":
            n_ftyp += 1
            _need(n_ftyp == 1 and moov is None and n >= 16, "ftyp がおかしい")
            brand = r.get(pos + hl, 4).decode("latin1")
        elif typ == b"moov":
            _need(moov is None and n - hl <= MOOV_MAX, "moov が複数ある・大きすぎる")
            moov = (pos + hl, n - hl, mdat is None)
        elif typ == b"mdat":
            mdat = pos if mdat is None else mdat
        elif typ == b"wide":
            _need(n == 8, "wide の中身がある")
        else:
            _need(typ in (b"free", b"skip", b"uuid") or typ not in _MOV_KNOWN, f"いちばん外側に扱っていない atom（{typ.decode('latin1')}）")
            _need(typ != b"uuid" or n - hl >= 16, "uuid が短い")
        pos += n
    _need(pos == size and moov is not None and mdat is not None, "moov か mdat がない")
    b = r.get(moov[0], moov[1])
    _need(len(b) == moov[1], "moov を読めない")
    metas: list = []
    top = _atoms(b, 0, len(b), "moov", metas)
    ms, me = _one(top, b"mvhd")
    ver = b[ms]
    _need(me - ms >= (32 if ver == 1 else 20), "mvhd が短い")
    fmt_ct = _mov_time(b, ms)
    movie_ts = struct.unpack_from(">I", b, ms + (20 if ver == 1 else 12))[0]
    _need(0 < movie_ts <= INT_MAX, "mvhd の時間の単位がおかしい")
    traks = [_mov_trak(b, s, e, movie_ts, r, metas) for s, e in top.get(b"trak", [])]
    vs = [t for t in traks if t["kind"] == "v"]
    au = [t for t in traks if t["kind"] == "a"]
    _need(len(vs) == 1 and len(au) <= 1, "映像 1 本・音声 0〜1 本でない")
    v, a = vs[0], (au[0] if au else None)
    # udta / meta の中の日付など。ffprobe の tags は creation_time → date → date-eng → 映像の creation_time の順に使われる。
    # creation_time（mvhd / mdhd）のほかにそれらを作りうるものがあれば言い切れない
    low = b"".join(b[s:e] for _typ, s, e in metas).lower()
    _need(not any(k in low for k in (b"creation_time", b"itunsmpb", b"covr", b"artwork", b"loci")), "udta / meta に creation_time・表紙の画像などがある")
    for m in metas:
        _check_meta(b, *m)
    _need(fmt_ct is not None or (b"date" not in low and b"\xa9day" not in low and b"yrrc" not in low), "udta / meta に日付があるかもしれない")
    # 長さ: ストリームごとの始まりと長さをマイクロ秒にして、最大の長さと（最も遅い終わり − 最も早い始まり）の大きいほう（demux.c の update_stream_timings）。
    # 編集リストのない音声の始まりは、ffprobe が最初の音声パケットまで読めたかどうかで決まる。映像が 0 から始まるなら、どちらでも結果は同じ
    if a is not None and not a["shape"]:
        _need(v["start"] == 0, "映像の始まりが 0 でなく、音声に編集リストがない")
    us = [(_rescale(t["start"], 1000000, t["ts"]), _rescale(t["dur"], 1000000, t["ts"]), t["kind"], t["kind"] == "v" or bool(t["shape"])) for t in traks]
    d_max, d_kind = max((d, k) for _s, d, k, _known in us)
    span = max(s + d for s, d, _k, known in us if known) - min(s for s, _d, _k, known in us if known)
    dur_us = max(d_max, span)
    _need(dur_us > 0, "長さが 0")
    num, den, exact = _av_reduce(v["ts"] * v["frames"], v["total"])
    _need(num > 0 and den > 0, "fps が出せない")
    out = dict(duration=_sec(dur_us), width=v["w"], height=v["h"], fps=float(num) / float(den), n_frames=v["frames"], vcodec=v["codec"],
               acodec=a["codec"] if a else None)
    sig = (brand, "head" if moov[2] else "tail", v["fmt"].decode("latin1"), v["w"], v["h"], "vfr" if v["vfr"] else "cfr", "ctts" if v["ctts"] else "",
           v["shape"], (a["fmt"].decode("latin1"), a["codec"], a["shape"]) if a else None, "+" if span > d_max else d_kind,
           "fmt" if fmt_ct else ("trak" if v["ctime"] else ""), "" if exact else "fps~")
    return out, fmt_ct or v["ctime"], sig
