"""横断プロジェクト（2 階層目）: ほかのプロジェクト（調査回ごとのプロジェクト = 1 階層目）の動画を参照して、調査回をまたぐ作業をする。

- project.json に kind = "cross" と sources（参照先プロジェクトの名前とフォルダ）。フォルダは走査しない。
- 対象の相対パスは「<参照先の名前>/<参照先での相対パス>」。絶対パスは参照先プロジェクトのルート（この PC で解決）から引く（Project.abs_path）。
- 取り込みは参照先から対象（メタデータ・判定・AI の判定・検出の枠・特徴量・枠の画像）を写す。動画本体は写さない（参照先の NAS などから直接読む）。
- 選び方: 種ごとの本数（人の判定から。地点 × 調査回の上限、連続動画の間引き、地点の除外、乱数の種）/ 地点を丸ごと / 参照先を丸ごと / CSV の一覧。
  使った条件は project.json の imports に残す（論文の方法に書けるように）。
- 書き戻し（横断で確定した判定を参照先へ）と再取得（参照先で直した判定を横断へ）は push / pull。"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import random
import shutil
from collections import Counter, defaultdict
from datetime import datetime

from .db import now_iso
from .judge import _apply_state, item_state
from .paths import key, norm, rel_key, safe_name
from .species import FIXED_CODES

log = logging.getLogger(__name__)
HUMAN_ORIGINS = ("csv", "manual")
NOT_SPECIES = {"human", "vehicle", "unknown", "area"}      # 種ごとの抽出で既定では外す固定項目（empty = 動物なしは残す）
ITEM_COPY_SKIP = {"id", "rel_path", "rel_key", "folder", "src_dir", "src_id", "src_rel", "batch", "imported_at", "src_updated_at", "src_sig",
                  "orig_rel_path", "orig_size", "archived_at", "assignee"}


# ---------- 参照先 ----------
def open_source(path: str):
    """参照先プロジェクトを開く（ルートか TrapNote フォルダ）。横断プロジェクトは参照先にできない"""
    from .train import open_source as _open
    p = _open(path)
    if p.is_cross:
        p.close()
        raise ValueError(f"横断プロジェクトは参照先にできません: {path}")
    return p


def source_by_name(project, name: str) -> dict | None:
    for s in project.sources():
        if s.get("name") == name:
            return s
    return None


def _project_at(path: str) -> tuple[str | None, str | None]:
    """path にあるプロジェクト（path 自体が TrapNote フォルダ、または <path>/TrapNote）→ (プロジェクトのフォルダ, kind)。なければ (None, None)"""
    from .project import PROJECT_JSON, find_project_for_root, is_project_dir, read_json
    path = norm(path)
    if os.path.isfile(path) and os.path.basename(path).lower() == "project.json":
        path = os.path.dirname(path)
    pdir = path if is_project_dir(path) else (find_project_for_root(path) if os.path.isdir(path) else None)
    if not pdir:
        return None, None
    data = read_json(os.path.join(pdir, PROJECT_JSON), None) or {}
    return norm(pdir), (data.get("kind") if isinstance(data, dict) else None) or "folder"


def _is_project_path(path: str) -> bool:
    """調査回のプロジェクト（横断プロジェクトではない）があるか。フォルダの下に横断プロジェクトの TrapNote があっても、そのフォルダは親フォルダとして扱う"""
    pdir, kind = _project_at(path)
    return bool(pdir) and kind != "cross"


def add_source(project, path: str, name: str | None = None, labels_csv: str | None = None) -> dict:
    """参照先を登録する。path が TrapNote のプロジェクト（ルートか TrapNote フォルダ）ならプロジェクト参照（kind = project）、
    そうでないフォルダなら親フォルダ参照（kind = tree: NAS の PRM_N など。<親>/<調査回>/<地点>/<動画> の置き方。既存の種名は labels_csv から）。
    名前は相対パスの先頭になる（/ や \\ は _ に）。最初の参照先がプロジェクトなら種リストと追加項目を写す"""
    if not project.is_cross:
        raise ValueError("横断プロジェクトではありません")
    path = norm(path)
    if _is_project_path(path):
        src = open_source(path)
        try:
            if key(src.dir) == key(project.dir):
                raise ValueError("自分自身は参照先にできません")
            for s in project.sources():
                if s.get("dir") and key(s["dir"]) == key(src.dir):
                    return dict(name=s["name"], dir=s["dir"], kind="project", added=False)
            nm = _unique_name(project, (name or "").strip() or src.name, src.root or path)
            first = not project.sources()
            project.data.setdefault("sources", []).append(dict(name=nm, kind="project", dir=src.dir, added_at=now_iso()))
            project.forget_source_roots()
            if first:
                copy_lists(project, src)
            project.save()
            return dict(name=nm, dir=src.dir, kind="project", added=True)
        finally:
            src.close()
    if not os.path.isdir(path):
        raise FileNotFoundError(f"フォルダがありません: {path}")
    if norm(path).lower().rstrip("/") + "/" == norm(project.dir).lower().rstrip("/") + "/" or norm(path).lower().startswith(norm(project.dir).lower().rstrip("/") + "/"):
        raise ValueError("横断プロジェクトの TrapNote フォルダは参照先にできません（動画の親フォルダを指定してください）")
    for s in project.sources():
        if s.get("root") and key(s["root"]) == key(path):
            return dict(name=s["name"], root=s["root"], kind="tree", added=False)
    pdir, pkind = _project_at(path)
    ignored = pdir if (pkind == "cross" and key(pdir) != key(project.dir)) else None     # 親フォルダの下に別の横断プロジェクトがあっても無視する
    nm = _unique_name(project, (name or "").strip(), path)
    entry = dict(name=nm, kind="tree", root=path, added_at=now_iso())
    if labels_csv:
        entry["labels_csv"] = norm(labels_csv)
    project.data.setdefault("sources", []).append(entry)
    project.forget_source_roots()
    project.save()
    return dict(name=nm, root=path, kind="tree", added=True, ignored_cross=ignored)


def add_sources_under(project, parent: str, labels_csv: str | None = None) -> dict:
    """親フォルダ（下に調査回フォルダが並ぶもの）を 1 つの参照先として登録する。調査回フォルダに TrapNote のプロジェクトがあればその判定を、
    なければ種名の CSV を使う（調査回ごとに自動で切り替える）。親フォルダ自体がプロジェクトのルートならプロジェクト参照"""
    parent = norm(parent)
    if not os.path.isdir(parent):
        raise FileNotFoundError(f"フォルダがありません: {parent}")
    if _is_project_path(parent):
        r = add_source(project, parent, None, labels_csv)
        return dict(added=[r["name"]] if r.get("added") else [], skipped=[] if r.get("added") else [r["name"]], errors=[], kind="project", n_sub=0, n_project_seasons=0)
    subs = _tree_seasons(parent)
    r = add_source(project, parent, None, labels_csv)
    n_proj = sum(1 for n in subs if _is_project_path(os.path.join(parent, n)))
    return dict(added=[r["name"]] if r.get("added") else [], skipped=[] if r.get("added") else [r["name"]], errors=[], kind="tree", n_sub=len(subs), n_project_seasons=n_proj,
                ignored_cross=r.get("ignored_cross"))


def tree_season_project(root: str, season: str) -> str | None:
    """調査回フォルダに TrapNote のプロジェクト（横断ではないもの）があればそのフォルダ（<root>/<season>/TrapNote）"""
    d = os.path.join(root, season)
    if not os.path.isdir(d):
        return None
    pdir, kind = _project_at(d)
    return pdir if pdir and kind != "cross" else None


def _tree_rows(project, s: dict, seasons: list[str] | None = None, progress=None, stop=None) -> tuple[list[dict], list[str]]:
    """親フォルダ参照の全動画の行。調査回フォルダにプロジェクトがあればその items と判定（src_dir / src_id 付き）、なければ種名の CSV の行。
    行: dict(season, station, file, rel=<season>/<station>/<file>, species(set), dt, status, ai, agree, det_state, duration, size, src_dir, src_id, src_rel)"""
    root = norm(s.get("root") or "")
    if not root or not os.path.isdir(root):
        return [], []
    all_seasons = _tree_seasons(root)
    want = [se for se in all_seasons if not seasons or se in set(seasons)]
    lab = _labels(project, s, (lambda a, b, msg: progress(0, len(want), f"種名の CSV: {msg}")) if progress else None, stop)
    by_season: dict[str, list[dict]] = defaultdict(list)
    for r in lab["rows"].values():
        by_season[r["season"]].append(r)
    out: list[dict] = []
    proj_seasons: list[str] = []
    for i, se in enumerate(want):
        if stop and stop():
            break
        if progress:
            progress(i, len(want), f"{s['name']}/{se}")
        pdir = tree_season_project(root, se)
        src = None
        if pdir:
            try:
                src = open_source(pdir)
            except Exception as e:
                log.warning("調査回のプロジェクト %s を開けません: %s", pdir, e)
        if src is not None:
            proj_seasons.append(se)
            try:
                hum: dict[int, list] = defaultdict(list)
                for j in src.db.q("SELECT j.item_id, j.species, j.origin, i.status FROM judgements j JOIN items i ON i.id=j.item_id WHERE j.species != 'area'"):
                    if j["status"] == "verified" or j["origin"] in HUMAN_ORIGINS:
                        hum[j["item_id"]].append(j["species"])
                for r in src.db.q("SELECT id, rel_path, station, name, COALESCE(datetime_corrected, datetime) AS dt, status, auto_json, agree, det_state, duration, size FROM items WHERE missing=0"):
                    ai = None
                    if r["auto_json"]:
                        try:
                            ai = ";".join(x["species"] for x in (json.loads(r["auto_json"]).get("species") or [])) or None
                        except Exception:
                            ai = None
                    out.append(dict(season=se, station=r["station"] or "", file=r["name"], rel=f"{se}/{r['rel_path']}", species=set(hum.get(r["id"], [])), dt=r["dt"] or "",
                                    status=r["status"], ai=ai, agree=r["agree"], det_state=r["det_state"], duration=r["duration"], size=r["size"], src_dir=src.dir, src_id=r["id"], src_rel=r["rel_path"]))
            finally:
                src.close()
        else:
            for r in by_season.get(se, []):
                out.append(dict(season=se, station=r["station"], file=r["file"], rel=f"{se}/{r['station']}/{r['file']}", species=set(r["species"]), dt=r["dt"] or "",
                                status="auto" if r["species"] else "new", ai=None, agree=None, det_state=0, duration=None, size=None, src_dir=None, src_id=None, src_rel=None))
    return out, proj_seasons


def _unique_name(project, wanted: str, path: str) -> str:
    """参照先の名前。指定がなければフォルダ名。重なれば ドライブ文字_フォルダ名（Y_PRM_N）、それでも重なれば番号を足す。指定した名前が重なるときはエラー"""
    taken = {s["name"] for s in project.sources()}
    if wanted:
        nm = safe_name(wanted).replace("/", "_") or "source"
        if nm in taken:
            raise ValueError(f"同じ名前の参照先があります: {nm}（名前を変えてください）")
        return nm
    base = safe_name(os.path.basename(norm(path).rstrip("/"))).replace("/", "_") or "source"
    if base not in taken:
        return base
    p = norm(path)
    prefix = p[0] if len(p) >= 2 and p[1] == ":" else safe_name(os.path.basename(os.path.dirname(p.rstrip("/"))))
    cand = f"{prefix}_{base}" if prefix else base
    if cand not in taken:
        return cand
    k = 2
    while f"{cand}_{k}" in taken:
        k += 1
    return f"{cand}_{k}"


def set_source_labels(project, name: str, labels_csv: str | None, wait: bool = True) -> dict:
    """親フォルダ参照に、既存の種名の CSV（Season, Station, File, Species[, DateTime]）を付ける・外す。wait=False なら読まずに戻る（裏で LabelsJob が読む）"""
    s = source_by_name(project, name)
    if s is None or s.get("kind") != "tree":
        raise ValueError(f"親フォルダ参照ではありません: {name}")
    if labels_csv:
        labels_csv = norm(labels_csv)
        if not os.path.isfile(labels_csv):
            raise FileNotFoundError(f"CSV がありません: {labels_csv}")
        s["labels_csv"] = labels_csv
    else:
        s.pop("labels_csv", None)
    project.save()
    _LABELS.pop(name, None)
    if not wait:
        return dict(ok=True, labels=dict(pending=not labels_ready(project, s)))
    lab = _labels(project, s)
    return dict(ok=True, labels=dict(n=len(lab["rows"]), unmapped=lab["unmapped"][:20], species=lab["species"], files=[dict(path=f["path"], n=f["n"], error=f["error"]) for f in lab["files"]]))


_LABELS: dict = {}       # name → (csv path, mtime, parsed)
LABEL_ALIAS = {"season": ("season", "調査回", "期"), "station": ("station", "地点", "site"), "file": ("file", "filename", "name", "ファイル", "ファイル名"),
               "species": ("species", "種", "種名", "label"), "datetime": ("datetime", "date_time", "日時", "timestamp")}


def label_files(s: dict) -> list[tuple[str, str]]:
    """親フォルダ参照の種名 CSV の候補 [(path, その CSV の調査回 or "")]。
    指定があればそれだけ。なければ親フォルダの直下（調査回 ""）と各調査回フォルダの直下の *.csv を自動で探す"""
    if s.get("labels_csv"):
        return [(norm(s["labels_csv"]), "")] if os.path.isfile(s["labels_csv"]) else []
    root = norm(s.get("root") or "")
    if not root or not os.path.isdir(root):
        return []
    out = []
    try:
        for e in sorted(os.scandir(root), key=lambda e: e.name):
            if e.is_file() and e.name.lower().endswith(".csv"):
                out.append((norm(e.path), ""))
    except OSError:
        pass
    for se in _tree_seasons(root):
        try:
            for e in sorted(os.scandir(os.path.join(root, se)), key=lambda e: e.name):
                if e.is_file() and e.name.lower().endswith(".csv"):
                    out.append((norm(e.path), se))
        except OSError:
            pass
    return out


class LabelRow:
    """種名 CSV の 1 行（動画 1 本）。dict のように r["season"] で引ける。数十万行あるので __slots__ で小さく"""
    __slots__ = ("season", "station", "file", "species", "dt")

    def __init__(self, season: str, station: str, file: str, species: tuple = (), dt: str = ""):
        self.season, self.station, self.file, self.species, self.dt = season, station, file, species, dt

    def __getitem__(self, k):
        return getattr(self, k)

    def __setitem__(self, k, v):
        setattr(self, k, v)

    def get(self, k, default=None):
        return getattr(self, k, default)

    def __getstate__(self):
        return (self.season, self.station, self.file, self.species, self.dt)

    def __setstate__(self, st):
        self.season, self.station, self.file, self.species, self.dt = st


class _Counting:
    """行を数えながら渡す（進み具合 = 読んだ文字数）"""

    def __init__(self, text: str):
        self.f = io.StringIO(text)
        self.pos = 0

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self.f)
        self.pos += len(line)
        return line


def _parse_label_file(project, path: str, season_default: str, rows: dict, unmapped: Counter, code_of: dict, progress=None, stop=None, intern: dict | None = None) -> dict:
    """1 つの種名 CSV を rows に足す。返り値 dict(path, n, cols, error)。
    行は LabelRow(season, station, file, species=tuple(code...), dt)。調査回・地点・種の組は同じ文字列オブジェクトを使い回す（数十万行でもメモリを抑える）"""
    from . import media
    from .export import _sniff, read_text_any
    label_fn = os.path.basename(path)
    if progress:
        progress(0, 1, f"{label_fn}: 読んでいます")
    try:
        text = read_text_any(path)
    except Exception as e:
        return dict(path=path, n=0, cols={}, error=str(e))
    src = _Counting(text)
    total = max(1, len(text))
    rd = csv.reader(src, delimiter=_sniff(text))
    try:
        header = next(rd)
    except StopIteration:
        return dict(path=path, n=0, cols={}, error="空の CSV")
    head = {(h or "").strip().lower(): i for i, h in enumerate(header)}
    cols: dict[str, str] = {}
    idx: dict[str, int] = {}
    for k, names in LABEL_ALIAS.items():
        for n in names:
            if n in head:
                cols[k] = header[head[n]]
                idx[k] = head[n]
                break
    if "file" not in idx or "species" not in idx:
        return dict(path=path, n=0, cols=cols, error=f"File と Species の列がありません（見出し: {', '.join(h for h in header if h)[:120]}）")
    sl = project.species
    z = bool(project.setting("z_is_utc"))
    intern = intern if intern is not None else {}
    i_file, i_sp = idx["file"], idx["species"]
    i_se, i_st, i_dt = idx.get("season"), idx.get("station"), idx.get("datetime")
    n = 0
    k_row = 0
    for r in rd:
        k_row += 1
        if k_row % 20000 == 0:
            if stop and stop():
                break
            if progress:
                progress(src.pos, total, f"{label_fn}: {k_row:,} 行")
        try:
            fn = (r[i_file] or "").strip()
        except IndexError:
            continue
        if not fn:
            continue
        se = ((r[i_se] or "").strip() if i_se is not None and i_se < len(r) else "") or season_default
        st = (r[i_st] or "").strip() if i_st is not None and i_st < len(r) else ""
        label = (r[i_sp] or "").strip() if i_sp < len(r) else ""
        se = intern.setdefault(se, se)
        st = intern.setdefault(st, st)
        if label not in code_of:
            code_of[label] = (sl.code_for_label(label) or sl.guess_code(label)) if label else None
        code = code_of[label]
        k = (intern.setdefault(se.lower(), se.lower()), intern.setdefault(st.lower(), st.lower()), fn.lower())
        d = rows.get(k)
        if d is None:
            d = rows[k] = LabelRow(se, st, fn)
            n += 1
            if i_dt is not None and i_dt < len(r) and r[i_dt]:
                d.dt = media.parse_dt_any(r[i_dt] or "", z) or ""
        if code:
            if code not in d["species"]:
                tup = tuple(sorted(d["species"] + (code,)))
                d["species"] = intern.setdefault(tup, tup)
        elif label:
            unmapped[label] += 1
    if progress:
        progress(total, total, f"{label_fn}: {k_row:,} 行")
    return dict(path=path, n=n, cols=cols, error=None)


def _labels_sig(project, files: list[tuple[str, str]]) -> tuple:
    sp = os.path.join(project.dir, "species.csv")
    out = [("species", os.path.getmtime(sp) if os.path.isfile(sp) else 0, bool(project.setting("z_is_utc")))]
    for p, _ in files:
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime, st.st_size))
        except OSError:
            pass
    return tuple(out)


def _labels_cache_path(project, s: dict) -> str:
    d = project.cache_dir("labels")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, safe_name(s["name"]).replace("/", "_") + ".pkl")


def labels_ready(project, s: dict) -> bool:
    """種名 CSV を読んだものが手元（メモリか cache/labels）にあるか。あればメモリに載せる。読んではいない"""
    import pickle
    files = label_files(s)
    sig = _labels_sig(project, files)
    hit = _LABELS.get(s["name"])
    if hit and hit[0] == sig:
        return True
    cp = _labels_cache_path(project, s)
    if os.path.isfile(cp):
        try:
            with open(cp, "rb") as fh:
                csig, out = pickle.load(fh)
            if csig == sig:
                _LABELS[s["name"]] = (sig, out)
                return True
        except Exception as e:
            log.warning("種名 CSV のキャッシュを読めません（読み直します）: %s", e)
    return False


def labels_files_info(s: dict) -> list[dict]:
    out = []
    for p, se in label_files(s):
        try:
            out.append(dict(path=p, season=se, size=os.path.getsize(p)))
        except OSError:
            out.append(dict(path=p, season=se, size=0))
    return out


def _labels(project, s: dict, progress=None, stop=None) -> dict:
    """親フォルダ参照の既存の種名 CSV（指定した 1 つ、または自動で見つけたもの全部）を読む。ファイルが変わっていなければ覚えたもの（メモリ → cache/labels の pickle）を使う。
    返り値 dict(rows={(season, station, file) の小文字: dict(season, station, file, species=tuple(code), dt)}, unmapped=[(label, n)], species=[(code, n)], files=[...], cols)"""
    import pickle
    if labels_ready(project, s):
        return _LABELS[s["name"]][1]
    files = label_files(s)
    sig = _labels_sig(project, files)
    rows: dict[tuple, dict] = {}
    unmapped: Counter = Counter()
    code_of: dict[str, str | None] = {}
    intern: dict = {}
    used = []
    for path, se in files:
        if stop and stop():
            break
        used.append(_parse_label_file(project, path, se, rows, unmapped, code_of, progress, stop, intern))
    sp_counts: Counter = Counter()
    for d in rows.values():
        if "empty" in d["species"] and len(d["species"]) > 1:
            tup = tuple(c for c in d["species"] if c != "empty")
            d["species"] = intern.setdefault(tup, tup)
        for c in d["species"]:
            sp_counts[c] += 1
    out = dict(rows=rows, unmapped=unmapped.most_common(), species=sp_counts.most_common(), files=used, cols=(used[0]["cols"] if used else {}))
    if stop and stop():
        return out                                   # 途中でやめたものは覚えない
    _LABELS[s["name"]] = (sig, out)
    try:
        cp = _labels_cache_path(project, s)
        with open(cp + ".tmp", "wb") as fh:
            pickle.dump((sig, out), fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(cp + ".tmp", cp)
    except Exception as e:
        log.warning("種名 CSV のキャッシュを書けません: %s", e)
    return out


def _tree_seasons(root: str) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(root) if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in ("trapnote", "staytime"))
    except OSError:
        return []


def _tree_station_files(root: str, season: str, station: str) -> list[str]:
    from .paths import kind_of
    d = os.path.join(root, season, station)
    try:
        return sorted(e.name for e in os.scandir(d) if e.is_file() and kind_of(e.name))
    except OSError:
        return []


def copy_lists(project, src) -> None:
    """種リストと追加項目の定義を参照先から写す（最初の参照先のとき）"""
    from .fields import Fields
    from .species import SpeciesList
    for fn in ("species.csv", "fields.json"):
        a, b = os.path.join(src.dir, fn), os.path.join(project.dir, fn)
        if os.path.isfile(a):
            shutil.copyfile(a, b)
    project.species = SpeciesList(os.path.join(project.dir, "species.csv"))
    project.fields = Fields(os.path.join(project.dir, "fields.json"))


def remove_source(project, name: str) -> dict:
    n = int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (name + "/%",)) or 0)
    if n:
        raise ValueError(f"{name} から取り込んだ対象が {n} 件あるので外せません（先に対象を消してください）")
    project.data["sources"] = [s for s in project.sources() if s.get("name") != name]
    project.forget_source_roots()
    project.save()
    return dict(ok=True)


def source_summary(src, name: str, project=None) -> dict:
    """参照先の様子: 件数、確認済み、人の判定のあるもの、地点、調査回、種ごとの本数（人の判定）"""
    db = src.db
    where = "missing=0"
    n_items = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where}") or 0)
    n_verified = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND status='verified'") or 0)
    n_human = int(db.scalar(f"SELECT COUNT(DISTINCT i.id) FROM items i JOIN judgements j ON j.item_id=i.id WHERE i.{where} AND (i.status='verified' OR j.origin IN ('csv','manual'))") or 0)
    n_det = int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND det_state=1") or 0)
    stations = [dict(station=r[0] or "", n=r[1]) for r in db.q(f"SELECT station, COUNT(*) FROM items WHERE {where} GROUP BY station ORDER BY station")]
    seasons = [r[0] or "" for r in db.q(f"SELECT DISTINCT season FROM items WHERE {where} ORDER BY season")]
    species = [dict(code=r[0], n=r[1]) for r in db.q(
        f"SELECT j.species, COUNT(DISTINCT i.id) FROM items i JOIN judgements j ON j.item_id=i.id WHERE i.{where} AND (i.status='verified' OR j.origin IN ('csv','manual')) GROUP BY j.species ORDER BY 2 DESC")]
    n_in = int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (name + "/%",)) or 0) if project is not None else 0
    return dict(name=name, dir=src.dir, root=src.root, project_name=src.name, n_items=n_items, n_verified=n_verified, n_human=n_human, n_det=n_det,
                stations=stations, seasons=seasons, species=species, n_imported=n_in,
                speciesnet_model=src.setting("speciesnet_model"), pattern=src.setting("pattern"))


def tree_summary(project, s: dict, wait: bool = False) -> dict:
    """親フォルダ参照の様子: ルートがあるか、調査回のフォルダ数、種名 CSV（本数・種ごと・対応しない値）。
    wait=False で種名 CSV をまだ読んでいなければ labels=dict(pending=True, files=[...]) を返す（読むのは LabelsJob）"""
    root = norm(s.get("root") or "")
    available = bool(root and os.path.isdir(root))
    seasons = _tree_seasons(root) if available else []
    n_in = int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (s["name"] + "/%",)) or 0)
    proj_seasons = [se for se in seasons if tree_season_project(root, se)] if available else []
    d = dict(name=s["name"], kind="tree", root=root, dir=None, project_name=s["name"], ok=True, available=available, seasons=seasons, project_seasons=proj_seasons, stations=[],
             n_items=None, n_verified=None, n_human=None, n_det=None, species=[], n_imported=n_in, labels_csv=s.get("labels_csv") or "", labels=None)
    if available and not wait and not labels_ready(project, s):
        fi = labels_files_info(s)
        d["labels"] = dict(pending=True, files=[dict(path=f["path"], n=None, error=None) for f in fi], size=sum(f["size"] for f in fi), auto=not s.get("labels_csv")) if fi else dict(files=[], auto=not s.get("labels_csv"))
        return d
    if available:
        try:
            lab = _labels(project, s)
            here = set(seasons) - set(proj_seasons)                                     # プロジェクトのある調査回は CSV ではなくプロジェクトの判定を使う
            rows_here = [r for r in lab["rows"].values() if r["season"] in here]        # この親フォルダにある調査回の分だけ数える
            sp: Counter = Counter()
            for r in rows_here:
                for c in r["species"]:
                    sp[c] += 1
            d["labels"] = dict(n=len(lab["rows"]), n_here=len(rows_here), unmapped=lab["unmapped"][:20], n_unmapped=sum(n for _, n in lab["unmapped"]), cols=lab["cols"],
                               files=[dict(path=f["path"], n=f["n"], error=f["error"]) for f in lab["files"]], auto=not s.get("labels_csv"),
                               seasons=sorted({r["season"] for r in lab["rows"].values() if r["season"]}))
            d["species"] = [dict(code=c, n=n) for c, n in sp.most_common()]
            d["n_human"] = sum(1 for r in rows_here if r["species"])
            d["n_items"] = len(rows_here)
        except Exception as e:
            d["labels"] = dict(error=str(e))
    return d


def sources_info(project, wait: bool = False) -> list[dict]:
    """登録した参照先の様子（開けないものは error）。wait=False なら種名 CSV をまだ読んでいない親フォルダ参照は labels.pending"""
    out = []
    for s in project.sources():
        if s.get("kind") == "tree":
            out.append(tree_summary(project, s, wait))
            continue
        try:
            src = open_source(s["dir"])
        except Exception as e:
            out.append(dict(name=s["name"], kind="project", dir=s["dir"], ok=False, error=str(e), n_imported=int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (s["name"] + "/%",)) or 0)))
            continue
        try:
            d = source_summary(src, s["name"], project)
            d.update(ok=True, kind="project", available=bool(src.root and os.path.isdir(src.root)))
            out.append(d)
        finally:
            src.close()
    return out


# ---------- 候補を集める・選ぶ ----------
def _human_rows(src, only_verified: bool) -> dict[int, dict]:
    """人の判定のある対象 {src_id: dict(id, rel, name, station, season, dt, status, species(set))}"""
    cond = "i.status='verified'" if only_verified else "(i.status='verified' OR j.origin IN ('csv','manual'))"
    rows = src.db.q("SELECT i.id, i.rel_path, i.name, i.station, i.season, COALESCE(i.datetime_corrected, i.datetime) AS dt, i.status, j.species, j.origin "
                    f"FROM items i JOIN judgements j ON j.item_id=i.id WHERE i.missing=0 AND {cond}")
    out: dict[int, dict] = {}
    for r in rows:
        if r["status"] != "verified" and r["origin"] not in HUMAN_ORIGINS:
            continue                                     # 確認済みでない対象の AI の判定は人の判定ではない
        d = out.get(r["id"])
        if d is None:
            d = out[r["id"]] = dict(id=r["id"], rel=r["rel_path"], name=r["name"], station=r["station"] or "", season=r["season"] or "", dt=r["dt"] or "", status=r["status"], species=set())
        if r["species"] != "area":
            d["species"].add(r["species"])
    return out


def _parse_dt(s: str) -> float | None:
    if not s:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).timestamp()
        except ValueError:
            continue
    return None


def _thin(rows: list[dict], gap_min: float) -> list[dict]:
    """同じ地点で gap_min 分以内に続く動画は 1 本だけ（時刻のないものはそのまま）"""
    if gap_min <= 0:
        return rows
    out, last = [], {}
    for r in sorted(rows, key=lambda x: (x["station"], x["dt"] or "", x["id"])):
        ts = _parse_dt(r["dt"])
        if ts is None:
            out.append(r)
            continue
        prev = last.get(r["station"])
        if prev is not None and ts - prev < gap_min * 60:
            continue
        last[r["station"]] = ts
        out.append(r)
    return out


def select_items(project, params: dict, progress=None) -> dict:
    """params に従って参照先から対象を選ぶ。返り値 dict(picked=[(name, src_dir, src_row)], counts={species: n}, by_source={name: n}, n=...)。
    mode: species（種ごとに n_per_species 本）/ stations（地点を丸ごと）/ all（参照先を丸ごと）/ csv（Season, Station, File の一覧）"""
    mode = params.get("mode") or "species"
    want_sources = set(params.get("sources") or []) or {s["name"] for s in project.sources()}
    exclude_st = {str(x) for x in (params.get("exclude_stations") or [])}
    seed = int(params.get("seed") or 1)
    only_verified = bool(params.get("only_verified"))
    picked: list[tuple[str, str, dict]] = []
    counts: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    already = {rel_key(r[0]) for r in project.db.q("SELECT rel_path FROM items")}
    csv_rows = parse_csv_list(params.get("csv_text") or "") if mode == "csv" else []
    pool: dict[str, list[dict]] = defaultdict(list)          # 種 → 候補（mode = species）
    for s in project.sources():
        name = s["name"]
        if name not in want_sources:
            continue
        if progress:
            progress(name)
        if s.get("kind") == "tree":
            _select_tree(project, s, mode, params, exclude_st, csv_rows, pool, picked)
            continue
        src = open_source(s["dir"])
        try:
            if mode == "species":
                rows = _human_rows(src, only_verified)
                cands = [r for r in rows.values() if r["station"] not in exclude_st]
                if params.get("single_only", True):
                    cands = [r for r in cands if len(r["species"]) <= 1]
                for r in _thin(cands, float(params.get("gap_min") or 0)):
                    for sp in r["species"]:
                        pool[sp].append(dict(r, src_name=name, src_dir=src.dir))
            elif mode == "stations":
                want_st = {str(x) for x in (params.get("stations") or [])}
                for r in src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0"):
                    if (r["station"] or "") in want_st and (r["station"] or "") not in exclude_st:
                        picked.append((name, src.dir, dict(r)))
            elif mode == "all":
                for r in src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0"):
                    if (r["station"] or "") not in exclude_st:
                        picked.append((name, src.dir, dict(r)))
            elif mode == "csv":
                for row in csv_rows:
                    if row.get("season") and row["season"] != name and row["season"].lower() != src.name.lower():
                        continue
                    if row.get("station"):
                        hits = src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0 AND station=? AND LOWER(name)=LOWER(?)", (row["station"], row["file"]))
                    else:
                        hits = src.db.q("SELECT id, rel_path, name, station, season FROM items WHERE missing=0 AND LOWER(name)=LOWER(?)", (row["file"],))
                    for r in hits:
                        picked.append((name, src.dir, dict(r)))
                        row["_hit"] = True
        finally:
            src.close()
    if mode == "species":
        n_per = int(params.get("n_per_species") or 0)
        cap = int(params.get("cap") or 0)
        want_sp = set(params.get("species") or []) or {sp for sp in pool if sp not in NOT_SPECIES}
        rng = random.Random(seed)
        seen: set[tuple[str, str]] = set()
        for sp in sorted(want_sp):
            cands = list(pool.get(sp, []))
            rng.shuffle(cands)
            per_cell: dict[tuple, int] = defaultdict(int)
            n_sp = 0
            for r in cands:
                if n_per and n_sp >= n_per:
                    break
                cell = (r["src_name"], r["station"], r["season"])
                if cap and per_cell[cell] >= cap:
                    continue
                k = (r["src_name"], rel_key(r["rel"]))
                if k in seen:
                    continue
                per_cell[cell] += 1
                seen.add(k)
                n_sp += 1
                picked.append((r["src_name"], r["src_dir"], r))
                counts[sp] += 1
    # 既に取り込んであるものは除く・重複を除く
    uniq: list[tuple[str, str, dict]] = []
    seen2: set[tuple[str, str]] = set()
    n_already = 0
    for name, sdir, r in picked:
        rel = r["rel"] if "rel" in r else r["rel_path"]
        k = (name, rel_key(rel))
        if k in seen2:
            continue
        seen2.add(k)
        if rel_key(f"{name}/{rel}") in already:
            n_already += 1
            continue
        uniq.append((name, sdir, r))
        by_source[name] += 1
    n_unmatched = sum(1 for row in csv_rows if not row.get("_hit")) if mode == "csv" else 0
    return dict(picked=uniq, counts=dict(counts), by_source=dict(by_source), n=len(uniq), n_already=n_already, n_csv=len(csv_rows), n_unmatched=n_unmatched,
                available={sp: len(v) for sp, v in pool.items()} if mode == "species" else {})


def _select_tree(project, s: dict, mode: str, params: dict, exclude_st: set, csv_rows: list, pool: dict, picked: list) -> None:
    """親フォルダ参照から候補を集める（_tree_rows: 調査回のプロジェクトがあればその判定、なければ種名の CSV）。
    行は dict(id=src_id or None, rel=<調査回>/<地点>/<動画>, name, station, season, dt, species(set), status, src_kind="tree", src_dir, src_rel, prefix=調査回)"""
    name, root = s["name"], norm(s.get("root") or "")
    if not root or not os.path.isdir(root):
        raise FileNotFoundError(f"{name} のフォルダが見えません: {root}")
    trows, _ = _tree_rows(project, s)

    def row_of(r):
        return dict(id=r["src_id"], rel=r["rel"], name=r["file"], station=r["station"], season=r["season"], dt=r["dt"] or "", status=r["status"], species=set(r["species"]),
                    src_name=name, src_dir=r["src_dir"] or "", src_kind="tree", src_root=root, src_rel=r["src_rel"], prefix=r["season"])
    if mode == "species":
        if not any(r["species"] for r in trows):
            raise ValueError(f"{name}: 種ごとに選ぶには既存の判定が要ります（調査回のプロジェクトの判定か、親フォルダ・調査回フォルダに置いた種名の CSV）")
        only_verified = bool(params.get("only_verified"))
        cands = [row_of(r) for r in trows if r["species"] and r["station"] not in exclude_st and (r["status"] == "verified" or not only_verified)]
        if params.get("single_only", True):
            cands = [r for r in cands if len(r["species"]) <= 1]
        for r in _thin(cands, float(params.get("gap_min") or 0)):
            for sp in r["species"]:
                pool[sp].append(r)
    elif mode == "stations":
        want_st = {str(x) for x in (params.get("stations") or [])}
        for r in trows:
            if r["station"] in want_st and r["station"] not in exclude_st:
                picked.append((name, r["src_dir"] or "", row_of(r)))
    elif mode == "all":
        for r in trows:
            if r["station"] not in exclude_st:
                picked.append((name, r["src_dir"] or "", row_of(r)))
    elif mode == "csv":
        idx = {(r["season"].lower(), r["station"].lower(), r["file"].lower()): r for r in trows}
        for row in csv_rows:
            se, st, fn = row.get("season") or "", row.get("station") or "", row["file"]
            if not se or not st:
                continue
            r = idx.get((se.lower(), st.lower(), fn.lower()))
            if r is None:
                continue
            picked.append((name, r["src_dir"] or "", row_of(r)))
            row["_hit"] = True


def parse_csv_list(text: str) -> list[dict]:
    """Season, Station, File の一覧（列名は大文字小文字を問わない。日本語の 調査回・地点・ファイル も可。rel_path 列があればそこから）"""
    if not text.strip():
        return []
    rd = csv.reader(io.StringIO(text.strip()))
    rows = list(rd)
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    alias = {"season": ("season", "調査回", "期"), "station": ("station", "地点", "site"), "file": ("file", "filename", "name", "ファイル", "ファイル名"), "rel": ("rel_path", "relativepath", "path")}
    idx = {}
    for k, names in alias.items():
        for i, h in enumerate(head):
            if h in names:
                idx[k] = i
                break
    body = rows[1:] if idx else rows
    if not idx:                                          # 見出しがなければ 列 = Season, Station, File とみなす
        idx = {"season": 0, "station": 1, "file": 2} if len(rows[0]) >= 3 else {"file": len(rows[0]) - 1}
    out = []
    for r in body:
        if not any(x.strip() for x in r):
            continue
        g = lambda k: (r[idx[k]].strip() if k in idx and idx[k] < len(r) else "")
        rel = g("rel").replace("\\", "/")
        if rel and "file" not in idx:
            parts = rel.split("/")
            out.append(dict(season=parts[0] if len(parts) >= 3 else "", station=parts[-2] if len(parts) >= 2 else "", file=parts[-1]))
        else:
            out.append(dict(season=g("season"), station=g("station"), file=g("file") or os.path.basename(rel)))
    return [x for x in out if x["file"]]


def plan(project, params: dict) -> dict:
    sel = select_items(project, params)
    sel.pop("picked", None)
    return sel


# ---------- 取り込み ----------
def import_items(project, params: dict, batch: str, progress=None, stop=None) -> dict:
    """select_items() で選んだ対象を参照先から写す。返り値 dict(n, n_already, counts, by_source)"""
    sel = select_items(project, params, lambda name: progress and progress(0, 0, f"{name} を調べています"))
    n_done = import_rows(project, sel["picked"], batch, progress, stop)
    rec = dict(at=now_iso(), batch=batch, params={k: v for k, v in params.items() if k != "csv_text"}, n=n_done, counts=sel["counts"], by_source=sel["by_source"],
               n_csv=sel.get("n_csv"), n_unmatched=sel.get("n_unmatched"))
    project.data.setdefault("imports", []).append(rec)
    project.save()
    return dict(n=n_done, n_already=sel["n_already"], counts=sel["counts"], by_source=sel["by_source"], n_csv=sel.get("n_csv"), n_unmatched=sel.get("n_unmatched"))


def import_rows(project, picked: list, batch: str, progress=None, stop=None) -> int:
    """picked = [(参照先の名前, プロジェクトのフォルダ or "", 行)] を写す。行はプロジェクト参照なら id（参照先の items.id）、親フォルダ参照なら src_kind = tree と rel"""
    from .train import crop_path, features_path
    db = project.db
    cols = [r["name"] for r in db.q("PRAGMA table_info(items)")]
    copy_cols = [c for c in cols if c not in ITEM_COPY_SKIP]
    by_src: dict[tuple, list[dict]] = defaultdict(list)
    for name, sdir, r in picked:
        by_src[(name, sdir or "")].append(r)
    n_done = 0
    now = now_iso()
    for (name, sdir), rows in by_src.items():
        if stop and stop():
            break
        if not sdir:                                           # 親フォルダ参照で、調査回にプロジェクトがないもの（種名の CSV だけ）
            n_done += _import_tree_rows(project, name, rows, batch, now, lambda a, b, msg: progress and progress(n_done + a, len(picked), msg), stop)
            continue
        src = open_source(sdir)
        try:
            fdir = project.cache_dir("features")
            os.makedirs(fdir, exist_ok=True)
            for i0 in range(0, len(rows), 200):
                if stop and stop():
                    break
                chunk = rows[i0:i0 + 200]
                with db.tx() as c:
                    for r in chunk:
                        sid = int(r["id"])
                        srow = src.db.q1("SELECT * FROM items WHERE id=?", (sid,))
                        if srow is None:
                            continue
                        srow = dict(srow)
                        src_rel = srow["rel_path"]
                        prefix = r.get("prefix") or ""                     # 親フォルダ参照の中の調査回プロジェクトなら <調査回>/ が付く
                        rel = f"{name}/{prefix}/{src_rel}" if prefix else f"{name}/{src_rel}"
                        vals = {c_: srow.get(c_) for c_ in copy_cols}
                        st0 = item_state(src.db, sid)
                        vals.update(rel_path=rel, rel_key=rel_key(rel), folder=os.path.dirname(rel), season=srow.get("season") or prefix or name,
                                    src_dir=src.dir, src_id=sid, src_rel=src_rel, batch=batch or None, imported_at=now, src_updated_at=srow.get("updated_at"),
                                    src_sig=state_sig(st0, srow.get("train_exclude"), srow.get("recheck")), updated_at=now)
                        names = list(vals.keys())
                        cur = c.execute(f"INSERT OR IGNORE INTO items({', '.join(names)}) VALUES({', '.join('?' * len(names))})", [vals[k] for k in names])
                        if cur.rowcount == 0:
                            continue
                        nid = cur.lastrowid
                        _apply_state(c, nid, st0, None)
                        c.execute("UPDATE items SET status=?, verified_by=?, verified_at=?, agree=?, recheck=?, updated_at=? WHERE id=?",
                                  (srow.get("status") or "new", srow.get("verified_by"), srow.get("verified_at"), srow.get("agree"), srow.get("recheck") or 0, now, nid))
                        c.executemany("INSERT INTO auto_results(item_id, species, confidence, model, created_at) VALUES(?,?,?,?,?)",
                                      [(nid, a["species"], a["confidence"], a["model"], a["created_at"]) for a in src.db.q("SELECT species, confidence, model, created_at FROM auto_results WHERE item_id=? ORDER BY id", (sid,))])
                        c.executemany("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                      [(nid, d["t_sec"], d["category"], d["conf"], d["x"], d["y"], d["w"], d["h"], d["species"], d["sp_conf"])
                                       for d in src.db.q("SELECT t_sec, category, conf, x, y, w, h, species, sp_conf FROM detections WHERE item_id=? ORDER BY id", (sid,))])
                        # 特徴量と枠の画像（あれば写す。なければあとで自動判定の「枠はいまあるものを使う」で付く）
                        fa, fb = features_path(src, src_rel), features_path(project, rel)
                        if os.path.isfile(fa) and not os.path.isfile(fb):
                            try:
                                shutil.copyfile(fa, fb)
                            except OSError as e:
                                log.warning("特徴量を写せません %s: %s", fa, e)
                        ca, cb = os.path.dirname(crop_path(src, src_rel, 0)), os.path.dirname(crop_path(project, rel, 0))
                        if os.path.isdir(ca) and not os.path.isdir(cb):
                            try:
                                shutil.copytree(ca, cb)
                            except OSError as e:
                                log.warning("枠の画像を写せません %s: %s", ca, e)
                        n_done += 1
                        if progress and n_done % 20 == 0:
                            progress(n_done, len(picked), rel)
        finally:
            src.close()
    if n_done:
        _catalog_mark_imported(project)
    return n_done


def _import_tree_rows(project, name: str, rows: list[dict], batch: str, now: str, progress=None, stop=None) -> int:
    """親フォルダ参照の対象を作る（動画の stat と既存の種名だけ。メタデータはあとで MetaJob が取り、検出は自動判定で付ける）"""
    from .paths import kind_of
    db = project.db
    root = rows[0]["src_root"]
    n = 0
    for i0 in range(0, len(rows), 200):
        if stop and stop():
            break
        chunk = rows[i0:i0 + 200]
        with db.tx() as c:
            for r in chunk:
                rel = f"{name}/{r['rel']}"
                ab = os.path.join(root, *r["rel"].split("/"))
                try:
                    st = os.stat(ab)
                    size, mtime, missing = st.st_size, st.st_mtime, 0
                except OSError:
                    size, mtime, missing = None, None, 1
                codes = sorted(r.get("species") or ())
                cur = c.execute("INSERT OR IGNORE INTO items(rel_path, rel_key, name, kind, folder, size, mtime, season, station, pattern_json, datetime, datetime_source, meta_state, missing, "
                                "status, det_state, cls_state, src_dir, src_id, src_rel, batch, imported_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (rel, rel_key(rel), r["name"], kind_of(r["name"]) or "video", os.path.dirname(rel), size, mtime, r["season"] or None, r["station"] or None, "{}",
                                 r.get("dt") or None, "csv" if r.get("dt") else None, 0, missing, "auto" if codes else "new", 0, 0, None, None, r["rel"], batch or None, now, now))
                if cur.rowcount == 0:
                    continue
                nid = cur.lastrowid
                for code in codes:
                    c.execute("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                              (nid, code, None, "csv", f"labels:{name}", now, None))
                n += 1
                if progress and n % 50 == 0:
                    progress(n, len(rows), rel)
    return n


# ---------- カタログ（参照先の全動画の目録。ここから取り込む対象を選ぶ） ----------
def build_catalog(project, progress=None, stop=None) -> dict:
    """参照先の全動画の目録を作り直す（メディアは持たない）。プロジェクト参照は items と判定から、親フォルダ参照は種名の CSV から"""
    db = project.db
    by_src: dict[str, int] = {}
    with db.tx() as c:
        c.execute("DELETE FROM catalog")
    for s in project.sources():
        if stop and stop():
            break
        name = s["name"]
        if progress:
            progress(0, 0, name)
        rows: list[tuple] = []
        if s.get("kind") == "tree":
            trows, _ = _tree_rows(project, s, None, lambda a, b, msg: progress and progress(a, b, msg), stop)
            for r in trows:
                rows.append((name, r["src_id"], r["rel"], r["season"], r["station"], r["file"], r["dt"] or None, r["status"],
                             ";".join(sorted(r["species"])) or None, r["ai"], r["agree"], r["det_state"] or 0, r["duration"], r["size"]))
        else:
            try:
                src = open_source(s["dir"])
            except Exception as e:
                log.warning("参照先 %s を開けません: %s", name, e)
                continue
            try:
                hum: dict[int, list] = defaultdict(list)
                for j in src.db.q("SELECT j.item_id, j.species, j.origin, i.status FROM judgements j JOIN items i ON i.id=j.item_id WHERE j.species != 'area'"):
                    if j["status"] == "verified" or j["origin"] in HUMAN_ORIGINS:
                        hum[j["item_id"]].append(j["species"])
                for r in src.db.q("SELECT id, rel_path, season, station, name, COALESCE(datetime_corrected, datetime) AS dt, status, auto_json, agree, det_state, duration, size FROM items WHERE missing=0"):
                    ai = None
                    if r["auto_json"]:
                        try:
                            ai = ";".join(x["species"] for x in (json.loads(r["auto_json"]).get("species") or [])) or None
                        except Exception:
                            ai = None
                    rows.append((name, r["id"], r["rel_path"], r["season"] or name, r["station"], r["name"], r["dt"], r["status"],
                                 ";".join(sorted(set(hum.get(r["id"], [])))) or None, ai, r["agree"], r["det_state"], r["duration"], r["size"]))
            finally:
                src.close()
        for i0 in range(0, len(rows), 5000):
            if stop and stop():
                break
            with db.tx() as c:
                c.executemany("INSERT OR IGNORE INTO catalog(source, src_id, rel, season, station, name, datetime, status, species, ai, agree, det_state, duration, size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows[i0:i0 + 5000])
            if progress:
                progress(i0 + len(rows[i0:i0 + 5000]), len(rows), name)
        by_src[name] = len(rows)
    _catalog_mark_imported(project)
    n = int(db.scalar("SELECT COUNT(*) FROM catalog") or 0)
    project.data["catalog"] = dict(built_at=now_iso(), n=n, by_source=by_src)
    project.save()
    return dict(n=n, by_source=by_src)


def _catalog_mark_imported(project) -> None:
    """取り込み済みの対象に横断側の items.id を付ける（rel_path = 名前/rel で照合）"""
    db = project.db
    with db.tx() as c:
        c.execute("UPDATE catalog SET item_id=(SELECT i.id FROM items i WHERE i.rel_path = catalog.source || '/' || catalog.rel)")


def catalog_where(f: dict) -> tuple[str, list]:
    w, p = ["1=1"], []
    if f.get("source"):
        w.append("source=?"); p.append(f["source"])
    if f.get("season"):
        w.append("season=?"); p.append(f["season"])
    st = [x.strip() for x in str(f.get("station") or "").replace("、", ",").split(",") if x.strip()]
    if st:
        w.append(f"station IN ({','.join('?' * len(st))})"); p += st
    ex = [x.strip() for x in str(f.get("exclude_stations") or "").replace("、", ",").split(",") if x.strip()] if isinstance(f.get("exclude_stations"), str) else list(f.get("exclude_stations") or [])
    if ex:
        w.append(f"(station IS NULL OR station NOT IN ({','.join('?' * len(ex))}))"); p += ex
    sp = f.get("species")
    if sp == "none":
        w.append("species IS NULL")
    elif sp == "any":
        w.append("species IS NOT NULL")
    elif sp:
        w.append("(species = ? OR species LIKE ? OR species LIKE ? OR species LIKE ?)"); p += [sp, sp + ";%", "%;" + sp, "%;" + sp + ";%"]
    if f.get("single_only"):
        w.append("(species IS NULL OR species NOT LIKE '%;%')")
    if f.get("status"):
        w.append("status=?"); p.append(f["status"])
    if f.get("imported") in ("1", 1, True):
        w.append("item_id IS NOT NULL")
    elif f.get("imported") in ("0", 0, False) and f.get("imported") is not None and f.get("imported") != "":
        w.append("item_id IS NULL")
    if f.get("agree"):
        w.append({"agree": "agree=1", "disagree": "agree=3", "partial": "agree=2", "nohuman": "species IS NULL AND ai IS NOT NULL"}.get(f["agree"], "1=1"))
    if f.get("text"):
        w.append("(name LIKE ? OR rel LIKE ?)"); p += [f"%{f['text']}%", f"%{f['text']}%"]
    rels = list(f.get("rels") or [])           # CSV の一覧（Season, Station, File）で絞る: 親フォルダ参照は 調査回/地点/ファイル、プロジェクト参照は 地点/ファイル
    if f.get("csv_text"):
        for row in parse_csv_list(f["csv_text"]):
            if row.get("season") and row.get("station"):
                rels.append(f"{row['season']}/{row['station']}/{row['file']}")
            if row.get("station"):
                rels.append(f"{row['station']}/{row['file']}")
            rels.append(row["file"])
    if rels:
        rels = [r.lower() for r in rels][:5000]
        w.append(f"(LOWER(rel) IN ({','.join('?' * len(rels))}) OR LOWER(name) IN ({','.join('?' * len(rels))}))"); p += rels + rels
    return " AND ".join(w), p


def catalog_counts(project, f: dict) -> dict:
    db = project.db
    where, p = catalog_where(f)
    total = int(db.scalar(f"SELECT COUNT(*) FROM catalog WHERE {where}", p) or 0)
    imported = int(db.scalar(f"SELECT COUNT(*) FROM catalog WHERE {where} AND item_id IS NOT NULL", p) or 0)
    by_source = {r[0]: r[1] for r in db.q(f"SELECT source, COUNT(*) FROM catalog WHERE {where} GROUP BY source", p)}
    by_status = {r[0] or "new": r[1] for r in db.q(f"SELECT status, COUNT(*) FROM catalog WHERE {where} GROUP BY status", p)}
    seasons = [r[0] or "" for r in db.q(f"SELECT DISTINCT season FROM catalog WHERE {where} ORDER BY season", p)]
    stations = [r[0] or "" for r in db.q(f"SELECT DISTINCT station FROM catalog WHERE {where} ORDER BY station", p)]
    pivot: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in db.q(f"SELECT species, season, COUNT(*) FROM catalog WHERE {where} GROUP BY species, season", p):
        for code in (r[0] or "(none)").split(";"):
            pivot[code][r[1] or ""] += r[2]
    by_species = {k: sum(v.values()) for k, v in pivot.items()}
    return dict(total=total, imported=imported, by_source=by_source, by_status=by_status, seasons=seasons, stations=stations,
                pivot={k: dict(v) for k, v in pivot.items()}, by_species=by_species, built=project.data.get("catalog"))


CAT_SORTS = {"rel": "source, rel", "datetime": "COALESCE(datetime, ''), rel", "station": "station, COALESCE(datetime, ''), rel", "species": "species, rel", "status": "status, rel", "season": "season, station, rel"}


def catalog_rows(project, f: dict, sort: str = "rel", desc: bool = False, offset: int = 0, limit: int = 200) -> dict:
    db = project.db
    where, p = catalog_where(f)
    order = CAT_SORTS.get(sort or "rel", CAT_SORTS["rel"])
    if desc:
        order = ", ".join(x.strip() + " DESC" for x in order.split(","))
    rows = [dict(r) for r in db.q(f"SELECT id, source, src_id, rel, season, station, name, datetime, status, species, ai, agree, det_state, duration, item_id FROM catalog WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*p, int(limit), int(offset)))]
    total = int(db.scalar(f"SELECT COUNT(*) FROM catalog WHERE {where}", p) or 0)
    return dict(rows=rows, total=total, offset=int(offset))


def catalog_sample(project, f: dict, params: dict) -> dict:
    """カタログの絞り込みの中から、種ごとに n 本（地点 × 調査回の上限、同じ地点で gap 分以内の連続動画は 1 本、乱数の種）。
    返り値 dict(ids, counts={code: n}, available={code: n})。params に n_per_species が 0 なら絞り込みの全部"""
    db = project.db
    where, p = catalog_where(dict(f, imported="0"))
    rows = [dict(r) for r in db.q(f"SELECT id, source, season, station, datetime, species FROM catalog WHERE {where}", p)]
    n_per = int(params.get("n_per_species") or 0)
    if not n_per:
        return dict(ids=[r["id"] for r in rows], counts={}, available={}, n=len(rows))
    cap = int(params.get("cap") or 0)
    gap_min = float(params.get("gap_min") or 0)
    seed = int(params.get("seed") or 1)
    want_sp = set(params.get("species") or [])
    pool: dict[str, list[dict]] = defaultdict(list)
    thinned = _thin([dict(r, dt=r["datetime"] or "") for r in rows if r["species"]], gap_min)
    for r in thinned:
        for code in r["species"].split(";"):
            if not want_sp or code in want_sp:
                pool[code].append(r)
    rng = random.Random(seed)
    ids: list[int] = []
    seen: set[int] = set()
    counts: dict[str, int] = {}
    for code in sorted(pool):
        cands = list(pool[code])
        rng.shuffle(cands)
        per_cell: dict[tuple, int] = defaultdict(int)
        n_sp = 0
        for r in cands:
            if n_sp >= n_per:
                break
            cell = (r["source"], r["station"], r["season"])
            if cap and per_cell[cell] >= cap:
                continue
            if r["id"] in seen:
                continue
            per_cell[cell] += 1
            seen.add(r["id"]); ids.append(r["id"]); n_sp += 1
        counts[code] = n_sp
    return dict(ids=ids, counts=counts, available={k: len(v) for k, v in pool.items()}, n=len(ids))


def catalog_import(project, ids: list[int], batch: str, record: dict | None = None, progress=None, stop=None) -> dict:
    """カタログの行（id）を取り込む"""
    db = project.db
    picked = []
    src_of = {s["name"]: s for s in project.sources()}
    for i0 in range(0, len(ids), 900):
        part = ids[i0:i0 + 900]
        for r in db.q(f"SELECT id, source, src_id, rel, season, station, name, datetime, species FROM catalog WHERE id IN ({','.join('?' * len(part))}) AND item_id IS NULL", part):
            s = src_of.get(r["source"])
            if s is None:
                continue
            if s.get("kind") == "tree":
                root = norm(s.get("root") or "")
                pdir = tree_season_project(root, r["season"]) if r["src_id"] is not None and r["season"] else None
                if pdir:                                            # 調査回のプロジェクトから（判定・検出・特徴量ごと写す）
                    sub = r["rel"].split("/", 1)[1] if "/" in r["rel"] else r["rel"]
                    picked.append((r["source"], pdir, dict(id=r["src_id"], rel_path=sub, name=r["name"], station=r["station"], season=r["season"], prefix=r["season"])))
                else:
                    picked.append((r["source"], "", dict(id=None, rel=r["rel"], name=r["name"], station=r["station"] or "", season=r["season"] or "", dt=r["datetime"] or "",
                                                         status="auto" if r["species"] else "new", species=set((r["species"] or "").split(";")) - {""}, src_name=r["source"], src_dir="", src_kind="tree", src_root=root)))
            else:
                picked.append((r["source"], s["dir"], dict(id=r["src_id"], rel_path=r["rel"], name=r["name"], station=r["station"], season=r["season"])))
    n = import_rows(project, picked, batch, progress, stop)
    rec = dict(at=now_iso(), batch=batch, params=dict(record or {}, mode="catalog"), n=n, counts=(record or {}).get("counts") or {}, by_source={})
    for name, _, _ in picked:
        rec["by_source"][name] = rec["by_source"].get(name, 0) + 1
    project.data.setdefault("imports", []).append(rec)
    project.save()
    return dict(n=n, n_requested=len(ids))


def export_catalog(project, path: str, f: dict | None = None) -> dict:
    """カタログ（参照先の全動画と判定）を 1 つの CSV に: Source, Season, Station, File, DateTime, Status, Species, AI, Agree, Imported, RelPathInSource"""
    db = project.db
    where, p = catalog_where(f or {})
    sl = project.species
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Source", "Season", "Station", "File", "DateTime", "Status", "Species", "AI", "Agree", "Imported", "RelPathInSource"])
        for r in db.q(f"SELECT source, season, station, name, datetime, status, species, ai, agree, item_id, rel FROM catalog WHERE {where} ORDER BY source, rel", p):
            w.writerow([r[0], r[1] or "", r[2] or "", r[3], r[4] or "", r[5] or "", ";".join(sl.csv_label(c) for c in (r[6] or "").split(";") if c), ";".join(sl.csv_label(c) for c in (r[7] or "").split(";") if c),
                        {1: "match", 2: "partial", 3: "mismatch"}.get(r[8], ""), 1 if r[9] else 0, r[10]])
            n += 1
    return dict(path=path, n=n)


# ---------- 走査の代わり: 参照先の動画があるかを確かめる ----------
def check_missing(project, progress=None, stop=None) -> dict:
    db = project.db
    rows = db.q("SELECT id, rel_path, missing FROM items")
    gone, back = [], []
    for n, r in enumerate(rows):
        if stop and stop():
            break
        ok = os.path.isfile(project.abs_path(r["rel_path"]))
        if not ok and not r["missing"]:
            gone.append(r["id"])
        elif ok and r["missing"]:
            back.append(r["id"])
        if progress and n % 200 == 0:
            progress(n, len(rows), r["rel_path"])
    with db.tx() as c:
        if gone:
            c.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
        if back:
            c.executemany("UPDATE items SET missing=0 WHERE id=?", [(i,) for i in back])
    db.set_meta("last_scan", now_iso())
    return dict(found=len(rows) - len(gone), added=0, back=len(back), changed=0, gone=len(gone))


# ---------- 書き戻し・再取得 ----------
def state_sig(state: dict, train_exclude=0, recheck=0) -> str:
    """判定の署名（種と項目の値、状態、メモ、学習から外す、見直し）。取り込み・同期のときに控え、どちらが変わったかの判断に使う"""
    js = sorted((j["species"], json.dumps(j.get("fields") or {}, sort_keys=True, ensure_ascii=False)) for j in state.get("judgements", []))
    return json.dumps([js, state.get("status") or "new", state.get("fields") or {}, state.get("note") or "", int(train_exclude or 0), int(recheck or 0)], sort_keys=True, ensure_ascii=False)


def _differs(a: dict, b: dict) -> bool:
    return state_sig(a) != state_sig(b)


def _label_of_dir(project, sdir: str) -> str:
    """書き戻し先のプロジェクトの表示名: 参照先の名前（プロジェクト参照）か、親フォルダ参照なら <名前>/<調査回>"""
    for s in project.sources():
        if s.get("dir") and key(s["dir"]) == key(sdir):
            return s["name"]
        if s.get("kind") == "tree" and s.get("root") and key(sdir).startswith(key(s["root"]).rstrip("/") + "/"):
            return f"{s['name']}/{os.path.basename(os.path.dirname(norm(sdir)))}"
    return os.path.basename(os.path.dirname(norm(sdir))) or sdir


def sync_plan(project, direction: str) -> dict:
    """push（横断 → 参照先）/ pull（参照先 → 横断）で何が変わるかを数える。
    push: 横断で確認済みにした（取り込み後に更新された）対象のうち、参照先と内容が違うもの。参照先も取り込み後に変わっていれば衝突。
    pull: 参照先で取り込み後に更新された対象のうち、横断と違うもの。横断でも変えていれば衝突"""
    db = project.db
    out = dict(n_update=0, n_conflict=0, n_same=0, by_source={}, conflicts=[])
    for sdir in [r[0] for r in db.q("SELECT DISTINCT src_dir FROM items WHERE src_dir IS NOT NULL AND src_id IS NOT NULL ORDER BY src_dir")]:
        name = _label_of_dir(project, sdir)
        rows = db.q("SELECT id, src_id, rel_path, status, verified_at, updated_at, imported_at, src_updated_at, src_sig, train_exclude, recheck FROM items WHERE src_dir=? AND src_id IS NOT NULL", (sdir,))
        if not rows:
            continue
        try:
            src = open_source(sdir)
        except Exception as e:
            out["by_source"][name] = dict(error=str(e))
            continue
        try:
            n_up = n_conf = n_same = 0
            for r in rows:
                here = item_state(db, r["id"])
                there = item_state(src.db, r["src_id"])
                if not there:
                    continue
                srow = src.db.q1("SELECT updated_at, train_exclude, recheck FROM items WHERE id=?", (r["src_id"],))
                sig_here = state_sig(here, r["train_exclude"], r["recheck"])
                sig_there = state_sig(there, srow["train_exclude"], srow["recheck"])
                here_changed = sig_here != (r["src_sig"] or "")
                there_changed = sig_there != (r["src_sig"] or "")
                if sig_here == sig_there:
                    n_same += 1
                    continue
                if direction == "push":
                    if not here_changed:
                        n_same += 1
                        continue
                    if there_changed and there["status"] == "verified":
                        n_conf += 1
                        out["conflicts"].append(dict(id=r["id"], rel_path=r["rel_path"], here=[j["species"] for j in here["judgements"]], there=[j["species"] for j in there["judgements"]]))
                    else:
                        n_up += 1
                else:
                    if not there_changed:
                        n_same += 1
                        continue
                    if here_changed and here["status"] == "verified":
                        n_conf += 1
                        out["conflicts"].append(dict(id=r["id"], rel_path=r["rel_path"], here=[j["species"] for j in here["judgements"]], there=[j["species"] for j in there["judgements"]]))
                    else:
                        n_up += 1
            out["by_source"][name] = dict(update=n_up, conflict=n_conf, same=n_same)
            out["n_update"] += n_up; out["n_conflict"] += n_conf; out["n_same"] += n_same
        finally:
            src.close()
    out["conflicts"] = out["conflicts"][:200]
    return out


def sync_apply(project, direction: str, by: str, overwrite_conflicts: bool = False, progress=None, stop=None) -> dict:
    """sync_plan の「更新」を実際に書く。overwrite_conflicts なら衝突も push/pull の側で上書きする"""
    from .judge import update_agree
    db = project.db
    n_done = n_skip = 0
    for sdir in [r[0] for r in db.q("SELECT DISTINCT src_dir FROM items WHERE src_dir IS NOT NULL AND src_id IS NOT NULL ORDER BY src_dir")]:
        rows = db.q("SELECT id, src_id, rel_path, status, updated_at, imported_at, src_updated_at, src_sig, train_exclude, recheck FROM items WHERE src_dir=? AND src_id IS NOT NULL", (sdir,))
        if not rows:
            continue
        src = open_source(sdir)
        try:
            changed_src_ids, changed_ids = [], []
            for n, r in enumerate(rows):
                if stop and stop():
                    break
                if progress and n % 50 == 0:
                    progress(n, len(rows), r["rel_path"])
                here = item_state(db, r["id"])
                there = item_state(src.db, r["src_id"])
                if not there:
                    continue
                srow = src.db.q1("SELECT updated_at, train_exclude, recheck FROM items WHERE id=?", (r["src_id"],))
                sig_here = state_sig(here, r["train_exclude"], r["recheck"])
                sig_there = state_sig(there, srow["train_exclude"], srow["recheck"])
                here_changed = sig_here != (r["src_sig"] or "")
                there_changed = sig_there != (r["src_sig"] or "")
                if sig_here == sig_there:
                    if r["src_sig"] != sig_here:
                        with db.tx() as c:
                            c.execute("UPDATE items SET src_sig=? WHERE id=?", (sig_here, r["id"]))
                    continue
                now = now_iso()
                if direction == "push":
                    if not here_changed:
                        continue
                    if there_changed and there["status"] == "verified" and not overwrite_conflicts:
                        n_skip += 1
                        continue
                    with src.db.tx() as c:
                        c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                                  (r["src_id"], now, "cross_push", by, json.dumps(there, ensure_ascii=False), json.dumps(here, ensure_ascii=False)))
                        _apply_state(c, r["src_id"], here, by)
                        c.execute("UPDATE items SET train_exclude=?, recheck=?, updated_at=? WHERE id=?", (int(r["train_exclude"] or 0), int(r["recheck"] or 0), now, r["src_id"]))
                    with db.tx() as c:
                        c.execute("UPDATE items SET src_updated_at=?, src_sig=? WHERE id=?", (now, sig_here, r["id"]))
                    changed_src_ids.append(r["src_id"])
                else:
                    if not there_changed:
                        continue
                    if here_changed and here["status"] == "verified" and not overwrite_conflicts:
                        n_skip += 1
                        continue
                    with db.tx() as c:
                        c.execute("INSERT INTO history(item_id, time, action, by, before_json, after_json) VALUES(?,?,?,?,?,?)",
                                  (r["id"], now, "cross_pull", by, json.dumps(here, ensure_ascii=False), json.dumps(there, ensure_ascii=False)))
                        _apply_state(c, r["id"], there, by)
                        c.execute("UPDATE items SET train_exclude=?, recheck=?, src_updated_at=?, src_sig=?, updated_at=? WHERE id=?",
                                  (int(srow["train_exclude"] or 0), int(srow["recheck"] or 0), srow["updated_at"], sig_there, now, r["id"]))
                    changed_ids.append(r["id"])
                n_done += 1
            if changed_src_ids:
                update_agree(src.db, changed_src_ids)
            if changed_ids:
                update_agree(db, changed_ids)
        finally:
            src.close()
    return dict(n=n_done, n_skipped=n_skip)


# ---------- 選んだ動画をフォルダへ写す・一覧を書く ----------
def export_list(project, path: str, f: dict | None = None) -> dict:
    """対象の一覧（Season, Station, File, Source, Batch, Status, Species）を CSV に"""
    from .judge import build_where
    where, p = build_where(dict(f or {}, include_missing=True))
    rows = project.db.q(f"SELECT id, rel_path, src_rel, season, station, name, batch, status FROM items WHERE {where} ORDER BY rel_path", p)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Source", "Season", "Station", "File", "Batch", "Status", "Species", "RelPathInSource"])
        for r in rows:
            sp = ";".join(j["species"] for j in project.db.q("SELECT species FROM judgements WHERE item_id=? ORDER BY id", (r["id"],)))
            w.writerow([r["rel_path"].split("/")[0], r["season"] or "", r["station"] or "", r["name"], r["batch"] or "", r["status"], sp, r["src_rel"] or ""])
    return dict(path=path, n=len(rows))


def copy_videos(project, out_dir: str, f: dict | None = None, progress=None, stop=None) -> dict:
    """対象の動画を out_dir/<参照先>/<参照先での相対パス> に写す（研究用の手元の複製や配布に）"""
    from .judge import build_where
    where, p = build_where(dict(f or {}))
    rows = project.db.q(f"SELECT id, rel_path FROM items WHERE {where} ORDER BY rel_path", p)
    n_ok = n_miss = 0
    for n, r in enumerate(rows):
        if stop and stop():
            break
        src = project.abs_path(r["rel_path"])
        dst = os.path.join(out_dir, *r["rel_path"].split("/"))
        if progress and n % 10 == 0:
            progress(n, len(rows), r["rel_path"])
        if not os.path.isfile(src):
            n_miss += 1
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            n_ok += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n_ok += 1
    return dict(n=n_ok, n_missing=n_miss, out_dir=norm(out_dir))
