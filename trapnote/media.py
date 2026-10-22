"""ffmpeg / ffprobe を使ったメタデータの取得、サムネイル、再生用 MP4 への変換、キャッシュの上限"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from .config import RESOURCES_DIR
from .paths import DIRECT_CONTAINERS, cache_name, kind_of

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DT_FMT = "%Y/%m/%d %H:%M:%S"
_lock = threading.Lock()
_found: dict[str, str | None] = {}


# ---------- ffmpeg の場所 ----------
def _exe(name: str) -> str:
    return name + (".exe" if sys.platform.startswith("win") else "")


def find_ffmpeg(setting: str = "") -> tuple[str | None, str | None]:
    """(ffmpeg, ffprobe) の場所。設定 → PATH → resources/ffmpeg → imageio_ffmpeg の順に探す。見つからなければ None"""
    with _lock:
        ck = setting or ""
        if ck in _found and "ffprobe:" + ck in _found:
            return _found[ck], _found["ffprobe:" + ck]
    ffmpeg = ffprobe = None
    cands = []
    if setting:
        s = setting
        if os.path.isdir(s):
            cands += [os.path.join(s, _exe("ffmpeg")), os.path.join(s, "bin", _exe("ffmpeg"))]
        else:
            cands.append(s)
    w = shutil.which("ffmpeg")
    if w:
        cands.append(w)
    cands += [str(RESOURCES_DIR / "ffmpeg" / _exe("ffmpeg")), str(RESOURCES_DIR / "ffmpeg" / "bin" / _exe("ffmpeg"))]
    for c in cands:
        if c and os.path.isfile(c):
            ffmpeg = c
            break
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = None
    if ffmpeg:
        sib = os.path.join(os.path.dirname(ffmpeg), _exe("ffprobe"))
        if os.path.isfile(sib):
            ffprobe = sib
    if not ffprobe:
        w = shutil.which("ffprobe")
        if w:
            ffprobe = w
    with _lock:
        _found[ck] = ffmpeg
        _found["ffprobe:" + ck] = ffprobe
    if not ffmpeg:
        log.warning("ffmpeg が見つかりません（設定の ffmpeg の場所、PATH、resources/ffmpeg、imageio_ffmpeg を探しました）")
    return ffmpeg, ffprobe


def reset_ffmpeg_cache() -> None:
    with _lock:
        _found.clear()


def _run(cmd: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


# ---------- 日時 ----------
def fmt_dt(dt: datetime) -> str:
    return dt.strftime(DT_FMT)


def parse_dt_any(s: str, z_is_utc: bool = False) -> str | None:
    """いろいろな書式の日時を YYYY/MM/DD HH:MM:SS にする。末尾 Z は既定でカメラの時刻そのまま（時差の変換なし）"""
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})[-/:.](\d{1,2})[-/:.](\d{1,2})[T _](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$", s)
    if not m:
        m2 = re.match(r"^(\d{4})[-/:.](\d{1,2})[-/:.](\d{1,2})$", s)
        if m2:
            return f"{int(m2.group(1)):04d}/{int(m2.group(2)):02d}/{int(m2.group(3)):02d} 00:00:00"
        return None
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    sec = int(m.group(6) or 0)
    tz = m.group(7)
    try:
        dt = datetime(y, mo, d, h, mi, sec)
    except ValueError:
        return None
    if tz and z_is_utc:
        if tz.upper() == "Z":
            dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        else:
            sign = 1 if tz[0] == "+" else -1
            hh, mm = int(tz[1:3]), int(tz[-2:])
            from datetime import timedelta
            off = timezone(sign * timedelta(hours=hh, minutes=mm))
            dt = dt.replace(tzinfo=off).astimezone().replace(tzinfo=None)
    return fmt_dt(dt)


def shift_dt(s: str | None, offset_sec: int | None) -> str | None:
    """YYYY/MM/DD HH:MM:SS に秒を足す（修正後の日時）"""
    if not s or offset_sec is None:
        return None
    try:
        from datetime import timedelta
        return fmt_dt(datetime.strptime(s, DT_FMT) + timedelta(seconds=int(offset_sec)))
    except ValueError:
        return None


def mtime_dt(path: str) -> str | None:
    try:
        return fmt_dt(datetime.fromtimestamp(os.path.getmtime(path)))
    except OSError:
        return None


# ---------- メタデータ ----------
def _parse_fps(s: str) -> float | None:
    try:
        if "/" in s:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else None
        return float(s)
    except Exception:
        return None


def probe_video(path: str, ffmpeg: str | None, ffprobe: str | None, z_is_utc: bool = False) -> dict:
    """duration, width, height, fps, n_frames, vcodec, acodec, container, datetime, datetime_source"""
    out: dict = dict(duration=None, width=None, height=None, fps=None, n_frames=None, vcodec=None, acodec=None,
                     container=os.path.splitext(path)[1].lower().lstrip("."), datetime=None, datetime_source=None)
    ct = None
    if ffprobe:
        r = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], timeout=120)
        if r.returncode == 0:
            try:
                d = json.loads(r.stdout.decode("utf-8", "replace"))
            except Exception:
                d = {}
            fmt = d.get("format") or {}
            tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
            ct = tags.get("creation_time") or tags.get("date") or tags.get("date-eng")
            try:
                out["duration"] = float(fmt.get("duration")) if fmt.get("duration") else None
            except Exception:
                pass
            for st in d.get("streams") or []:
                if st.get("codec_type") == "video" and out["vcodec"] is None:
                    out["vcodec"] = st.get("codec_name")
                    out["width"], out["height"] = st.get("width"), st.get("height")
                    out["fps"] = _parse_fps(st.get("avg_frame_rate") or "") or _parse_fps(st.get("r_frame_rate") or "")
                    try:
                        out["n_frames"] = int(st.get("nb_frames")) if st.get("nb_frames") else None
                    except Exception:
                        pass
                    if out["duration"] is None and st.get("duration"):
                        try:
                            out["duration"] = float(st["duration"])
                        except Exception:
                            pass
                    stags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
                    ct = ct or stags.get("creation_time")
                elif st.get("codec_type") == "audio" and out["acodec"] is None:
                    out["acodec"] = st.get("codec_name")
        else:
            log.debug("ffprobe の失敗 %s: %s", path, r.stderr.decode("utf-8", "replace")[-300:])
    elif ffmpeg:
        r = _run([ffmpeg, "-hide_banner", "-i", path], timeout=120)
        text = r.stderr.decode("utf-8", "replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        if m:
            out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        m = re.search(r"Video:\s*([A-Za-z0-9_]+).*?,\s*(\d{2,5})x(\d{2,5}).*?(\d+(?:\.\d+)?)\s*fps", text, re.S)
        if m:
            out["vcodec"], out["width"], out["height"], out["fps"] = m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))
        m = re.search(r"Audio:\s*([A-Za-z0-9_]+)", text)
        if m:
            out["acodec"] = m.group(1)
        m = re.search(r"creation_time\s*:\s*(\S+)", text)
        if m:
            ct = m.group(1)
    else:
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            if cap.isOpened():
                out["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
                out["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or None
                n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) or None
                out["fps"], out["n_frames"] = fps, n
                if fps and n:
                    out["duration"] = n / fps
            cap.release()
        except Exception as e:
            log.debug("OpenCV で読めません %s: %s", path, e)
    if out["n_frames"] is None and out["duration"] and out["fps"]:
        out["n_frames"] = int(round(out["duration"] * out["fps"]))
    if ct:
        dt = parse_dt_any(str(ct), z_is_utc)
        if dt and not dt.startswith("1970/") and not dt.startswith("1904/"):
            out["datetime"], out["datetime_source"] = dt, "metadata"
    if not out["datetime"]:
        out["datetime"], out["datetime_source"] = mtime_dt(path), "mtime"
    return out


def probe_image(path: str, z_is_utc: bool = False) -> dict:
    out: dict = dict(duration=None, width=None, height=None, fps=None, n_frames=None, vcodec=None, acodec=None,
                     container=os.path.splitext(path)[1].lower().lstrip("."), datetime=None, datetime_source=None)
    try:
        from PIL import Image
        with Image.open(path) as im:
            out["width"], out["height"] = im.size
            exif = im.getexif()
            dt = None
            try:
                ifd = exif.get_ifd(0x8769)       # Exif IFD
                dt = ifd.get(0x9003) or ifd.get(0x9004)      # DateTimeOriginal / DateTimeDigitized
            except Exception:
                pass
            dt = dt or exif.get(0x0132)                     # DateTime
            if dt:
                p = parse_dt_any(str(dt).replace(":", "/", 2), z_is_utc)
                if p:
                    out["datetime"], out["datetime_source"] = p, "exif"
    except Exception as e:
        log.debug("画像を読めません %s: %s", path, e)
    if not out["datetime"]:
        out["datetime"], out["datetime_source"] = mtime_dt(path), "mtime"
    return out


def probe(path: str, ffmpeg: str | None, ffprobe: str | None, z_is_utc: bool = False) -> dict:
    return probe_image(path, z_is_utc) if kind_of(path) == "image" else probe_video(path, ffmpeg, ffprobe, z_is_utc)


# ---------- 再生 ----------
def direct_playable(vcodec: str | None, acodec: str | None, container: str | None) -> bool:
    """ブラウザがそのまま再生できるか（H.264 の MP4/MOV、VP8/VP9 の WebM。音は AAC / MP3 / Opus / なし）"""
    ext = "." + (container or "").lower().lstrip(".")
    if ext not in DIRECT_CONTAINERS:
        return False
    v = (vcodec or "").lower()
    a = (acodec or "").lower()
    if ext == ".webm":
        return v in ("vp8", "vp9", "av1") and a in ("", "opus", "vorbis")
    return v == "h264" and a in ("", "aac", "mp3")


def video_cache_path(cache_dir: str, rel: str, codec: str = "h264") -> str:
    return os.path.join(cache_dir, cache_name(rel) + (".webm" if codec == "vp9" else ".mp4"))


def thumb_cache_path(cache_dir: str, rel: str) -> str:
    return os.path.join(cache_dir, cache_name(rel) + ".jpg")


def transcode(ffmpeg: str, src: str, dst: str, max_width: int = 1280, codec: str = "h264", vcodec: str | None = None, nvenc: bool = False) -> str:
    """再生用 MP4（H.264、AAC、faststart）を作る。途中のファイルは .tmp で、終わってから名前を変える。返り値は使った方法。
    中身が H.264 なら入れ物を MP4 に替えるだけ（copy。一瞬）。それ以外は NVENC（GPU）があればそれで、なければ libx264 veryfast。
    codec="vp9" なら WebM（試験用）"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    scale = ["-vf", f"scale='min({int(max_width)},iw)':-2"]
    if codec == "vp9":
        tmp = dst + ".tmp.webm"
        cmds = [("vp9", [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", src, *scale,
                         "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-b:a", "64k", "-ac", "2", tmp])]
    else:
        tmp = dst + ".tmp.mp4"
        head = [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", src]
        tail = ["-movflags", "+faststart", "-c:a", "aac", "-b:a", "96k", "-ac", "2", tmp]
        cmds = []
        if (vcodec or "").lower() == "h264":
            cmds.append(("copy", head + ["-c:v", "copy"] + tail))
        if nvenc:
            cmds.append(("nvenc", head + scale + ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", "26", "-b:v", "0", "-pix_fmt", "yuv420p"] + tail))
        cmds.append(("libx264", head + scale + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"] + tail))
    err = ""
    for how, cmd in cmds:
        for an in (False, True):          # 音の変換で失敗することがあるので、音なしでも試す
            c = [x for x in cmd if x not in ("-c:a", "aac", "libopus", "-b:a", "96k", "64k", "-ac", "2")] if an else cmd
            if an:
                c.insert(-1, "-an")
            r = _run(c, timeout=3600)
            if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, dst)
                return how + ("/an" if an else "")
            err = r.stderr.decode("utf-8", "replace").strip()[-400:] or err
            try:
                os.remove(tmp)
            except OSError:
                pass
    raise RuntimeError(f"変換に失敗: {err}")


def make_thumb(ffmpeg: str | None, src: str, dst: str, width: int = 320, at_sec: float = 0.5) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.jpg"
    if kind_of(src) == "image":
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((width, width * 3))
            im.save(tmp, "JPEG", quality=80)
        os.replace(tmp, dst)
        return
    if not ffmpeg:
        import cv2
        cap = cv2.VideoCapture(src)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            raise RuntimeError("フレームを読めません")
        h, w = frame.shape[:2]
        s = width / w
        frame = cv2.resize(frame, (width, max(1, int(h * s))))
        cv2.imwrite(tmp, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        os.replace(tmp, dst)
        return
    for ss in (at_sec, 0.0):
        cmd = [ffmpeg, "-y", "-v", "error", "-nostdin", "-ss", f"{ss:.2f}", "-i", src, "-frames:v", "1",
               "-vf", f"scale={int(width)}:-2", "-q:v", "5", tmp]
        r = _run(cmd, timeout=120)
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, dst)
            return
    raise RuntimeError("サムネイルを作れません")


def extract_frames(ffmpeg: str, src: str, out_dir: str, times: list[float], width: int | None = None) -> list[str]:
    """指定した秒のフレームを JPEG で取り出す（第 2 段階の検出に使う）。動画は 1 回だけ復号する"""
    os.makedirs(out_dir, exist_ok=True)
    if not times:
        return []
    expr = "+".join(f"lt(abs(t-{t:.3f})\\,0.0005)" for t in times)
    vf = f"select='{expr}'"
    if width:
        vf += f",scale={int(width)}:-2"
    pattern = os.path.join(out_dir, "f%05d.jpg")
    cmd = [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", src, "-vf", vf, "-vsync", "0", "-q:v", "4", pattern]
    r = _run(cmd, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-400:])
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".jpg"))


# ---------- 保存用の変換 ----------
_nvenc: dict[str, bool] = {}


def has_nvenc(ffmpeg: str | None) -> bool:
    """ffmpeg に h264_nvenc があり、実際に使えるか（小さな映像で試す）。結果は覚えておく"""
    if not ffmpeg:
        return False
    if ffmpeg in _nvenc:
        return _nvenc[ffmpeg]
    ok = False
    try:
        r = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=60)
        if b"h264_nvenc" in r.stdout:
            r2 = _run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=256x128:d=0.5", "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=60)
            ok = r2.returncode == 0
    except Exception as e:
        log.debug("NVENC の確認の失敗: %s", e)
    _nvenc[ffmpeg] = ok
    return ok


def archive_encode(ffmpeg: str, src: str, dst: str, use_nvenc: bool, crf: int = 19, creation_time: str | None = None) -> str:
    """保存用の H.264 MP4 を作る（解像度と fps は元のまま、音はそのまま AAC）。creation_time は YYYY/MM/DD HH:MM:SS（カメラの時刻）。
    返り値は使った符号化器（nvenc / libx264）。途中のファイルは .tmp で、終わってから名前を変える"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.mp4"
    meta = []
    if creation_time:
        try:
            dt = datetime.strptime(creation_time, DT_FMT)
            meta = ["-metadata", "creation_time=" + dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")]
        except ValueError:
            pass
    base = [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", src, "-map_metadata", "-1", *meta, "-movflags", "+faststart", "-pix_fmt", "yuv420p"]
    audio = ["-c:a", "aac", "-b:a", "128k"]
    cmds = []
    if use_nvenc:
        cmds.append(base + ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(int(crf)), "-b:v", "0", "-profile:v", "high", *audio, tmp])
    cmds.append(base + ["-c:v", "libx264", "-preset", "medium", "-crf", str(int(crf)), "-profile:v", "high", *audio, tmp])
    err = ""
    for i, cmd in enumerate(cmds):
        for an in (False, True):     # 音の変換で失敗したら音なしで
            c = [x for x in cmd if x not in ("-c:a", "aac", "-b:a", "128k")] if an else cmd
            if an:
                c.insert(-1, "-an")
            r = _run(c, timeout=7200)
            if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, dst)
                return "nvenc" if (use_nvenc and i == 0) else "libx264"
            err = r.stderr.decode("utf-8", "replace").strip()[-400:]
            try:
                os.remove(tmp)
            except OSError:
                pass
            if "No such file" in err or "Invalid data found" in err:
                break
    raise RuntimeError(f"変換に失敗: {err}")


# ---------- キャッシュの上限 ----------
def trim_cache(cache_dir: str, limit_bytes: int, keep: set[str] | None = None) -> int:
    """合計が上限を超えていれば、古いものから消す。keep は消さないファイルのパス。返り値は消した数"""
    files = []
    total = 0
    for dp, _, fns in os.walk(cache_dir):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            total += st.st_size
            files.append((st.st_mtime, st.st_size, p))
    if total <= limit_bytes:
        return 0
    files.sort()
    n = 0
    for mt, sz, p in files:
        if total <= limit_bytes * 0.9:
            break
        if keep and p in keep:
            continue
        try:
            os.remove(p)
            total -= sz
            n += 1
        except OSError:
            pass
    return n


def touch(path: str) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass
