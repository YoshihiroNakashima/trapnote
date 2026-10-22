"""サーバー側（aiohttp）。Session がプロジェクトと裏の処理を持ち、API がそれを画面に出す"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

from . import APP_ID, APP_NAME, BUILD, BUILD_TIME, VERSION, detect, dialogs, export as export_mod, judge, media, merge as merge_mod, scan as scan_mod, timelapse, train, yolo
from .config import DATA_DIR, STATIC_DIR, Settings, State
from .jobs import ArchiveJob, DetectJob, Lane, MetaJob, ReclassifyJob, ScanJob, TrainJob, TranscodeJob, YoloExportJob, YoloImportJob
from .paths import abs_of, norm
from .pattern import names_in, preview as pattern_preview
from .project import PROJECT_DIRNAME, Project, find_project_for_root, is_project_dir

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class Session:
    def __init__(self):
        self.settings = Settings()
        self.state = State()
        self.project: Project | None = None
        self.io = ThreadPoolExecutor(max_workers=8, thread_name_prefix="io")
        self.bg = Lane("bg", self)
        self.tc = Lane("transcode", self)
        self.ar = Lane("archive", self)
        self.ml = Lane("ml", self)
        self._hub = None
        self._tc_lock = threading.Lock()
        self._transcoding: dict[int, threading.Event] = {}
        self._thumb_sem = threading.Semaphore(4)
        self.undo_stack: list[int] = []
        self.opening = False

    # ---------- ジョブから使う ----------
    def db(self):
        return self.project.db if self.project else None

    def after_scan(self, res: dict) -> None:
        if self.project and (res.get("added") or res.get("changed") or self.project.db.scalar("SELECT COUNT(*) FROM items WHERE meta_state=0 AND missing=0")):
            self.bg.submit(MetaJob())

    # ---------- プロジェクト ----------
    def open_project(self, path: str, create: bool = False) -> dict:
        path = norm(path)
        if os.path.isfile(path) and os.path.basename(path).lower() == "project.json":
            path = os.path.dirname(path)
        if not os.path.isdir(path):
            raise ApiError(f"フォルダがありません: {path}")
        if is_project_dir(path):
            pdir, root = path, None
        else:
            found = find_project_for_root(path)
            if found:
                pdir, root = found, path
            elif create:
                pdir, root = norm(os.path.join(path, PROJECT_DIRNAME)), path
            else:
                return dict(needs_create=True, path=path)
        self.close_project()
        try:
            self.project = Project(pdir, root=root, create=create)
        except FileNotFoundError as e:
            raise ApiError(str(e))
        if not self.project.root:
            return dict(needs_root=True, project=self.project.to_dict())
        self.settings.remember_project(self.project.dir)
        self.state.last_project = self.project.dir
        self.state.save()
        self.bg.submit(ScanJob())
        return dict(project=self.project.to_dict())

    def set_root(self, root: str) -> dict:
        if not self.project:
            raise ApiError("プロジェクトが開いていません")
        root = norm(root)
        if not os.path.isdir(root):
            raise ApiError(f"フォルダがありません: {root}")
        self.project.set_root(root)
        self.settings.remember_project(self.project.dir)
        self.state.last_project = self.project.dir
        self.state.save()
        self.bg.submit(ScanJob())
        return dict(project=self.project.to_dict())

    def close_project(self) -> None:
        if self.project:
            self.bg.stop_all()
            self.tc.stop_all()
            self.ar.stop_all()
            self.project.close()
            self.project = None

    def need(self) -> Project:
        if not self.project or not self.project.root:
            raise ApiError("プロジェクトが開いていません")
        return self.project

    # ---------- 再生用の動画 ----------
    def item_path(self, item_id: int) -> tuple[dict, str]:
        p = self.need()
        r = p.db.q1("SELECT id, rel_path, kind, vcodec, acodec, container, meta_state FROM items WHERE id=?", (item_id,))
        if r is None:
            raise ApiError("対象がありません", 404)
        return dict(r), abs_of(r["rel_path"], p.root)

    def ensure_playable(self, item_id: int, wait: bool, no_mjpeg: bool = False) -> dict:
        """再生できる状態にする。direct: 元のファイルをそのまま再生。mjpeg: MJPEG の AVI を画面側がそのまま読む（変換なし）。
        それ以外は変換した MP4（cache）。wait=False なら変換を始めて戻る"""
        it, src = self.item_path(item_id)
        if it["kind"] != "video":
            return dict(ready=True, direct=True, made=False)
        if not os.path.isfile(src):
            raise ApiError("ファイルがありません", 404)
        if it["meta_state"] != 1:
            ffmpeg, ffprobe = media.find_ffmpeg(self.settings.get("ffmpeg_path", ""))
            m = media.probe(src, ffmpeg, ffprobe, bool(self.project.setting("z_is_utc")))
            it.update(vcodec=m["vcodec"], acodec=m["acodec"], container=m["container"])
        codec = self.settings.get("transcode_codec", "h264")
        if codec == "h264" and media.direct_playable(it["vcodec"], it["acodec"], it["container"]):
            return dict(ready=True, direct=True, made=False)
        if (not no_mjpeg and self.settings.get("avi_direct", True) and (it["vcodec"] or "").lower() == "mjpeg"
                and (it["container"] or "").lower() in ("avi", "mjpeg")):
            return dict(ready=True, direct=False, mjpeg=True, made=False)
        dst = media.video_cache_path(self.project.cache_dir("video"), it["rel_path"], codec)
        if os.path.isfile(dst):
            media.touch(dst)
            return dict(ready=True, direct=False, made=False)
        with self._tc_lock:
            ev = self._transcoding.get(item_id)
            if ev is None:
                ev = threading.Event()
                self._transcoding[item_id] = ev
                mine = True
            else:
                mine = False
        if not mine:
            if wait:
                ev.wait()
                return dict(ready=os.path.isfile(dst), direct=False, made=False)
            return dict(ready=False, direct=False, transcoding=True)
        if not wait:
            self.io.submit(self._transcode_one, item_id, src, dst, ev, it["vcodec"])
            return dict(ready=False, direct=False, transcoding=True)
        self._transcode_one(item_id, src, dst, ev, it["vcodec"])
        if not os.path.isfile(dst):
            raise ApiError("変換に失敗しました")
        return dict(ready=True, direct=False, made=True)

    def _transcode_one(self, item_id: int, src: str, dst: str, ev: threading.Event, vcodec: str | None = None) -> None:
        try:
            ffmpeg, _ = media.find_ffmpeg(self.settings.get("ffmpeg_path", ""))
            if not ffmpeg:
                raise RuntimeError("ffmpeg がありません（設定で場所を指定してください）")
            t0 = time.time()
            how = media.transcode(ffmpeg, src, dst, int(self.settings.get("transcode_max_width", 1280)), self.settings.get("transcode_codec", "h264"),
                                  vcodec, media.has_nvenc(ffmpeg))
            log.info("変換 %.1f 秒（%s）: %s", time.time() - t0, how, src)
            media.trim_cache(self.project.cache_dir("video"), int(float(self.settings.get("cache_limit_gb", 20)) * (1 << 30)), keep={dst})
        except Exception as e:
            log.warning("変換の失敗 %s: %s", src, e)
            self._last_tc_error = f"{e}"
        finally:
            with self._tc_lock:
                self._transcoding.pop(item_id, None)
            ev.set()

    def transcoding_ids(self) -> set[int]:
        with self._tc_lock:
            return set(self._transcoding.keys())

    def prefetch(self, ids: list[int]) -> None:
        if not ids:
            return
        p = self.need()
        todo = []
        for iid in ids:
            r = p.db.q1("SELECT kind, rel_path, vcodec, acodec, container, meta_state FROM items WHERE id=?", (iid,))
            if r is None or r["kind"] != "video":
                continue
            codec = self.settings.get("transcode_codec", "h264")
            if codec == "h264" and r["meta_state"] == 1 and media.direct_playable(r["vcodec"], r["acodec"], r["container"]):
                continue
            if os.path.isfile(media.video_cache_path(p.cache_dir("video"), r["rel_path"], codec)):
                continue
            todo.append(iid)
        if todo:
            self.tc.stop_all()        # 先に積んだ分は捨てて、いま見ているものの続きを優先する
            self.tc.submit(TranscodeJob(todo, "次の動画の変換"))

    # ---------- 保存用の変換 ----------
    def archive_targets(self, item_ids: list[int] | None, f: dict | None = None) -> list[dict]:
        """変換の対象: ブラウザで直接再生できない動画（AVI など。メタデータ取得済み）で、まだ変換していないもの"""
        p = self.need()
        if item_ids:
            qs = ",".join("?" * len(item_ids))
            rows = p.db.q(f"SELECT id, rel_path, kind, vcodec, acodec, container, meta_state, duration, datetime, size FROM items WHERE id IN ({qs}) AND missing=0 ORDER BY id", tuple(item_ids))
        else:
            where, params = judge.build_where(f or {})
            rows = p.db.q(f"SELECT id, rel_path, kind, vcodec, acodec, container, meta_state, duration, datetime, size FROM items WHERE {where} AND kind='video' AND meta_state=1 ORDER BY station, datetime, id", params)
        return [dict(r) for r in rows if r["kind"] == "video" and r["meta_state"] == 1 and not media.direct_playable(r["vcodec"], r["acodec"], r["container"])]

    def archive_plan(self, f: dict) -> dict:
        p = self.need()
        rows = self.archive_targets(None, f)
        ffmpeg, _ = media.find_ffmpeg(self.settings.get("ffmpeg_path", ""))
        done = p.db.q1("SELECT COUNT(*) AS n, COALESCE(SUM(orig_size), 0) AS o, COALESCE(SUM(size), 0) AS s FROM items WHERE orig_rel_path IS NOT NULL")
        pending_meta = int(p.db.scalar("SELECT COUNT(*) FROM items WHERE kind='video' AND meta_state=0 AND missing=0") or 0)
        return dict(n=len(rows), bytes=sum(int(r["size"] or 0) for r in rows), nvenc=media.has_nvenc(ffmpeg), ffmpeg=ffmpeg,
                    done=dict(n=done["n"], orig_bytes=done["o"], new_bytes=done["s"]), pending_meta=pending_meta,
                    containers=sorted({(r["container"] or "") + "/" + (r["vcodec"] or "?") for r in rows}))

    def thumb(self, item_id: int) -> str:
        it, src = self.item_path(item_id)
        dst = media.thumb_cache_path(self.project.cache_dir("thumb"), it["rel_path"])
        if os.path.isfile(dst):
            return dst
        if not os.path.isfile(src):
            raise ApiError("ファイルがありません", 404)
        with self._thumb_sem:
            if not os.path.isfile(dst):
                ffmpeg, _ = media.find_ffmpeg(self.settings.get("ffmpeg_path", ""))
                media.make_thumb(ffmpeg, src, dst)
        return dst

    # ---------- 検出と種判別（第 2 段階） ----------
    def models_dir(self) -> str:
        d = self.settings.get("models_dir") or str(DATA_DIR / "models")
        os.makedirs(d, exist_ok=True)
        train.SHARED_DIR = os.path.join(d, "local")      # 追加学習のモデルの共有の置き場
        return d

    def model_hub(self):
        if self._hub is None:
            self._hub = detect.ModelHub(self.settings, self.models_dir())
        return self._hub

    def detect_plan(self, f: dict) -> dict:
        """検出の対象の数と環境"""
        p = self.need()
        where, params = judge.build_where(f or {})
        base = f"FROM items WHERE {where} AND missing=0 AND meta_state<>2"
        n_all = int(p.db.scalar(f"SELECT COUNT(*) {base}", params) or 0)
        n_todo = int(p.db.scalar(f"SELECT COUNT(*) {base} AND det_state=0", params) or 0)
        n_todo_nv = int(p.db.scalar(f"SELECT COUNT(*) {base} AND det_state=0 AND status<>'verified'", params) or 0)
        n_done = int(p.db.scalar(f"SELECT COUNT(*) {base} AND det_state=1", params) or 0)
        n_fail = int(p.db.scalar(f"SELECT COUNT(*) {base} AND det_state=2", params) or 0)
        n_low = int(p.db.scalar(f"SELECT COUNT(*) {base} AND flag='low'", params) or 0)
        pending_meta = int(p.db.scalar("SELECT COUNT(*) FROM items WHERE meta_state=0 AND missing=0") or 0)
        env = detect.env_status(self.settings, self.models_dir())
        if env["ok"] and self._hub is None and env["torch"] and not env["fake"]:
            pass        # torch はまだ読まない（読み込みは最初のジョブで）
        return dict(n_all=n_all, n_todo=n_todo, n_todo_unverified=n_todo_nv, n_done=n_done, n_failed=n_fail, n_low=n_low, pending_meta=pending_meta,
                    env=env, local_models=[dict(name=m["name"], kind=m.get("kind")) for m in train.list_models(p)], settings={k: p.setting(k) for k in ("md_model", "classifier", "speciesnet_model", "frame_first_sec", "frame_first_fps", "frame_interval_sec",
                                                                   "animal_threshold", "animal_min_frames", "person_threshold", "person_min_frames",
                                                                   "species_p1", "species_p2", "species_k", "max_crops", "detect_image_size", "save_features", "detect_skip_verified", "country")},
                    device=self.settings.get("device", "auto"), batch_size=self.settings.get("batch_size", 8))

    def train_plan(self) -> dict:
        """追加学習の材料の数（確認済み・特徴量あり・学習から外す・地点ごと・種ごと）と、学習済みのモデル"""
        p = self.need()
        db = p.db
        n_verified = int(db.scalar("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0") or 0)
        n_ready = int(db.scalar("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0 AND det_state=1 AND cls_state=1") or 0)
        n_excl = int(db.scalar("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0 AND det_state=1 AND cls_state=1 AND train_exclude=1") or 0)
        n_hard = int(db.scalar("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0 AND det_state=1 AND cls_state=1 AND (agree=3 OR recheck=1)") or 0)
        fdir = p.cache_dir("features")
        n_feat = len([f for f in os.listdir(fdir) if f.endswith(".npz")]) if os.path.isdir(fdir) else 0
        stations = [dict(station=r["station"] or "", n=r["n"]) for r in db.q("SELECT station, COUNT(*) AS n FROM items WHERE status='verified' AND missing=0 AND det_state=1 AND cls_state=1 GROUP BY station ORDER BY station")]
        species = [dict(code=r["species"], n=r["n"]) for r in db.q("SELECT j.species, COUNT(DISTINCT j.item_id) AS n FROM judgements j JOIN items i ON i.id=j.item_id WHERE i.status='verified' AND i.missing=0 AND i.det_state=1 AND i.cls_state=1 GROUP BY j.species ORDER BY n DESC")]
        env = detect.env_status(self.settings, self.models_dir())
        return dict(n_verified=n_verified, n_ready=n_ready, n_excluded=n_excl, n_hard=n_hard, n_features=n_feat, stations=stations, species=species,
                    models=train.list_models(p), classifier=p.setting("classifier"), env=env,
                    settings={k: p.setting(k) for k in train.TRAIN_DEFAULTS}, device=self.settings.get("device", "auto"))

    def detections_of(self, item_id: int) -> dict:
        p = self.need()
        it = p.db.q1("SELECT det_state, det_json, cls_state, cls_json, flag FROM items WHERE id=?", (item_id,))
        if it is None:
            raise ApiError("対象がありません", 404)
        rows = p.db.q("SELECT t_sec, category, conf, x, y, w, h, species, sp_conf FROM detections WHERE item_id=? ORDER BY t_sec, conf DESC", (item_id,))
        return dict(det_state=it["det_state"], det=json.loads(it["det_json"]) if it["det_json"] else None, cls_state=it["cls_state"],
                    cls=json.loads(it["cls_json"]) if it["cls_json"] else None, flag=it["flag"],
                    boxes=[dict(t=r["t_sec"], c=r["category"], p=r["conf"], x=r["x"], y=r["y"], w=r["w"], h=r["h"], sp=r["species"], sp_p=r["sp_conf"]) for r in rows])

    # ---------- 画面に渡す状態 ----------
    def snapshot(self) -> dict:
        ffmpeg, ffprobe = media.find_ffmpeg(self.settings.get("ffmpeg_path", ""))
        d = dict(app=APP_ID, app_name=APP_NAME, version=VERSION, build=BUILD, build_time=BUILD_TIME,
                 settings=self.settings.to_dict(), project=self.project.to_dict() if self.project else None,
                 ffmpeg=dict(ffmpeg=ffmpeg, ffprobe=ffprobe), jobs=self.jobs_status(), ui=self.state.ui,
                 recent_projects=self.settings.get("recent_projects", []), platform=os.name)
        if self.project and self.project.root:
            d["facets"] = judge.facets(self.project.db)
            d["species"] = self.project.species.to_dict()
            d["fields"] = self.project.fields.to_dict()
            d["conflicts_n"] = len(json.loads(self.project.db.get_meta("conflicts") or "{}"))
            d["last_item"] = self.state.last_item.get(self.project.dir)
        return d

    def jobs_status(self) -> dict:
        return dict(bg=self.bg.status(), transcode=self.tc.status(), archive=self.ar.status(), ml=self.ml.status(), transcoding=sorted(self.transcoding_ids()))

    def close(self) -> None:
        self.close_project()
        self.bg.close()
        self.tc.close()
        self.ar.close()
        self.ml.close()
        self.io.shutdown(wait=False)


# ---------- HTTP ----------
def jr(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False))


@web.middleware
async def errors_mw(request, handler):
    try:
        resp = await handler(request)
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp
    except ApiError as e:
        return jr(dict(error=str(e)), e.status)
    except web.HTTPException:
        raise
    except (ValueError, KeyError) as e:
        return jr(dict(error=str(e)), 400)
    except Exception as e:
        log.exception("API エラー: %s", request.path)
        return jr(dict(error=f"{type(e).__name__}: {e}"), 500)


async def _body(request) -> dict:
    text = await request.text()
    return json.loads(text) if text.strip() else {}


FILTER_KEYS = ("station", "season", "folder", "folder_prefix", "status", "kind", "species", "date_from", "date_to", "assignee", "flag", "missing", "text", "include_missing", "favorite", "corrected", "det", "agree", "exclude", "boxes")


def _filters(src) -> dict:
    f = {}
    for k in FILTER_KEYS:
        v = src.get(k)
        if v not in (None, "", "all"):
            f[k] = v
    if src.get("ids"):
        v = src["ids"]
        f["ids"] = [int(x) for x in (v.split(",") if isinstance(v, str) else v) if str(x).strip()]
    return f


def make_app(session: Session) -> web.Application:
    app = web.Application(middlewares=[errors_mw], client_max_size=64 * 1024 * 1024)
    app["session"] = session
    app["shutdown"] = asyncio.Event()
    S = session

    def io(fn, *a):
        return asyncio.get_running_loop().run_in_executor(S.io, fn, *a)

    def user() -> str:
        return S.settings.get("user_name") or ""

    async def index(request):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", f"{VERSION}.{BUILD}")
        return web.Response(text=html, content_type="text/html", charset="utf-8", headers={"Cache-Control": "no-store"})

    async def ping(request):
        return jr(dict(app=APP_ID, version=VERSION, build=BUILD, build_time=BUILD_TIME))

    async def state(request):
        return jr(await io(S.snapshot))

    async def dialog(request):
        b = await _body(request)
        p = await dialogs.pick(b.get("kind", "folder"), b.get("initial"), b.get("name"))
        return jr(dict(path=p))

    # ---- プロジェクト ----
    async def project_open(request):
        b = await _body(request)
        if S.opening:
            raise ApiError("いま開いています")
        S.opening = True
        try:
            res = await io(S.open_project, b.get("path", ""), bool(b.get("create")))
        finally:
            S.opening = False
        res["state"] = await io(S.snapshot)
        return jr(res)

    async def project_root(request):
        b = await _body(request)
        res = await io(S.set_root, b.get("path", ""))
        res["state"] = await io(S.snapshot)
        return jr(res)

    async def project_close(request):
        await io(S.close_project)
        S.state.last_project = None
        S.state.save()
        return jr(await io(S.snapshot))

    async def project_settings(request):
        b = await _body(request)
        p = S.need()
        old_pattern = p.setting("pattern")
        if "name" in b:
            p.rename(str(b.pop("name")))
        s = p.update_settings(b)
        if "pattern" in b and b["pattern"] != old_pattern:
            n = await io(scan_mod.apply_pattern, p)
            log.info("パターンを %s に変え、%d 件に当て直しました", b["pattern"], n)
        return jr(dict(settings=s, state=await io(S.snapshot)))

    async def pattern_preview_api(request):
        b = await _body(request)
        p = S.need()
        rels = await io(scan_mod.sample_rel_paths, p, 10)
        return jr(dict(names=names_in(b.get("pattern", "")), rows=pattern_preview(b.get("pattern", ""), rels)))

    async def rescan(request):
        S.need()
        S.bg.submit(ScanJob())
        return jr(dict(ok=True))

    async def facets(request):
        return jr(await io(judge.facets, S.need().db))

    # ---- 一覧 ----
    async def items(request):
        p = S.need()
        q = request.query
        f = _filters(q)
        sort, desc = q.get("sort", "datetime"), q.get("desc") in ("1", "true")
        offset, limit = int(q.get("offset", 0)), min(1000, int(q.get("limit", 200)))
        rows = await io(judge.list_items, p.db, f, sort, desc, offset, limit)
        total = await io(judge.count_items, p.db, f) if q.get("count") in ("1", "true") else None
        return jr(dict(rows=rows, offset=offset, total=total, transcoding=sorted(S.transcoding_ids())))

    async def items_count(request):
        p = S.need()
        return jr(dict(total=await io(judge.count_items, p.db, _filters(request.query))))

    async def item_detail(request):
        p = S.need()
        d = await io(judge.detail, p.db, int(request.match_info["id"]))
        if d is None:
            raise ApiError("対象がありません", 404)
        d["abs_path"] = abs_of(d["rel_path"], p.root)
        d["exists"] = os.path.isfile(d["abs_path"])
        S.state.last_item[p.dir] = d["id"]
        return jr(d)

    async def item_index(request):
        p = S.need()
        q = request.query
        i = await io(judge.index_of, p.db, _filters(q), q.get("sort", "datetime"), q.get("desc") in ("1", "true"), int(request.match_info["id"]))
        return jr(dict(index=i))

    async def item_neighbor(request):
        p = S.need()
        q = request.query
        r = await io(judge.neighbor, p.db, _filters(q), q.get("sort", "datetime"), q.get("desc") in ("1", "true"),
                     int(request.match_info["id"]), int(q.get("step", 1)))
        return jr(dict(item=r))

    async def item_save(request):
        p = S.need()
        b = await _body(request)
        res = await io(judge.save, p.db, int(request.match_info["id"]), b, user())
        S.undo_stack.append(res["history_id"])
        S.state.save()
        return jr(res)

    async def time_apply(request):
        p = S.need()
        b = await _body(request)
        f = _filters(b.get("filters") or {})
        if b.get("ids"):
            f["ids"] = [int(x) for x in b["ids"]]
        if not f:
            raise ApiError("範囲が指定されていません")
        off = b.get("offset_sec")
        n = await io(judge.apply_time_offset, p.db, f, int(off) if off is not None and not b.get("clear") else None, str(b.get("note") or ""), user())
        return jr(dict(n=n))

    async def item_favorite(request):
        p = S.need()
        b = await _body(request)
        await io(judge.set_favorite, p.db, int(request.match_info["id"]), bool(b.get("on")))
        return jr(dict(ok=True, favorite=bool(b.get("on"))))

    async def item_exclude(request):
        p = S.need()
        b = await _body(request)
        await io(judge.set_exclude, p.db, int(request.match_info["id"]), bool(b.get("on")))
        return jr(dict(ok=True, train_exclude=bool(b.get("on"))))

    async def items_ids(request):
        p = S.need()
        q = request.query
        return jr(dict(ids=await io(judge.list_ids, p.db, _filters(q), q.get("sort", "datetime"), q.get("desc") in ("1", "true"))))

    async def items_bulk(request):
        p = S.need()
        b = await _body(request)
        ids = [int(x) for x in (b.get("ids") or [])]
        action = b.get("action")
        if action == "delete":
            res = await io(judge.delete_items, p, ids, b.get("to_trash", True))
            S.undo_stack.clear()
            return jr(res)
        if action in ("judge", "clear"):
            payload = dict(b.get("payload") or {})
            if action == "clear":
                payload = dict(species=[], status="new")
            res = await io(judge.bulk_judge, p.db, ids, payload, user())
            S.undo_stack.extend(res["history_ids"])
            S.state.save()
            return jr(dict(n=res["n"], failed=res["failed"]))
        if action not in ("favorite", "unfavorite", "exclude", "include"):
            raise ApiError("action が不正です")
        return jr(dict(n=await io(judge.bulk_mark, p.db, ids, action)))

    async def item_note(request):
        p = S.need()
        b = await _body(request)
        await io(judge.set_note, p.db, int(request.match_info["id"]), str(b.get("note", "")))
        return jr(dict(ok=True))

    async def undo(request):
        p = S.need()
        b = await _body(request)
        hid = b.get("history_id")
        if hid is None and S.undo_stack:
            hid = S.undo_stack.pop()
        res = await io(judge.undo, p.db, hid, user())
        if res is None:
            raise ApiError("取り消せる保存がありません")
        return jr(res)

    async def assign(request):
        p = S.need()
        b = await _body(request)
        n = await io(judge.set_assignee, p.db, _filters(b.get("filters") or {}), str(b.get("assignee") or ""))
        return jr(dict(n=n))

    # ---- 再生 ----
    async def prepare(request):
        S.need()
        b = await _body(request)
        iid = int(request.match_info["id"])
        res = await io(S.ensure_playable, iid, False, bool(b.get("no_mjpeg")))
        nxt = [int(x) for x in (b.get("next_ids") or [])][:int(S.settings.get("prefetch_n", 3))]
        if nxt:
            await io(S.prefetch, nxt)
        return jr(res)

    async def video(request):
        p = S.need()
        iid = int(request.match_info["id"])
        it, src = await io(S.item_path, iid)
        if it["kind"] != "video":
            raise ApiError("動画ではありません")
        res = await io(S.ensure_playable, iid, False)
        if res.get("direct"):
            path = src
        else:
            path = media.video_cache_path(p.cache_dir("video"), it["rel_path"], S.settings.get("transcode_codec", "h264"))
            if not res.get("ready") or not os.path.isfile(path):
                return jr(dict(error="変換中です", transcoding=True), 409)
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

    async def thumb(request):
        iid = int(request.match_info["id"])
        try:
            path = await io(S.thumb, iid)
        except ApiError:
            raise
        except Exception as e:
            log.debug("サムネイルの失敗 %s: %s", iid, e)
            raise ApiError("サムネイルを作れません", 404)
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    async def image(request):
        it, src = await io(S.item_path, int(request.match_info["id"]))
        if not os.path.isfile(src):
            raise ApiError("ファイルがありません", 404)
        return web.FileResponse(src, headers={"Cache-Control": "private, max-age=3600"})

    async def raw(request):
        """元のファイルそのもの（MJPEG の AVI を画面側で直接読むため。Range に対応）"""
        it, src = await io(S.item_path, int(request.match_info["id"]))
        if not os.path.isfile(src):
            raise ApiError("ファイルがありません", 404)
        return web.FileResponse(src, chunk_size=1 << 20, headers={"Cache-Control": "private, max-age=3600"})

    # ---- 種リストと追加項目 ----
    async def species_get(request):
        return jr(S.need().species.to_dict())

    async def species_post(request):
        p = S.need()
        b = await _body(request)
        if "enabled" in b:
            p.species.set_enabled([str(c) for c in b["enabled"]])
        if "rows" in b:
            p.species.update(b["rows"])
        return jr(p.species.to_dict())

    async def species_add(request):
        p = S.need()
        b = await _body(request)
        if not any((b.get(k) or "").strip() for k in ("name_ja", "name_en", "name_sci")):
            raise ApiError("名前を入れてください")
        row = p.species.add(b)
        return jr(dict(added=row, **p.species.to_dict()))

    async def species_import(request):
        p = S.need()
        b = await _body(request)
        from .species import load_csv
        rows = await io(load_csv, b.get("path", ""))
        if b.get("replace"):
            p.species.update(rows)
        else:
            have = {r["code"] for r in p.species.rows}
            p.species.update(p.species.rows + [r for r in rows if r["code"] not in have])
        return jr(dict(n=len(rows), **p.species.to_dict()))

    async def species_export(request):
        p = S.need()
        b = await _body(request)
        from .species import save_csv
        path = b.get("path") or os.path.join(p.dir, "exports", "species.csv")
        await io(save_csv, path, p.species.rows)
        return jr(dict(path=path))

    async def fields_get(request):
        return jr(S.need().fields.to_dict())

    async def fields_post(request):
        p = S.need()
        b = await _body(request)
        p.fields.update(b.get("fields") or [])
        return jr(p.fields.to_dict())

    # ---- ジョブ ----
    async def jobs(request):
        return jr(S.jobs_status())

    async def jobs_meta(request):
        S.need()
        b = await _body(request)
        ids = [int(x) for x in (b.get("ids") or [])] or None
        if b.get("redo"):
            f = _filters(b.get("filters") or {})
            where, params = judge.build_where(f)
            S.project.db.x(f"UPDATE items SET meta_state=0 WHERE id IN (SELECT id FROM items WHERE {where})", params)
        S.bg.submit(MetaJob(ids))
        return jr(S.jobs_status())

    async def jobs_stop(request):
        b = await _body(request)
        lane = b.get("lane")
        if lane in (None, "bg"):
            S.bg.stop_all()
        if lane in (None, "transcode"):
            S.tc.stop_all()
        if lane in (None, "archive"):
            S.ar.stop_all()
        if lane in (None, "ml"):
            S.ml.stop_all()
        return jr(S.jobs_status())

    # ---- 保存用の変換 ----
    async def archive_plan(request):
        b = await _body(request)
        return jr(await io(S.archive_plan, _filters(b.get("filters") or {})))

    async def archive_start(request):
        p = S.need()
        b = await _body(request)
        if S.ar.status()["current"]:
            raise ApiError("保存用の変換はもう動いています")
        opts = dict(codec=b.get("codec") or p.setting("archive_codec"), crf=int(b.get("crf") or p.setting("archive_crf")),
                    original=b.get("original") or p.setting("archive_original"), move_to=b.get("move_to") or p.setting("archive_move_to"))
        p.update_settings(dict(archive_codec=opts["codec"], archive_crf=opts["crf"], archive_original=opts["original"], archive_move_to=opts["move_to"]))
        ids = [int(x) for x in (b.get("ids") or [])] or None
        if ids is None and b.get("filters"):
            ids = [r["id"] for r in await io(S.archive_targets, None, _filters(b["filters"]))] or [-1]
        S.ar.submit(ArchiveJob(ids, opts))
        return jr(S.jobs_status())

    async def archive_stop(request):
        S.ar.stop_all()
        return jr(S.jobs_status())

    # ---- 検出と種判別（第 2 段階） ----
    async def detect_plan(request):
        b = await _body(request)
        return jr(await io(S.detect_plan, _filters(b.get("filters") or {})))

    async def detect_start(request):
        p = S.need()
        b = await _body(request)
        if S.ml.status()["current"]:
            raise ApiError("検出はもう動いています")
        env = detect.env_status(S.settings, S.models_dir())
        if not env["ok"]:
            raise ApiError("torch / megadetector / speciesnet が入っていません（インストール.bat で環境を作り直してください）")
        ps = {}
        for k in ("md_model", "classifier", "speciesnet_model"):
            if b.get(k) not in (None, ""):
                ps[k] = str(b[k])
        if "country" in b:
            ps["country"] = str(b.get("country") or "").strip().upper()[:3]
        for k in ("frame_first_sec", "frame_first_fps", "animal_threshold", "person_threshold", "species_p1", "species_p2"):
            if b.get(k) not in (None, ""):
                ps[k] = float(b[k])
        if "frame_interval_sec" in b:          # 空か auto なら自動（GPU 0.2 秒 / CPU 0.5 秒）
            v = b.get("frame_interval_sec")
            try:
                f = float(v)
            except (TypeError, ValueError):
                f = 0.0
            ps["frame_interval_sec"] = f if f > 0 else "auto"
            ps["frame_interval_fixed"] = f > 0
        for k in ("animal_min_frames", "person_min_frames", "species_k", "max_crops", "detect_image_size"):
            if b.get(k) not in (None, ""):
                ps[k] = int(b[k])
        for k in ("save_features", "detect_skip_verified"):
            if k in b:
                ps[k] = bool(b[k])
        if ps:
            p.update_settings(ps)
        ch = {}
        if b.get("device") in ("auto", "cuda", "mps", "cpu") and b["device"] != S.settings.get("device"):
            ch["device"] = b["device"]
            if S._hub is not None and S._hub.device is not None:      # 機器を変えたらモデルを読み直す
                S._hub.unload()
                S._hub.device = None
        if b.get("batch_size"):
            ch["batch_size"] = int(b["batch_size"])
        if ch:
            S.settings.update(ch)
        ids = [int(x) for x in (b.get("ids") or [])] or None
        if ids is None and b.get("filters"):
            where, params = judge.build_where(_filters(b["filters"]))
            ids = [r["id"] for r in p.db.q(f"SELECT id FROM items WHERE {where}", params)] or [-1]
        opts = dict(redo=bool(b.get("redo")), include_verified=bool(b.get("include_verified")), use_existing=bool(b.get("use_existing")))
        if ids and opts["redo"] and not opts["use_existing"]:
            await io(detect.clear_detections, p.db, ids)
        S.ml.submit(DetectJob(ids, opts))
        return jr(S.jobs_status())

    async def detect_stop(request):
        S.ml.stop_all()
        return jr(S.jobs_status())

    async def detect_env(request):
        return jr(detect.env_status(S.settings, S.models_dir()))

    async def detect_eval(request):
        p = S.need()
        b = await _body(request)
        return jr(await io(detect.evaluate, p, _filters(b.get("filters") or {})))

    async def detect_eval_csv(request):
        p = S.need()
        b = await _body(request)
        path = b.get("path") or os.path.join(p.dir, "exports", f"agreement_{__import__('datetime').date.today().isoformat()}.csv")
        return jr(await io(detect.export_evaluation, p, path, _filters(b.get("filters") or {}), S.settings.get("language", "ja")))

    # ---- 追加学習（第 3 段階） ----
    async def train_plan(request):
        return jr(await io(S.train_plan))

    async def train_start(request):
        p = S.need()
        b = await _body(request)
        if S.ml.status()["current"]:
            raise ApiError("検出か学習がもう動いています")
        kind = "B" if b.get("kind") == "B" else "A"
        env = detect.env_status(S.settings, S.models_dir())
        if kind == "B" and not env["ok"]:
            raise ApiError("torch / speciesnet が入っていません（方式 B には要ります）")
        if kind == "B" and env.get("fake") and os.environ.get("TRAPNOTE_FAKE_ML") != "torch":
            raise ApiError("偽のモデルでは方式 B は使えません")
        name = train.safe_model_name(b.get("name") or f"{kind}_{time.strftime('%Y%m%d_%H%M')}")
        if os.path.isdir(os.path.join(train.models_root(p), name)) and not b.get("overwrite"):
            raise ApiError(f"同じ名前のモデルがあります: {name}")
        ps = {}
        for k in ("train_min_conf", "train_weight_hard", "train_holdout_frac", "train_base_mix", "train_lr_b", "train_unfreeze"):
            if b.get(k) not in (None, ""):
                ps[k] = float(b[k])
        for k in ("train_min_per_class", "train_max_per_class", "train_epochs_b", "train_batch_b", "train_seed"):
            if b.get(k) not in (None, ""):
                ps[k] = int(b[k])
        if "train_use_empty" in b:
            ps["train_use_empty"] = bool(b["train_use_empty"])
        if ps:
            p.update_settings(ps)
        if b.get("device") in ("auto", "cuda", "mps", "cpu") and b["device"] != S.settings.get("device"):
            S.settings.update(dict(device=b["device"]))
            if S._hub is not None and S._hub.device is not None:
                S._hub.unload()
                S._hub.device = None
        opts = dict(kind=kind, holdout_stations=[str(x) for x in (b.get("holdout_stations") or [])] or None)
        S.ml.submit(TrainJob(opts, name))
        return jr(dict(name=name, **S.jobs_status()))

    async def train_stop(request):
        S.ml.stop_all()
        return jr(S.jobs_status())

    async def train_models(request):
        p = S.need()
        return jr(dict(models=await io(train.list_models, p), classifier=p.setting("classifier")))

    async def train_models_delete(request):
        p = S.need()
        b = await _body(request)
        name = train.safe_model_name(str(b.get("name") or ""))
        if p.setting("classifier") == f"local:{name}":
            p.update_settings(dict(classifier="speciesnet"))
            if S._hub is not None:
                S._hub.set_local(p, "speciesnet")
        return jr(dict(ok=await io(train.delete_model, p, name)))

    async def train_use(request):
        """判定に使う分類器を替える（speciesnet / local:<名前>）。reclassify なら特徴量から判定を付け直す"""
        p = S.need()
        b = await _body(request)
        want = str(b.get("classifier") or "speciesnet")
        if want.startswith("local:") and train.model_folder(p, want[6:]) is None:
            raise ApiError(f"モデルがありません: {want}")
        p.update_settings(dict(classifier=want))
        if b.get("reclassify"):
            if S.ml.status()["current"]:
                raise ApiError("検出か学習がもう動いています")
            ids = None
            if b.get("filters"):
                where, params = judge.build_where(_filters(b["filters"]))
                ids = [r["id"] for r in p.db.q(f"SELECT id FROM items WHERE {where}", params)] or [-1]
            S.ml.submit(ReclassifyJob(ids, want))
        return jr(dict(classifier=want, **S.jobs_status()))

    async def train_result(request):
        p = S.need()
        name = train.safe_model_name(request.query.get("name") or "")
        d = train.model_folder(p, name)
        if d is None:
            raise ApiError("モデルがありません", 404)
        with open(os.path.join(d, "model.json"), encoding="utf-8") as fh:
            return jr(json.load(fh))

    async def train_models_share(request):
        p = S.need()
        b = await _body(request)
        return jr(dict(path=await io(train.share_model, p, str(b.get("name") or ""))))

    async def bulk_verify(request):
        p = S.need()
        b = await _body(request)
        f = _filters(b.get("filters") or {})
        if b.get("count_only"):
            return jr(dict(n=await io(judge.bulk_verify_count, p.db, f)))
        n = await io(judge.bulk_verify, p.db, f, user())
        return jr(dict(n=n))

    async def detect_labels(request):
        """SpeciesNet のラベル → 種コードの対応表（分類器を読み込んだあとだけ）"""
        p = S.need()
        hub = S._hub
        if hub is None or not hub.labels:
            raise ApiError("SpeciesNet をまだ読み込んでいません（自動判定を一度動かしてください）")
        codes = detect.build_label_map(hub.labels, p.species.rows)
        out: dict[str, list[str]] = {}
        for lab, c in zip(hub.labels, codes):
            parts = lab.split(";")
            name = (parts[6] if len(parts) > 6 and parts[6] else " ".join(x for x in parts[4:6] if x)) or lab
            out.setdefault(c, []).append(name)
        return jr(dict(n=len(hub.labels), model=hub.classifier_name, map={c: dict(n=len(v), names=v[:40]) for c, v in out.items()}))

    async def item_detections(request):
        S.need()
        return jr(await io(S.detections_of, int(request.match_info["id"])))

    async def item_boxes(request):
        p = S.need()
        b = await _body(request)
        return jr(await io(detect.save_boxes, p, int(request.match_info["id"]), list(b.get("boxes") or [])))

    # ---- 書き出しと取り込み ----
    async def export_csv(request):
        p = S.need()
        b = await _body(request)
        f = _filters(b.get("filters") or {})
        path = b.get("path") or os.path.join(p.dir, "exports", "trapnote_export.csv")
        res = await io(export_mod.export_csv, p, path, f, S.settings.get("language", "ja"), not b.get("judged_only"))
        if b.get("records"):
            rp = os.path.splitext(path)[0] + "_records.csv"
            res["records"] = await io(export_mod.export_records_csv, p, rp, f)
        return jr(res)

    async def export_md_json(request):
        p = S.need()
        b = await _body(request)
        path = b.get("path") or os.path.join(p.dir, "exports", "detections_md.json")
        return jr(await io(detect.export_md_json, p, path, _filters(b.get("filters") or {})))

    async def export_timelapse(request):
        p = S.need()
        b = await _body(request)
        path = b.get("path") or os.path.join(p.root or p.dir, f"{os.path.basename(p.root or p.dir)}.ddb")
        res = await io(timelapse.export_timelapse, p, path, _filters(b.get("filters") or {}), "flags" if b.get("species_style") == "flags" else "choice",
                       b.get("include_fields", True), b.get("use_corrected", True), S.settings.get("language", "ja"))
        return jr(res)

    async def export_index(request):
        p = S.need()
        return jr(await io(export_mod.write_index_csv, p))

    async def import_preview(request):
        b = await _body(request)
        path = norm(b.get("path", ""))
        if not os.path.isfile(path):
            raise ApiError(f"CSV がありません: {path}")
        return jr(await io(export_mod.csv_preview, path))

    async def import_values(request):
        b = await _body(request)
        return jr(await io(export_mod.csv_values, norm(b.get("path", "")), b.get("column", ""), 200, S.project.species if S.project else None))

    async def import_csv(request):
        p = S.need()
        b = await _body(request)
        res = await io(lambda: export_mod.import_csv(p, norm(b.get("path", "")), list(b.get("path_cols") or []), b.get("species_col") or "",
                                                     b.get("datetime_col") or None, b.get("mapping") or {}, user(), bool(b.get("overwrite_verified")), b.get("corrected_col") or None,
                                                     bool(b.get("as_verified"))))
        return jr(res)

    # ---- YOLO 形式の学習データ（<種>/images + <種>/labels） ----
    async def yolo_preview(request):
        p = S.need()
        return jr(await io(yolo.preview, p))

    async def yolo_import(request):
        p = S.need()
        b = await _body(request)
        mapping = {str(k): (str(v) if v else None) for k, v in (b.get("mapping") or {}).items()}
        S.bg.submit(YoloImportJob(mapping, dict(as_verified=b.get("as_verified", True), set_station=b.get("set_station", True), overwrite_verified=b.get("overwrite_verified"), station_only=b.get("station_only")), user()), front=True)
        return jr(S.jobs_status())

    async def yolo_export(request):
        p = S.need()
        b = await _body(request)
        opts = dict(out_dir=norm(b.get("out_dir") or os.path.join(p.dir, "exports", "yolo")), filters=_filters(b.get("filters") or {}),
                    per_species=b.get("per_species", True), class_mode=b.get("class_mode") or "animal0", write_images=b.get("write_images", True), in_place=bool(b.get("in_place")))
        S.bg.submit(YoloExportJob(opts), front=True)
        return jr(S.jobs_status())

    async def timelapse_preview(request):
        p = S.need()
        b = await _body(request)
        path = norm(b.get("path", ""))
        if not os.path.isfile(path):
            raise ApiError(f"ファイルがありません: {path}")
        return jr(await io(timelapse.preview, p, path))

    async def timelapse_import(request):
        p = S.need()
        b = await _body(request)
        path = norm(b.get("path", ""))
        if not os.path.isfile(path):
            raise ApiError(f"ファイルがありません: {path}")
        res = {}
        if b.get("template", True):
            tpl = await io(timelapse.read_template, path)
            res["template"] = await io(timelapse.import_template, p, tpl, b.get("mapping") or {}, bool(b.get("only_mapped")))
        if b.get("data") and tpl_has_data(path):
            res["data"] = await io(lambda: timelapse.import_data(p, path, b.get("prefix") or "", bool(b.get("as_verified")), user(), bool(b.get("overwrite_verified")), b.get("mapping") or {}))
        res["species"] = p.species.to_dict()
        res["fields"] = p.fields.to_dict()
        return jr(res)

    def tpl_has_data(path: str) -> bool:
        try:
            return timelapse.read_template(path)["has_data"]
        except Exception:
            return False

    async def merge_export(request):
        p = S.need()
        b = await _body(request)
        return jr(await io(merge_mod.export_judgements, p, b.get("path"), user(), _filters(b.get("filters") or {})))

    async def merge_import(request):
        p = S.need()
        b = await _body(request)
        path = norm(b.get("path", ""))
        if not os.path.isfile(path):
            raise ApiError(f"ファイルがありません: {path}")
        return jr(await io(merge_mod.import_judgements, p, path, user()))

    async def merge_conflicts(request):
        return jr(dict(conflicts=await io(merge_mod.list_conflicts, S.need().db)))

    async def merge_resolve(request):
        p = S.need()
        b = await _body(request)
        await io(merge_mod.resolve_conflict, p.db, int(b.get("item_id")), b.get("choice") or "mine", user())
        return jr(dict(ok=True))

    # ---- 設定など ----
    async def settings_get(request):
        return jr(S.settings.to_dict())

    async def settings_post(request):
        b = await _body(request)
        d = S.settings.update(b)
        if "ffmpeg_path" in b:
            media.reset_ffmpeg_cache()
        return jr(d)

    async def ui_post(request):
        b = await _body(request)
        S.state.ui.update(b)
        S.state.save()
        return jr(dict(ok=True))

    async def quit_(request):
        log.info("終了の要求")
        asyncio.get_running_loop().call_later(0.2, request.app["shutdown"].set)
        return jr(dict(ok=True))

    r = app.router
    r.add_get("/", index)
    r.add_get("/api/ping", ping)
    r.add_get("/api/state", state)
    r.add_post("/api/dialog", dialog)
    r.add_post("/api/project/open", project_open)
    r.add_post("/api/project/root", project_root)
    r.add_post("/api/project/close", project_close)
    r.add_post("/api/project/settings", project_settings)
    r.add_post("/api/project/pattern/preview", pattern_preview_api)
    r.add_post("/api/project/rescan", rescan)
    r.add_get("/api/project/facets", facets)
    r.add_get("/api/items", items)
    r.add_get("/api/items/count", items_count)
    r.add_get("/api/items/ids", items_ids)
    r.add_get("/api/items/{id}", item_detail)
    r.add_get("/api/items/{id}/index", item_index)
    r.add_get("/api/items/{id}/neighbor", item_neighbor)
    r.add_post("/api/items/{id}/save", item_save)
    r.add_post("/api/items/{id}/note", item_note)
    r.add_post("/api/items/{id}/favorite", item_favorite)
    r.add_post("/api/time/apply", time_apply)
    r.add_post("/api/items/{id}/prepare", prepare)
    r.add_get("/api/items/{id}/video", video)
    r.add_get("/api/items/{id}/thumb", thumb)
    r.add_get("/api/items/{id}/image", image)
    r.add_post("/api/undo", undo)
    r.add_post("/api/assign", assign)
    r.add_get("/api/species", species_get)
    r.add_post("/api/species", species_post)
    r.add_post("/api/species/add", species_add)
    r.add_post("/api/species/import", species_import)
    r.add_post("/api/species/export", species_export)
    r.add_get("/api/fields", fields_get)
    r.add_post("/api/fields", fields_post)
    r.add_get("/api/jobs", jobs)
    r.add_post("/api/jobs/meta", jobs_meta)
    r.add_post("/api/jobs/stop", jobs_stop)
    r.add_post("/api/archive/plan", archive_plan)
    r.add_post("/api/archive/start", archive_start)
    r.add_post("/api/archive/stop", archive_stop)
    r.add_post("/api/export/csv", export_csv)
    r.add_post("/api/export/index", export_index)
    r.add_post("/api/export/timelapse", export_timelapse)
    r.add_get("/api/yolo/preview", yolo_preview)
    r.add_post("/api/yolo/import", yolo_import)
    r.add_post("/api/yolo/export", yolo_export)
    r.add_post("/api/export/md_json", export_md_json)
    r.add_post("/api/detect/plan", detect_plan)
    r.add_post("/api/detect/start", detect_start)
    r.add_post("/api/detect/stop", detect_stop)
    r.add_get("/api/detect/env", detect_env)
    r.add_get("/api/detect/labels", detect_labels)
    r.add_post("/api/detect/eval", detect_eval)
    r.add_post("/api/detect/eval/csv", detect_eval_csv)
    r.add_post("/api/judge/bulk_verify", bulk_verify)
    r.add_get("/api/train/plan", train_plan)
    r.add_post("/api/train/start", train_start)
    r.add_post("/api/train/stop", train_stop)
    r.add_get("/api/train/models", train_models)
    r.add_post("/api/train/models/delete", train_models_delete)
    r.add_post("/api/train/models/share", train_models_share)
    r.add_post("/api/train/use", train_use)
    r.add_get("/api/train/result", train_result)
    r.add_get("/api/items/{id}/detections", item_detections)
    r.add_get("/api/items/{id}/raw", raw)
    r.add_post("/api/items/bulk", items_bulk)
    r.add_post("/api/items/{id}/exclude", item_exclude)
    r.add_post("/api/items/{id}/boxes", item_boxes)
    r.add_post("/api/import/csv/preview", import_preview)
    r.add_post("/api/import/csv/values", import_values)
    r.add_post("/api/import/csv", import_csv)
    r.add_post("/api/import/timelapse/preview", timelapse_preview)
    r.add_post("/api/import/timelapse", timelapse_import)
    r.add_post("/api/merge/export", merge_export)
    r.add_post("/api/merge/import", merge_import)
    r.add_get("/api/merge/conflicts", merge_conflicts)
    r.add_post("/api/merge/resolve", merge_resolve)
    r.add_get("/api/settings", settings_get)
    r.add_post("/api/settings", settings_post)
    r.add_post("/api/ui", ui_post)
    r.add_post("/api/quit", quit_)
    r.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def serve(session: Session, host: str, port: int, on_ready=None) -> None:
    app = make_app(session)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("サーバー: http://%s:%d/", host, port)
    if on_ready:
        on_ready(port)
    await app["shutdown"].wait()
    await asyncio.sleep(0.3)
    session.close()
    await runner.cleanup()
    log.info("終了しました")
