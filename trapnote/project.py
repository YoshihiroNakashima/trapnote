"""プロジェクト = <ルート>/TrapNote フォルダ（project.json、data.sqlite、species.csv、fields.json、cache/、models/、exports/、logs/）。
areacheck の StayTime と同じ考え方で、元のメディアのフォルダには何も作らない。project.json の骨組みも areacheck の StayTime/project.json に合わせる"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime

from . import APP_ID, VERSION
from .config import read_json, write_json
from .db import Database
from .fields import Fields
from .paths import norm, key
from .species import SpeciesList

log = logging.getLogger(__name__)
PROJECT_DIRNAME = "TrapNote"
PROJECT_JSON = "project.json"

PROJECT_DEFAULTS = {
    "pattern": "{station}/{file}",     # フォルダ名のパターン
    "season_name": "",                 # パターンに {season} がないときの調査回の名前（空ならルートのフォルダ名。ルート = 調査回のフォルダという置き方のため）
    "skip_names": ["TrapNote", "StayTime", "output_rfdetr", "history", "deleted"],
    # 第 2 段階（検出・判別）の設定
    "md_model": "auto",                # MegaDetector: auto（GPU なら MDv1000-redwood、なければ MDv1000-spruce）/ MDv1000-redwood / spruce / larch / cedar / MDv5a / MDv5b / モデルファイルのパス
    "frame_first_sec": 1.0, "frame_first_fps": 5.0, "frame_interval_sec": "auto", # 最初の 1 秒は 5 fps、以降 auto = GPU なら 0.2 秒ごと、CPU なら 0.5 秒ごと（数値で固定もできる）
    "animal_threshold": 0.2, "animal_min_frames": 2,                            # 動物: 信頼度 0.2 以上の枠が 2 フレーム以上
    "person_threshold": 0.2, "person_min_frames": 1,                            # 人・車: 1 フレーム以上
    "species_p1": 0.5, "species_p2": 0.6, "species_k": 3,                       # 第 1 種は p1 以上の枠が最多の種、第 2 種以降は p2 以上が K 枚以上
    "classifier": "speciesnet",        # speciesnet / none（検出だけ）/ （第 3 段階で）追加学習したモデルの名前
    "speciesnet_model": "kaggle:google/speciesnet/pyTorch/v4.0.3a/1",            # SpeciesNet の版（kaggle: / hf: / ローカルのフォルダ）
    "country": "JPN",                  # 撮影した国（ISO 3166-1 alpha-3）。SpeciesNet の geofence（その国にいない種のラベルを除く）に使う。空なら制限なし
    "max_crops": 12,                   # 1 本の動画で分類器に入れる枠の上限（信頼度の高い順）
    "crop_min_gap_sec": 0.6,           # 枠を選ぶとき、同じ場面に固まらないようにフレーム間をあける秒数（0 で無効）
    "crop_per_frame": 2,               # 1 フレームから選ぶ枠の上限（0 で無効）
    "detect_image_size": 0,            # MegaDetector に入れる画像の長辺（0 = モデルの既定。1280 など）
    "save_features": True,             # 枠ごとの特徴量を cache/features に保存する（第 3 段階の追加学習用）
    "detect_skip_verified": True,      # 確認済みの対象は検出を飛ばす（判定は変えない）
    "frame_interval_fixed": False,     # frame_interval_sec を人が数値で決めた（古い既定 0.5 を「自動」に直さない）
    # 第 3 段階（追加学習）の設定
    "train_min_conf": 0.5,             # 学習に使う枠の信頼度（MegaDetector）の下限
    "train_weight_hard": 2.0,          # 人と AI が食い違った・見直し済みの動画の枠の重み
    "train_holdout_frac": 0.2,         # 検証用に取り分ける地点の割合
    "train_base_mix": 0.3,             # 方式 A: 元の SpeciesNet の確率を混ぜる割合
    "train_min_per_class": 5,          # 学習に入れる種の最低の枠数
    "train_use_empty": True,           # 「動物なし」の動画の枠を「動物なし」として学習する
    "train_final_without_holdout": False,   # 検証用の地点を保存するモデルの学習にも使わない（検証用の地点で学習したものをそのまま保存）
    "train_max_per_class": 20000,      # 種ごとの枠の上限
    "train_epochs_b": 5,               # 方式 B: エポック数
    "train_batch_b": 16,               # 方式 B: バッチ
    "train_unfreeze": 0.3,             # 方式 B: 学習する層の割合（後ろから）
    "train_lr_b": 1e-4,                # 方式 B: 本体の学習率（最終層はその 10 倍）
    "train_seed": 1,
    "archive_codec": "auto",           # 保存用の変換: auto（NVENC があれば使う）/ nvenc / cpu（libx264）
    "archive_crf": 19,                 # 保存用の品質（小さいほど高画質・大きい。18〜23）
    "archive_original": "keep",        # 元のファイル: keep（残す）/ move（別のフォルダへ移す）/ delete（検算に通ったら消す）
    "archive_move_to": "",             # move のときの移し先（この下に相対パスで置く）
    "z_is_utc": False,                 # 末尾 Z の日時（動画の creation_time、CSV の DateTime）を UTC とみなして時差を変換するか。既定はカメラの時刻そのまま
    "catalog_scan_folders": False,     # 横断: カタログを作るとき、プロジェクトも種名の CSV の行もない調査回はフォルダを見て動画を載せる（NAS では遅い）
}


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "pc"


def is_project_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, PROJECT_JSON))


def find_project_for_root(root: str) -> str | None:
    p = os.path.join(root, PROJECT_DIRNAME)
    return norm(p) if is_project_dir(p) else None


def resolve_root_of(pdir: str, data: dict | None = None) -> str | None:
    """プロジェクトのフォルダ pdir のルート（この PC での動画のフォルダ）。Project を開かずに決める（横断プロジェクトの参照先の解決にも使う）。
    既定の置き方（<ルート>/TrapNote）なら親フォルダ → roots[この PC] → 存在する roots のどれか"""
    pdir = norm(pdir)
    if data is None:
        data = read_json(os.path.join(pdir, PROJECT_JSON), None) or {}
    roots = data.get("roots") or {}
    parent = os.path.dirname(pdir)
    if os.path.basename(pdir) == PROJECT_DIRNAME and os.path.isdir(parent):
        return norm(parent)
    r = roots.get(hostname())
    if r and os.path.isdir(r):
        return norm(r)
    for r in roots.values():
        if r and os.path.isdir(r):
            return norm(r)
    return None


class Project:
    def __init__(self, pdir: str, root: str | None = None, create: bool = False, kind: str | None = None):
        self.dir = norm(pdir)
        jp = os.path.join(self.dir, PROJECT_JSON)
        self.data = read_json(jp, None)
        if not isinstance(self.data, dict):
            if not create:
                raise FileNotFoundError(f"プロジェクトがありません: {jp}")
            self.data = dict(app=APP_ID, version=VERSION, name=os.path.basename(os.path.dirname(self.dir)) or "project",
                             roots={}, root="", settings=dict(PROJECT_DEFAULTS), created_at=self._now())
            if kind == "cross":
                self.data.update(kind="cross", sources=[], imports=[])
        self._src_roots: dict[str, str | None] = {}     # 横断プロジェクト: 参照先の名前 → ルート（この PC で解決したもの）
        self.data.setdefault("roots", {})
        self.data.setdefault("settings", {})
        for k, v in PROJECT_DEFAULTS.items():
            self.data["settings"].setdefault(k, v)
        # 古い既定（0.5 秒ごと）のままのプロジェクトは「自動」（GPU なら 0.2 秒ごと）に
        if self.data["settings"].get("frame_interval_sec") == 0.5 and not self.data["settings"].get("frame_interval_fixed"):
            self.data["settings"]["frame_interval_sec"] = "auto"
        if root:
            self.set_root(root, save=False)
        self.root = self.resolve_root()
        if self.root and key((self.data.get("roots") or {}).get(hostname()) or "") != key(self.root):
            self.set_root(self.root, save=False)             # 移された場合などに roots もそろえる（下の save() で書く）
        os.makedirs(self.dir, exist_ok=True)
        for sub in ("cache", "cache/video", "cache/thumb", "cache/features", "models", "exports", "logs"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        self.db = Database(os.path.join(self.dir, "data.sqlite"))
        self.species = SpeciesList(os.path.join(self.dir, "species.csv"))
        self.fields = Fields(os.path.join(self.dir, "fields.json"))
        self.save()

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    # ---------- ルート ----------
    def resolve_root(self) -> str | None:
        """この PC のルート。既定の置き方（<ルート>/TrapNote）なら親フォルダ（フォルダごと別のドライブにコピー・移動しても追いかける）
        → roots[hostname] → 存在する roots のどれか"""
        roots = self.data.get("roots") or {}
        parent = os.path.dirname(self.dir)
        r = roots.get(hostname())
        if os.path.basename(self.dir) == PROJECT_DIRNAME and os.path.isdir(parent) and r and os.path.isdir(r) and key(r) != key(parent):
            log.info("プロジェクト %s は %s から %s に移されたようなので、ルートを親フォルダにします", self.dir, r, parent)
        return resolve_root_of(self.dir, self.data)

    # ---------- 横断プロジェクト（ほかのプロジェクトの動画を参照する） ----------
    @property
    def kind(self) -> str:
        return self.data.get("kind") or "folder"

    @property
    def is_cross(self) -> bool:
        return self.kind == "cross"

    def sources(self) -> list[dict]:
        return list(self.data.get("sources") or [])

    def source_root(self, name: str) -> str | None:
        """参照先プロジェクト name のルート（この PC で解決。一度解決したら覚える）"""
        if name in self._src_roots:
            return self._src_roots[name]
        root = None
        for s in self.sources():
            if s.get("name") == name:
                if s.get("kind") == "tree":                # 親フォルダ（NAS の PRM_N など）を直接参照
                    r = norm(s.get("root") or "")
                    root = r if r and os.path.isdir(r) else None
                else:
                    root = resolve_root_of(s["dir"])
                break
        self._src_roots[name] = root
        return root

    def forget_source_roots(self) -> None:
        self._src_roots = {}

    def abs_path(self, rel: str) -> str:
        """対象の相対パス → この PC での絶対パス。横断プロジェクトでは先頭の要素が参照先プロジェクトの名前"""
        from .paths import abs_of
        if self.is_cross:
            name, _, rest = rel.replace("\\", "/").partition("/")
            root = self.source_root(name) if rest else None
            if root:
                return abs_of(rest, root)
        return abs_of(rel, self.root or "")

    def set_root(self, root: str, save: bool = True) -> None:
        self.data.setdefault("roots", {})[hostname()] = norm(root)
        self.data["root"] = norm(root)
        self.root = norm(root)
        if save:
            self.save()

    # ---------- 保存 ----------
    def save(self) -> None:
        self.data.update(app=APP_ID, version=VERSION, updated_at=self._now())
        if self.root:
            self.data["root"] = self.root
        write_json(os.path.join(self.dir, PROJECT_JSON), self.data)

    def setting(self, k: str, default=None):
        return self.data.get("settings", {}).get(k, PROJECT_DEFAULTS.get(k, default))

    def update_settings(self, changes: dict) -> dict:
        s = self.data.setdefault("settings", {})
        for k, v in changes.items():
            if k in PROJECT_DEFAULTS:
                s[k] = v
        if isinstance(s.get("skip_names"), str):
            s["skip_names"] = [x.strip() for x in s["skip_names"].split(",") if x.strip()]
        self.save()
        return dict(s)

    @property
    def name(self) -> str:
        return self.data.get("name") or os.path.basename(os.path.dirname(self.dir))

    def rename(self, name: str) -> None:
        self.data["name"] = name.strip() or self.name
        self.save()

    def cache_dir(self, kind: str) -> str:
        return os.path.join(self.dir, "cache", kind)

    def to_dict(self) -> dict:
        return dict(dir=self.dir, name=self.name, root=self.root, roots=self.data.get("roots", {}), kind=self.kind,
                    sources=[dict(name=s.get("name"), kind=s.get("kind") or "project", dir=s.get("dir"), root=self.source_root(s.get("name"))) for s in self.sources()] if self.is_cross else [],
                    settings=self.data.get("settings", {}), created_at=self.data.get("created_at"),
                    updated_at=self.data.get("updated_at"), is_default_place=(key(os.path.dirname(self.dir)) == key(self.root or "")))

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
