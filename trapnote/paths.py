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
    """比較用（Windows では大文字小文字を区別しない）"""
    return os.path.normcase(norm(p))


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
