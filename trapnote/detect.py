"""第 2 段階: MegaDetector による検出と SpeciesNet による種判別

流れ（1 本の動画）
  1. フレームを抜き出す（最初の first_sec 秒は first_fps、以降 interval_sec ごと。OpenCV で順に読む）
  2. MegaDetector を当て、animal / person / vehicle の枠を detections 表に残す（画面の重ね描きに使う）
  3. animal の枠（信頼度の高い順に max_crops 枚）を切り出して SpeciesNet の分類器に入れ、約 2,000 のラベルの確率を
     種リストのコードに集める（species.csv の speciesnet 列の学名で対応。残りは「不明」）
  4. 動画 1 本の判定に集約（第 1 種 = p1 以上の枠が最多の種、第 2 種以降 = p2 以上の枠が K 枚以上）。
     どの種も p1 に届かなければ判定を付けず「要確認」の印（flag = low）
  5. 枠ごとの特徴量（分類器の最終層の手前）を cache/features に保存（第 3 段階の追加学習用）

モデルは最初に使うときに読み込み、メモリに持ち続ける。torch などは必要になるまで import しない（起動を遅くしないため）。
モデルファイルは data/models（設定 models_dir）に置く。MegaDetector は GitHub から、SpeciesNet は kagglehub（または hf）から初回に取る。
TRAPNOTE_FAKE_ML=1 で、モデルなしの偽物（試験用）に置き換わる"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import statistics
import threading
import time
import urllib.request

import numpy as np

from .db import N_BOXES_SQL_ONE, now_iso
from .paths import cache_name
from .project import PROJECT_DEFAULTS

log = logging.getLogger(__name__)
DET_FLOOR = 0.1            # これ未満の枠は残さない
CATEGORY = {"1": "animal", "2": "person", "3": "vehicle"}
MD_URL_BASE = "https://github.com/agentmorris/MegaDetector/releases/download/"
MD_MODELS = {              # 表示名 → (ファイル名, URL, md5, 説明)
    "MDv1000-redwood": ("md_v1000.0.0-redwood.pt", MD_URL_BASE + "v1000.0/md_v1000.0.0-redwood.pt", "74474b3aec9cf1a990da38b37ddf9197", "v1000 redwood（精度。GPU 向け。約 200 MB）"),
    "MDv1000-spruce": ("md_v1000.0.0-spruce.pt", MD_URL_BASE + "v1000.0/md_v1000.0.0-spruce.pt", "1c9d1d2b3ba54931881471fdd508e6f2", "v1000 spruce（軽量。CPU でも速い。14 MB）"),
    "MDv1000-larch": ("md_v1000.0.0-larch.pt", MD_URL_BASE + "v1000.0/md_v1000.0.0-larch.pt", "cab94ebd190c2278e12fb70ffd548b6d", "v1000 larch（中間）"),
    "MDv1000-cedar": ("md_v1000.0.0-cedar.pt", MD_URL_BASE + "v1000.0/md_v1000.0.0-cedar.pt", "3d6472c9b95ba687b59ebe255f7c576b", "v1000 cedar（中間）"),
    "MDv1000-sorrel": ("md_v1000.0.0-sorrel.pt", MD_URL_BASE + "v1000.0/md_v1000.0.0-sorrel.pt", "4339a2c8af7a381f18ded7ac2a4df03e", "v1000 sorrel（軽量）"),
    "MDv5a": ("md_v5a.0.1.pt", MD_URL_BASE + "v5.0/md_v5a.0.1.pt", None, "v5a（従来。areacheck と同じ系統。280 MB）"),
    "MDv5b": ("md_v5b.0.1.pt", MD_URL_BASE + "v5.0/md_v5b.0.1.pt", None, "v5b（280 MB）"),
}
MD_CHOICES = [("auto", "自動（GPU なら MDv1000-redwood、なければ MDv1000-spruce）")] + [(k, v[3]) for k, v in MD_MODELS.items()]
SPECIESNET_DEFAULT = PROJECT_DEFAULTS["speciesnet_model"]


# ---------- 環境 ----------
def env_status(settings=None, models_dir: str | None = None) -> dict:
    """torch などが入っているか（import せずに調べる）。読み込み済みのモデルと機器"""
    def has(name):
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False
    st = dict(torch=has("torch"), megadetector=has("megadetector"), speciesnet=has("speciesnet"), ultralytics=has("ultralytics"),
              yolov5=has("yolov5"), kagglehub=has("kagglehub"), fake=bool(os.environ.get("TRAPNOTE_FAKE_ML")))
    st["ok"] = st["fake"] or (st["torch"] and st["megadetector"] and st["speciesnet"])
    st["dill"] = has("dill")
    st["torch_info"] = torch_info() if st["torch"] and not st["fake"] else None
    hub = ModelHub.instance
    st["loaded"] = dict(detector=hub.detector_name if hub and hub.detector else None, classifier=hub.classifier_name if hub and hub.classifier else None,
                        device=hub.device if hub else None, device_name=hub.device_name if hub else None, loading=hub.loading if hub else "",
                        feat_dim=(hub._feat_info or {}).get("feat_dim") if hub and hub.classifier else None,
                        feat_layer=(hub._feat_info or {}).get("name") if hub and hub.classifier else None,
                        local=dict(hub.local) if hub and hub.local else None)
    st["models_dir"] = models_dir
    st["md_files"] = {k: os.path.isfile(os.path.join(models_dir, v[0])) for k, v in MD_MODELS.items()} if models_dir else {}
    st["speciesnet_cached"] = bool(models_dir) and os.path.isdir(os.path.join(models_dir, "kagglehub"))
    st["md_choices"] = MD_CHOICES
    return st


_torch_info: dict | None = None


def torch_info() -> dict:
    """torch の版と CUDA の有無（最初に呼んだときに import して覚える。1〜3 秒）"""
    global _torch_info
    if _torch_info is None:
        try:
            import torch
            cuda = bool(torch.cuda.is_available())
            _torch_info = dict(version=torch.__version__, cuda=cuda, cuda_version=getattr(torch.version, "cuda", None),
                               gpu=torch.cuda.get_device_name(0) if cuda else None,
                               mps=bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()))
        except Exception as e:
            _torch_info = dict(version=None, cuda=False, cuda_version=None, gpu=None, mps=False, error=str(e))
    return _torch_info


def probe_device(pref: str = "auto") -> tuple[str, str]:
    """(device, 表示名)。pref: auto / cuda / mps / cpu"""
    try:
        import torch
    except Exception:
        return "cpu", "CPU（torch なし）"
    if pref in ("auto", "cuda") and torch.cuda.is_available():
        return "cuda", f"GPU (cuda · {torch.cuda.get_device_name(0)})"
    if pref in ("auto", "mps") and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps", "GPU (Apple MPS)"
    return "cpu", "CPU"


def download(url: str, dst: str, progress=None, md5: str | None = None, stop=None) -> str:
    """URL を dst に落とす（途中は .part）。progress(done_bytes, total_bytes)"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    part = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "TrapNote"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        h = hashlib.md5()
        while True:
            if stop is not None and stop():
                raise InterruptedError("中止")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if md5 and h.hexdigest() != md5:
        os.remove(part)
        raise RuntimeError("ダウンロードしたファイルの md5 が合いません（途中で切れた可能性）")
    os.replace(part, dst)
    return dst


# ---------- フレームの抜き出し ----------
def frame_times(duration: float | None, first_sec: float, first_fps: float, interval: float) -> list[float]:
    d = float(duration or 0)
    if d <= 0:
        return [0.0]
    out = []
    t = 0.0
    step1 = 1.0 / max(first_fps, 0.1)
    while t < min(first_sec, d):
        out.append(round(t, 3))
        t += step1
    t = round(max(first_sec, t), 3)
    while t < d:
        out.append(round(t, 3))
        t += max(interval, 0.05)
    return out or [0.0]


def read_frames(path: str, times: list[float], fps_hint: float | None = None):
    """指定した秒に近いフレームを PIL で返す（[(t_sec, PIL.Image)]）。OpenCV で順に読む（MJPEG の AVI も速い）"""
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise IOError("動画を開けません")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or float(fps_hint or 0) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    targets = sorted({max(0, int(round(t * fps))) for t in times})
    if n > 0:
        targets = [i for i in targets if i < n] or [0]
    out = []
    pos = 0                      # 次に grab() で出てくるフレームの番号
    for idx in targets:
        if idx - pos > 15:       # 離れていれば飛ぶ（MJPEG の AVI は速い。H.264 でも直前のキーフレームから）
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            if pos > idx or pos < 0:
                pos = idx
        ok = True
        while pos < idx and ok:
            ok = cap.grab()
            pos += 1
        if not ok:
            break
        ok, frame = cap.read()
        pos += 1
        if not ok or frame is None:
            break
        out.append((round(idx / fps, 3), Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))))
    cap.release()
    return out


def read_image(path: str):
    from PIL import Image, ImageOps
    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert("RGB")
    return [(0.0, im)]


# ---------- モデル ----------
def probe_model(model, n_labels: int, device: str = "cpu", img_size: int = 480) -> dict | None:
    """分類器に 1 枚（NHWC、ゼロ）を通して各モジュール（葉）の入出力の形を記録し、最終層（特徴量 D → ラベル数 n）を探す。
    SpeciesNet の PyTorch 版は ONNX から onnx2torch で変換した GraphModule で、最終層は Linear（Gemm）のことも MatMul + Add のこともあるため、形で探す。
    返り値: dict(kind, name, module, feat_dim)。kind = linear（nn.Linear の入力が特徴量）/ proj（MatMul などの入力が特徴量）/ feat（そのモジュールの出力が特徴量）"""
    import torch
    rec: list[tuple] = []
    hooks = []
    for name, m in model.named_modules():
        if not name or list(m.children()):
            continue

        def mk(name_, mod_):
            def h(mod, inp, out):
                i0 = inp[0] if inp and torch.is_tensor(inp[0]) else None
                rec.append((name_, mod_, tuple(i0.shape) if i0 is not None else None, tuple(out.shape) if torch.is_tensor(out) else None))
            return h
        hooks.append(m.register_forward_hook(mk(name, m)))
    try:
        with torch.no_grad():
            model(torch.zeros((1, img_size, img_size, 3), dtype=torch.float32, device=device))
    finally:
        for h in hooks:
            h.remove()
    for name, mod, ish, osh in rec:            # (a) 入力 [1, D] → 出力 [1, n] のモジュール（最終層そのもの）
        if osh and len(osh) == 2 and osh[1] == n_labels and ish and len(ish) == 2 and ish[1] != n_labels:
            return dict(kind="linear" if isinstance(mod, torch.nn.Linear) else "proj", name=name, module=mod, feat_dim=int(ish[1]))
    last = None
    for name, mod, ish, osh in rec:            # (b) 出力が 2 次元 [1, D ≠ n] の最後のモジュール（その出力が特徴量）
        if osh and len(osh) == 2 and osh[1] != n_labels:
            last = (name, mod, int(osh[1]))
    if last:
        return dict(kind="feat", name=last[0], module=last[1], feat_dim=last[2])
    return None


class ModelHub:
    """検出器と分類器を 1 つずつ持つ（読み込みは最初に使うとき）"""
    instance: "ModelHub | None" = None

    def __init__(self, settings, models_dir: str):
        self.settings = settings
        self.models_dir = models_dir
        self.lock = threading.Lock()
        self.detector = None
        self.detector_name = None
        self.classifier = None
        self.classifier_name = None
        self.labels: list[str] = []
        self.device = None
        self.device_name = None
        self.loading = ""                 # 画面に出す「読み込み中」の説明
        self._feat_hook = None
        self._feat_buf: list = []
        self._feat_info: dict | None = None    # probe_model() の結果（最終層の場所と特徴量の次元）
        self.geofence_map: dict | None = None     # SpeciesNet の geofence（国ごとに許す・除くラベル）
        self._masks: dict[str, np.ndarray | None] = {}
        self.head = None                  # 第 3 段階 方式 A: 特徴量 → 種コードの分類器（train.Head）
        self.local: dict | None = None    # 使っている追加学習のモデル {name, kind, classes}
        ModelHub.instance = self

    def cls_label(self) -> str:
        """判定の記録に残す分類器の名前（追加学習のモデルを使っていればその名前も）"""
        base = self.classifier_name or "speciesnet"
        return f"{base} + {self.local['name']}" if self.local else base

    def set_local(self, project, classifier: str | None, base_name: str | None = None) -> dict | None:
        """設定 classifier（speciesnet / none / local:<名前>）に合わせて追加学習のモデルを付ける・外す。
        方式 B は本体の重みを差し替えるので、外すとき・替えるときは本体を読み直す。方式 A は本体がなくても付けられる（特徴量だけで判定し直せる）"""
        from . import train
        base_name = base_name or self.classifier_name or SPECIESNET_DEFAULT
        want = classifier[6:] if classifier and classifier.startswith("local:") else None
        if want is None or (self.local and self.local.get("name") != want):
            if self.local and self.local.get("kind") == "B":        # 本体の重みを元に戻す = 読み直す
                self.classifier = self.classifier_name = None
                self.head, self.local = None, None
                self.load_classifier(base_name)
            self.head, self.local = None, None
        if want is None:
            return None
        if self.local and self.local.get("name") == want:
            return self.local
        info = train.model_info(project, want)
        if info.get("kind") == "B" and self.classifier is None:
            self.load_classifier(base_name)
        if self.classifier_name is None:
            self.classifier_name = base_name                        # 方式 A で本体を読まないときも、記録に残す名前は要る
        info = train.load_local(project, self, want)
        log.info("追加学習のモデル %s（方式 %s）を使います", want, info.get("kind"))
        return self.local

    def ensure_device(self):
        if self.device is None:
            self.device, self.device_name = probe_device(self.settings.get("device", "auto"))
        return self.device

    def resolve_md(self, name: str) -> str:
        if name in (None, "", "auto"):
            return "MDv1000-redwood" if self.ensure_device() == "cuda" else "MDv1000-spruce"
        for k in MD_MODELS:
            if name.lower() == k.lower():
                return k
        return name           # ファイルのパス

    def md_file(self, name: str, progress=None, stop=None) -> str:
        """モデルファイルの場所（なければ取ってくる）"""
        if name not in MD_MODELS:
            if os.path.isfile(name):
                return name
            raise FileNotFoundError(f"MegaDetector のモデルがありません: {name}")
        fn, url, md5, _ = MD_MODELS[name]
        dst = os.path.join(self.models_dir, fn)
        if os.path.isfile(dst):
            return dst
        self.loading = f"{name} をダウンロード中"
        log.info("MegaDetector %s を %s から取得します", name, url)
        return download(url, dst, progress, md5, stop)

    def load_detector(self, name: str, progress=None, stop=None):
        real = self.resolve_md(name)
        with self.lock:
            if self.detector is not None and self.detector_name == real:
                return self.detector
            if os.environ.get("TRAPNOTE_FAKE_ML"):
                self.detector, self.detector_name = FakeDetector(), real
                return self.detector
            t0 = time.time()
            path = self.md_file(real, progress, stop)
            self.loading = f"{real} を読み込み中"
            try:
                from megadetector.detection import run_detector
                dev = self.ensure_device()
                det = run_detector.load_detector(path, force_cpu=(dev == "cpu"))
            finally:
                self.loading = ""
            self.detector, self.detector_name = det, real
            log.info("MegaDetector %s を読み込みました（%.1f 秒、%s）", real, time.time() - t0, self.device_name)
            return det

    def load_classifier(self, name: str, progress=None, stop=None):
        name = name or SPECIESNET_DEFAULT
        with self.lock:
            if self.classifier is not None and self.classifier_name == name:
                return self.classifier
            self.head, self.local = None, None            # 本体を読み直す = 追加学習のモデルは外れる（set_local で付け直す）
            if os.environ.get("TRAPNOTE_FAKE_ML"):
                if os.environ.get("TRAPNOTE_FAKE_ML") == "torch":
                    self.classifier, self.classifier_name = FakeTorchClassifier(self.ensure_device()), name
                    self.labels = list(self.classifier.labels)
                    self._install_feature_hook(self.classifier.model)
                else:
                    self.classifier, self.classifier_name = FakeClassifier(), name
                    self.labels = self.classifier.labels
                return self.classifier
            t0 = time.time()
            # モデルの置き場所を data/models の下に（kagglehub と huggingface のキャッシュ）
            os.environ.setdefault("KAGGLEHUB_CACHE", os.path.join(self.models_dir, "kagglehub"))
            os.environ.setdefault("HF_HOME", os.path.join(self.models_dir, "huggingface"))
            self.loading = f"SpeciesNet {name} を取得・読み込み中"
            try:
                from speciesnet.classifier import SpeciesNetClassifier
                dev = self.ensure_device()
                clf = SpeciesNetClassifier(name, device=dev)
            finally:
                self.loading = ""
            self.classifier, self.classifier_name = clf, name
            self.labels = [clf.labels[i] for i in range(len(clf.labels))]
            self._install_feature_hook(clf.model)
            self.geofence_map, self._masks = None, {}
            try:
                gf = getattr(clf.model_info, "geofence", None)
                if gf and os.path.isfile(str(gf)):
                    with open(str(gf), encoding="utf-8") as fh:
                        self.geofence_map = json.load(fh)
                    log.info("geofence を読みました（%d 項目）", len(self.geofence_map))
            except Exception as e:
                log.info("geofence を読めません: %s", e)
            log.info("SpeciesNet %s を読み込みました（%.1f 秒、%d ラベル）", name, time.time() - t0, len(self.labels))
            return clf

    def _install_feature_hook(self, model, head=None) -> None:
        """特徴量（最終層の入力）を拾うフックを付ける。head（Linear）を渡せばその入力。
        渡さなければ probe_model() で最終層を探す（SpeciesNet の PyTorch 版は ONNX から変換した GraphModule で、最終層が Linear とは限らない）"""
        try:
            import torch
            if self._feat_hook is not None:
                try:
                    self._feat_hook.remove()
                except Exception:
                    pass
                self._feat_hook = None
            if head is not None:
                info = dict(kind="linear", name="head", module=head, feat_dim=getattr(head, "in_features", None))
            else:
                self.loading = "分類器の最終層を調べ中"
                try:
                    info = probe_model(model, len(self.labels), self.ensure_device(), getattr(self.classifier, "IMG_SIZE", 480))
                finally:
                    self.loading = ""
                self._feat_info = info
            if info is None:
                log.warning("分類器の最終層が見つからないので特徴量は保存しません（追加学習は使えません）")
                return
            hub = self
            if info["kind"] == "feat":
                def hook(mod, inp, out):
                    hub._feat_buf.append(out.detach().float().cpu().numpy())
            else:
                def hook(mod, inp, out):
                    hub._feat_buf.append(inp[0].detach().float().cpu().numpy())
            self._feat_hook = info["module"].register_forward_hook(hook)
            log.info("特徴量: %s（%s、%s 次元）", info["name"], info["kind"], info.get("feat_dim"))
        except Exception as e:
            log.warning("特徴量のフックを付けられません: %s", e)

    def geofence_mask(self, country: str | None, protect: set | None = None) -> np.ndarray | None:
        """国にいない種のラベルを 0 にするマスク（labels と同じ長さ）。geofence がない・国が空なら None。
        protect = 残すラベルの番号の集合（種リストの具体的な種・属・科に対応するラベル）。外来種（キョン、ハクビシン、アライグマ）は geofence に
        「その国にいない」とされていても種リストにある以上は撮れる種なので除外しない。SpeciesNet にない日本の種（カモシカなど）の確率は近縁種のラベルに
        出るので、属や科で対応させたラベルも残す"""
        country = (country or "").strip().upper()
        if not country or len(country) != 3 or not self.geofence_map or not self.labels:
            return None
        protect = frozenset(protect or ())
        key = (country, protect)
        if key not in self._masks:
            m = np.ones(len(self.labels), dtype=np.float32)
            n = kept = 0
            for i, lab in enumerate(self.labels):
                if should_geofence(lab, country, self.geofence_map):
                    if i in protect:
                        kept += 1
                        continue
                    m[i] = 0.0
                    n += 1
            self._masks[key] = m
            log.info("geofence %s: %d / %d ラベルを除外（種リストに対応するので残した: %d）", country, n, len(self.labels), kept)
        return self._masks[key]

    def unload(self) -> None:
        with self.lock:
            self.detector = self.classifier = None
            self.detector_name = self.classifier_name = None
            self.labels = []
            self.head, self.local = None, None
            try:
                import gc
                gc.collect()
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    # ---- 推論 ----
    def detect(self, images, threshold: float, image_size: int | None = None) -> list[list[dict]]:
        """PIL の列 → 各画像の枠 [{category, conf, bbox[x,y,w,h]}]（正規化座標）"""
        det = self.detector
        if isinstance(det, FakeDetector):
            return det.detect(images, threshold)
        out = []
        bs = max(1, int(self.settings.get("batch_size") or 8))
        for i in range(0, len(images), bs):
            chunk = images[i:i + bs]
            res = det.generate_detections_one_batch(chunk, [f"f{i + k}" for k in range(len(chunk))], detection_threshold=threshold, image_size=image_size or None)
            for r in res:
                if r.get("failure"):
                    log.warning("検出の失敗: %s", r["failure"])
                out.append([d for d in (r.get("detections") or []) if d.get("conf", 0) >= threshold])
        return out

    def classify(self, crops_spec: list[tuple]) -> tuple[np.ndarray, np.ndarray | None]:
        """[(PIL image, bbox[x,y,w,h])] → (確率 [n, labels], 特徴量 [n, D] or None)"""
        clf = self.classifier
        if isinstance(clf, FakeClassifier):
            return clf.classify(crops_spec)
        import torch
        arrs = []
        if isinstance(clf, FakeTorchClassifier):
            from .train import crop_image
            arrs = [np.asarray(crop_image(img, bb), dtype=np.float32) / 255.0 for img, bb in crops_spec]
        else:
            from speciesnet.utils import BBox
            for img, bb in crops_spec:
                pre = clf.preprocess(img, [BBox(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))], resize=True)
                if not pre:            # full_image 型のモデル
                    pre = clf.preprocess(img, None, resize=True)
                arrs.append(pre[0].arr / 255.0)
        n_out = len(self.local["classes"]) if self.local and self.local.get("kind") == "B" else len(self.labels)
        if not arrs:
            return np.zeros((0, n_out), dtype=np.float32), None
        batch = np.stack(arrs, axis=0).astype(np.float32)
        probs, feats = [], []
        bs = max(1, int(self.settings.get("batch_size") or 8))
        with torch.no_grad():
            for i in range(0, len(batch), bs):
                self._feat_buf = []
                x = torch.from_numpy(batch[i:i + bs]).to(self.device)
                logits = clf.model(x).float().cpu()
                probs.append(torch.softmax(logits, dim=-1).numpy())
                if self._feat_buf:
                    feats.append(np.concatenate(self._feat_buf, axis=0))
                del x
        self._feat_buf = []
        p = np.concatenate(probs, axis=0)
        f = np.concatenate(feats, axis=0) if feats and sum(len(x) for x in feats) == len(p) else None
        if f is None and not getattr(self, "_warned_feat", False):
            self._warned_feat = True
            log.warning("特徴量が取れていません（フック: %s、取れた数 %s / %d）。追加学習（方式 A）には特徴量が要ります",
                        "あり" if self._feat_hook is not None else "なし", sum(len(x) for x in feats) if feats else 0, len(p))
        return p, f


class FakeDetector:
    """試験用: 画像の明るさの散らばりから適当に枠を返す（真ん中に 1 枠。のっぺりした画像ほど低い信頼度）"""
    def detect(self, images, threshold):
        out = []
        for im in images:
            a = np.asarray(im.convert("L").resize((64, 64)), dtype=np.float32)
            conf = float(min(0.99, a.std() / 96.0))
            cat = "2" if a.mean() > 200 else "1"
            out.append([dict(category=cat, conf=round(conf, 3), bbox=[0.3, 0.3, 0.4, 0.4])] if conf >= threshold else [])
        return out


class FakeClassifier:
    labels = ["aaa;mammalia;artiodactyla;cervidae;cervus;nippon;sika deer", "bbb;mammalia;artiodactyla;suidae;sus;scrofa;wild boar",
              "ccc;mammalia;carnivora;canidae;nyctereutes;procyonoides;raccoon dog", "ddd;mammalia;rodentia;sciuridae;;;squirrel family",
              "f1856211-cfb7-4a5b-9158-c0f72fd09ee6;;;;;;blank", "990ae9dd-7a59-4344-afcb-1b7b21368000;mammalia;primates;hominidae;homo;sapiens;human",
              "e2895ed5-780b-48f6-8a11-9e27cb594511;;;;;;vehicle"]

    def classify(self, crops_spec):
        n = len(crops_spec)
        p = np.zeros((n, len(self.labels)), dtype=np.float32)
        feats = np.zeros((n, 16), dtype=np.float32)
        rng = np.random.default_rng(0)
        for i, (img, bb) in enumerate(crops_spec):
            a = np.asarray(img.convert("L").resize((32, 32)), dtype=np.float32)
            h = int(a.mean()) % 3
            p[i, h] = 0.8
            p[i, (h + 1) % 3] = 0.15
            p[i, 4] = 0.05
            feats[i] = rng.normal(0, 0.3, 16).astype(np.float32)      # 特徴量は「偽のラベル」と結びつける（方式 A の学習の試験用）
            feats[i, h] += 2.0
            feats[i, 8 + h] += float(a.mean()) / 255.0
        return p, feats


class FakeTorchClassifier:
    """試験用（TRAPNOTE_FAKE_ML=torch）: 小さな torch のモデル（入力は SpeciesNet と同じ NHWC の 480×480）。方式 B の学習の流れを確かめる"""
    labels = FakeClassifier.labels

    def __init__(self, device: str = "cpu"):
        import torch

        class Tiny(torch.nn.Module):
            def __init__(self, n):
                super().__init__()
                self.pool = torch.nn.AdaptiveAvgPool2d(4)
                self.fc1 = torch.nn.Linear(48, 32)
                self.fc2 = torch.nn.Linear(32, n)

            def forward(self, x):
                x = x.permute(0, 3, 1, 2)
                x = self.pool(x).flatten(1)
                return self.fc2(torch.relu(self.fc1(x * 8.0))) * 8.0       # 出力を尖らせる（確率が p1 を超えるように）
        torch.manual_seed(0)
        self.model = Tiny(len(self.labels)).to(device)
        self.model.eval()
        for p in self.model.parameters():
            p.requires_grad = False


# ---------- geofence（SpeciesNet と同じ規則。国の単位だけ） ----------
def should_geofence(label: str, country: str, geofence_map: dict) -> bool:
    """SpeciesNet の geofence_utils.should_geofence_animal_classification と同じ判定（admin1 は使わない）。
    キーは「綱;目;科;属;種」。allow に国があればそれ以外は除く、block に国があれば除く"""
    parts = label.split(";")
    if len(parts) != 7:
        return False
    key = ";".join(parts[1:6])
    rule = geofence_map.get(key)
    if not rule:
        return False
    allow = rule.get("allow")
    if allow and country not in allow:
        return True
    block = rule.get("block")
    if block and country in block and not block[country]:
        return True
    return False


# ---------- ラベル → 種コードの対応 ----------
def _parse_label(label: str) -> dict:
    parts = label.split(";")
    while len(parts) < 7:
        parts.append("")
    return dict(uuid=parts[0], cls=parts[1].strip().lower(), order=parts[2].strip().lower(), family=parts[3].strip().lower(),
                genus=parts[4].strip().lower(), species=parts[5].strip().lower(), common=parts[6].strip().lower())


def build_label_map(labels: list[str], species_rows: list[dict]) -> list[str]:
    """各ラベルが入る種コード（なければ 'unknown'）。species.csv の speciesnet 列（学名。空なら自動では対応しない）で対応。
    2 語なら属＋種、1 語なら属・科・目・綱のどれかとして当て、より具体的な対応を優先する"""
    rules = []       # (code, matcher)
    for r in species_rows:
        if not r.get("enabled"):
            continue
        sn = (r.get("speciesnet") or "").strip().lower()
        sn = sn.split("（")[0].split("(")[0].strip()
        toks = [t for t in sn.replace(",", " ").split() if t]
        if not toks:
            continue
        if len(toks) >= 2:
            rules.append((r["code"], ("binomial", toks[0], toks[1])))
        else:
            rules.append((r["code"], ("taxon", toks[0])))
    # 属・科だけのラベル（"muntiacus species"、"cervidae family"）を、その属・科で唯一の種に寄せるための表
    genus_of_code: dict[str, set] = {}
    family_of_genus: dict[str, str] = {}
    for lab in labels:
        d = _parse_label(lab)
        if d["genus"] and d["family"]:
            family_of_genus[d["genus"]] = d["family"]
    for c, m in rules:
        if m[0] == "binomial":
            genus_of_code.setdefault(m[1], set()).add(c)
    only_in_genus = {g: next(iter(cs)) for g, cs in genus_of_code.items() if len(cs) == 1}
    fam_codes: dict[str, set] = {}
    for g, c in only_in_genus.items():
        if g in family_of_genus:
            fam_codes.setdefault(family_of_genus[g], set()).add(c)
    for c, m in rules:                      # 属で対応させた行（capricornis など）も科の唯一候補に数える
        if m[0] == "taxon" and m[1] in family_of_genus:
            fam_codes.setdefault(family_of_genus[m[1]], set()).add(c)
    only_in_family = {f: next(iter(cs)) for f, cs in fam_codes.items() if len(cs) == 1}
    out = []
    for lab in labels:
        d = _parse_label(lab)
        if d["common"] == "blank":
            out.append("empty")
            continue
        if d["common"] == "human" or (d["genus"] == "homo" and d["species"] == "sapiens"):
            out.append("human")
            continue
        if d["common"] == "vehicle":
            out.append("vehicle")
            continue
        best = None
        for c, m in rules:
            if m[0] == "binomial":
                if d["genus"] == m[1] and d["species"] == m[2]:
                    score = 0
                else:
                    continue
            else:
                tx = m[1]
                if d["genus"] == tx:
                    score = 1
                elif d["family"] == tx:
                    score = 2
                elif d["order"] == tx:
                    score = 3
                elif d["cls"] == tx:
                    score = 4
                else:
                    continue
            if best is None or score < best[0]:
                best = (score, c)
        if best is None and d["genus"] and not d["species"] and d["genus"] in only_in_genus:
            best = (1, only_in_genus[d["genus"]])            # "muntiacus species" → キョン（その属で使う種が 1 つだけ）
        if best is None and d["family"] and not d["genus"] and d["family"] in only_in_family:
            best = (2, only_in_family[d["family"]])          # "cervidae family" → その科で使う種が 1 つだけならそれ
        out.append(best[1] if best else "unknown")
    return out


def aggregate_codes(probs: np.ndarray, label_codes: list[str]) -> tuple[list[str], np.ndarray]:
    """ラベルの確率 [n, labels] → コードの確率 [n, codes]"""
    codes: list[str] = []
    idx: dict[str, int] = {}
    for c in label_codes:
        if c not in idx:
            idx[c] = len(codes)
            codes.append(c)
    M = np.zeros((probs.shape[1], len(codes)), dtype=np.float32)
    for j, c in enumerate(label_codes):
        M[j, idx[c]] = 1.0
    return codes, probs @ M


def aggregate_video(pc: np.ndarray, codes: list[str], p1: float, p2: float, k: int) -> dict:
    """枠ごとの種の確率 [n, codes] から動画の判定へ。species = [(code, confidence)]、probs = コードごとの平均（上位）"""
    if len(pc) == 0:
        return dict(species=[], probs={})
    mean = {c: float(pc[:, j].mean()) for j, c in enumerate(codes)}
    skip = {"empty", "vehicle", "human"}      # 「動物なし」「車」「人」は枠の種としては採らない（検出の方で決める）
    cnt1 = {c: int((pc[:, j] >= p1).sum()) for j, c in enumerate(codes) if c not in skip}
    cnt1 = {c: n for c, n in cnt1.items() if n}
    species = []
    if cnt1:
        first = max(cnt1.items(), key=lambda x: (x[1], mean[x[0]]))[0]
        j = codes.index(first)
        vals = [float(pc[i, j]) for i in range(len(pc)) if int(np.argmax(pc[i])) == j] or [float(pc[:, j].max())]
        species.append((first, round(statistics.median(vals), 3)))
        for jj, c in enumerate(codes):
            if c == first or c in skip:
                continue
            n = int((pc[:, jj] >= p2).sum())
            if n >= k:
                vals = [float(pc[i, jj]) for i in range(len(pc)) if int(np.argmax(pc[i])) == jj] or [float(pc[:, jj].max())]
                species.append((c, round(statistics.median(vals), 3)))
    if len(species) > 1 and species[0][0] == "unknown":   # 「不明」が第 1 種で他にもあれば、他を先に
        species = species[1:] + species[:1]
    top = sorted(mean.items(), key=lambda x: -x[1])[:8]
    je = codes.index("empty") if "empty" in codes else -1
    empty_mean = float(mean["empty"]) if je >= 0 else 0.0
    empty_frac = float((np.argmax(pc, axis=1) == je).mean()) if je >= 0 else 0.0     # 「動物なし」が最大の枠の割合
    return dict(species=species, probs={c: round(v, 4) for c, v in top}, empty_mean=round(empty_mean, 4), empty_frac=round(empty_frac, 3))


# ---------- 1 本の処理 ----------
def project_settings(project) -> dict:
    return {**PROJECT_DEFAULTS, **(project.data.get("settings") or {})}


INTERVAL_GPU, INTERVAL_CPU = 0.2, 0.5


def frame_interval(s: dict, device: str | None) -> float:
    """最初の 1 秒のあとのフレームの間隔（秒）。設定が auto（既定）なら GPU で 0.2、CPU で 0.5"""
    v = s.get("frame_interval_sec", "auto")
    try:
        f = float(v)
        if f > 0:
            return f
    except (TypeError, ValueError):
        pass
    return INTERVAL_GPU if device in ("cuda", "mps") else INTERVAL_CPU


def load_frames(project, item: dict, device: str | None = None, times: list[float] | None = None):
    """対象のフレームを読む（検出の前に別スレッドで先に読んでおける）。device は間隔の自動（GPU 0.2 秒 / CPU 0.5 秒）のため。
    times を渡せばその秒だけ（既存の枠に種判別を付けるとき）"""
    src = project.abs_path(item["rel_path"])
    s = project_settings(project)
    if item["kind"] == "image":
        return read_image(src)
    if times is not None:
        return read_frames(src, sorted(set(float(t) for t in times)), item.get("fps"))
    return read_frames(src, frame_times(item.get("duration"), float(s["frame_first_sec"]), float(s["frame_first_fps"]), frame_interval(s, device)), item.get("fps"))


def existing_boxes(db, item_id: int) -> list[list]:
    """detections 表にある枠（YOLO のラベルなど人が付けたものや前の検出）を [t, category, conf, x, y, w, h] で"""
    return [[r["t_sec"], r["category"], float(r["conf"]), r["x"], r["y"], r["w"], r["h"]]
            for r in db.q("SELECT t_sec, category, conf, x, y, w, h FROM detections WHERE item_id=? ORDER BY t_sec, conf DESC", (item_id,))]


def pick_crops(rows: list, a_th: float, max_crops: int, min_gap: float = 0.6, per_frame: int = 2) -> list[tuple[int, list]]:
    """種判別に入れる動物の枠を選ぶ。信頼度の高い順だが、同じ場面に固まらないように分散させる:
    選んだ枠と min_gap 秒未満しか離れていないフレームの枠は後回し、1 フレームからは per_frame 枠まで。
    それでも max_crops に届かなければ残りから信頼度順に足す（静止画や短い動画では全部の枠を使う）。返り値 [(rows の添字, row)]"""
    cands = sorted([(i, r) for i, r in enumerate(rows) if r[1] == "animal" and r[2] >= a_th], key=lambda x: -x[1][2])
    if len(cands) <= max_crops or min_gap <= 0 and per_frame <= 0:
        return cands[:max_crops]
    chosen: list[tuple[int, list]] = []
    taken: set[int] = set()
    n_in_frame: dict[float, int] = {}
    strong_th = max(a_th, 0.5)                           # 分散させるのは自信のある枠の中で（自信のない枠を場面の数合わせに拾わない）
    for i, r in cands:
        if len(chosen) >= max_crops or r[2] < strong_th:
            break
        t = float(r[0])
        if per_frame > 0 and n_in_frame.get(t, 0) >= per_frame:
            continue
        if min_gap > 0 and any(0 < abs(t - ft) < min_gap for ft in n_in_frame):
            continue
        chosen.append((i, r)); taken.add(i); n_in_frame[t] = n_in_frame.get(t, 0) + 1
    for i, r in cands:                                   # 足りなければ残りから信頼度順に
        if len(chosen) >= max_crops:
            break
        if i not in taken:
            chosen.append((i, r)); taken.add(i)
    return chosen


def process_item(project, hub: ModelHub, item: dict, classify: bool = True, frames=None, use_existing: bool = False) -> dict:
    """検出（と判別）を 1 本について行い、DB に書く。返り値は要約。frames は load_frames() で先に読んだもの（省略可）。
    use_existing なら detections 表にある枠（YOLO のラベルなど）をそのまま使い、検出はせずに種判別と特徴量だけ付ける（枠がなければ普通に検出）"""
    db = project.db
    s = project_settings(project)
    a_th, a_min = float(s["animal_threshold"]), int(s["animal_min_frames"])
    p_th, p_min = float(s["person_threshold"]), int(s["person_min_frames"])
    p1, p2, kk = float(s["species_p1"]), float(s["species_p2"]), int(s["species_k"])
    max_crops = max(1, int(s["max_crops"]))
    t0 = time.time()
    rows = existing_boxes(db, item["id"]) if use_existing else []      # [t, category, conf, x, y, w, h]
    det_model = hub.detector_name
    if rows:
        need = sorted({r[0] for r in rows})
        if frames is None or not all(t in {ft for ft, _ in frames} for t in need):
            frames = load_frames(project, item, hub.device, need)
        if not frames:
            raise RuntimeError("フレームを読めません")
        have = {ft for ft, _ in frames}
        if not all(t in have for t in need):           # 近い時刻のフレームに寄せる（動画の秒がぴったり合わないとき）
            ts = sorted(have)
            for r in rows:
                if r[0] not in have:
                    r[0] = min(ts, key=lambda x: abs(x - r[0]))
        try:
            det_model = json.loads(item.get("det_json") or "{}").get("model") or "existing"
        except Exception:
            det_model = "existing"
    else:
        if frames is None:
            frames = load_frames(project, item, hub.device)
        if not frames:
            raise RuntimeError("フレームを読めません")
        if hub.detector is None:
            hub.load_detector(s.get("md_model") or "auto")
            det_model = hub.detector_name
        floor = min(DET_FLOOR, a_th, p_th)
        dets = hub.detect([im for _, im in frames], floor, int(s.get("detect_image_size") or 0) or None)
        for (t, im), ds in zip(frames, dets):
            for d in ds:
                cat = CATEGORY.get(str(d.get("category")), str(d.get("category")))
                b = d["bbox"]
                rows.append([t, cat, float(d["conf"]), float(b[0]), float(b[1]), float(b[2]), float(b[3])])

    def nframes(cat, th):
        return len({r[0] for r in rows if r[1] == cat and r[2] >= th})
    summary = dict(n_frames=len(frames), animal_frames=nframes("animal", a_th), person_frames=nframes("person", p_th), vehicle_frames=nframes("vehicle", p_th),
                   max_animal=round(max([r[2] for r in rows if r[1] == "animal"], default=0.0), 3),
                   max_person=round(max([r[2] for r in rows if r[1] == "person"], default=0.0), 3),
                   max_vehicle=round(max([r[2] for r in rows if r[1] == "vehicle"], default=0.0), 3),
                   max_area=round(max([r[5] * r[6] for r in rows if r[1] == "animal" and r[2] >= a_th], default=0.0), 4),
                   animal_threshold=a_th, person_threshold=p_th, model=det_model, at=now_iso())
    nf = max(1, len({r[0] for r in rows} | {t for t, _ in frames}))
    summary.update(animal=summary["animal_frames"] >= min(a_min, nf), person=summary["person_frames"] >= min(p_min, nf), vehicle=summary["vehicle_frames"] >= min(p_min, nf))   # 静止画や短い動画はフレーム数で頭打ち
    # 種判別（動物の枠だけ）
    cls = None
    sp_by_idx: dict[int, tuple[str, float]] = {}
    if classify and summary["animal"]:
        cands = pick_crops(rows, a_th, max_crops, float(s.get("crop_min_gap_sec", 0.6)), int(s.get("crop_per_frame", 2)))
        by_t = {t: im for t, im in frames}
        probs, feats = hub.classify([(by_t[r[0]], r[3:7]) for _, r in cands])
        if hub.local and hub.local.get("kind") == "B":
            # 方式 B: 本体を学習し直したモデルは種コードを直接出す（geofence・ラベルの対応は要らない）
            codes, pc, mask = list(hub.local["classes"]), probs, None
        else:
            label_codes = build_label_map(hub.labels, project.species.rows)
            # 種リストの具体的な行（綱・目の行は除く）に対応するラベルは geofence で消さない
            specific = {r["code"] for r in project.species.rows if r.get("enabled") and (r.get("speciesnet") or "").strip() and r.get("rank") not in ("綱", "目")}
            protect = {i for i, c in enumerate(label_codes) if c in specific}
            mask = hub.geofence_mask(s.get("country"), protect)
            if mask is not None:                     # その国にいない種のラベルを除いて正規化し直す
                masked = probs * mask[None, :]
                ssum = masked.sum(axis=1, keepdims=True)
                probs = np.where(ssum > 1e-6, masked / np.maximum(ssum, 1e-6), probs)
            codes, pc = aggregate_codes(probs, label_codes)
            if feats is not None and s.get("save_features", True):      # 特徴量は元のモデルのもの（方式 A の学習に使う）
                fdir = project.cache_dir("features")
                os.makedirs(fdir, exist_ok=True)
                np.savez_compressed(os.path.join(fdir, cache_name(item["rel_path"]) + ".npz"), feats=feats.astype(np.float16),
                                    t=np.array([r[0] for _, r in cands], dtype=np.float32), bbox=np.array([r[3:7] for _, r in cands], dtype=np.float32),
                                    conf=np.array([r[2] for _, r in cands], dtype=np.float32), codes=np.array(codes), pc=pc.astype(np.float16),
                                    model=np.array(hub.classifier_name or ""))
            if hub.head is not None:                  # 方式 A: 追加学習した分類器の確率を混ぜる
                codes, pc = hub.head.apply(feats, codes, pc)
        per_crop = []
        for n, (i, r) in enumerate(cands):
            j = int(np.argmax(pc[n]))
            per_crop.append(dict(t=r[0], code=codes[j], p=round(float(pc[n, j]), 3), conf=round(r[2], 3)))
            sp_by_idx[i] = (codes[j], float(pc[n, j]))
        cls = aggregate_video(pc, codes, p1, p2, kk)
        cls.update(per_crop=per_crop, model=hub.cls_label(), n_crops=len(cands), at=now_iso(), geofence=(s.get("country") or "") if mask is not None else "")
    with db.tx() as c:
        c.execute("DELETE FROM detections WHERE item_id=?", (item["id"],))
        for i, r in enumerate(rows):
            sp = sp_by_idx.get(i, (None, None))
            c.execute("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (item["id"], r[0], r[1], round(r[2], 4), round(r[3], 4), round(r[4], 4), round(r[5], 4), round(r[6], 4),
                       sp[0], round(sp[1], 4) if sp[1] is not None else None))
    db.x(N_BOXES_SQL_ONE, (a_th, item["id"]))
    summary = finish_item(project, hub, item, summary, cls, classify)
    summary["elapsed"] = round(time.time() - t0, 2)
    return summary


def finish_item(project, hub: ModelHub, item: dict, summary: dict, cls: dict | None, classify: bool = True) -> dict:
    """検出の要約 summary と種判別 cls から動画 1 本の判定を決め、items（det_json / cls_json / auto_json / flag）と判定（set_auto）に書く。
    process_item と、特徴量からの判定のやり直し（train.reclassify_item）の両方から使う"""
    db = project.db
    s = project_settings(project)
    a_th = float(s["animal_threshold"])
    # 動画 1 本の判定
    results: list[dict] = []
    flag = None
    p_empty = float(s.get("species_empty_p") or 0)
    if summary["animal"]:
        if cls is None:
            if not classify:
                flag = "low"                       # 検出だけ: 動物がいるが種は人が付ける
        elif cls["species"]:
            results = [dict(species=c, confidence=p) for c, p in cls["species"]]
        elif p_empty > 0 and float(cls.get("empty_mean") or 0) >= p_empty:
            # 枠はあるが分類器が全部の枠を「動物なし」と見た（MegaDetector の誤検出。学習で「動物なし」の枠を覚えさせた後に効く）
            results = [dict(species="empty", confidence=round(float(cls["empty_mean"]), 3))]
        else:
            flag = "low"
    if summary["person"]:
        results.append(dict(species="human", confidence=summary["max_person"]))
    if summary["vehicle"]:
        results.append(dict(species="vehicle", confidence=summary["max_vehicle"]))
    if not results and not summary["animal"]:
        if summary["max_animal"] >= a_th:
            # 閾値以上の動物の枠はある（ただしフレーム数が規則に届かない）: 「動物なし」にはせず「不明」＋要確認
            results = [dict(species="unknown", confidence=summary["max_animal"])]
            flag = "low"
        else:
            results = [dict(species="empty", confidence=round(1.0 - max(summary["max_animal"], summary["max_person"], summary["max_vehicle"]), 3))]
    if results and results[0]["species"] == "unknown":     # 種リストにない種（分類器は何かを出したが対応がない）も要確認
        flag = "low"
    # CSV / Timelapse で人が付けた判定（origin csv / manual）があるときは、モデルの出力は記録（auto_results）だけにして判定も要確認の印も付けない
    human = db.q1("SELECT 1 FROM judgements WHERE item_id=? AND origin IN ('csv','manual') LIMIT 1", (item["id"],)) is not None
    if human and flag == "low":
        flag = None
    # 書き込み
    with db.tx() as c:
        model_tag = hub.cls_label() + " + " + (hub.detector_name or summary.get("model") or "md") if cls is not None else (hub.detector_name or summary.get("model") or "md")
        auto = dict(model=model_tag, species=[dict(species=r["species"], confidence=r.get("confidence")) for r in results], low=flag == "low", at=now_iso())
        c.execute("UPDATE items SET det_state=1, det_json=?, cls_state=?, cls_json=?, auto_json=?, meta_error=NULL, "
                  "flag=CASE WHEN status='verified' OR flag='delete' THEN flag ELSE ? END, updated_at=? WHERE id=?",
                  (json.dumps(summary, ensure_ascii=False), 1 if cls is not None else 0, json.dumps(cls, ensure_ascii=False) if cls else None,
                   json.dumps(auto, ensure_ascii=False), flag, now_iso(), item["id"]))
    if human:
        if results:
            from .judge import set_auto
            set_auto(db, item["id"], results, model_tag, keep_origins=("csv", "manual"))
    elif results:
        from .judge import set_auto
        set_auto(db, item["id"], results, model_tag)
    elif flag == "low":
        # 自信がないときは、前の回のモデルの判定（origin auto）は消す（CSV 取り込みと確認済みは残す）
        with db.tx() as c:
            st = c.execute("SELECT status FROM items WHERE id=?", (item["id"],)).fetchone()
            if st and st["status"] != "verified":
                c.execute("DELETE FROM auto_results WHERE item_id=? AND model=?", (item["id"], model_tag))
                c.execute("DELETE FROM field_values WHERE item_id=? AND judgement_id IN (SELECT id FROM judgements WHERE item_id=? AND origin='auto')", (item["id"], item["id"]))
                c.execute("DELETE FROM judgements WHERE item_id=? AND origin='auto'", (item["id"],))
                left = c.execute("SELECT COUNT(*) FROM judgements WHERE item_id=?", (item["id"],)).fetchone()[0]
                c.execute("UPDATE items SET status=?, updated_at=? WHERE id=?", ("auto" if left else "new", now_iso(), item["id"]))
    from .judge import update_agree
    update_agree(db, [item["id"]])
    summary["results"] = results
    summary["flag"] = flag
    return summary


def save_boxes(project, item_id: int, boxes: list[dict]) -> dict:
    """画面で編集した枠で detections 表を置き換える（boxes = [{t, c, p, x, y, w, h, sp, sp_p}]）。
    det_json の要約を数え直し（model に (edited) を付ける）、種判別と特徴量は付け直しが要る状態にする（cls_state = 0、cache の特徴量と枠の画像を消す）"""
    from .paths import cache_name
    db = project.db
    it = db.q1("SELECT rel_path, det_json FROM items WHERE id=?", (item_id,))
    if it is None:
        raise ValueError("対象がありません")
    s = project_settings(project)
    a_th, p_th = float(s["animal_threshold"]), float(s["person_threshold"])
    try:
        old = json.loads(it["det_json"] or "{}")
    except Exception:
        old = {}
    rows = []
    for b in boxes:
        try:
            cat = str(b.get("c") or "animal")
            if cat not in ("animal", "person", "vehicle"):
                cat = "animal"
            x, y, w, h = (max(0.0, min(1.0, float(b.get(k, 0)))) for k in ("x", "y", "w", "h"))
            if w <= 0 or h <= 0:
                continue
            rows.append((item_id, round(float(b.get("t") or 0.0), 3), cat, round(max(0.0, min(1.0, float(b.get("p", 1.0) if b.get("p") is not None else 1.0))), 4),
                         round(x, 4), round(y, 4), round(min(w, 1.0 - x), 4), round(min(h, 1.0 - y), 4), b.get("sp") or None, float(b["sp_p"]) if b.get("sp_p") is not None else None))
        except (TypeError, ValueError):
            continue
    n_frames = int(old.get("n_frames") or 0) or max(1, len({r[1] for r in rows}))
    nf = lambda cat, th: len({r[1] for r in rows if r[2] == cat and r[3] >= th})
    summary = dict(old)
    summary.update(n_frames=n_frames, animal_frames=nf("animal", a_th), person_frames=nf("person", p_th), vehicle_frames=nf("vehicle", p_th),
                   max_animal=round(max([r[3] for r in rows if r[2] == "animal"], default=0.0), 3), max_person=round(max([r[3] for r in rows if r[2] == "person"], default=0.0), 3),
                   max_vehicle=round(max([r[3] for r in rows if r[2] == "vehicle"], default=0.0), 3), max_area=round(max([r[6] * r[7] for r in rows if r[2] == "animal" and r[3] >= a_th], default=0.0), 4),
                   animal_threshold=a_th, person_threshold=p_th, edited=True, edited_at=now_iso(), model=(old.get("model") or "edited").replace(" (edited)", "") + " (edited)")
    a_min, p_min = min(int(s["animal_min_frames"]), n_frames), min(int(s["person_min_frames"]), n_frames)
    summary.update(animal=summary["animal_frames"] >= a_min, person=summary["person_frames"] >= p_min, vehicle=summary["vehicle_frames"] >= p_min)
    with db.tx() as c:
        c.execute("DELETE FROM detections WHERE item_id=?", (item_id,))
        c.executemany("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        c.execute("UPDATE items SET det_state=1, det_json=?, cls_state=0, cls_json=NULL, updated_at=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), now_iso(), item_id))
        c.execute(N_BOXES_SQL_ONE, (a_th, item_id))
    # 古い特徴量と枠の画像は捨てる（枠が変わったので）
    try:
        fp = os.path.join(project.cache_dir("features"), cache_name(it["rel_path"]) + ".npz")
        if os.path.isfile(fp):
            os.remove(fp)
        cdir = os.path.join(project.cache_dir("crops"), cache_name(it["rel_path"]))
        if os.path.isdir(cdir):
            import shutil
            shutil.rmtree(cdir, ignore_errors=True)
    except OSError:
        pass
    return dict(n=len(rows), det=summary)


def clear_detections(db, item_ids: list[int] | None = None) -> int:
    """検出をやり直すために消す（判定は消さない）"""
    with db.tx() as c:
        if item_ids:
            qs = ",".join("?" * len(item_ids))
            c.execute(f"DELETE FROM detections WHERE item_id IN ({qs})", tuple(item_ids))
            cur = c.execute(f"UPDATE items SET det_state=0, det_json=NULL, cls_state=0, cls_json=NULL, auto_json=NULL, agree=NULL, n_boxes=0 WHERE id IN ({qs})", tuple(item_ids))
        else:
            c.execute("DELETE FROM detections")
            cur = c.execute("UPDATE items SET det_state=0, det_json=NULL, cls_state=0, cls_json=NULL, auto_json=NULL, agree=NULL, n_boxes=0")
        return cur.rowcount


def export_md_json(project, path: str, f: dict | None = None) -> dict:
    """検出枠を MegaDetector の標準 JSON（images[].detections、動画は frames[]）で書く。Timelapse などとの互換用。
    座標は MegaDetector と同じ正規化 [x, y, w, h]。フレームは抜き出した時刻の秒 × fps を frame_number に"""
    from .judge import build_where
    db = project.db
    where, params = build_where(f or {})
    rows = db.q(f"SELECT id, rel_path, kind, fps, det_json FROM items WHERE {where} AND det_state=1 ORDER BY station, id", params)
    images = []
    model = ""
    for r in rows:
        try:
            model = model or (json.loads(r["det_json"] or "{}").get("model") or "")
        except Exception:
            pass
        dets = db.q("SELECT t_sec, category, conf, x, y, w, h, species, sp_conf FROM detections WHERE item_id=? ORDER BY t_sec, conf DESC", (r["id"],))
        inv = {v: k for k, v in CATEGORY.items()}

        def d_of(d):
            o = dict(category=inv.get(d["category"], d["category"]), conf=d["conf"], bbox=[d["x"], d["y"], d["w"], d["h"]])
            if d["species"]:
                o["classifications"] = [[d["species"], d["sp_conf"]]]
            return o
        if r["kind"] == "image":
            images.append(dict(file=r["rel_path"], detections=[d_of(d) for d in dets]))
        else:
            fps = float(r["fps"] or 0)
            frames: dict[float, list] = {}
            for d in dets:
                frames.setdefault(d["t_sec"], []).append(d_of(d))
            images.append(dict(file=r["rel_path"], frames=[dict(frame_number=int(round(t * fps)) if fps else i, time_sec=t, detections=ds)
                                                            for i, (t, ds) in enumerate(sorted(frames.items()))]))
    out = dict(images=images, detection_categories={"1": "animal", "2": "person", "3": "vehicle"},
               info=dict(detector=model, format_version="1.4", app="TrapNote", detection_completion_time=now_iso()))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return dict(path=path, items=len(images))


def evaluate(project, f: dict | None = None) -> dict:
    """自動判定（auto_results）と人の判定（確認済み、または CSV / Timelapse 由来）を突き合わせる。
    正答 = モデルの第 1 種が人の判定に含まれる。人の判定が複数なら第 1 種で集計する"""
    from .judge import build_where
    db = project.db
    where, params = build_where(f or {})
    items = db.q(f"SELECT id, status, auto_json FROM items WHERE {where} AND det_state=1 AND missing=0", params)
    if not items:
        return dict(n=0, by_species={}, correct=0, n_low=0, n_model=0)
    ids = [r["id"] for r in items]
    status = {r["id"]: r["status"] for r in items}
    auto_json = {r["id"]: r["auto_json"] for r in items}
    human: dict[int, list[str]] = {}
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        qs = ",".join("?" * len(part))
        for j in db.q(f"SELECT item_id, species, origin FROM judgements WHERE item_id IN ({qs}) ORDER BY id", part):
            if status[j["item_id"]] == "verified" or j["origin"] in ("csv", "manual"):
                if j["species"] != "area":
                    human.setdefault(j["item_id"], []).append(j["species"])
    from .species import FIXED_CODES
    enabled = {r["code"] for r in project.species.rows if r.get("enabled")} | set(FIXED_CODES)
    model: dict[int, list[tuple[str, float]]] = {}      # いまの AI の判定（auto_json。第 1 種が先頭）
    n_folded = 0
    for iid, aj in auto_json.items():
        if not aj:
            continue
        try:
            lst = []
            for x in json.loads(aj).get("species", []):
                code = x["species"]
                if code not in enabled:                 # 種リストで「使わない」にした種の AI の判定は「不明」に数える（付け直せば消える）
                    code = "unknown"
                    n_folded += 1
                if not any(c == code for c, _ in lst):
                    lst.append((code, x.get("confidence") or 0.0))
            model[iid] = lst
        except Exception:
            pass
    by: dict[str, dict] = {}
    n = correct = n_low = n_model = 0
    for iid, hs in human.items():
        if not hs:
            continue
        n += 1
        h1 = hs[0]
        d = by.setdefault(h1, dict(n=0, correct=0, low=0, conf={}))
        d["n"] += 1
        ms = model.get(iid) or []
        if not ms:
            n_low += 1
            d["low"] += 1
            d["conf"]["(low)"] = d["conf"].get("(low)", 0) + 1
            continue
        n_model += 1
        top = ms[0][0]
        d["conf"][top] = d["conf"].get(top, 0) + 1
        if top in hs:
            correct += 1
            d["correct"] += 1
    # 行（人）と列（AI）は種リストの順にそろえる（対角線が斜めに並ぶように）。列には行にある種も入れる。要確認は最後
    order = species_order(project)
    key = lambda c: (c == "(low)", order.get(c, len(order)), c)
    matrix: dict[str, dict[str, int]] = {h: dict(d["conf"]) for h, d in sorted(by.items(), key=lambda x: key(x[0]))}     # 人の第 1 種 × AI の第 1 種（(low) 含む）
    ai_cols = sorted({c for row in matrix.values() for c in row} | set(matrix.keys()), key=key)
    for d in by.values():
        d["conf"] = dict(sorted(d["conf"].items(), key=lambda x: -x[1])[:6])
    return dict(n=n, correct=correct, n_low=n_low, n_model=n_model, by_species=dict(sorted(by.items(), key=lambda x: key(x[0]))),
                matrix=matrix, ai_cols=ai_cols, n_folded=n_folded)


def species_order(project) -> dict[str, int]:
    """種コード → 種リストでの順番（固定項目は最後）"""
    from .species import FIXED
    order = {}
    for r in project.species.rows:
        order.setdefault(r["code"], len(order))
    for f in FIXED:
        order.setdefault(f["code"], len(order))
    return order


def export_evaluation(project, path: str, f: dict | None = None, lang: str = "ja") -> dict:
    """一致率の集計を CSV に書く: 種ごとの表（人の判定、本数、一致、一致率、要確認、AI の内訳）と、人 × AI の行列（_matrix.csv）"""
    import csv as _csv
    ev = evaluate(project, f)
    sp = project.species
    name = lambda c: "要確認" if c == "(low)" else (sp.label(c, lang) if hasattr(sp, "label") else c)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = _csv.writer(fh)
        w.writerow(["Human", "Human_ja", "n", "Agree", "AgreeRate", "Low", "AI_breakdown"])
        for c, d in ev["by_species"].items():
            judged = d["n"] - d["low"]
            w.writerow([sp.csv_label(c) if hasattr(sp, "csv_label") else c, name(c), d["n"], d["correct"], f"{d['correct'] / judged:.3f}" if judged else "",
                        d["low"], "; ".join(f"{name(k)}={v}" for k, v in d["conf"].items())])
        w.writerow([])
        w.writerow(["Total", "", ev["n"], ev["correct"], f"{ev['correct'] / ev['n_model']:.3f}" if ev["n_model"] else "", ev["n_low"], ""])
    mpath = os.path.splitext(path)[0] + "_matrix.csv"
    cols = ev["ai_cols"]
    with open(mpath, "w", newline="", encoding="utf-8-sig") as fh:
        w = _csv.writer(fh)
        w.writerow(["Human \\ AI"] + [name(c) for c in cols] + ["n"])
        for h, row in ev["matrix"].items():
            w.writerow([name(h)] + [row.get(c, 0) for c in cols] + [sum(row.values())])
    return dict(path=path, matrix_path=mpath, n=ev["n"], correct=ev["correct"], n_model=ev["n_model"])
