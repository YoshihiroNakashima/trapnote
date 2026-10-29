"""第 3 段階: 追加学習（この地域・このカメラのデータへの合わせ込み）

方式 A（軽い分類器）: 第 2 段階で保存した枠ごとの特徴量（cache/features/*.npz。SpeciesNet の最終層の手前）に、
  種リストのコードを出力とする線形の分類器（softmax 回帰）を学習する。numpy だけで動き CPU でも速い。
  判定のときは、新しい分類器の確率に元の SpeciesNet の確率を base_mix の割合で混ぜる（学習に出てこなかった種も残るように）。
方式 B（本体の再学習）: SpeciesNet 本体の後ろの層と、種リストのコードを出す新しい最終層を学習する（GPU 向け）。
  学習用の枠は動画から切り出して cache/crops に置く（JPEG、480×480）。

学習に使うもの: 確認済み（人が確定した）対象の枠。「学習から外す」（train_exclude）の対象と、信頼度の低い枠は外す。
  - 種が 1 つの動画: その動画の枠は全部その種
  - 種が複数の動画: 枠ごとに元のモデルの第 1 候補がその動画の種のどれかならそれ（違えば使わない）
  - 「動物なし」と確定した動画に枠があれば「動物なし」（誤検出を覚える）。「不明」「人」「車」だけの動画は使わない
  - 人の判定と AI が食い違っていた動画（agree = 3）と見直し済み（recheck）の枠は重み weight_hard 倍
評価: 地点（station）のうち holdout_frac の割合（または横断プロジェクトの組 holdout_batches = gold など）を検証用に取り分け（学習に使わない）、動画 1 本の判定（第 2 段階と同じ p1 / p2 / K の規則）
  を人の判定と突き合わせて、学習前（元の SpeciesNet）と学習後の一致率を出す。検証用の地点には「学習から外す」対象も入れる。
  評価のあと、全部の地点で学習し直したものを保存する（models/<名前>/）"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import shutil
import time
from collections import Counter

import numpy as np

from .db import now_iso
from .paths import cache_name
from .project import PROJECT_DEFAULTS

log = logging.getLogger(__name__)
SKIP_CODES = {"human", "vehicle", "unknown", "area"}
TRAIN_DEFAULTS = {k: v for k, v in PROJECT_DEFAULTS.items() if k.startswith("train_")}     # 既定は project.py に
CROP_SIZE = 480


def train_settings(project, overrides: dict | None = None) -> dict:
    s = {**TRAIN_DEFAULTS}
    for k in TRAIN_DEFAULTS:
        v = (project.data.get("settings") or {}).get(k)
        if v is not None:
            s[k] = v
    if overrides:
        for k, v in overrides.items():
            if k in TRAIN_DEFAULTS and v is not None:
                s[k] = v
    return s


def models_root(project) -> str:
    d = os.path.join(project.dir, "models")
    os.makedirs(d, exist_ok=True)
    return d


SHARED_DIR: str | None = None     # 共有のモデル置き場（data/models/local。サーバーが起動時に入れる）。どのプロジェクトからも使える


def shared_root() -> str:
    global SHARED_DIR
    if not SHARED_DIR:
        from .config import DATA_DIR
        SHARED_DIR = os.path.join(str(DATA_DIR), "models", "local")
    os.makedirs(SHARED_DIR, exist_ok=True)
    return SHARED_DIR


def model_folder(project, name: str) -> str | None:
    """モデルのフォルダ: プロジェクトの models/<name> → 共有の models/local/<name> の順に探す"""
    name = safe_model_name(name)
    for root in (models_root(project), shared_root()):
        d = os.path.join(root, name)
        if os.path.isfile(os.path.join(d, "model.json")):
            return d
    return None


def share_model(project, name: str) -> str:
    """プロジェクトのモデルを共有の置き場にコピーする（ほかのプロジェクトでも使えるように）"""
    src = os.path.join(models_root(project), safe_model_name(name))
    if not os.path.isfile(os.path.join(src, "model.json")):
        raise FileNotFoundError(f"追加学習のモデルがありません: {name}")
    dst = os.path.join(shared_root(), safe_model_name(name))
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return dst


def safe_model_name(name: str) -> str:
    name = "".join(ch for ch in (name or "").strip() if ch not in '\\/:*?"<>|').strip(". ")
    return name[:60] or "model"


def list_models(project) -> list[dict]:
    """このプロジェクトのモデルと、共有の置き場のモデル（shared = True。同じ名前がプロジェクトにあればそちら）"""
    out = []
    seen = set()
    for root, shared in ((models_root(project), False), (shared_root(), True)):
        for n in sorted(os.listdir(root)):
            jp = os.path.join(root, n, "model.json")
            if not os.path.isfile(jp) or n in seen:
                continue
            try:
                with open(jp, encoding="utf-8") as fh:
                    d = json.load(fh)
                d["name"] = n
                d["shared"] = shared
                d["also_shared"] = (not shared) and os.path.isfile(os.path.join(shared_root(), n, "model.json"))
                out.append(d)
                seen.add(n)
            except Exception as e:
                log.warning("models/%s を読めません: %s", n, e)
    out.sort(key=lambda d: d.get("trained_at") or "", reverse=True)
    return out


def model_info(project, name: str) -> dict:
    d = model_folder(project, name)
    if d is None:
        raise FileNotFoundError(f"追加学習のモデルがありません: {name}")
    with open(os.path.join(d, "model.json"), encoding="utf-8") as fh:
        return json.load(fh)


def delete_model(project, name: str) -> bool:
    """プロジェクトのモデルを消す。プロジェクトになければ共有のを消す"""
    for root in (models_root(project), shared_root()):
        d = os.path.join(root, safe_model_name(name))
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            return True
    return False


def features_path(project, rel_path: str) -> str:
    return os.path.join(project.cache_dir("features"), cache_name(rel_path) + ".npz")


# ---------- ほかのプロジェクトを材料に加える ----------
def open_source(path: str):
    """材料に加えるプロジェクトを開く（プロジェクトのフォルダ <root>/TrapNote か、そのルート）。使い終わったら close() する"""
    from .paths import norm
    from .project import Project, find_project_for_root, is_project_dir
    path = norm(path)
    if os.path.isfile(path) and os.path.basename(path).lower() == "project.json":
        path = os.path.dirname(path)
    if is_project_dir(path):
        pdir = path
    else:
        pdir = find_project_for_root(path) if os.path.isdir(path) else None
        if not pdir:
            raise FileNotFoundError(f"プロジェクトがありません: {path}")
    p = Project(pdir)
    if not p.root:
        p.close()
        raise FileNotFoundError(f"プロジェクトのルート（動画のフォルダ）がこの PC にありません: {pdir}")
    return p


def plan_counts(project) -> dict:
    """追加学習の材料の数（確認済み・学習に使える・特徴量あり・学習から外す・食い違い・地点ごと・種ごと）"""
    db = project.db
    where = "status='verified' AND missing=0 AND det_state=1 AND cls_state=1"
    n_verified = int(db.scalar("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0") or 0)
    n_ready = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where}") or 0)
    n_excl = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND train_exclude=1") or 0)
    n_hard = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND (agree=3 OR recheck=1)") or 0)
    fdir = project.cache_dir("features")
    n_feat = len([f for f in os.listdir(fdir) if f.endswith(".npz")]) if os.path.isdir(fdir) else 0
    stations = [dict(station=r["station"] or "", n=r["n"]) for r in db.q(f"SELECT station, COUNT(*) AS n FROM items WHERE {where} GROUP BY station ORDER BY station")]
    species = [dict(code=r["species"], n=r["n"]) for r in db.q(f"SELECT j.species, COUNT(DISTINCT j.item_id) AS n FROM judgements j JOIN items i ON i.id=j.item_id WHERE i.{where.replace(' AND ', ' AND i.')} GROUP BY j.species ORDER BY n DESC")]
    batches = [dict(batch=r[0], n=r[1], n_ready=r[2] or 0, n_verified=r[3] or 0)          # 横断プロジェクトの組（gold など。検証用に選べる）
               for r in db.q(f"SELECT batch, COUNT(*), SUM({where}), SUM(status='verified') FROM items WHERE batch IS NOT NULL AND batch != '' GROUP BY batch ORDER BY batch")]
    return dict(n_verified=n_verified, n_ready=n_ready, n_excluded=n_excl, n_hard=n_hard, n_features=n_feat, stations=stations, species=species,
                batches=batches, eval_batches=[str(x) for x in (project.data.get("eval_batches") or [])],
                name=project.name, dir=project.dir, root=project.root, speciesnet_model=project.setting("speciesnet_model"))


def source_info(project, path: str) -> dict:
    """材料に加える候補のプロジェクトの様子（開いて数えて閉じる）。いま開いているプロジェクトと同じなら same = True"""
    from .paths import key
    try:
        src = open_source(path)
    except Exception as e:
        return dict(path=path, ok=False, error=str(e))
    try:
        d = plan_counts(src)
        d.update(path=path, ok=True, same=key(src.dir) == key(project.dir),
                 mismatch=(src.setting("speciesnet_model") or "") != (project.setting("speciesnet_model") or ""))
        return d
    finally:
        src.close()


def source_label(project) -> str:
    return project.name or os.path.basename(os.path.dirname(project.dir)) or "project"


def collect_all(project, sources: list[str] | None, opts: dict, progress=None, stop=None) -> dict:
    """いま開いているプロジェクトと、材料に加えるプロジェクト（sources = パスの列）から集める。
    返り値は collect() と同じ形だが、items の鍵は (何番目のプロジェクト, id)、各 item に proj（Project）、地点は加えたプロジェクトでは「<名前>:<地点>」。
    加えたプロジェクトは開いたままなので、使い終わったら close_sources() する"""
    from .paths import key
    projs = [project]
    seen = {key(project.dir)}
    for path in sources or []:
        src = open_source(path)
        if key(src.dir) in seen:
            src.close()
            continue
        base_main, base_src = project.setting("speciesnet_model") or "", src.setting("speciesnet_model") or ""
        if base_main != base_src:
            src.close()
            raise RuntimeError(f"{source_label(src)} は別の SpeciesNet（{base_src}）の設定なので一緒に学習できません（いまのプロジェクトは {base_main}）")
        projs.append(src)
        seen.add(key(src.dir))
    items: dict = {}
    n_verified = n_nofeat = 0
    srcs = []
    dims: set[int] = set()
    try:
        for si, pr in enumerate(projs):
            label = source_label(pr)
            col = collect(pr, opts, lambda a, b: progress and progress(a, b, label if si else ""), stop)
            for iid, it in col["items"].items():
                it["proj"] = pr
                it["src"] = si
                it["item_id"] = iid
                it["id"] = (si, iid)                   # build_dataset の samples はこの id を使う
                if si and it["station"]:
                    it["station"] = f"{label}:{it['station']}"
                if it["feats"] is not None and len(it["feats"]):
                    dims.add(int(it["feats"].shape[1]))
                items[(si, iid)] = it
            n_verified += col["n_verified"]
            n_nofeat += col["n_nofeat"]
            srcs.append(dict(name=label, dir=pr.dir, n_items=len(col["items"]), n_verified=col["n_verified"], n_nofeat=col["n_nofeat"]))
            if stop and stop():
                break
        if len(dims) > 1:
            raise RuntimeError(f"特徴量の次元がプロジェクトで違います（{sorted(dims)}）。同じ SpeciesNet で自動判定をやり直してください")
    except Exception:
        close_sources(projs[1:])
        raise
    return dict(items=items, n_verified=n_verified, n_nofeat=n_nofeat, sources=srcs, projects=projs)


def close_sources(projs: list) -> None:
    for pr in projs:
        try:
            pr.close()
        except Exception:
            pass


# ---------- 学習データを集める ----------
def collect(project, opts: dict, progress=None, stop=None) -> dict:
    """確認済みの対象から学習・評価用の枠を集める。返り値:
    items: {id: dict(id, rel_path, station, human(set), excluded, hard, codes(list), pc(ndarray), feats(ndarray|None), t, bbox, conf, labels(list[str|None]))}
    labels[k] = 枠 k の学習用のコード（None なら学習に使わない）"""
    from .judge import human_codes
    db = project.db
    s = opts
    min_conf = float(s.get("train_min_conf", 0.5))
    use_empty = bool(s.get("train_use_empty", True))
    rows = db.q("SELECT id, rel_path, station, status, agree, recheck, train_exclude, batch FROM items WHERE status='verified' AND missing=0 AND det_state=1 AND cls_state=1 ORDER BY station, id")
    ids = [r["id"] for r in rows]
    js: dict[int, list[dict]] = {i: [] for i in ids}
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        qs = ",".join("?" * len(part))
        for j in db.q(f"SELECT item_id, species, origin FROM judgements WHERE item_id IN ({qs})", part):
            js[j["item_id"]].append(dict(species=j["species"], origin=j["origin"]))
    items: dict[int, dict] = {}
    n_nofeat = 0
    for k, r in enumerate(rows):
        if stop and stop():
            break
        if progress and k % 50 == 0:
            progress(k, len(rows))
        human = human_codes("verified", js[r["id"]])
        if not human:
            continue
        fp = features_path(project, r["rel_path"])
        if not os.path.isfile(fp):
            n_nofeat += 1
            continue
        try:
            with np.load(fp, allow_pickle=False) as z:
                feats = z["feats"].astype(np.float32) if "feats" in z else None
                codes = [str(c) for c in z["codes"]]
                pc = z["pc"].astype(np.float32)
                t = z["t"].astype(np.float32)
                bbox = z["bbox"].astype(np.float32)
                conf = z["conf"].astype(np.float32)
        except Exception as e:
            log.warning("特徴量を読めません %s: %s", fp, e)
            n_nofeat += 1
            continue
        n = len(conf)
        if n == 0 or len(pc) != n:
            continue
        animals = human - SKIP_CODES - {"empty"}
        labels: list[str | None] = [None] * n
        if "unknown" in human or not (animals or (human == {"empty"} and use_empty)):
            pass                                         # 不明・人・車だけ: 学習には使わない（評価には使う）
        elif not animals:                                # 動物なしなのに枠がある → 誤検出として「動物なし」
            labels = ["empty"] * n
        elif len(animals) == 1:
            labels = [next(iter(animals))] * n
        else:
            for i in range(n):
                top = codes[int(np.argmax(pc[i]))] if len(codes) else None
                labels[i] = top if top in animals else None
        for i in range(n):
            if labels[i] is not None and conf[i] < min_conf:
                labels[i] = None
        items[r["id"]] = dict(id=r["id"], rel_path=r["rel_path"], station=r["station"] or "", batch=r["batch"] or "", human=human, excluded=bool(r["train_exclude"]),
                              hard=bool(r["agree"] == 3 or r["recheck"]), codes=codes, pc=pc, feats=feats, t=t, bbox=bbox, conf=conf, labels=labels, holdout=False)
    return dict(items=items, n_verified=len(rows), n_nofeat=n_nofeat)


def split_stations(stations: list[str], frac: float, seed: int, explicit: list[str] | None = None) -> set[str]:
    """検証用の地点。explicit があればそれ、なければ frac の割合を乱数で（seed で再現）"""
    if explicit:
        return set(explicit)
    st = sorted({x for x in stations if x})          # 地点のない対象（"" = 一覧の「—」）は 1 つの地点とはみなさず、学習側に置く
    if len(st) < 3 or frac <= 0:
        return set()
    rng = random.Random(seed)
    rng.shuffle(st)
    k = max(1, int(round(len(st) * frac)))
    return set(st[:k])


def mark_holdout(items: dict, holdout_stations: set[str], holdout_batches: list[str] | None = None) -> list:
    """検証用の対象に印を付ける（it["holdout"]）: 検証用の地点にある対象と、検証用の組（gold など）の対象。返り値は検証用の id の列"""
    hb = set(holdout_batches or [])
    ids = []
    for iid, it in items.items():
        it["holdout"] = bool(it["station"] in holdout_stations or (hb and it.get("batch") in hb))
        if it["holdout"]:
            ids.append(iid)
    return ids


def pick_holdout(items: dict, opts: dict) -> tuple[set[str], list[str]]:
    """検証用の地点と組を設定から決める。組（holdout_batches）があれば地点の割合は使わない（明示した地点だけ）"""
    hb = [str(x) for x in (opts.get("holdout_batches") or []) if str(x)]
    if hb:
        holdout = set(opts.get("holdout_stations") or [])
    else:
        holdout = split_stations([it["station"] for it in items.values()], float(opts.get("train_holdout_frac", 0.2)), int(opts.get("train_seed", 1)), opts.get("holdout_stations"))
    return holdout, hb


def build_dataset(items: dict, holdout: set[str], opts: dict, use_marks: bool = True) -> dict:
    """学習に使う枠（検証用の地点・組と「学習から外す」対象は除く）を並べる。種ごとの枠数が少ない種は外す。
    holdout = 検証用の地点。use_marks なら検証用の印（it["holdout"] = 地点と組。mark_holdout）も外す（全部で学習し直すときは False）"""
    min_per = int(opts.get("train_min_per_class", 5))
    max_per = int(opts.get("train_max_per_class", 20000))
    w_hard = float(opts.get("train_weight_hard", 2.0))
    skip = lambda it: it["excluded"] or it["station"] in holdout or (use_marks and it.get("holdout"))
    cnt = Counter()
    for it in items.values():
        if skip(it):
            continue
        for lab in it["labels"]:
            if lab:
                cnt[lab] += 1
    classes = sorted([c for c, n in cnt.items() if n >= min_per], key=lambda c: -cnt[c])
    dropped = {c: n for c, n in cnt.items() if n < min_per}
    cidx = {c: i for i, c in enumerate(classes)}
    samples = []      # (item_id, k, class_idx, weight)
    for it in items.values():
        if skip(it):
            continue
        w = w_hard if it["hard"] else 1.0
        for k, lab in enumerate(it["labels"]):
            if lab in cidx:
                samples.append((it["id"], k, cidx[lab], w))
    # 多すぎる種は間引く（再現できるように乱数の種を固定）
    rng = random.Random(int(opts.get("train_seed", 1)))
    by_c: dict[int, list] = {}
    for smp in samples:
        by_c.setdefault(smp[2], []).append(smp)
    out = []
    for c, lst in by_c.items():
        if len(lst) > max_per:
            rng.shuffle(lst)
            lst = lst[:max_per]
        out.extend(lst)
    out.sort()
    n_items = len({s[0] for s in out})
    return dict(classes=classes, samples=out, counts={c: cnt[c] for c in classes}, dropped=dropped, n_items=n_items)


# ---------- 方式 A: 線形の分類器（numpy） ----------
class Head:
    """特徴量 → 種コードの確率。W [D, K], b [K]、標準化 mu / sd。元のモデルの確率を base_mix で混ぜる"""

    def __init__(self, classes: list[str], W, b, mu, sd, base_mix: float = 0.3, name: str = ""):
        self.classes = list(classes)
        self.W = np.asarray(W, dtype=np.float32)
        self.b = np.asarray(b, dtype=np.float32)
        self.mu = np.asarray(mu, dtype=np.float32)
        self.sd = np.asarray(sd, dtype=np.float32)
        self.base_mix = float(base_mix)
        self.name = name

    def predict(self, feats: np.ndarray) -> np.ndarray:
        x = (np.asarray(feats, dtype=np.float32) - self.mu) / self.sd
        z = x @ self.W + self.b
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def apply(self, feats: np.ndarray | None, codes: list[str], pc: np.ndarray) -> tuple[list[str], np.ndarray]:
        """元の（コードごとの）確率 pc [n, codes] と混ぜる。返り値は (コードの列, 確率 [n, codes'])"""
        if feats is None or len(feats) != len(pc):
            return codes, pc
        ph = self.predict(feats)
        union = list(self.classes) + [c for c in codes if c not in self.classes]
        out = np.zeros((len(pc), len(union)), dtype=np.float32)
        idx = {c: i for i, c in enumerate(union)}
        beta = self.base_mix
        for j, c in enumerate(self.classes):
            out[:, idx[c]] += (1.0 - beta) * ph[:, j]
        for j, c in enumerate(codes):
            out[:, idx[c]] += beta * pc[:, j]
        ssum = out.sum(axis=1, keepdims=True)
        out = np.where(ssum > 1e-6, out / np.maximum(ssum, 1e-6), out)
        return union, out

    def save(self, folder: str) -> None:
        np.savez_compressed(os.path.join(folder, "head.npz"), W=self.W, b=self.b, mu=self.mu, sd=self.sd, classes=np.array(self.classes))

    @classmethod
    def load(cls, folder: str, info: dict | None = None) -> "Head":
        with np.load(os.path.join(folder, "head.npz"), allow_pickle=False) as z:
            classes = [str(c) for c in z["classes"]]
            h = cls(classes, z["W"], z["b"], z["mu"], z["sd"], float((info or {}).get("base_mix", 0.3)), os.path.basename(folder))
        return h


def train_head(X: np.ndarray, y: np.ndarray, w: np.ndarray, n_classes: int, opts: dict, Xv=None, yv=None, progress=None, stop=None) -> tuple[dict, dict]:
    """softmax 回帰（Adam、L2、クラスの重みはおおまかに均す、検証の損失で早めに止める）。返り値 (W, b, mu, sd の dict, 記録)"""
    rng = np.random.default_rng(int(opts.get("train_seed", 1)))
    N, D = X.shape
    K = n_classes
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-6
    Xs = (X - mu) / sd
    Xvs = (Xv - mu) / sd if Xv is not None and len(Xv) else None
    # クラスの重み: 多い種を抑える（√ で緩く）
    cnt = np.bincount(y, minlength=K).astype(np.float64)
    cw = np.sqrt(cnt.sum() / (K * np.maximum(cnt, 1)))
    sw = (w * cw[y]).astype(np.float32)
    sw *= len(sw) / sw.sum()
    W = np.zeros((D, K), dtype=np.float32)
    b = np.zeros(K, dtype=np.float32)
    mW = np.zeros_like(W); vW = np.zeros_like(W); mb = np.zeros_like(b); vb = np.zeros_like(b)
    lr, l2, bs = float(opts.get("train_lr_a", 0.01)), float(opts.get("train_l2_a", 1e-4)), 256
    epochs = int(opts.get("train_epochs_a", 40))
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    step = 0
    best = (float("inf"), W.copy(), b.copy(), 0)
    patience, bad = 6, 0
    hist = []

    def loss_of(Xq, yq):
        z = Xq @ W + b
        z = z - z.max(axis=1, keepdims=True)
        lse = np.log(np.exp(z).sum(axis=1))
        nll = lse - z[np.arange(len(yq)), yq]
        acc = float((np.argmax(z, axis=1) == yq).mean()) if len(yq) else 0.0
        return float(nll.mean()), acc

    for ep in range(epochs):
        if stop and stop():
            break
        order = rng.permutation(N)
        tot = 0.0
        for i in range(0, N, bs):
            idx = order[i:i + bs]
            xb, yb, wb = Xs[idx], y[idx], sw[idx]
            z = xb @ W + b
            z -= z.max(axis=1, keepdims=True)
            e = np.exp(z)
            p = e / e.sum(axis=1, keepdims=True)
            tot += float(-(np.log(p[np.arange(len(yb)), yb] + 1e-9) * wb).sum())
            g = p
            g[np.arange(len(yb)), yb] -= 1.0
            g *= wb[:, None] / len(yb)
            gW = xb.T @ g + l2 * W
            gb = g.sum(axis=0)
            step += 1
            mW = beta1 * mW + (1 - beta1) * gW; vW = beta2 * vW + (1 - beta2) * gW * gW
            mb = beta1 * mb + (1 - beta1) * gb; vb = beta2 * vb + (1 - beta2) * gb * gb
            a = lr * math.sqrt(1 - beta2 ** step) / (1 - beta1 ** step)
            W -= a * mW / (np.sqrt(vW) + eps)
            b -= a * mb / (np.sqrt(vb) + eps)
        tr_loss = tot / N
        if Xvs is not None:
            vl, va = loss_of(Xvs, yv)
        else:
            vl, va = loss_of(Xs, y)
        hist.append(dict(epoch=ep + 1, train_loss=round(tr_loss, 4), val_loss=round(vl, 4), val_acc=round(va, 4)))
        if progress:
            progress(ep + 1, epochs, hist[-1])
        if vl < best[0] - 1e-4:
            best = (vl, W.copy(), b.copy(), ep + 1)
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    _, W, b, best_ep = best
    return dict(W=W, b=b, mu=mu, sd=sd), dict(history=hist, best_epoch=best_ep, n=int(N), d=int(D))


# ---------- 評価（動画 1 本の判定を人の判定と突き合わせる） ----------
def video_judgement(codes: list[str], pc: np.ndarray, s: dict) -> list[str]:
    from .detect import aggregate_video
    cls = aggregate_video(pc, codes, float(s["species_p1"]), float(s["species_p2"]), int(s["species_k"]))
    return [c for c, _ in cls["species"]]


def evaluate_items(items: dict, ids: list[int], pred_fn, s: dict) -> dict:
    """ids の対象について、pred_fn(item) → (codes, pc) で動画の判定を作り、人の判定と比べる（agree 1/2/3 と、第 1 種の正答）"""
    from .judge import compare_codes
    n = exact = partial = first_ok = low = 0
    by: dict[str, dict] = {}
    matrix: dict[str, dict[str, int]] = {}
    for iid in ids:
        it = items[iid]
        human = it["human"]
        if not human:
            continue
        codes, pc = pred_fn(it)
        auto = video_judgement(codes, pc, s) if len(pc) else []
        n += 1
        h1 = sorted(human)[0] if len(human) == 1 else next(iter(human))
        d = by.setdefault(h1, dict(n=0, exact=0, first=0, low=0))
        d["n"] += 1
        if not auto:
            low += 1
            d["low"] += 1
            matrix.setdefault(h1, {})["(low)"] = matrix.setdefault(h1, {}).get("(low)", 0) + 1
            continue
        a = compare_codes(human, auto)
        if a == 1:
            exact += 1
            d["exact"] += 1
        if a in (1, 2):
            partial += 1
        if auto[0] in human:
            first_ok += 1
            d["first"] += 1
        matrix.setdefault(h1, {})[auto[0]] = matrix.setdefault(h1, {}).get(auto[0], 0) + 1
    return dict(n=n, exact=exact, partial=partial, first=first_ok, low=low, by_species=dict(sorted(by.items(), key=lambda x: -x[1]["n"])), matrix=matrix)


def base_pred(it: dict) -> tuple[list[str], np.ndarray]:
    return it["codes"], it["pc"]


# ---------- 方式 A の全体 ----------
def run_train_a(project, opts: dict, name: str, progress=None, stop=None, sources: list[str] | None = None) -> dict:
    """集める → 検証用の地点を分けて学習・評価 → 全部で学習し直して保存。返り値は記録（model.json の中身）。
    sources = 材料に加えるほかのプロジェクト（パスの列）"""
    from .detect import project_settings
    s = project_settings(project)
    t0 = time.time()
    if progress:
        progress("collect", 0, 0, "")
    col = collect_all(project, sources, opts, lambda a, b, lab: progress and progress("collect", a, b, lab), stop)
    try:
        return _run_train_a(project, opts, name, progress, stop, s, t0, col)
    finally:
        close_sources(col["projects"][1:])


def _run_train_a(project, opts, name, progress, stop, s, t0, col) -> dict:
    items = col["items"]
    if not items:
        raise RuntimeError("学習に使える対象がありません（確認済みで、検出・種判別が済んでいて、特徴量が保存されているもの）")
    holdout, hb = pick_holdout(items, opts)
    ev_ids = mark_holdout(items, holdout, hb)                 # 検証用 = 検証用の地点の対象 + 検証用の組（gold など）の対象
    ds = build_dataset(items, holdout, opts)
    if len(ds["classes"]) < 2:
        raise RuntimeError(f"学習に入れられる種が {len(ds['classes'])} つしかありません（種ごとに {opts.get('train_min_per_class', 5)} 枚以上の枠が要ります）")
    base_mix = float(opts.get("train_base_mix", 0.3))

    def fit(samples, classes, tag):
        # 学習用の枠のうち 1 割の動画は早めに止めるための検証に（検証用の地点とは別）
        item_ids = sorted({smp[0] for smp in samples})
        rng = random.Random(int(opts.get("train_seed", 1)) + 7)
        rng.shuffle(item_ids)
        n_val = max(1, len(item_ids) // 10) if len(item_ids) >= 20 else 0
        val_ids = set(item_ids[:n_val])
        X, y, w, Xv, yv = [], [], [], [], []
        for iid, k, c, wt in samples:
            f = items[iid]["feats"]
            if f is None or k >= len(f):
                continue
            if iid in val_ids:
                Xv.append(f[k]); yv.append(c)
            else:
                X.append(f[k]); y.append(c); w.append(wt)
        if not X:
            raise RuntimeError("特徴量のある枠がありません（検出のときに「特徴量を保存」が切れていたか、分類器の最終層が見つからなかった）")
        X = np.stack(X); y = np.asarray(y, dtype=np.int64); w = np.asarray(w, dtype=np.float32)
        Xv = np.stack(Xv) if Xv else None; yv = np.asarray(yv, dtype=np.int64) if yv else None
        prm, rec = train_head(X, y, w, len(classes), opts, Xv, yv, lambda e, n, h: progress and progress(tag, e, n, f"loss {h['train_loss']:.3f} / val {h['val_loss']:.3f} ({h['val_acc'] * 100:.0f}%)"), stop)
        return Head(classes, prm["W"], prm["b"], prm["mu"], prm["sd"], base_mix, name), rec

    result = dict(kind="A", name=name, base_classifier=str(next(iter(items.values()))["codes"] and s.get("speciesnet_model")), trained_at=now_iso(),
                  classes=ds["classes"], counts=ds["counts"], dropped=ds["dropped"], n_items=ds["n_items"], n_crops=len(ds["samples"]),
                  n_verified=col["n_verified"], n_nofeat=col["n_nofeat"], holdout_stations=sorted(holdout), holdout_batches=hb, n_holdout=len(ev_ids), base_mix=base_mix,
                  settings={k: opts.get(k, TRAIN_DEFAULTS[k]) for k in TRAIN_DEFAULTS}, species_rule=dict(p1=s["species_p1"], p2=s["species_p2"], k=s["species_k"]),
                  project=source_label(project), sources=[dict(name=x["name"], dir=x["dir"], n_items=x["n_items"]) for x in col["sources"][1:]])
    # 1) 検証用（地点・組）を除いて学習 → 検証用で学習前・後を比べる
    if ev_ids:
        head, rec = fit(ds["samples"], ds["classes"], "train")
        before = evaluate_items(items, ev_ids, base_pred, s)
        after = evaluate_items(items, ev_ids, lambda it: head.apply(it["feats"], it["codes"], it["pc"]), s)
        result.update(eval=dict(before=before, after=after, n_items=len(ev_ids), stations=sorted(holdout), batches=hb), train_record=rec)
        if progress:
            progress("eval", 1, 1, "")
    else:
        result.update(eval=None)
    # 2) 全部で学習し直して保存（「検証用は入れない」か、検証用の組（gold）があるなら、1) で学習したものをそのまま保存 = gold を含まないモデル）
    if stop and stop():
        raise RuntimeError("止めました")
    keep_out = (bool(opts.get("train_final_without_holdout")) or bool(hb)) and bool(ev_ids)
    if keep_out:
        ds_all, head_all, rec_all = ds, head, rec
    else:
        ds_all = build_dataset(items, set(), opts, use_marks=False)
        head_all, rec_all = fit(ds_all["samples"], ds_all["classes"], "train_all")
    result.update(classes=ds_all["classes"], counts=ds_all["counts"], dropped=ds_all["dropped"], n_items=ds_all["n_items"], n_crops=len(ds_all["samples"]), train_record_all=rec_all,
                  final_without_holdout=keep_out)
    # 学習に使った全部（検証用の地点も含む）での一致率も参考に
    all_ids = list(items.keys())
    result["fit_all"] = dict(before=evaluate_items(items, all_ids, base_pred, s), after=evaluate_items(items, all_ids, lambda it: head_all.apply(it["feats"], it["codes"], it["pc"]), s))
    folder = os.path.join(models_root(project), name)
    os.makedirs(folder, exist_ok=True)
    head_all.save(folder)
    result["elapsed"] = round(time.time() - t0, 1)
    with open(os.path.join(folder, "model.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return result


# ---------- 方式 B: 本体の再学習（torch） ----------
def crop_path(project, rel_path: str, k: int) -> str:
    return os.path.join(project.cache_dir("crops"), cache_name(rel_path), f"{k}.jpg")


def crop_image(img, bbox) -> "PIL.Image.Image":
    """SpeciesNet と同じ切り出し（枠を int で切り、480×480 に押し込む）"""
    x, y, w, h = [float(v) for v in bbox]
    W, H = img.width, img.height
    left, top = int(x * W), int(y * H)
    right, bottom = left + int(w * W), top + int(h * H)
    right, bottom = max(right, left + 1), max(bottom, top + 1)
    return img.crop((left, top, right, bottom)).resize((CROP_SIZE, CROP_SIZE))


CROP_BYTES_EST = 70 * 1024         # 枠の画像 1 枚のおおよその大きさ（空きの見積もりに）


def _crop_ok(path: str) -> bool:
    """枠の画像がある（ディスクがいっぱいで途中までしか書けなかった 0 バイトのものは「ない」扱い）"""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _is_enospc(e: BaseException) -> bool:
    return isinstance(e, OSError) and (getattr(e, "errno", None) == 28 or getattr(e, "winerror", None) == 112 or "space" in str(e).lower() or "空き領域" in str(e))


def ensure_crops(project, items: dict, ids: list[int], progress=None, stop=None) -> int:
    """ids の対象の枠を動画から切り出して cache/crops に置く（あるものは飛ばす）。返り値は作った枚数。
    始める前にドライブの空きを見積もり、足りなければ止める（ディスクがいっぱいのまま何千枚も失敗しないように）"""
    from .detect import read_frames, read_image
    # まず足りない枚数を数えて、プロジェクト（ドライブ）ごとに空きを確かめる
    need_of: dict = {}
    per_proj: dict = {}
    for iid in ids:
        it = items[iid]
        pr = it.get("proj") or project
        need = [k for k in range(len(it["t"])) if not _crop_ok(crop_path(pr, it["rel_path"], k))]
        if need:
            need_of[iid] = need
            per_proj[id(pr)] = (pr, per_proj.get(id(pr), (pr, 0))[1] + len(need))
    for pr, n_need in per_proj.values():
        d = pr.cache_dir("crops")
        os.makedirs(d, exist_ok=True)
        free = shutil.disk_usage(d).free
        want = n_need * CROP_BYTES_EST + 256 * 1024 * 1024
        if free < want:
            raise RuntimeError(f"{source_label(pr)} のドライブの空きが足りません（枠の画像 {n_need} 枚に約 {want / 1e9:.1f} GB、空き {free / 1e9:.1f} GB）。"
                               "不要なファイルやキャッシュ（設定 → キャッシュ）を消してからやり直してください")
    made = 0
    for n, iid in enumerate(ids):
        if stop and stop():
            break
        it = items[iid]
        pr = it.get("proj") or project                 # 材料に加えたプロジェクトの対象は、そのプロジェクトの動画と cache/crops
        need = need_of.get(iid)
        if progress:
            progress(n, len(ids), it["rel_path"])
        if not need:
            continue
        src = pr.abs_path(it["rel_path"])
        try:
            if src.lower().endswith((".jpg", ".jpeg", ".png")):
                frames = read_image(src)
            else:
                frames = read_frames(src, sorted({float(it["t"][k]) for k in need}))
        except Exception as e:
            log.warning("枠の切り出しに失敗 %s: %s", it["rel_path"], e)
            continue
        if not frames:
            continue
        ts = np.array([t for t, _ in frames], dtype=np.float32)
        try:
            os.makedirs(os.path.dirname(crop_path(pr, it["rel_path"], 0)), exist_ok=True)
        except OSError as e:
            if _is_enospc(e):
                raise RuntimeError("ドライブの空きがなくなりました（枠の画像を書けません）。不要なファイルやキャッシュ（設定 → キャッシュ）を消してからやり直してください") from e
            raise
        for k in need:
            j = int(np.argmin(np.abs(ts - float(it["t"][k]))))
            dst = crop_path(pr, it["rel_path"], k)
            try:
                crop_image(frames[j][1], it["bbox"][k]).save(dst, "JPEG", quality=90)
                made += 1
            except Exception as e:
                try:
                    os.remove(dst)                      # 途中までの壊れたファイルを残さない
                except OSError:
                    pass
                if _is_enospc(e):
                    raise RuntimeError("ドライブの空きがなくなりました（枠の画像を書けません）。不要なファイルやキャッシュ（設定 → キャッシュ）を消してからやり直してください") from e
                log.warning("枠の保存に失敗 %s #%d: %s", it["rel_path"], k, e)
    return made


class ProjHead:
    """（torch を読んでから作る）MatMul などの射影モジュールを置き換える新しい最終層。forward(x, *rest) で元の重みの引数は無視する"""


def _proj_head_class():
    import torch

    class _ProjHead(torch.nn.Module):
        def __init__(self, d, k):
            super().__init__()
            self.lin = torch.nn.Linear(d, k)

        def forward(self, x, *rest):
            return self.lin(x)

    class _PassThrough(torch.nn.Module):          # 元の最終層のあとのバイアスの Add を素通しにする
        def forward(self, x, *rest):
            return x
    return _ProjHead, _PassThrough


def _parent_of(model, name: str):
    parent = model
    parts = name.split(".")
    for p in parts[:-1]:
        parent = getattr(parent, p)
    return parent, parts[-1]


def find_head(model):
    """新しい最終層（Linear）とその親・属性名。replace_head で差し替えた ProjHead の lin を優先し、なければ最後の nn.Linear"""
    import torch
    last = None
    for name, m in model.named_modules():
        if type(m).__name__ == "_ProjHead":
            return m, "lin", m.lin
        if isinstance(m, torch.nn.Linear):
            last = (name, m)
    if last is None:
        return None
    name, mod = last
    parent, attr = _parent_of(model, name)
    return parent, attr, mod


def replace_head(model, n_classes: int, info: dict | None = None):
    """最終層を種コード数の新しい Linear に差し替える。info は detect.probe_model() の結果。返り値 (新しい Linear, 元に戻す関数)
    - linear: その nn.Linear を差し替える
    - proj（ONNX 変換の MatMul など）: ProjHead（Linear）に差し替え、直後のバイアスの Add（GraphModule の graph から探す）を素通しにする"""
    import torch
    undo: list[tuple] = []
    if info is None or info["kind"] == "linear":
        import torch as _t
        target = None
        if info is not None:
            target = info["name"]
        else:
            for name, m in model.named_modules():
                if isinstance(m, _t.nn.Linear):
                    target = name
        if target is None:
            raise RuntimeError("分類器の最終層（Linear）が見つからないので方式 B は使えません")
        parent, attr = _parent_of(model, target)
        old = getattr(parent, attr)
        new = torch.nn.Linear(old.in_features, n_classes).to(old.weight.device, old.weight.dtype)
        setattr(parent, attr, new)
        undo.append((parent, attr, old))
        return new, lambda: [setattr(p_, a_, o_) for p_, a_, o_ in reversed(undo)]
    if info["kind"] != "proj":
        raise RuntimeError("分類器の最終層が Linear でも MatMul でもないので方式 B は使えません（方式 A は使えます）")
    ProjHeadCls, PassThrough = _proj_head_class()
    dev = next(model.parameters()).device
    dt = next(model.parameters()).dtype
    parent, attr = _parent_of(model, info["name"])
    old = getattr(parent, attr)
    new = ProjHeadCls(int(info["feat_dim"]), n_classes).to(dev, dt)
    setattr(parent, attr, new)
    undo.append((parent, attr, old))
    # 直後のバイアスの Add を素通しに（fx の graph で、この層の出力を使う call_module）
    graph = getattr(model, "graph", None)
    if graph is not None:
        node = next((n for n in graph.nodes if n.op == "call_module" and n.target == info["name"]), None)
        users = list(node.users) if node is not None else []
        for u in users:
            if u.op == "call_module" and type(getattr(model, u.target.split(".")[0], None)).__name__ != "_ProjHead":
                p2, a2 = _parent_of(model, u.target)
                old2 = getattr(p2, a2)
                setattr(p2, a2, PassThrough())
                undo.append((p2, a2, old2))
                break
    else:
        raise RuntimeError("最終層のあとのバイアスを外せないので方式 B は使えません（方式 A は使えます）")
    return new.lin, lambda: [setattr(p_, a_, o_) for p_, a_, o_ in reversed(undo)]


def _load_crop_tensor(path: str, augment: bool, rng: random.Random):
    """JPEG → float [480, 480, 3]（0〜1、NHWC。SpeciesNet の入力と同じ）。学習時は左右反転・明るさ・少しの切り出し"""
    from PIL import Image, ImageEnhance
    img = Image.open(path).convert("RGB")
    if augment:
        if rng.random() < 0.5:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        sc = rng.uniform(0.8, 1.0)
        w, h = img.size
        cw, ch = int(w * sc), int(h * sc)
        left, top = rng.randint(0, w - cw), rng.randint(0, h - ch)
        img = img.crop((left, top, left + cw, top + ch)).resize((CROP_SIZE, CROP_SIZE))
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.8, 1.2))
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.8, 1.2))
    return np.asarray(img, dtype=np.float32) / 255.0


def finetune(model, device: str, samples: list, paths: dict, n_classes: int, opts: dict, val: list | None, progress=None, stop=None) -> tuple[dict, dict]:
    """model（SpeciesNet 本体。最終層は replace_head で差し替え済み）の後ろの層を学習する。
    samples = [(item_id, k, class, weight)]、paths[(item_id, k)] = 枠の JPEG。返り値 (学習した重み {name: tensor}, 記録)"""
    import torch
    rng = random.Random(int(opts.get("train_seed", 1)))
    epochs, bs = int(opts.get("train_epochs_b", 5)), int(opts.get("train_batch_b", 16))
    lr = float(opts.get("train_lr_b", 1e-4))
    frac = float(opts.get("train_unfreeze", 0.3))
    names = [n for n, _ in model.named_parameters()]
    head = find_head(model)[2]
    head_names = {n for n, p in model.named_parameters() if p is head.weight or p is head.bias}
    body = [n for n in names if n not in head_names]
    k = int(round(len(body) * frac))
    train_names = set(body[len(body) - k:]) | head_names
    for n, p in model.named_parameters():
        p.requires_grad = n in train_names
    params_body = [p for n, p in model.named_parameters() if n in train_names and n not in head_names]
    params_head = [p for n, p in model.named_parameters() if n in head_names]
    opt = torch.optim.AdamW([dict(params=params_body, lr=lr), dict(params=params_head, lr=lr * 10)], weight_decay=1e-4)
    cnt = np.bincount([s[2] for s in samples], minlength=n_classes).astype(np.float64)
    cw = torch.tensor(np.sqrt(cnt.sum() / (n_classes * np.maximum(cnt, 1))), dtype=torch.float32, device=device)
    use_amp = device == "cuda"
    scaler = torch.cuda.amp.GradScaler() if use_amp else None
    model.eval()                     # BatchNorm の統計は動かさない（少ないデータで壊さないため）
    hist = []
    best = (float("inf"), None, 0)
    bad, patience = 0, 2

    bad_files: set = set()

    def batch_tensor(lst, augment):
        """枠の画像を読んで [B, 480, 480, 3] に。読めない画像（壊れたファイルなど）は飛ばす（返り値は (x, 読めた分の lst)）"""
        arrs, kept = [], []
        for smp in lst:
            pth = paths[(smp[0], smp[1])]
            try:
                arrs.append(_load_crop_tensor(pth, augment, rng))
                kept.append(smp)
            except Exception as e:
                if pth not in bad_files:
                    bad_files.add(pth)
                    log.warning("枠の画像を読めません %s: %s", pth, e)
        if not arrs:
            return None, []
        return torch.from_numpy(np.stack(arrs)).to(device), kept

    def val_loss():
        if not val:
            return None, None
        tot = correct = n = 0
        with torch.no_grad():
            for i in range(0, len(val), bs):
                x, lst = batch_tensor(val[i:i + bs], False)
                if x is None:
                    continue
                y = torch.tensor([c for _, _, c, _ in lst], device=device)
                with torch.autocast(device_type="cuda", enabled=use_amp):
                    z = model(x).float()
                tot += float(torch.nn.functional.cross_entropy(z, y, reduction="sum"))
                correct += int((z.argmax(1) == y).sum())
                n += len(lst)
        return tot / max(n, 1), correct / max(n, 1)

    steps = 0
    if progress:
        progress(0, epochs, "準備中")
    for ep in range(epochs):
        if stop and stop():
            break
        order = list(samples)
        rng.shuffle(order)
        tot = n = 0
        for i in range(0, len(order), bs):
            if stop and stop():
                break
            x, lst = batch_tensor(order[i:i + bs], True)
            if x is None:
                continue
            y = torch.tensor([c for _, _, c, _ in lst], device=device)
            w = torch.tensor([wt for _, _, _, wt in lst], dtype=torch.float32, device=device) * cw[y]
            opt.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", enabled=use_amp):
                z = model(x).float()
                loss = (torch.nn.functional.cross_entropy(z, y, reduction="none") * w).sum() / w.sum()
            if scaler:
                scaler.scale(loss).backward(); scaler.step(opt); scaler.update()
            else:
                loss.backward(); opt.step()
            tot += float(loss.detach()) * len(lst); n += len(lst)
            steps += 1
            if progress and (steps <= 10 or steps % 5 == 0):          # 最初はこまめに（動いていることが分かるように）
                progress(ep + 1, epochs, f"{i + len(lst)} / {len(order)}  loss {tot / max(n, 1):.3f}")
        vl, va = val_loss()
        h = dict(epoch=ep + 1, train_loss=round(tot / max(n, 1), 4), val_loss=round(vl, 4) if vl is not None else None, val_acc=round(va, 4) if va is not None else None)
        hist.append(h)
        if progress:
            progress(ep + 1, epochs, f"loss {h['train_loss']:.3f}" + (f" / val {vl:.3f} ({va * 100:.0f}%)" if vl is not None else ""))
        score = vl if vl is not None else h["train_loss"]
        if score < best[0] - 1e-4:
            best = (score, {n_: p.detach().cpu().clone() for n_, p in model.named_parameters() if n_ in train_names}, ep + 1)
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    if best[1] is None:
        best = (0.0, {n_: p.detach().cpu().clone() for n_, p in model.named_parameters() if n_ in train_names}, len(hist))
    # 一番よかった重みに戻す
    with torch.no_grad():
        for n_, p in model.named_parameters():
            if n_ in best[1]:
                p.copy_(best[1][n_].to(p.device))
    for p in model.parameters():
        p.requires_grad = False
    return best[1], dict(history=hist, best_epoch=best[2], n=len(samples), trained_params=sorted(train_names))


def classify_crops(model, device: str, paths: list[str], bs: int = 16) -> np.ndarray:
    """枠の画像 → 種の確率 [n, K]。読めない画像の行はゼロ（どの種にもならない）"""
    import torch
    out = []
    with torch.no_grad():
        for i in range(0, len(paths), bs):
            arrs, ok = [], []
            for j, p in enumerate(paths[i:i + bs]):
                try:
                    arrs.append(_load_crop_tensor(p, False, random.Random(0)))
                    ok.append(j)
                except Exception as e:
                    log.warning("枠の画像を読めません %s: %s", p, e)
            if not arrs:
                out.append(None)
                continue
            x = torch.from_numpy(np.stack(arrs)).to(device)
            z = torch.softmax(model(x).float().cpu(), dim=-1).numpy()
            full = np.zeros((len(paths[i:i + bs]), z.shape[1]), dtype=np.float32)
            full[ok] = z
            out.append(full)
    k = next((o.shape[1] for o in out if o is not None), 0)
    out = [o if o is not None else np.zeros((bs, k), dtype=np.float32) for o in out]
    return np.concatenate(out, axis=0)[:len(paths)] if out else np.zeros((0, 0), dtype=np.float32)


def apply_finetuned(model, state: dict) -> None:
    """保存した重み（学習した層と最終層）を model に入れる"""
    import torch
    with torch.no_grad():
        own = dict(model.named_parameters())
        for n, v in state.items():
            if n in own:
                own[n].copy_(v.to(own[n].device, own[n].dtype))


def run_train_b(project, hub, opts: dict, name: str, progress=None, stop=None, init_from: str | None = None, sources: list[str] | None = None) -> dict:
    """方式 B: 枠を切り出し → 検証用の地点を分けて学習・評価 → 全部で学習し直して保存。
    init_from を渡すと、その方式 B のモデル（hub に付けてある前提）の重みから続けて学習する（最終層は種の顔ぶれが変わるので新しく作る）。
    sources = 材料に加えるほかのプロジェクト（パスの列。枠の画像はそれぞれのプロジェクトの cache/crops に置く）"""
    import torch
    from .detect import project_settings
    s = project_settings(project)
    t0 = time.time()
    init_info, init_state = None, {}
    if init_from:
        init_info = model_info(project, init_from)
        if init_info.get("kind") != "B":
            raise RuntimeError(f"出発点にできるのは方式 B のモデルだけです: {init_from}")
        if (init_info.get("base_classifier") or "") != (s.get("speciesnet_model") or "") and init_info.get("base_classifier") and s.get("speciesnet_model"):
            raise RuntimeError(f"出発点のモデル {init_from} は別の SpeciesNet（{init_info.get('base_classifier')}）で学習したものです")
        if not (hub.local and hub.local.get("name") == init_from and hub.local.get("kind") == "B"):
            raise RuntimeError(f"出発点のモデル {init_from} が読み込まれていません")
        init_state = torch.load(os.path.join(model_folder(project, init_from), "finetune.pt"), map_location="cpu", weights_only=False)["state"]
    col = collect_all(project, sources, opts, lambda a, b, lab: progress and progress("collect", a, b, lab), stop)
    try:
        return _run_train_b(project, hub, opts, name, progress, stop, init_from, s, t0, init_info, init_state, col)
    finally:
        close_sources(col["projects"][1:])


def _run_train_b(project, hub, opts, name, progress, stop, init_from, s, t0, init_info, init_state, col) -> dict:
    import torch
    items = col["items"]
    if not items:
        raise RuntimeError("学習に使える対象がありません（確認済みで、検出・種判別が済んでいるもの）")
    holdout, hb = pick_holdout(items, opts)
    ev_ids = mark_holdout(items, holdout, hb)
    ds = build_dataset(items, holdout, opts)
    ds_all = build_dataset(items, set(), opts, use_marks=False)
    if len(ds_all["classes"]) < 2:
        raise RuntimeError(f"学習に入れられる種が {len(ds_all['classes'])} つしかありません")
    # 枠の切り出し（学習に使う対象と、検証用の対象）
    need_ids = sorted({smp[0] for smp in ds_all["samples"]} | set(ev_ids))
    ensure_crops(project, items, need_ids, lambda a, b, c: progress and progress("crops", a, b, c), stop)
    if stop and stop():
        raise RuntimeError("止めました")
    device = hub.ensure_device()
    base_model = hub.classifier.model
    base_state = {n: p.detach().cpu().clone() for n, p in base_model.named_parameters()}
    if hub._feat_info is None:
        raise RuntimeError("分類器の最終層が見つからないので方式 B は使えません")
    undos: list = []

    def restore():
        while undos:
            undos.pop()()
        with torch.no_grad():
            for n, p in base_model.named_parameters():
                if n in base_state:
                    p.copy_(base_state[n].to(p.device, p.dtype))
        for p in base_model.parameters():
            p.requires_grad = False
        base_model.eval()

    def paths_of(samples):
        d = {}
        for iid, k, _, _ in samples:
            pth = crop_path(items[iid].get("proj") or project, items[iid]["rel_path"], k)
            if os.path.isfile(pth):
                d[(iid, k)] = pth
        return d

    def fit(dsx, tag):
        classes = dsx["classes"]
        paths = paths_of(dsx["samples"])
        samples = [smp for smp in dsx["samples"] if (smp[0], smp[1]) in paths]
        if not samples:
            raise RuntimeError("枠の画像がありません（動画を読めなかった可能性）")
        item_ids = sorted({smp[0] for smp in samples})
        rng = random.Random(int(opts.get("train_seed", 1)) + 7)
        rng.shuffle(item_ids)
        n_val = max(1, len(item_ids) // 10) if len(item_ids) >= 20 else 0
        val_ids = set(item_ids[:n_val])
        tr = [smp for smp in samples if smp[0] not in val_ids]
        va = [smp for smp in samples if smp[0] in val_ids]
        restore()
        _, undo = replace_head(base_model, len(classes), hub._feat_info)
        undos.append(undo)
        state, rec = finetune(base_model, device, tr, paths, len(classes), opts, va, lambda e, n, msg: progress and progress(tag, e, n, msg), stop)
        return classes, state, rec

    result = dict(kind="B", name=name, base_classifier=s.get("speciesnet_model"), trained_at=now_iso(), holdout_stations=sorted(holdout), holdout_batches=hb, n_holdout=len(ev_ids),
                  n_verified=col["n_verified"], settings={k: opts.get(k, TRAIN_DEFAULTS[k]) for k in TRAIN_DEFAULTS}, species_rule=dict(p1=s["species_p1"], p2=s["species_p2"], k=s["species_k"]))
    if init_from:
        result.update(init_from=init_from, init_chain=list(init_info.get("init_chain") or []) + [init_from], init_project=init_info.get("project"))
    result.update(project=source_label(project), sources=[dict(name=x["name"], dir=x["dir"], n_items=x["n_items"]) for x in col["sources"][1:]])
    try:
        if ev_ids and len(ds["classes"]) >= 2:
            classes, state, rec = fit(ds, "train")

            def pred(it):
                pths = [crop_path(it.get("proj") or project, it["rel_path"], k) for k in range(len(it["t"]))]
                ok = [p for p in pths if os.path.isfile(p)]
                if not ok:
                    return classes, np.zeros((0, len(classes)), dtype=np.float32)
                return classes, classify_crops(base_model, device, ok, int(opts.get("train_batch_b", 16)))
            before = evaluate_items(items, ev_ids, base_pred, s)
            after = evaluate_items(items, ev_ids, pred, s)
            result.update(eval=dict(before=before, after=after, n_items=len(ev_ids), stations=sorted(holdout), batches=hb), train_record=rec)
        else:
            result.update(eval=None)
        if stop and stop():
            raise RuntimeError("止めました")
        keep_out = (bool(opts.get("train_final_without_holdout")) or bool(hb)) and bool(ev_ids) and len(ds["classes"]) >= 2
        if keep_out:                                   # 検証用を除いて学習したもの（いま本体に入っている重み）をそのまま保存する（gold を含まないモデル）
            rec_all = rec
            ds_all = ds
        else:
            classes, state, rec_all = fit(ds_all, "train_all")
        result.update(classes=classes, counts=ds_all["counts"], dropped=ds_all["dropped"], n_items=ds_all["n_items"], n_crops=len(ds_all["samples"]), train_record_all=rec_all,
                      final_without_holdout=keep_out)
        # 全部（検証用の地点も含む）での一致率も参考に（枠の画像があるものだけ）
        all_ids = list(items.keys())
        ensure_crops(project, items, all_ids, lambda a, b, c: progress and progress("crops", a, b, c), stop)

        def pred_all(it):
            pths = [crop_path(it.get("proj") or project, it["rel_path"], k) for k in range(len(it["t"]))]
            ok = [p for p in pths if os.path.isfile(p)]
            if not ok:
                return classes, np.zeros((0, len(classes)), dtype=np.float32)
            return classes, classify_crops(base_model, device, ok, int(opts.get("train_batch_b", 16)))
        result["fit_all"] = dict(before=evaluate_items(items, all_ids, base_pred, s), after=evaluate_items(items, all_ids, pred_all, s))
        folder = os.path.join(models_root(project), name)
        os.makedirs(folder, exist_ok=True)
        # 出発点のモデルから続けた場合: 今回動かさなかった層は出発点の重みのままなので、それも一緒に保存する（元の SpeciesNet に載せて再現できるように）
        full_state = dict(init_state)
        full_state.update(state)
        torch.save(dict(state=full_state, classes=classes), os.path.join(folder, "finetune.pt"))
        result["elapsed"] = round(time.time() - t0, 1)
        with open(os.path.join(folder, "model.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
    finally:
        restore()
    return result


# ---------- 学習したモデルを判定に使う ----------
def load_local(project, hub, name: str) -> dict:
    """models/<name> を hub に付ける。方式 A は hub.head、方式 B は本体の重みを差し替える（hub.local に記録）"""
    folder = model_folder(project, name)
    if folder is None:
        raise FileNotFoundError(f"追加学習のモデルがありません: {name}")
    info = model_info(project, name)
    if info.get("kind") == "A":
        hub.head = Head.load(folder, info)
        hub.local = dict(name=name, kind="A")
    else:
        import torch
        d = torch.load(os.path.join(folder, "finetune.pt"), map_location="cpu", weights_only=False)
        model = hub.classifier.model
        replace_head(model, len(d["classes"]), hub._feat_info)
        apply_finetuned(model, d["state"])
        model.eval()
        for p in model.parameters():
            p.requires_grad = False
        hub.head = None
        hub.local = dict(name=name, kind="B", classes=list(d["classes"]))
        hub.labels_local = list(d["classes"])
        hub._install_feature_hook(model, find_head(model)[2])
    return info


def fold_disabled(project, codes: list[str], pc: np.ndarray) -> tuple[list[str], np.ndarray]:
    """種リストで使わない種（enabled でない。固定項目は除く）の列を「不明」にまとめる"""
    from .species import FIXED_CODES
    enabled = {r["code"] for r in project.species.rows if r.get("enabled")} | set(FIXED_CODES)
    if all(c in enabled for c in codes):
        return codes, pc
    out: list[str] = []
    cols: dict[str, list[int]] = {}
    for j, c in enumerate(codes):
        key = c if c in enabled else "unknown"
        if key not in cols:
            cols[key] = []
            out.append(key)
        cols[key].append(j)
    new = np.zeros((len(pc), len(out)), dtype=np.float32)
    for i, key in enumerate(out):
        new[:, i] = pc[:, cols[key]].sum(axis=1)
    return out, new


def reclassify_item(project, hub, item: dict, s: dict) -> dict | None:
    """保存した特徴量（方式 A）または枠の画像（方式 B）から、動画の種判定だけを付け直す（動画は読み直さない。検出の結果はそのまま）。
    返り値は process_item の _finish と同じ要約（None なら特徴量がない）"""
    from .detect import finish_item, aggregate_video
    fp = features_path(project, item["rel_path"])
    if not os.path.isfile(fp):
        return None
    with np.load(fp, allow_pickle=False) as z:
        feats = z["feats"].astype(np.float32) if "feats" in z else None
        codes = [str(c) for c in z["codes"]]
        pc = z["pc"].astype(np.float32)
        t = z["t"].astype(np.float32)
        bbox = z["bbox"].astype(np.float32)
        conf = z["conf"].astype(np.float32)
    if len(pc) == 0:
        return None
    if hub.local and hub.local.get("kind") == "B":
        it = dict(rel_path=item["rel_path"], t=t, bbox=bbox)
        ensure_crops(project, {item["id"]: it}, [item["id"]])
        pths = [crop_path(project, item["rel_path"], k) for k in range(len(t))]
        if not all(_crop_ok(p) for p in pths):
            return None
        codes2, pc2 = list(hub.local["classes"]), classify_crops(hub.classifier.model, hub.ensure_device(), pths, int(hub.settings.get("batch_size") or 8))
    elif hub.head is not None:
        codes2, pc2 = hub.head.apply(feats, codes, pc)
    else:
        codes2, pc2 = codes, pc
    codes2, pc2 = fold_disabled(project, codes2, pc2)         # 種リストで「使わない」にした種は「不明」に寄せる（付け直しでいまの種リストを反映）
    cls = aggregate_video(pc2, codes2, float(s["species_p1"]), float(s["species_p2"]), int(s["species_k"]))
    per_crop = []
    for n in range(len(pc2)):
        j = int(np.argmax(pc2[n]))
        per_crop.append(dict(t=float(t[n]), code=codes2[j], p=round(float(pc2[n, j]), 3), conf=round(float(conf[n]), 3)))
    summary = json.loads(project.db.q1("SELECT det_json FROM items WHERE id=?", (item["id"],))["det_json"] or "{}")
    if not summary:
        return None
    cls.update(per_crop=per_crop, model=hub.cls_label(), n_crops=len(pc2), at=now_iso(), geofence=summary.get("geofence", ""))
    # 枠ごとの種（重ね描き用）も付け直す
    rows = project.db.q("SELECT id, t_sec, x, y, w, h FROM detections WHERE item_id=? AND category='animal'", (item["id"],))
    ups = []
    for n in range(len(pc2)):
        best = None
        for r in rows:
            dd = abs(r["t_sec"] - float(t[n])) + abs(r["x"] - float(bbox[n][0])) + abs(r["y"] - float(bbox[n][1])) + abs(r["w"] - float(bbox[n][2])) + abs(r["h"] - float(bbox[n][3]))
            if dd < 0.01 and (best is None or dd < best[0]):
                best = (dd, r["id"])
        if best:
            ups.append((per_crop[n]["code"], per_crop[n]["p"], best[1]))
    if ups:
        project.db.xmany("UPDATE detections SET species=?, sp_conf=? WHERE id=?", ups)
    return finish_item(project, hub, item, summary, cls, bool(summary.get("animal")))
