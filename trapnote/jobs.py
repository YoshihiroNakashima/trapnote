"""裏の処理（ジョブ）。レーンごとに 1 つのスレッドが順に処理する。
  bg レーン: 走査、メタデータの取得
  transcode レーン: 次に見る動画の先の変換（prefetch）
  archive レーン: 保存用の変換
  ml レーン: 検出と種判別（第 2 段階。GPU を使うので 1 本ずつ）
進捗はメモリと jobs 表に持ち、途中で止めても次回は未処理のものから再開できる（対象ごとの状態 meta_state などが DB にあるため）"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from . import media
from .db import now_iso

log = logging.getLogger(__name__)


class Job:
    def __init__(self, kind: str, params: dict | None = None, label: str = ""):
        self.kind = kind
        self.params = params or {}
        self.label = label or kind
        self.id: int | None = None
        self.state = "queued"          # queued | running | done | stopped | failed
        self.total = 0
        self.done = 0
        self.failed = 0
        self.skipped = 0
        self.message = ""
        self.current = ""
        self.started = 0.0
        self.finished = 0.0
        self.stop_flag = threading.Event()
        self.error: str | None = None

    def status(self) -> dict:
        el = (self.finished or time.time()) - self.started if self.started else 0.0
        rate = self.done / el if el > 0 and self.done else 0.0
        remain = (self.total - self.done - self.skipped) / rate if rate > 0 else None
        return dict(id=self.id, kind=self.kind, label=self.label, state=self.state, total=self.total, done=self.done,
                    failed=self.failed, skipped=self.skipped, message=self.message, current=self.current,
                    elapsed=round(el, 1), remain=round(remain, 1) if remain is not None else None, error=self.error)

    def run(self, ctx) -> None:        # 継承して書く
        raise NotImplementedError


class Lane:
    def __init__(self, name: str, ctx):
        self.name = name
        self.ctx = ctx
        self.queue: deque[Job] = deque()
        self.current: Job | None = None
        self.history: deque[Job] = deque(maxlen=20)
        self.cv = threading.Condition()
        self.thread = threading.Thread(target=self._loop, name=f"lane-{name}", daemon=True)
        self.closing = False
        self.thread.start()

    _seq = 0

    def submit(self, job: Job, front: bool = False) -> Job:
        db = self.ctx.db()
        if db is not None:
            try:
                job.id = db.job_create(job.kind, job.params)
            except Exception as e:
                log.warning("ジョブを記録できません: %s", e)
        if job.id is None:                            # プロジェクトが開いていないときも id を持つ（画面が「入れた処理が終わった」を id で見るため。負の数で DB の id と区別）
            Lane._seq += 1
            job.id = -Lane._seq
        with self.cv:
            (self.queue.appendleft if front else self.queue.append)(job)
            self.cv.notify()
        return job

    def _loop(self) -> None:
        while not self.closing:
            with self.cv:
                while not self.queue and not self.closing:
                    self.cv.wait(1.0)
                if self.closing:
                    return
                job = self.queue.popleft()
                self.current = job
            job.state, job.started = "running", time.time()
            self._record(job, state="running", started_at=now_iso())
            try:
                job.run(self.ctx)
                job.state = "stopped" if job.stop_flag.is_set() else "done"
            except Exception as e:
                job.state, job.error = "failed", f"{type(e).__name__}: {e}"
                log.error("ジョブ %s の失敗: %s\n%s", job.label, e, traceback.format_exc())
            job.finished = time.time()
            self._record(job, state=job.state, total=job.total, done=job.done, failed=job.failed, skipped=job.skipped,
                         message=(job.error or job.message)[:500], finished_at=now_iso())
            with self.cv:
                self.current = None
                self.history.appendleft(job)

    def _record(self, job: Job, **kw) -> None:
        db = self.ctx.db()
        if db is None or job.id is None or job.id < 0:
            return
        try:
            db.job_update(job.id, **kw)
        except Exception as e:
            log.debug("ジョブの記録の失敗: %s", e)

    def yield_current(self, kinds) -> bool:
        """いま実行中の処理が kinds（"meta" など、あとでやり直せるもの）なら止めて、次の処理に順番を譲る。止めたら True（呼ぶ側が同じ処理を列の最後に入れ直す）"""
        with self.cv:
            j = self.current
            if j is not None and j.kind in kinds and not j.stop_flag.is_set():
                j.stop_flag.set()
                j.message = "別の処理に順番を譲りました（あとで続きをします）"
                return True
        return False

    def stop_all(self) -> None:
        with self.cv:
            for j in self.queue:
                j.state = "stopped"
                j.stop_flag.set()
            self.queue.clear()
            if self.current:
                self.current.stop_flag.set()

    def status(self) -> dict:
        with self.cv:
            return dict(current=self.current.status() if self.current else None,
                        queued=[j.status() for j in self.queue], recent=[j.status() for j in list(self.history)[:5]])

    def close(self) -> None:
        self.closing = True
        self.stop_all()
        with self.cv:
            self.cv.notify_all()


# ---------- 具体的なジョブ ----------
def apply_meta(c, iid: int, m: dict | None, err: str | None) -> None:
    """ffprobe の結果 m（media.probe）を items に書く（失敗なら meta_state=2）。CSV 由来の日時は残す。時刻の修正のずれは保つ"""
    if m is None:
        c.execute("UPDATE items SET meta_state=2, meta_error=?, updated_at=? WHERE id=?", (err, now_iso(), iid))
        return
    off = c.execute("SELECT time_offset_sec, datetime_source, datetime FROM items WHERE id=?", (iid,)).fetchone()
    c.execute("UPDATE items SET duration=?, width=?, height=?, fps=?, n_frames=?, vcodec=?, acodec=?, container=?, "
              "datetime=COALESCE(CASE WHEN datetime_source='csv' THEN datetime END, ?), "
              "datetime_source=CASE WHEN datetime_source='csv' THEN 'csv' ELSE ? END, "
              "meta_state=1, meta_error=NULL, updated_at=? WHERE id=?",
              (m["duration"], m["width"], m["height"], m["fps"], m["n_frames"], m["vcodec"], m["acodec"], m["container"],
               m["datetime"], m["datetime_source"], now_iso(), iid))
    if off and off[0] is not None:       # 修正のずれを保ったまま修正後を計算し直す
        base = off[2] if off[1] == "csv" else m["datetime"]
        c.execute("UPDATE items SET datetime_corrected=? WHERE id=?", (media.shift_dt(base, off[0]), iid))


def ensure_meta(project, item_id: int, ffmpeg_path: str = "") -> dict | None:
    """対象のメタデータがまだなら、その場で ffprobe して書く（横断プロジェクトは取り込んだ全動画のメタデータを前もって取らず、開いたとき・検出するときに 1 本ずつ取る）。
    返り値は items の行（dict）か None"""
    db = project.db
    r = db.q1("SELECT id, rel_path, kind, meta_state, missing FROM items WHERE id=?", (item_id,))
    if r is None:
        return None
    if r["meta_state"] == 1 or r["kind"] != "video" or r["missing"]:
        return dict(r)
    src = project.abs_path(r["rel_path"])
    if not os.path.isfile(src):
        return dict(r)
    ffmpeg, ffprobe = media.find_ffmpeg(ffmpeg_path)
    try:
        m, err = media.probe(src, ffmpeg, ffprobe, bool(project.setting("z_is_utc"))), None
    except Exception as e:
        m, err = None, f"{type(e).__name__}: {e}"
    with db.tx() as c:
        apply_meta(c, item_id, m, err)
    return dict(db.q1("SELECT id, rel_path, kind, meta_state, missing, duration, fps FROM items WHERE id=?", (item_id,)))


class MetaJob(Job):
    """メタデータ（長さ、解像度、fps、日時）を未取得の対象について取る。ffprobe は別プロセスなので並列にする"""

    def __init__(self, item_ids: list[int] | None = None, workers: int = 4):
        super().__init__("meta", dict(n=len(item_ids) if item_ids else None), "メタデータの取得")
        self.item_ids = item_ids
        self.workers = workers

    def run(self, ctx) -> None:
        project = ctx.project
        if project is None:
            return
        db = project.db
        ffmpeg, ffprobe = media.find_ffmpeg(ctx.settings.get("ffmpeg_path", ""))
        z = bool(project.setting("z_is_utc"))
        if self.item_ids:
            qs = ",".join("?" * len(self.item_ids))
            rows = db.q(f"SELECT id, rel_path, kind FROM items WHERE id IN ({qs}) AND missing=0", tuple(self.item_ids))
        else:
            rows = db.q("SELECT id, rel_path, kind FROM items WHERE meta_state=0 AND missing=0 ORDER BY id")
        self.total = len(rows)
        pending = []
        last_flush = time.time()

        def one(r):
            p = project.abs_path(r["rel_path"])
            try:
                m = media.probe(p, ffmpeg, ffprobe, z)
                return (r["id"], m, None)
            except Exception as e:
                return (r["id"], None, f"{type(e).__name__}: {e}")

        def flush():
            nonlocal pending
            if not pending:
                return
            with db.tx() as c:
                for iid, m, err in pending:
                    apply_meta(c, iid, m, err)
            pending = []

        step = max(1, self.workers) * 4                      # ex.map は全部を先に投げてしまい「止める」が効かないので、少しずつ投げる
        with ThreadPoolExecutor(max(1, self.workers), thread_name_prefix="meta") as ex:
            for i0 in range(0, len(rows), step):
                if self.stop_flag.is_set():
                    break
                for iid, m, err in ex.map(one, rows[i0:i0 + step]):
                    pending.append((iid, m, err))
                    if err:
                        self.failed += 1
                    else:
                        self.done += 1
                    self.current = rows[i0]["rel_path"] if i0 < len(rows) else ""
                if len(pending) >= 50 or time.time() - last_flush > 2.0:
                    flush()
                    last_flush = time.time()
        flush()
        self.message = f"{self.done} 件取得、{self.failed} 件失敗"


class TranscodeJob(Job):
    """再生用 MP4 を先に作っておく（prefetch）。既にあるものは飛ばす"""

    def __init__(self, item_ids: list[int], label: str = "再生用の変換"):
        super().__init__("transcode", dict(n=len(item_ids)), label)
        self.item_ids = item_ids

    def run(self, ctx) -> None:
        self.total = len(self.item_ids)
        for iid in self.item_ids:
            if self.stop_flag.is_set():
                break
            try:
                r = ctx.ensure_playable(iid, wait=True)
                if r.get("made"):
                    self.done += 1
                else:
                    self.skipped += 1
            except Exception as e:
                self.failed += 1
                log.warning("変換の失敗 %s: %s", iid, e)


class ArchiveJob(Job):
    """保存用の変換: AVI など（ブラウザで直接再生できない動画）を H.264 の MP4 に変換し、検算に通れば一覧のパスを付け替える。
    元のファイルは設定により 残す / 別のフォルダへ移す / 消す。途中で止めても、済んだものは飛ばして再開できる"""

    def __init__(self, item_ids: list[int] | None, options: dict):
        super().__init__("archive", dict(n=len(item_ids) if item_ids else None, **options), "保存用の変換")
        self.item_ids = item_ids
        self.options = options
        self.orig_bytes = 0
        self.new_bytes = 0
        self.encoder = ""

    def run(self, ctx) -> None:
        import shutil
        from .paths import rel_key, abs_of
        project = ctx.project
        if project is None:
            return
        db = project.db
        ffmpeg, ffprobe = media.find_ffmpeg(ctx.settings.get("ffmpeg_path", ""))
        if not ffmpeg:
            raise RuntimeError("ffmpeg がありません（設定で場所を指定してください）")
        opt = self.options
        codec = opt.get("codec") or "auto"
        use_nvenc = codec in ("auto", "nvenc") and media.has_nvenc(ffmpeg)
        if codec == "nvenc" and not use_nvenc:
            raise RuntimeError("NVENC（GPU の H.264 エンコーダ）が使えません。codec を auto か cpu にしてください")
        self.encoder = "nvenc" if use_nvenc else "libx264"
        crf = int(opt.get("crf") or 19)
        original = opt.get("original") or "keep"
        move_to = opt.get("move_to") or ""
        if original == "move" and not move_to:
            raise RuntimeError("移し先のフォルダが指定されていません")
        z = bool(project.setting("z_is_utc"))
        rows = ctx.archive_targets(self.item_ids)
        self.total = len(rows)
        for r in rows:
            if self.stop_flag.is_set():
                break
            iid, rel = r["id"], r["rel_path"]
            self.current = rel
            src = project.abs_path(rel)
            try:
                if not os.path.isfile(src):
                    raise FileNotFoundError("元のファイルがありません")
                stem, ext = os.path.splitext(src)
                dst = stem + (".MP4" if ext.isupper() else ".mp4")
                if os.path.isfile(dst):
                    raise FileExistsError("同じ名前の MP4 が既にあります")
                st = os.stat(src)
                free = shutil.disk_usage(os.path.dirname(dst)).free
                if free < st.st_size + 512 * 1024 * 1024:           # 変換後のファイルを置く余地がない（元のサイズ + 512 MB）: 失敗を重ねずに止める
                    raise RuntimeError(f"ドライブの空きが足りません（空き {free / 1e9:.1f} GB）。不要なファイルやキャッシュ（設定 → キャッシュ）を消してからやり直してください")
                t0 = time.time()
                enc = media.archive_encode(ffmpeg, src, dst, use_nvenc, crf, r["datetime"])
                m = media.probe(dst, ffmpeg, ffprobe, z)
                od, nd = r["duration"], m.get("duration")
                if not m.get("width") or (od and nd and abs(od - nd) > max(0.5, 0.02 * od)):
                    os.remove(dst)
                    raise RuntimeError(f"検算に通りません（長さ {od} → {nd}）")
                os.utime(dst, (st.st_atime, st.st_mtime))       # 更新時刻（AVI の撮影日時の代わり）を引き継ぐ
                nst = os.stat(dst)
                new_rel = os.path.splitext(rel)[0] + os.path.splitext(dst)[1]
                with db.tx() as c:
                    c.execute("UPDATE items SET rel_path=?, rel_key=?, name=?, size=?, mtime=?, container=?, vcodec=?, acodec=?, width=?, height=?, fps=?, n_frames=?, "
                              "duration=COALESCE(?, duration), datetime=COALESCE(datetime, ?), datetime_source=COALESCE(datetime_source, ?), meta_state=1, "
                              "orig_rel_path=COALESCE(orig_rel_path, ?), orig_size=COALESCE(orig_size, ?), archived_at=?, updated_at=? WHERE id=?",
                              (new_rel, rel_key(new_rel), os.path.basename(new_rel), nst.st_size, nst.st_mtime, m["container"], m["vcodec"], m["acodec"],
                               m["width"], m["height"], m["fps"], m["n_frames"], nd, m["datetime"], m["datetime_source"], rel, st.st_size, now_iso(), now_iso(), iid))
                if original == "delete":
                    os.remove(src)
                elif original == "move":
                    dst2 = os.path.join(move_to, rel.replace("/", os.sep))
                    os.makedirs(os.path.dirname(dst2), exist_ok=True)
                    shutil.move(src, dst2)
                self.orig_bytes += st.st_size
                self.new_bytes += nst.st_size
                self.done += 1
                log.info("保存用に変換 %.1f 秒 %s（%s、%.1f MB → %.1f MB）", time.time() - t0, rel, enc, st.st_size / 1e6, nst.st_size / 1e6)
            except Exception as e:
                if "空きが足りません" in str(e):
                    self.error = str(e)
                    self.message = str(e)
                    break
                self.failed += 1
                log.warning("保存用の変換の失敗 %s: %s", rel, e)
                db.x("UPDATE items SET meta_error=? WHERE id=?", (f"archive: {e}", iid))
            self.message = self._summary()
        self.message = self._summary()

    def _summary(self) -> str:
        return (f"{self.done} 本変換（{self.encoder}）、{self.failed} 本失敗。{self.orig_bytes / 1e9:.2f} GB → {self.new_bytes / 1e9:.2f} GB"
                + (f"（{100 * self.new_bytes / self.orig_bytes:.0f}%）" if self.orig_bytes else ""))

    def status(self) -> dict:
        d = super().status()
        d.update(orig_bytes=self.orig_bytes, new_bytes=self.new_bytes, encoder=self.encoder)
        return d


class DetectJob(Job):
    """第 2 段階: MegaDetector で検出し、SpeciesNet で種を判別する（ml レーン）。
    対象は det_state=0（未）のもの（やり直しなら全部）。確認済みは設定により飛ばす。途中で止めても次回は残りから"""

    def __init__(self, item_ids: list[int] | None, options: dict):
        super().__init__("detect", dict(n=len(item_ids) if item_ids else None, **options), "検出と種判別")
        self.item_ids = item_ids
        self.options = options
        self.counts = dict(animal=0, person=0, vehicle=0, empty=0, low=0)
        self.phase = ""
        self.dl_done = 0
        self.dl_total = 0

    def run(self, ctx) -> None:
        from . import detect
        project = ctx.project
        if project is None:
            return
        db = project.db
        opt = self.options
        hub = ctx.model_hub()
        s = detect.project_settings(project)
        classify = (opt.get("classifier") or s.get("classifier") or "speciesnet") != "none"
        # 対象
        where = ["missing=0", "meta_state<>2"]
        params: list = []
        if self.item_ids:
            where.append(f"id IN ({','.join('?' * len(self.item_ids))})")
            params += list(self.item_ids)
        use_existing = bool(opt.get("use_existing"))
        if not opt.get("redo"):
            where.append("cls_state=0" if use_existing else "det_state=0")     # 既存の枠を使うとき: 種判別がまだのもの（編集した枠など）だけ。やり直しなら全部
        if s.get("detect_skip_verified", True) and not opt.get("include_verified") and not use_existing:
            where.append("status<>'verified'")
        rows = db.q(f"SELECT id, rel_path, kind, duration, fps, det_json, meta_state FROM items WHERE {' AND '.join(where)} ORDER BY station, id", tuple(params))
        self.total = len(rows)
        if not rows:
            self.message = "対象がありません"
            return
        ffmpeg_path = ctx.settings.get("ffmpeg_path", "")
        # モデル
        def prog(done, total):
            self.dl_done, self.dl_total = done, total
            self.current = f"モデルを取得中 {done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else f"モデルを取得中 {done / 1e6:.0f} MB"
        self.phase = "load"
        self.current = "モデルを読み込み中"
        try:
            if not use_existing:                       # 既存の枠を使うときは検出器は要らない（枠のない対象があれば process_item が読む）
                hub.load_detector(opt.get("md_model") or s.get("md_model") or "auto", prog, self.stop_flag.is_set)
            if classify:
                base = opt.get("speciesnet_model") or s.get("speciesnet_model")
                hub.load_classifier(base, prog, self.stop_flag.is_set)
                hub.set_local(project, opt.get("classifier") or s.get("classifier") or "speciesnet", base)     # 追加学習のモデル（local:<名前>）
        except ModuleNotFoundError as e:
            raise RuntimeError(f"Python のモジュール {e.name} がありません。インストール.bat をもう一度実行してください"
                               f"（または Anaconda Prompt で conda activate trapnote → pip install {e.name}）") from e
        self.phase = "run"
        self.message = f"{hub.device_name}（{detect.frame_interval(s, hub.device)} 秒ごと）"
        t_start = time.time()
        # 次の対象のフレームは、いまの対象をモデルに通している間に別スレッドで読んでおく（CPU の読み出しと GPU の推論を重ねる）
        reader = ThreadPoolExecutor(1, thread_name_prefix="frames")
        nxt = None

        def prefetch(row):
            times = None
            if use_existing:
                ex = detect.existing_boxes(db, row["id"])
                if ex:
                    times = sorted({x[0] for x in ex})
            item = dict(row)
            if row["kind"] == "video" and row["meta_state"] != 1:          # メタデータがまだ（横断で取り込んだ動画など）なら、ここで取る（長さが分からないとフレームを選べない）
                got = ensure_meta(project, row["id"], ffmpeg_path)
                if got:
                    item.update(duration=got.get("duration"), fps=got.get("fps"))
            return detect.load_frames(project, item, hub.device, times)
        for k, r in enumerate(rows):
            if self.stop_flag.is_set():
                break
            self.current = r["rel_path"]
            fut, nxt = nxt, (reader.submit(prefetch, rows[k + 1]) if k + 1 < len(rows) else None)
            try:
                frames = fut.result() if fut is not None else None
                res = detect.process_item(project, hub, dict(r), classify, frames, use_existing)
                self.done += 1
                sp = [x["species"] for x in res.get("results", [])]
                if res.get("flag") == "low":
                    self.counts["low"] += 1
                elif "empty" in sp:
                    self.counts["empty"] += 1
                else:
                    if res.get("animal"):
                        self.counts["animal"] += 1
                    if res.get("person"):
                        self.counts["person"] += 1
                    if res.get("vehicle"):
                        self.counts["vehicle"] += 1
            except Exception as e:
                self.failed += 1
                log.warning("検出の失敗 %s: %s", r["rel_path"], e)
                db.x("UPDATE items SET det_state=2, meta_error=?, updated_at=? WHERE id=?", (f"detect: {e}", now_iso(), r["id"]))
            self.message = self._summary(time.time() - t_start)
        reader.shutdown(wait=False, cancel_futures=True)
        self.message = self._summary(time.time() - t_start)

    def _summary(self, el: float) -> str:
        c = self.counts
        per = f"{el / self.done:.1f} 秒/本" if self.done else ""
        return f"{self.done} 本（動物 {c['animal']}、人 {c['person']}、車 {c['vehicle']}、なし {c['empty']}、要確認 {c['low']}）、{self.failed} 本失敗 {per}"

    def status(self) -> dict:
        d = super().status()
        d.update(counts=self.counts, phase=self.phase, dl_done=self.dl_done, dl_total=self.dl_total)
        return d


class TrainJob(Job):
    """第 3 段階: 追加学習（ml レーン）。方式 A は特徴量から（CPU で可）、方式 B は本体の再学習（SpeciesNet を読み込む）"""

    def __init__(self, options: dict, name: str):
        super().__init__("train", dict(kind=options.get("kind", "A"), name=name, init_from=options.get("init_from"), sources=options.get("sources") or []), "追加学習")
        self.options = options
        self.name = name
        self.phase = ""
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import detect, train
        project = ctx.project
        if project is None:
            return
        opts = train.train_settings(project, self.options)
        opts["holdout_stations"] = self.options.get("holdout_stations") or None
        opts["holdout_batches"] = [str(x) for x in (self.options.get("holdout_batches") or []) if str(x)]      # 検証用の組（gold など）
        kind = self.options.get("kind", "A")
        sources = [str(x) for x in (self.options.get("sources") or [])] or None      # 材料に加えるほかのプロジェクト

        def prog(phase, a, b, msg):
            self.phase = phase
            self.done, self.total = int(a), int(b)
            self.current = msg or ""
        self.phase = "collect"
        if kind == "B":
            hub = ctx.model_hub()
            s = detect.project_settings(project)
            self.current = "モデルを読み込み中"
            hub.load_classifier(s.get("speciesnet_model"), None, self.stop_flag.is_set)
            init = self.options.get("init_from") or None
            hub.set_local(project, f"local:{init}" if init else "speciesnet", s.get("speciesnet_model"))        # 元の重み（または出発点のモデル）で学習する
            self.result = train.run_train_b(project, hub, opts, self.name, prog, self.stop_flag.is_set, init_from=init, sources=sources)
        else:
            self.result = train.run_train_a(project, opts, self.name, prog, self.stop_flag.is_set, sources=sources)
        r = self.result
        ev = r.get("eval")
        if ev and ev["before"]["n"]:
            b, a = ev["before"], ev["after"]
            who = f"組 {', '.join(ev['batches'])}" if ev.get("batches") else "地点"
            self.message = f"{self.name}: 検証用（{who}）{ev['n_items']} 本の一致率 {100 * b['exact'] / b['n']:.0f}% → {100 * a['exact'] / a['n']:.0f}%（学習 {r['n_items']} 本・{r['n_crops']} 枠・{len(r['classes'])} 種）"
        else:
            self.message = f"{self.name}: 学習 {r['n_items']} 本・{r['n_crops']} 枠・{len(r['classes'])} 種（検証用なし）"
        self.phase = "done"

    def status(self) -> dict:
        d = super().status()
        d.update(phase=self.phase, name=self.name, method=self.options.get("kind", "A"))
        return d


class ReclassifyJob(Job):
    """追加学習のモデル（または元の SpeciesNet）で、保存した特徴量から動画の種判定を付け直す（動画は読み直さない）"""

    def __init__(self, item_ids: list[int] | None, classifier: str):
        super().__init__("reclassify", dict(classifier=classifier, n=len(item_ids) if item_ids else None), "判定の付け直し")
        self.item_ids = item_ids
        self.classifier = classifier
        self.counts = dict(changed=0, skipped=0)

    def run(self, ctx) -> None:
        from . import detect, train
        project = ctx.project
        if project is None:
            return
        db = project.db
        hub = ctx.model_hub()
        s = detect.project_settings(project)
        where = ["missing=0", "det_state=1", "cls_state=1"]
        params: list = []
        if self.item_ids:
            where.append(f"id IN ({','.join('?' * len(self.item_ids))})")
            params += list(self.item_ids)
        rows = db.q(f"SELECT id, rel_path, kind, auto_json FROM items WHERE {' AND '.join(where)} ORDER BY station, id", tuple(params))
        self.total = len(rows)
        if not rows:
            self.message = "対象がありません"
            return
        want = self.classifier
        self.current = "モデルを読み込み中"
        hub.set_local(project, want if want.startswith("local:") else "speciesnet", s.get("speciesnet_model"))     # 方式 A なら本体は読まない
        if hub.classifier_name is None:
            hub.classifier_name = s.get("speciesnet_model")
        project.update_settings(dict(classifier=want))
        for r in rows:
            if self.stop_flag.is_set():
                break
            self.current = r["rel_path"]
            try:
                res = train.reclassify_item(project, hub, dict(r), s)
                if res is None:
                    self.skipped += 1
                else:
                    self.done += 1
                    new = db.q1("SELECT auto_json FROM items WHERE id=?", (r["id"],))["auto_json"]
                    if new != r["auto_json"]:
                        self.counts["changed"] += 1
            except Exception as e:
                self.failed += 1
                log.warning("判定の付け直しに失敗 %s: %s", r["rel_path"], e)
            self.message = f"{self.done} 本（判定が変わった {self.counts['changed']}、特徴量なし {self.skipped}、失敗 {self.failed}）"

    def status(self) -> dict:
        d = super().status()
        d.update(counts=self.counts)
        return d


class CrossImportJob(Job):
    """横断プロジェクト: 参照先から対象を取り込む（bg レーン）。
    spec: dict(mode="select", params)（参照先を直接選ぶ）/ dict(mode="catalog", ids)（カタログの行）/ dict(mode="catalog_sample", filters, params)（カタログから種ごとに抽出）"""

    def __init__(self, spec: dict, batch: str):
        super().__init__("cross_import", dict(mode=spec.get("mode"), batch=batch), "参照先からの取り込み")
        self.spec = spec
        self.batch = batch
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project = ctx.project
        if project is None:
            return

        def prog(a, b, msg):
            self.done, self.total, self.current = a, b, msg or ""
        self.message = "選んでいます"
        mode = self.spec.get("mode") or "select"
        if mode == "catalog":
            ids = [int(x) for x in (self.spec.get("ids") or [])]
            r = cross.catalog_import(project, ids, self.batch, dict(ids_n=len(ids)), prog, self.stop_flag.is_set)
        elif mode == "catalog_sample":
            f, params = self.spec.get("filters") or {}, self.spec.get("params") or {}
            smp = cross.catalog_sample(project, f, params)
            r = cross.catalog_import(project, smp["ids"], self.batch, dict(filters=f, params=params, counts=smp["counts"], unit=smp.get("unit"), units=smp.get("units") or []), prog, self.stop_flag.is_set)
            r["counts"] = smp["counts"]
            r["units"] = smp.get("units") or []
        elif mode in ("species", "stations", "all", "csv"):           # spec 自体が select_items の params
            r = cross.import_items(project, self.spec, self.batch, prog, self.stop_flag.is_set)
        else:
            r = cross.import_items(project, self.spec.get("params") or {}, self.batch, prog, self.stop_flag.is_set)
        self.result = r
        self.done = self.total = r["n"]
        self.message = f"{r['n']} 件を取り込みました" + (f"（取り込み済みで飛ばした {r['n_already']}）" if r.get("n_already") else "") + (f"（一覧で見つからなかった {r['n_unmatched']}）" if r.get("n_unmatched") else "")
        ctx.after_scan(dict(added=r["n"]))

    def status(self) -> dict:
        d = super().status()
        d.update(result=self.result, batch=self.batch)
        return d


class CrossLaneJob(Job):
    """横断プロジェクトの長い処理（種名 CSV の読み込み、カタログの作成）の土台。cross レーンで動き、開いているプロジェクトとは別に自分で横断プロジェクトを開くので、
    その間に別のプロジェクトを開いて作業できる（プロジェクトを閉じても止まらない）。project.json は update_data で項目だけ書き、終わったら開いているプロジェクトに反映する"""

    def __init__(self, kind: str, params: dict, label: str, pdir: str):
        super().__init__(kind, params, label)
        self.pdir = pdir

    def open_project(self, ctx):
        """常に自分で開く（開いているプロジェクトと同じでも。途中で閉じられたり別のプロジェクトを開かれたりしても続けられるように）→ (Project, 自分で開いた)"""
        from .project import Project
        return Project(self.pdir), True

    def sync_live(self, ctx, *keys: str) -> None:
        """終わったら、同じ横断プロジェクトが開いていれば project.json の項目を読み直させる"""
        from .paths import key
        live = getattr(ctx, "project", None)
        if live is not None and key(live.dir) == key(self.pdir):
            try:
                live.reload_data(*keys)
            except Exception as e:
                log.warning("project.json の読み直しに失敗: %s", e)


class CatalogImportJob(CrossLaneJob):
    """横断プロジェクト: 別の横断プロジェクトからカタログ（参照先・対応表・行・種名 CSV のキャッシュ）を持ってくる（作り直さずに済ませる）"""

    def __init__(self, other_path: str, pdir: str = ""):
        super().__init__("cross_catalog", dict(mode="import", other=other_path), "カタログを持ってくる", pdir)
        self.other_path = other_path
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project, owned = self.open_project(ctx)
        try:
            if not project.is_cross:
                return

            def prog(a, b, msg):
                self.done, self.total, self.current = a, b, msg or ""
            self.message = "別の横断プロジェクトから写しています"
            r = cross.import_catalog_from(project, self.other_path, prog, self.stop_flag.is_set)
            self.result = r
            self.done = self.total = r["n"]
            self.message = f"カタログ {r['n']:,} 本を {r['from_dir']} から持ってきました" + (f"（種名 CSV のキャッシュ {r['n_cache']}）" if r["n_cache"] else "") \
                + ("。対応表か種リストが違うので「当て直し」が要ります" if "labels" in r["stale"] else "")
        finally:
            if owned:
                project.close()
            self.sync_live(ctx, "catalog")


class CatalogBuildJob(CrossLaneJob):
    """横断プロジェクト: カタログ（参照先の全動画の目録）を作り直す。mode: full（全部作り直す）/ remap（対応表だけ当て直す）/ auto（対応表か種リストしか変わっていなければ remap、それ以外は full）"""

    def __init__(self, mode: str = "full", pdir: str = ""):
        super().__init__("cross_catalog", dict(mode=mode), "カタログの作成", pdir)
        self.mode = mode
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project, owned = self.open_project(ctx)
        try:
            if not project.is_cross:
                return

            def prog(a, b, msg):
                self.done, self.total, self.current = a, b, msg or ""
            mode = self.mode
            if mode == "auto":                       # 対応表（か種リスト）しか変わっていなければ当て直し。参照先が変わった・古い形式・まだない → 作り直し
                st = cross.catalog_status(project)
                mode = "remap" if (st["built"] and st["n"] and all(r == "labels" for r in st["reasons"])) else "full"
            if mode == "remap":
                self.label = "カタログの対応の当て直し"
                self.message = "表記の組ごとに種を当て直しています"
                r = cross.remap_catalog(project, prog, self.stop_flag.is_set)
                self.result = r
                self.done = self.total = max(r["n_combos"], 1)
                self.message = f"対応を当て直しました（変わった行 {r['n_changed']:,}）"
                return
            self.message = "参照先を読んでいます"
            r = cross.build_catalog(project, prog, self.stop_flag.is_set)
            self.result = r
            self.done = self.total = r["n"]
            self.message = ("止めました: " if self.stop_flag.is_set() else "") + f"カタログ {r['n']} 本（" + " · ".join(f"{k} {v}" for k, v in r["by_source"].items()) + "）"
            if r["n"] == 0 and not self.stop_flag.is_set():
                self.message += "。参照先の調査回に TrapNote のプロジェクトも種名の CSV の行もありません。親フォルダ（下に調査回が並ぶ）か調査回のフォルダを登録しているか、" \
                                "種名の CSV（親フォルダか調査回フォルダの直下）があるかを 1 の段の表で確かめてください。種名のない新しい調査回なら「フォルダを走査する」を付けて作り直す"
        finally:
            if owned:
                project.close()
            self.sync_live(ctx, "catalog")


class LabelsJob(CrossLaneJob):
    """横断プロジェクト: 親フォルダ参照の種名 CSV を読む（数十万行・NAS だと分単位でかかるので裏で。読んだものは cache/labels に残す）"""

    def __init__(self, name: str, pdir: str = ""):
        super().__init__("cross_labels", dict(name=name), f"種名の CSV の読み込み（{name}）", pdir)
        self.name = name

    def run(self, ctx) -> None:
        from . import cross
        project, owned = self.open_project(ctx)
        try:
            if not project.is_cross:
                return
            s = cross.source_by_name(project, self.name)
            if s is None or s.get("kind") != "tree":
                return

            def prog(a, b, msg):
                self.done, self.total, self.current = a, b, msg or ""
            self.message = "大きな CSV では数分かかります"
            lab = cross._labels(project, s, prog, self.stop_flag.is_set)
            self.done = self.total = max(self.total, 1)
            if self.stop_flag.is_set():
                self.message = "止めました（読んだ分は捨てました）"
                return
            self.message = f"{len(lab['rows']):,} 件（{', '.join(os.path.basename(f['path']) for f in lab['files'])}）" + (f" · 対応しない種名 {sum(n for _, n in lab['unmapped'])}" if lab["unmapped"] else "")
        finally:
            if owned:
                project.close()

    def status(self) -> dict:
        return dict(super().status(), name=self.name)


class CrossRemoveJob(Job):
    """横断プロジェクト: 取り込んだ対象を外す（組 / 絞り込み / 全部。動画本体には触らない）"""

    def __init__(self, spec: dict):
        super().__init__("cross_remove", dict(batch=spec.get("batch"), all=bool(spec.get("all"))), "取り込みの取り消し")
        self.spec = spec
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project = ctx.project
        if project is None:
            return

        def prog(a, b, msg):
            self.done, self.total, self.current = a, b, msg or ""
        self.message = "対象から外しています"
        r = cross.remove_items(project, self.spec.get("batch"), self.spec.get("filters"), bool(self.spec.get("all")), prog, self.stop_flag.is_set)
        self.result = r
        self.done = self.total = r["n"]
        self.message = f"{r['n']:,} 件を対象から外しました（動画は消していません）"


class CrossSyncJob(Job):
    """横断プロジェクト: 判定の書き戻し（push: 横断 → 参照先）/ 再取得（pull: 参照先 → 横断）"""

    def __init__(self, direction: str, by: str | None, overwrite: bool):
        super().__init__("cross_sync", dict(direction=direction), "判定の書き戻し" if direction == "push" else "判定の再取得")
        self.direction = direction
        self.by = by
        self.overwrite = overwrite
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project = ctx.project
        if project is None:
            return

        def prog(a, b, msg):
            self.done, self.total, self.current = a, b, msg or ""
        r = cross.sync_apply(project, self.direction, self.by or "", self.overwrite, prog, self.stop_flag.is_set)
        self.result = r
        self.message = f"{r['n']} 件を{'書き戻し' if self.direction == 'push' else '再取得し'}ました" + (f"（衝突で飛ばした {r['n_skipped']}）" if r["n_skipped"] else "")

    def status(self) -> dict:
        d = super().status()
        d.update(result=self.result)
        return d


class CrossCopyJob(Job):
    """横断プロジェクト: 対象の動画をフォルダへ写す"""

    def __init__(self, out_dir: str, filters: dict | None):
        super().__init__("cross_copy", dict(out_dir=out_dir), "動画のコピー")
        self.out_dir = out_dir
        self.filters = filters
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project = ctx.project
        if project is None:
            return

        def prog(a, b, msg):
            self.done, self.total, self.current = a, b, msg or ""
        r = cross.copy_videos(project, self.out_dir, self.filters, prog, self.stop_flag.is_set, int(ctx.settings.get("copy_workers", 3) or 3))
        self.result = r
        self.done = self.total = r["n"]
        self.message = (f"止めました: {r['n']} 本まで" if r.get("stopped") else f"{r['n']} 本") + (f"（{r['speed']}、{r['seconds'] / 60:.0f} 分）" if r.get("speed") else "") + f" を {r['out_dir']} に写しました" + (f"（見つからない {r['n_missing']}）" if r["n_missing"] else "")

    def status(self) -> dict:
        d = super().status()
        d.update(result=self.result)
        return d


class SubsetExportJob(Job):
    """選んだ対象を動画と判定ごと別のフォルダへ写し、そこに新しいプロジェクトを作る（bg レーン。どのプロジェクトからでも）"""

    def __init__(self, ids: list[int], out_dir: str, copy_videos: bool = True, project_dirname: str = ""):
        super().__init__("subset_export", dict(out_dir=out_dir, n=len(ids)), "別のフォルダへ写す")
        self.ids = ids
        self.out_dir = out_dir
        self.copy_videos = copy_videos
        self.project_dirname = project_dirname
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import cross
        project = ctx.project
        if project is None:
            return

        def prog(a, b, msg):
            self.done, self.total, self.current = a, b, msg or ""
        workers = int(ctx.settings.get("copy_workers", 3) or 3)
        r = cross.export_subset(project, self.ids, self.out_dir, self.copy_videos, prog, self.stop_flag.is_set, self.project_dirname, workers)
        self.result = r
        self.done = r["n"] + r["n_missing"] + r["n_skipped"]
        self.total = len(self.ids)
        sp = f"、{r['speed']}、{r['seconds'] / 60:.0f} 分" if r.get("speed") else ""
        self.message = (f"止めました: {r['n']} 本を写したところまで（残り {r['n_left']}。同じ写す先でやり直せば続きから）" if r.get("stopped") else f"{r['n']} 本（{sp.lstrip('、')}）を {r['out_dir']} に写しました") \
            + f"（プロジェクト {r['project_dir']}）" + (f"（見つからない {r['n_missing']}）" if r["n_missing"] else "") + (f"（写してあった {r['n_skipped']}）" if r["n_skipped"] else "")

    def status(self) -> dict:
        d = super().status()
        d.update(result=self.result)
        return d


class YoloImportJob(Job):
    """YOLO 形式の学習データ（<種>/images + <種>/labels）の取り込み（bg レーン。枚数が多いので裏で）"""

    def __init__(self, mapping: dict, options: dict, by: str | None):
        super().__init__("yolo", dict(n_folders=len(mapping)), "YOLO ラベルの取り込み")
        self.mapping = mapping
        self.options = options
        self.by = by
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import yolo
        project = ctx.project
        if project is None:
            return

        def prog(a, b):
            self.done, self.total = a, b
            if self.stop_flag.is_set():
                raise RuntimeError("止めました")
        self.message = "読み込み中"
        r = yolo.import_yolo(project, self.mapping, self.by, bool(self.options.get("as_verified", True)), bool(self.options.get("set_station", True)),
                             bool(self.options.get("overwrite_verified")), prog, bool(self.options.get("station_only")))
        self.result = r
        self.done = self.total = r["items"]
        self.message = f"{r['items']} 枚（枠 {r['boxes']}、枠なし {r['no_boxes']}、確認済みで飛ばした {r['skipped_verified']}、季節・地点を付けた {r['station_set']}）"

    def status(self) -> dict:
        d = super().status()
        d.update(result=self.result)
        return d


class YoloExportJob(Job):
    """検出枠を YOLO 形式で書き出す（bg レーン。動画はフレームを切り出すので裏で）"""

    def __init__(self, options: dict):
        super().__init__("yolo_export", dict(out=options.get("out_dir")), "YOLO 形式の書き出し")
        self.options = options
        self.result: dict | None = None

    def run(self, ctx) -> None:
        from . import yolo
        project = ctx.project
        if project is None:
            return
        o = self.options

        def prog(a, b):
            self.done, self.total = a, b
        self.message = "書き出し中"
        r = yolo.export_yolo(project, o.get("out_dir") or "", o.get("filters") or {}, bool(o.get("per_species", True)), o.get("class_mode") or "animal0",
                             bool(o.get("write_images", True)), bool(o.get("in_place")), prog, self.stop_flag.is_set)
        self.result = r
        self.done = self.total = r["items"]
        self.message = f"{r['items']} 件（ラベル {r['labels']}、枠 {r['boxes']}、画像 {r['images']}、飛ばした {r['skipped']}）→ {r['out_dir']}"

    def status(self) -> dict:
        d = super().status()
        d.update(result=self.result)
        return d


class ScanJob(Job):
    def __init__(self):
        super().__init__("scan", {}, "フォルダの走査")

    def run(self, ctx) -> None:
        from . import scan as scan_mod
        project = ctx.project
        if project is None or not project.root:
            return
        if project.is_cross:                              # 横断プロジェクトは走査しない: 参照先の動画があるかだけ確かめる
            from . import cross
            self.label = "動画の確認"
            self.message = "参照先の動画を確かめています"

            def prog2(a, b, where):
                self.done, self.total, self.current = a, b, where
            res = cross.check_missing(project, prog2, self.stop_flag.is_set)
            self.done = self.total = res["found"]
            self.message = f"{res['found']} 件（見つからない {res['gone']}、戻った {res['back']}）"
            ctx.after_scan(res)
            return
        self.message = "走査中"

        def prog(n, where):
            self.done = n
            self.current = where

        found = scan_mod.walk_media(project.root, project.setting("skip_names"), prog, self.stop_flag.is_set)
        if self.stop_flag.is_set():
            return
        self.current = "登録中"
        res = scan_mod.register(project, found)
        self.total = res["found"]
        self.done = res["found"]
        self.message = f"{res['found']} 件（追加 {res['added']}、変更 {res['changed']}、見つからない {res['gone']}）"
        ctx.after_scan(res)
