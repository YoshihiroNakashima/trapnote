"""data.sqlite の読み書き。対象（items）、判定（judgements）、追加項目の値（field_values）、履歴（history）、ジョブ（jobs）。
WAL モードで、裏のジョブ（別スレッド）と画面の読み書きを両立する。接続はスレッドごとに 1 つ"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime

from . import SCHEMA_VERSION

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  rel_path TEXT NOT NULL UNIQUE,         -- ルートからの相対パス（/ 区切り）
  rel_key TEXT NOT NULL UNIQUE,          -- 比較用（小文字）
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                    -- video | image
  folder TEXT NOT NULL,                  -- 相対フォルダ
  size INTEGER,
  mtime REAL,
  season TEXT, station TEXT,             -- フォルダ名のパターンから
  pattern_json TEXT,                     -- パターンのほかの項目（site、camera など）
  duration REAL, width INTEGER, height INTEGER, fps REAL, n_frames INTEGER,
  vcodec TEXT, acodec TEXT, container TEXT,
  datetime TEXT,                         -- YYYY/MM/DD HH:MM:SS
  datetime_source TEXT,                  -- metadata | exif | mtime | csv
  meta_state INTEGER NOT NULL DEFAULT 0, -- 0 未 / 1 済 / 2 失敗
  meta_error TEXT,
  missing INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',    -- new（未処理）/ auto（自動判定・取り込み）/ verified（確認済み）
  verified_by TEXT, verified_at TEXT,
  assignee TEXT,
  note TEXT,
  det_state INTEGER NOT NULL DEFAULT 0,  -- 第 2 段階: 検出 0 未 / 1 済 / 2 失敗
  det_json TEXT,                         -- 検出の要約
  cls_state INTEGER NOT NULL DEFAULT 0,  -- 第 2 段階: 種判別
  cls_json TEXT,
  flag TEXT,                             -- 要確認などの印
  orig_rel_path TEXT,                    -- 保存用の変換で置き換える前の相対パス（AVI）
  orig_size INTEGER,                     -- 置き換える前のサイズ
  archived_at TEXT,                      -- 保存用の変換をした日時
  favorite INTEGER NOT NULL DEFAULT 0,   -- お気に入り（きれいに撮れた、面白い行動など）
  datetime_corrected TEXT,               -- 時刻の修正後（カメラの時計のずれを直したもの）。NULL = 修正なし
  time_offset_sec INTEGER,               -- 修正に使ったずれ（秒）。修正後 = 元 + ずれ
  time_note TEXT,                        -- 修正の理由など
  auto_json TEXT,                        -- 第 2 段階: モデルの判定（{"model", "species": [{"species","confidence"}], "at"}）。人の判定と並べて見る・比べるため
  agree INTEGER,                         -- 人の判定（確認済み / CSV / Timelapse 由来）とモデルの判定の比較: NULL = 比べられない / 1 一致 / 2 部分一致 / 3 不一致
  recheck INTEGER NOT NULL DEFAULT 0,    -- 人の判定と AI の判定が食い違っていたものを、人が見直して確定した（第 3 段階の追加学習で重みを付ける）
  train_exclude INTEGER NOT NULL DEFAULT 0,  -- 学習から外す（人の目にしか分からない動画、地点ごと不適など。評価には使う）
  n_boxes INTEGER NOT NULL DEFAULT 0,        -- 枠の数（1 フレームに入る動物の枠の最大数。閾値以上のもの。絞り込みと並べ替えに）
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_station ON items(station);
CREATE INDEX IF NOT EXISTS idx_items_season ON items(season);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_datetime ON items(datetime);
CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder);
CREATE INDEX IF NOT EXISTS idx_items_meta ON items(meta_state);

CREATE TABLE IF NOT EXISTS judgements (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  species TEXT NOT NULL,                 -- 種のコード（種リストの code。固定項目は empty / human / vehicle / area / unknown）
  confidence REAL,
  origin TEXT NOT NULL,                  -- manual | auto | csv
  model TEXT,
  created_at TEXT, created_by TEXT,
  UNIQUE(item_id, species)
);
CREATE INDEX IF NOT EXISTS idx_judge_item ON judgements(item_id);
CREATE INDEX IF NOT EXISTS idx_judge_species ON judgements(species);

CREATE TABLE IF NOT EXISTS auto_results (                 -- モデルの出力はそのまま残す（人が直しても消さない。評価と学習に使う）
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  species TEXT NOT NULL,
  confidence REAL,
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_auto_item ON auto_results(item_id);

CREATE TABLE IF NOT EXISTS field_values (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  judgement_id INTEGER REFERENCES judgements(id) ON DELETE CASCADE,   -- NULL = 対象ごとの項目
  field_key TEXT NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,        -- ＋で増やした記録の番号（0 始まり）
  value TEXT
);
CREATE INDEX IF NOT EXISTS idx_fv_item ON field_values(item_id);
CREATE INDEX IF NOT EXISTS idx_fv_judge ON field_values(judgement_id);

CREATE TABLE IF NOT EXISTS history (                      -- 確定の取り消し用（対象ごとの判定の前後）
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL,
  time TEXT NOT NULL,
  action TEXT NOT NULL,
  by TEXT,
  before_json TEXT,
  after_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_hist_item ON history(item_id);

CREATE TABLE IF NOT EXISTS detections (                   -- 第 2 段階: フレームごとの検出枠（MegaDetector）と枠ごとの種（SpeciesNet）
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  t_sec REAL NOT NULL,                   -- 動画の秒（静止画は 0）
  category TEXT NOT NULL,                -- animal | person | vehicle
  conf REAL NOT NULL,
  x REAL, y REAL, w REAL, h REAL,        -- 正規化座標（左上と幅・高さ。0〜1）
  species TEXT,                          -- 枠の種（コード。分類器に入れた枠だけ）
  sp_conf REAL
);
CREATE INDEX IF NOT EXISTS idx_det_item ON detections(item_id);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                    -- meta | transcode | detect | classify | train | import ...
  params_json TEXT,
  state TEXT NOT NULL,                   -- queued | running | done | stopped | failed
  total INTEGER DEFAULT 0, done INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
  message TEXT,
  started_at TEXT, finished_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


# 枠の数（1 フレームに入る動物の枠の最大数、信頼度が閾値以上のもの）を items.n_boxes に
N_BOXES_SQL_ONE = ("UPDATE items SET n_boxes = COALESCE((SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM detections d WHERE d.item_id = items.id AND d.category = 'animal' "
                   "AND d.conf >= ? GROUP BY d.t_sec)), 0) WHERE id = ?")
N_BOXES_SQL_ALL = ("UPDATE items SET n_boxes = COALESCE((SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM detections d WHERE d.item_id = items.id AND d.category = 'animal' "
                   "AND d.conf >= ? GROUP BY d.t_sec)), 0) WHERE det_state = 1")


class Database:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self.write_lock = threading.RLock()      # 書き込みは 1 本ずつ（SQLite の busy を避ける）
        self._init()

    # ---------- 接続 ----------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA temp_store=MEMORY")
            c.execute("PRAGMA cache_size=-65536")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self._local.conn = None

    def _init(self) -> None:
        c = self.conn()
        with self.write_lock:
            c.executescript(SCHEMA)
            ver = self.get_meta("schema_version")
            if ver is None:
                self.set_meta("schema_version", str(SCHEMA_VERSION))
            elif int(ver) < SCHEMA_VERSION:
                self.migrate(int(ver))
                self.set_meta("schema_version", str(SCHEMA_VERSION))
            # 移行で足した列の索引は、移行のあとで作る（古い表に対して SCHEMA の中で作ると失敗するため）
            c.execute("CREATE INDEX IF NOT EXISTS idx_items_fav ON items(favorite)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_items_det ON items(det_state)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_items_flag ON items(flag)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_items_agree ON items(agree)")

    def migrate(self, from_ver: int) -> None:
        """版が上がったときの移行"""
        log.info("data.sqlite を版 %d から %d へ移行します", from_ver, SCHEMA_VERSION)
        c = self.conn()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
        if from_ver < 2:
            for col, typ in (("orig_rel_path", "TEXT"), ("orig_size", "INTEGER"), ("archived_at", "TEXT")):
                if col not in cols:
                    c.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
        if from_ver < 3 and "favorite" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
        if from_ver < 4:
            for col, typ in (("datetime_corrected", "TEXT"), ("time_offset_sec", "INTEGER"), ("time_note", "TEXT")):
                if col not in cols:
                    c.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
        # 5: detections 表（SCHEMA の CREATE TABLE IF NOT EXISTS で作られる。列の追加はない）
        if from_ver < 6:
            for col, typ in (("auto_json", "TEXT"), ("agree", "INTEGER")):
                if col not in cols:
                    c.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
            # 既に検出済みの対象は、auto_results（モデルの出力の記録）から auto_json を作っておく
            by: dict[int, dict] = {}
            for r in c.execute("SELECT a.item_id, a.species, a.confidence, a.model FROM auto_results a JOIN items i ON i.id=a.item_id "
                               "WHERE i.det_state=1 AND a.model NOT LIKE 'csv%' ORDER BY a.item_id, a.id").fetchall():
                by.setdefault(r[0], dict(model=r[3], species=[]))["species"].append(dict(species=r[1], confidence=r[2]))
            for r in c.execute("SELECT id FROM items WHERE det_state=1").fetchall():
                d = by.get(r[0]) or dict(model=None, species=[], low=True)
                c.execute("UPDATE items SET auto_json=? WHERE id=?", (json.dumps(d, ensure_ascii=False), r[0]))
            try:
                from .judge import update_agree
                update_agree(self)
            except Exception as e:
                log.warning("agree の計算に失敗: %s", e)
        if from_ver < 7 and "recheck" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN recheck INTEGER NOT NULL DEFAULT 0")
        if from_ver < 8 and "train_exclude" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN train_exclude INTEGER NOT NULL DEFAULT 0")
        if from_ver < 9 and "n_boxes" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN n_boxes INTEGER NOT NULL DEFAULT 0")
            c.execute(N_BOXES_SQL_ALL, (0.2,))

    # ---------- 小道具 ----------
    def get_meta(self, k: str):
        r = self.conn().execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, k: str, v: str) -> None:
        with self.write_lock:
            self.conn().execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

    class Tx:
        def __init__(self, db: "Database"):
            self.db = db

        def __enter__(self):
            self.db.write_lock.acquire()
            c = self.db.conn()
            for _ in range(50):
                try:
                    c.execute("BEGIN IMMEDIATE")
                    return c
                except sqlite3.OperationalError as e:
                    if "locked" in str(e) or "busy" in str(e):
                        time.sleep(0.1)
                        continue
                    self.db.write_lock.release()
                    raise
            self.db.write_lock.release()
            raise sqlite3.OperationalError("database is locked")

        def __exit__(self, et, ev, tb):
            c = self.db.conn()
            try:
                if et is None:
                    c.execute("COMMIT")
                else:
                    c.execute("ROLLBACK")
            finally:
                self.db.write_lock.release()
            return False

    def tx(self) -> "Database.Tx":
        return Database.Tx(self)

    def q(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def q1(self, sql: str, params=()):
        return self.conn().execute(sql, params).fetchone()

    def scalar(self, sql: str, params=()):
        r = self.conn().execute(sql, params).fetchone()
        return r[0] if r else None

    def x(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.write_lock:
            return self.conn().execute(sql, params)

    def xmany(self, sql: str, rows) -> None:
        with self.write_lock:
            self.conn().executemany(sql, rows)

    # ---------- ジョブ ----------
    def job_create(self, kind: str, params: dict, total: int = 0) -> int:
        with self.tx() as c:
            cur = c.execute("INSERT INTO jobs(kind, params_json, state, total, started_at) VALUES(?,?,?,?,?)",
                            (kind, json.dumps(params, ensure_ascii=False), "queued", total, now_iso()))
            return cur.lastrowid

    def job_update(self, job_id: int, **kw) -> None:
        if not kw:
            return
        cols = ", ".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE jobs SET {cols} WHERE id=?", (*kw.values(), job_id))

    def job_get(self, job_id: int) -> dict | None:
        r = self.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
        return dict(r) if r else None

    def jobs_recent(self, n: int = 20) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (n,))]


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
