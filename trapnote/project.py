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
from .paths import is_under, key, norm
from .species import SpeciesList

log = logging.getLogger(__name__)
PROJECT_DIRNAME = "TrapNote"
PROJECT_JSON = "project.json"
# プロジェクトのフォルダに置く目印のファイル（フォルダを見ただけで横断か調査回かが分かるように。中身は説明の文）
MARKER_CROSS = "_これは横断プロジェクト (TrapNote cross project).txt"
MARKER_SEASON = "_これは調査回プロジェクト (TrapNote season project).txt"

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
    "species_pick": "confidence",                                                # 第 1 種の決め方: confidence（上位 3 枚の平均が最大）/ count（p1 以上の枚数が最多）
    "species_empty_p": 0.8,                                                      # どの種も p1 に届かず、枠の「動物なし」の確率の平均がこれ以上なら動画を「動物なし」に（0 で使わない = 要確認のまま）
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
    "train_drop_static": False,        # 動画の間ずっと同じ位置にある枠（倒木・石の誤検出）を動画の種ではなく「動物なし」として学習する（動く枠が 1 つでもある動画で）。既定オフ: 学習フレームは人が確認する前提なので、自動の選別はしない
    "train_check_agree": False,        # 動画の種が枠の判定の上位 3 位に入らない枠は学習に使わない（元のモデルで枠を選別。モデルが当てにならないうちは切る）
    "train_frames": "one",             # 1 動画から学習に使う枠: one = 学習フレーム 1 枚（人が確定したときに選んでいた枠。選んでいなければ信頼度が最も高い枠。種が複数なら種ごとに 1 枚）/
                                       # one_plus = 学習フレーム + 同じ種とモデルも判定した静止でない枠 / all = 最大 12 枚全部（従来）。「動物なし」の枠は one なら 1 枚、ほかは全部
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
    "cross_purpose": "",               # 横断の目的: merge（調査回のデータを 1 つにまとめる）/ train（種判別モデルを鍛える）/ ""（まだ決めていない）
}


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "pc"


def is_project_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, PROJECT_JSON))


def find_project_for_root(root: str) -> str | None:
    """動画のフォルダ root の中のプロジェクト: <root>/TrapNote、なければ root 直下で「ルートは親」の印（root_is_parent。書き出しで名前を付けたプロジェクト）のあるフォルダ"""
    p = os.path.join(root, PROJECT_DIRNAME)
    if is_project_dir(p):
        return norm(p)
    try:
        with os.scandir(root) as it:
            subs = sorted(e.name for e in it if e.is_dir(follow_symlinks=False) and not e.name.startswith("."))
    except OSError:
        return None
    for d in subs:
        q = os.path.join(root, d)
        if is_project_dir(q):
            data = read_json(os.path.join(q, PROJECT_JSON), None) or {}
            if data.get("root_is_parent") and data.get("kind") != "cross":
                return norm(q)
    return None


def resolve_root_of(pdir: str, data: dict | None = None) -> str | None:
    """プロジェクトのフォルダ pdir のルート（この PC での動画のフォルダ）。Project を開かずに決める（横断プロジェクトの参照先の解決にも使う）。
    既定の置き方（<ルート>/TrapNote）なら親フォルダ → roots[この PC] → 存在する roots のどれか"""
    pdir = norm(pdir)
    if data is None:
        data = read_json(os.path.join(pdir, PROJECT_JSON), None) or {}
    roots = data.get("roots") or {}
    parent = os.path.dirname(pdir)
    if (os.path.basename(pdir) == PROJECT_DIRNAME or data.get("root_is_parent")) and os.path.isdir(parent):      # 既定の置き方か、名前を付けたプロジェクト（書き出し）
        return norm(parent)
    r = roots.get(hostname())
    if r and os.path.isdir(r):
        return norm(r)
    for r in roots.values():
        if r and os.path.isdir(r):
            return norm(r)
    if data.get("kind") == "cross":                  # 横断プロジェクトは置き場のフォルダ自体がプロジェクト（動画は参照先にある）。別の PC に移してもそのまま開ける
        return pdir
    return None


class Project:
    def __init__(self, pdir: str, root: str | None = None, create: bool = False, kind: str | None = None):
        self.dir = norm(pdir)
        jp = os.path.join(self.dir, PROJECT_JSON)
        self.data = read_json(jp, None)
        if not isinstance(self.data, dict):
            if not create:
                raise FileNotFoundError(f"プロジェクトがありません: {jp}")
            own = kind == "cross" and os.path.basename(self.dir) != PROJECT_DIRNAME          # 横断: 置き場のフォルダ自体がプロジェクト → 名前はそのフォルダ名
            self.data = dict(app=APP_ID, version=VERSION, name=(os.path.basename(self.dir) if own else os.path.basename(os.path.dirname(self.dir))) or "project",
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
        if (os.path.basename(self.dir) == PROJECT_DIRNAME or self.data.get("root_is_parent")) and os.path.isdir(parent) and r and os.path.isdir(r) and key(r) != key(parent):
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
        self.write_marker()

    def marker_text(self) -> str:
        """目印のファイルの中身（日本語と英語。何のフォルダか、動画はどこか、どう開くか）"""
        if self.is_cross:
            srcs = self.sources()
            lines = [f"このフォルダは TrapNote の横断プロジェクト「{self.name}」です（フォルダ自体がプロジェクト）。",
                     "動画はここにはなく、参照先（下）から直接読みます。判定・組・カタログ・学習したモデルがこの中にあります。消すとそれらが失われます。",
                     "開くには TrapNote の「プロジェクトを開く」でこのフォルダを指定します。",
                     "参照先: " + ("、".join(f"{x.get('name')} = {x.get('root') or x.get('dir') or ''}" for x in srcs) if srcs else "（まだなし）"),
                     "",
                     f"This folder is the TrapNote cross project \"{self.name}\" (the folder itself is the project).",
                     "The videos are not here; they are read from the sources above. Judgements, batches, the catalogue and trained models live here; deleting the folder loses them.",
                     "Open it with \"Open project\" in TrapNote by choosing this folder."]
        else:
            lines = [f"このフォルダは TrapNote の調査回プロジェクト「{self.name}」の記録です。",
                     f"動画のフォルダ: {self.root or '（未設定）'}（このフォルダの{'親' if key(os.path.dirname(self.dir)) == key(self.root or '') else '外'}）。判定・追加項目・AI の判定・枠の画像がこの中にあります。消すとそれらが失われます。",
                     "開くには TrapNote の「プロジェクトを開く」で動画のフォルダ（上）を指定します。",
                     "",
                     f"This folder holds the TrapNote season project \"{self.name}\".",
                     f"Videos: {self.root or '(not set)'}. Judgements, fields, AI results and crop images live here; deleting the folder loses them.",
                     "Open it with \"Open project\" in TrapNote by choosing the video folder above."]
        ex = self.data.get("exported_from")
        if ex:
            lines.append(f"元: 「別のフォルダへ写す」で {ex.get('project')}（{ex.get('dir')}）から {str(ex.get('at') or '')[:16]} に作成 / Copied from project {ex.get('project')}.")
        return "\n".join(lines) + "\n"

    def write_marker(self) -> None:
        """目印のファイルを置く（中身が変わったときだけ書く。反対の種類の目印が残っていれば消す）"""
        want, other = (MARKER_CROSS, MARKER_SEASON) if self.is_cross else (MARKER_SEASON, MARKER_CROSS)
        try:
            text = self.marker_text()
            fp = os.path.join(self.dir, want)
            cur = None
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8-sig") as fh:
                    cur = fh.read()
            if cur != text:
                with open(fp, "w", encoding="utf-8-sig", newline="\r\n") as fh:      # メモ帳で読めるように BOM 付き・CRLF
                    fh.write(text)
            op = os.path.join(self.dir, other)
            if os.path.isfile(op):
                os.remove(op)
        except OSError as e:
            log.debug("目印のファイルを書けません: %s", e)

    def update_data(self, **kv) -> None:
        """project.json の一部の項目だけを書く（いまのファイルを読み直して項目を入れ替える）。
        別の Project インスタンス（裏の処理が自分で開いたもの）と同じファイルを触るときに、相手の変更を消さないため"""
        jp = os.path.join(self.dir, PROJECT_JSON)
        cur = read_json(jp, None)
        if not isinstance(cur, dict):
            cur = dict(self.data)
        cur.update(kv)
        cur.update(app=APP_ID, version=VERSION, updated_at=self._now())
        self.data.update(kv)
        write_json(jp, cur)

    def reload_data(self, *keys: str) -> None:
        """project.json から項目を読み直す（裏の処理が別のインスタンスで書いたものを取り込む）"""
        cur = read_json(os.path.join(self.dir, PROJECT_JSON), None)
        if isinstance(cur, dict):
            for k in keys:
                if k in cur:
                    self.data[k] = cur[k]

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
                    updated_at=self.data.get("updated_at"), is_default_place=(key(os.path.dirname(self.dir)) == key(self.root or "")),
                    exported_from=self.data.get("exported_from"),      # 「別のフォルダへ写す」で作ったプロジェクト: 元のプロジェクト（横断など）
                    legacy_named=bool(self.data.get("root_is_parent")) and not self.is_cross)   # 旧形式: 名前を付けたプロジェクトで動画は親フォルダに（「整える」で自己完結のフォルダに）

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass


def tidy_named_project(pdir: str, progress=None) -> dict:
    """旧形式の「名前を付けたプロジェクト」（<親>/<名前>/project.json、動画は <親>/<相対パス>。root_is_parent）を、自己完結のフォルダに整える:
    <親>/<名前>/<相対パス> に動画（この プロジェクトの対象だけ。同じ親の下のほかの動画は触らない）、<親>/<名前>/TrapNote にプロジェクト。
    同じドライブの中の移動（rename）なので一瞬で終わる。プロジェクトは閉じてから呼ぶこと。返り値 dict(project_dir, root, n_moved, n_missing)"""
    import shutil
    from .db import Database
    pdir = norm(pdir)
    data = read_json(os.path.join(pdir, PROJECT_JSON), None) or {}
    if not isinstance(data, dict) or not data.get("root_is_parent") or data.get("kind") == "cross":
        raise ValueError("旧形式（名前を付けたプロジェクト）ではありません")
    if os.path.basename(pdir) == PROJECT_DIRNAME:
        raise ValueError("プロジェクトのフォルダ名が TrapNote のものは整える対象ではありません")
    parent = os.path.dirname(pdir)
    new_root = pdir
    new_pdir = os.path.join(pdir, PROJECT_DIRNAME)
    if os.path.exists(new_pdir):
        raise ValueError(f"{new_pdir} が既にあります")
    db = Database(os.path.join(pdir, "data.sqlite"))
    try:
        rels = [r[0] for r in db.q("SELECT rel_path FROM items ORDER BY rel_path")]
    finally:
        db.close()
    # 1. 動画を <親>/<rel> → <親>/<名前>/<rel> に（この対象だけ）
    n_moved = n_missing = 0
    for i, rel in enumerate(rels):
        src = os.path.join(parent, *rel.split("/"))
        dst = os.path.join(new_root, *rel.split("/"))
        if os.path.isfile(dst):
            continue
        if not os.path.isfile(src):
            n_missing += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        n_moved += 1
        if progress and i % 100 == 0:
            progress(i, len(rels), rel)
    # 空になった動画のフォルダは消す（親の直下の参照先フォルダ <親>/PRM_N まで。中にほかのファイルがあれば残る）
    n_pruned = 0
    for top in sorted({rel.split("/")[0] for rel in rels if "/" in rel}):
        n_pruned += prune_empty_dirs(os.path.join(parent, top))
    # 2. プロジェクトのファイルを <名前>/TrapNote に（動画のフォルダは残す）
    os.makedirs(new_pdir, exist_ok=True)
    moved_names = []
    with os.scandir(pdir) as it:
        entries = [e for e in it if e.name != PROJECT_DIRNAME]
    video_dirs = {rel.split("/")[0] for rel in rels if "/" in rel}
    for e in entries:
        if e.is_dir(follow_symlinks=False) and e.name in video_dirs:
            continue
        shutil.move(e.path, os.path.join(new_pdir, e.name))
        moved_names.append(e.name)
    data.pop("root_is_parent", None)
    data["roots"] = {hostname(): norm(new_root)}
    data["root"] = norm(new_root)
    data["name"] = os.path.basename(pdir)
    data["tidied_from"] = dict(dir=pdir, parent=parent, at=datetime.now().astimezone().isoformat(timespec="seconds"))
    write_json(os.path.join(new_pdir, PROJECT_JSON), data)
    try:                                                              # 目印も新しい形で
        p = Project(new_pdir)
        p.close()
    except Exception as e:
        log.warning("整えたプロジェクトを開けません: %s", e)
    return dict(project_dir=norm(new_pdir), root=norm(new_root), n_moved=n_moved, n_missing=n_missing, moved=moved_names, n_pruned=n_pruned)


def prune_empty_dirs(top: str) -> int:
    """top の下の空のフォルダを下から消す（top 自体も空になれば消す）。ファイルは一切消さない。返り値は消したフォルダの数"""
    n = 0
    top = norm(top)
    if not os.path.isdir(top):
        return 0
    for dirpath, dirnames, filenames in os.walk(top, topdown=False):
        if filenames:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                n += 1
        except OSError as e:
            log.debug("空のフォルダを消せません %s: %s", dirpath, e)
    return n
