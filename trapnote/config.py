"""アプリ全体の設定（App/data/settings.json）と作業状態（state.json）。areacheck の config.py と同じ作り。
プロジェクトごとの設定（フォルダ名のパターン、閾値など）は project.py の project.json に持つ"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # .../TrapNote
DATA_DIR = Path(os.environ.get("TRAPNOTE_DATA_DIR") or (APP_DIR / "data"))   # --data-dir で別の場所にできる（試験用）
STATIC_DIR = APP_DIR / "static"
RESOURCES_DIR = APP_DIR / "resources"
SETTINGS_FILE = DATA_DIR / "settings.json"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "app.log"

DEFAULTS = {
    "language": "ja",               # 画面の言語（ja / en）
    "user_name": "",                # 確認者として記録する名前
    "port": 8765,
    "ffmpeg_path": "",              # 空なら自動で探す（PATH、resources/ffmpeg、imageio_ffmpeg）
    "device": "auto",               # auto / cuda / mps / cpu（第 2 段階の検出・判別）
    "batch_size": 8,                # 検出・判別で一度にモデルに入れる枚数（GPU のメモリに合わせる。CPU なら 4 くらい）
    "models_dir": "",               # モデルファイルの置き場所（空なら data/models。MegaDetector の .pt と SpeciesNet の kagglehub キャッシュ）
    "cache_limit_gb": 20,           # 再生用 MP4 とサムネイルの上限（GB）。超えたら古いものから消す
    "transcode_max_width": 1280,    # 再生用 MP4 の最大幅（px）
    "transcode_codec": "h264",      # 再生用の動画の codec。h264（MP4。既定）/ vp9（WebM。H.264 を再生できないブラウザの試験用）
    "avi_direct": True,             # MJPEG の AVI は変換せず、画面側でコマ（JPEG）を直接読んで再生する（音は出ない）
    "prefetch_n": 3,                # 開いた動画の次の何本を先に変換しておくか
    "auto_advance": True,           # 確定したら次の対象へ進む
    "play_speed": 1.0,              # 再生の速さ（0.5 / 1 / 2 / 4）
    "loop": False,                  # ループ再生
    "show_boxes": True,             # 検出枠の重ね描き（第 2 段階）
    "keys": {},                     # キー割当の上書き（画面側の既定に対して）
    "recent_projects": [],          # 最近開いたプロジェクトのフォルダ（新しい順、最大 10）
    "input_history": {},            # 入力欄の候補（欄の名前 → 過去に入れた値。× で消せる）
    "input_hidden": {},             # 候補から消したもの（欄の名前 → 値。組の名前など、実在するものを候補に出さないために）
}

log = logging.getLogger(__name__)
_lock = threading.Lock()


def read_json(path: Path | str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:  # 壊れていても起動は止めない
        log.warning("%s を読めません: %s", path, e)
        return default


def write_json(path: Path | str, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Settings:
    def __init__(self):
        self.data = dict(DEFAULTS)
        self.data.update({k: v for k, v in read_json(SETTINGS_FILE, {}).items() if k in DEFAULTS})
        self._validate()

    def _validate(self) -> None:
        d = self.data
        if d.get("language") not in ("ja", "en"):
            d["language"] = "ja"
        d["user_name"] = str(d.get("user_name") or "").strip()
        try:
            d["port"] = int(d["port"])
        except Exception:
            d["port"] = DEFAULTS["port"]
        d["ffmpeg_path"] = str(d.get("ffmpeg_path") or "").strip()
        if d.get("device") not in ("auto", "cuda", "mps", "cpu"):
            d["device"] = "auto"
        for k, lo, hi in (("cache_limit_gb", 1, 10000), ("transcode_max_width", 320, 4096), ("prefetch_n", 0, 20)):
            try:
                d[k] = min(hi, max(lo, float(d[k])))
            except Exception:
                d[k] = DEFAULTS[k]
        d["transcode_max_width"] = int(d["transcode_max_width"])
        if d.get("transcode_codec") not in ("h264", "vp9"):
            d["transcode_codec"] = "h264"
        d["prefetch_n"] = int(d["prefetch_n"])
        try:
            d["batch_size"] = min(64, max(1, int(d.get("batch_size") or 8)))
        except Exception:
            d["batch_size"] = DEFAULTS["batch_size"]
        d["models_dir"] = str(d.get("models_dir") or "").strip()
        d["auto_advance"] = bool(d.get("auto_advance", True))
        try:
            d["play_speed"] = float(d.get("play_speed") or 1.0)
        except Exception:
            d["play_speed"] = 1.0
        if d["play_speed"] not in (0.5, 1.0, 2.0, 4.0):
            d["play_speed"] = 1.0
        d["loop"] = bool(d.get("loop"))
        d["avi_direct"] = bool(d.get("avi_direct", True))
        d["show_boxes"] = bool(d.get("show_boxes", True))
        if not isinstance(d.get("keys"), dict):
            d["keys"] = {}
        rp = d.get("recent_projects")
        d["recent_projects"] = [str(p) for p in (rp if isinstance(rp, list) else []) if str(p).strip()][:10]
        for k in ("input_history", "input_hidden"):           # 欄の名前 → 値の列（文字列。1 欄 30 個まで）
            h = d.get(k)
            d[k] = {str(a): [str(x) for x in v if str(x).strip()][:30] for a, v in (h.items() if isinstance(h, dict) else []) if isinstance(v, list)}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def update(self, changes: dict) -> dict:
        with _lock:
            for k, v in changes.items():
                if k in DEFAULTS:
                    self.data[k] = v
            self._validate()
            write_json(SETTINGS_FILE, self.data)
            return dict(self.data)

    def remember_project(self, path: str) -> None:
        with _lock:
            rp = [p for p in self.data.get("recent_projects", []) if p.lower() != path.lower()]
            self.data["recent_projects"] = [path] + rp[:9]
            write_json(SETTINGS_FILE, self.data)

    def to_dict(self) -> dict:
        return dict(self.data)


class State:
    """前回開いていたプロジェクトと対象、画面の状態"""

    def __init__(self):
        d = read_json(STATE_FILE, {})
        self.last_project: str | None = d.get("last_project")
        self.last_item: dict = d.get("last_item") or {}        # project path -> item id
        self.ui: dict = d.get("ui") or {}                      # 画面側が覚えたいもの（絞り込みなど）

    def save(self) -> None:
        with _lock:
            write_json(STATE_FILE, {"last_project": self.last_project, "last_item": self.last_item, "ui": self.ui})
