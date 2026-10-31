"""横断プロジェクト（2 階層目）: ほかのプロジェクト（調査回ごとのプロジェクト = 1 階層目）の動画を参照して、調査回をまたぐ作業をする。

- project.json に kind = "cross" と sources（参照先プロジェクトの名前とフォルダ）。フォルダは走査しない。
- 対象の相対パスは「<参照先の名前>/<参照先での相対パス>」。絶対パスは参照先プロジェクトのルート（この PC で解決）から引く（Project.abs_path）。
- 取り込みは参照先から対象（メタデータ・判定・AI の判定・検出の枠・特徴量・枠の画像）を写す。動画本体は写さない（参照先の NAS などから直接読む）。
- 選び方: 種ごとの本数（人の判定から。地点 × 調査回の上限、連続動画の間引き、地点の除外、乱数の種）/ 地点を丸ごと / 参照先を丸ごと / CSV の一覧。
  使った条件は project.json の imports に残す（論文の方法に書けるように）。
- 書き戻し（横断で確定した判定を参照先へ）と再取得（参照先で直した判定を横断へ）は push / pull。
- 種名の対応表（project.json の label_map: 表記 → 種コード）: 種名の CSV の値と調査回プロジェクトの判定の種コードを、横断の種リストのどの種と見なすか。
  カタログ・取り込み・書き戻し・再取得のすべてに当てる。種名の CSV は生の表記で読んで cache/labels に残し、対応は読み込み時に当てる（_apply_map）。"""
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
    fk = classify_folder(path)
    if fk == "station":
        raise ValueError(f"地点のフォルダのようです（動画が直下にある）: {path}。調査回のフォルダ（下に地点が並ぶ）か親フォルダ（下に調査回が並ぶ）を指定してください")
    if fk == "season":
        # 調査回のフォルダを 1 つだけ: 親フォルダを参照先にして、その調査回だけを見る（親フォルダの種名 CSV も使える。相対パスは <名前>/<調査回>/<地点>/<動画> で親と同じ）
        parent, season = os.path.dirname(path.rstrip("/")), os.path.basename(path.rstrip("/"))
        for s in project.sources():
            if s.get("kind") == "tree" and s.get("root") and key(s["root"]) == key(parent):
                only = s.get("seasons")
                if not only:
                    return dict(name=s["name"], root=s["root"], kind="tree", added=False, season=season, covered=True)       # 親フォルダごと登録済み
                if season in only:
                    return dict(name=s["name"], root=s["root"], kind="tree", added=False, season=season)
                only.append(season)
                s["seasons"] = sorted(set(only))
                project.save()
                return dict(name=s["name"], root=s["root"], kind="tree", added=True, season=season, seasons=s["seasons"])
        nm = _unique_name(project, (name or "").strip(), parent)
        entry = dict(name=nm, kind="tree", root=parent, seasons=[season], added_at=now_iso())
        if labels_csv:
            entry["labels_csv"] = norm(labels_csv)
        project.data.setdefault("sources", []).append(entry)
        project.forget_source_roots()
        project.save()
        return dict(name=nm, root=parent, kind="tree", added=True, season=season, seasons=[season])
    for s in project.sources():
        if s.get("root") and key(s["root"]) == key(path):
            if s.get("seasons"):                               # 調査回を限って登録してあった親フォルダを、丸ごとに広げる
                s.pop("seasons", None)
                project.save()
                return dict(name=s["name"], root=s["root"], kind="tree", added=True, widened=True)
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
    r = add_source(project, parent, None, labels_csv)
    if r.get("season"):                                         # 実は調査回のフォルダだった: その調査回だけの参照先
        return dict(added=[r["name"]] if r.get("added") else [], skipped=[] if r.get("added") else [r["name"]], errors=[], kind="tree", n_sub=1,
                    n_project_seasons=1 if _is_project_path(parent) else 0, season=r["season"], covered=r.get("covered"))
    subs = _tree_seasons(parent)
    n_proj = sum(1 for n in subs if _is_project_path(os.path.join(parent, n)))
    return dict(added=[r["name"]] if r.get("added") else [], skipped=[] if r.get("added") else [r["name"]], errors=[], kind="tree", n_sub=len(subs), n_project_seasons=n_proj,
                ignored_cross=r.get("ignored_cross"), widened=r.get("widened"))


def tree_season_project(root: str, season: str) -> str | None:
    """調査回フォルダに TrapNote のプロジェクト（横断ではないもの）があればそのフォルダ（<root>/<season>/TrapNote）"""
    d = os.path.join(root, season)
    if not os.path.isdir(d):
        return None
    pdir, kind = _project_at(d)
    return pdir if pdir and kind != "cross" else None


def _human_codes(project, src, lm: dict, code_names: dict | None = None, source: str = "") -> tuple[dict[int, list], dict[int, list]]:
    """参照先プロジェクトの人の判定 → ({item_id: [種コード]}（対応表を当てたもの）, {item_id: [元の種コード]})。code_names に、横断の種リストにないコードの名前と本数を控える"""
    hum: dict[int, list] = defaultdict(list)
    raw_counts: Counter = Counter()
    for j in src.db.q("SELECT j.item_id, j.species, j.origin, i.status FROM judgements j JOIN items i ON i.id=j.item_id WHERE j.species != 'area' AND i.missing=0"):
        if j["status"] == "verified" or j["origin"] in HUMAN_ORIGINS:
            hum[j["item_id"]].append(j["species"])
            raw_counts[j["species"]] += 1
    if code_names is not None and raw_counts:
        known = project.species.by_code()
        sb = src.species.by_code()
        for code, n in raw_counts.items():
            if code in known:
                continue
            d = code_names.setdefault(code, dict(ja=(sb.get(code) or {}).get("name_ja") or "", en=(sb.get(code) or {}).get("name_en") or "", n=0, source=source))
            d["n"] += n
    raw = dict(hum)
    if lm:
        hum = {k: map_codes(lm, v) for k, v in hum.items()}
    return hum, raw


def _tree_rows_by_season(project, s: dict, seasons: list[str] | None = None, progress=None, stop=None, code_names: dict | None = None):
    """親フォルダ参照の全動画の行を調査回ごとに yield する（(調査回, 行の list, プロジェクトのフォルダ or None)。120 万本を一度にメモリに持たないため）。
    調査回フォルダにプロジェクトがあればその items と判定（src_dir / src_id 付き）、なければ種名の CSV の行。
    行: dict(season, station, file, rel=<season>/<station>/<file>, species(set), dt, status, ai, agree, det_state, duration, size, src_dir, src_id, src_rel, labels)"""
    root = norm(s.get("root") or "")
    if not root or not os.path.isdir(root):
        return
    all_seasons = source_seasons(s)
    want = [se for se in all_seasons if not seasons or se in set(seasons)]
    lab = _labels(project, s, (lambda a, b, msg: progress(0, len(want), f"種名の CSV: {msg}")) if progress else None, stop)
    lm = label_map(project)
    scan_folders = bool(project.setting("catalog_scan_folders"))
    by_season = lab["by_season"]
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
        out: list[dict] = []
        if src is not None:
            try:
                hum, hum_raw = _human_codes(project, src, lm, code_names, f"{s['name']}/{se}")
                for r in src.db.q("SELECT id, rel_path, station, name, COALESCE(datetime_corrected, datetime) AS dt, status, auto_json, agree, det_state, duration, size FROM items WHERE missing=0"):
                    ai = None
                    if r["auto_json"]:
                        try:
                            ai = ";".join(x["species"] for x in (json.loads(r["auto_json"]).get("species") or [])) or None
                        except Exception:
                            ai = None
                    out.append(dict(season=se, station=r["station"] or "", file=r["name"], rel=f"{se}/{r['rel_path']}", species=set(hum.get(r["id"], [])), dt=r["dt"] or "",
                                    status=r["status"], ai=ai, agree=r["agree"], det_state=r["det_state"], duration=r["duration"], size=r["size"], src_dir=src.dir, src_id=r["id"], src_rel=r["rel_path"],
                                    labels=";".join(sorted(set(hum_raw.get(r["id"], [])))) or None))
            finally:
                src.close()
            yield se, out, pdir
            continue
        if by_season.get(se):
            for r in by_season.get(se, []):
                out.append(dict(season=se, station=r["station"], file=r["file"], rel=f"{se}/{r['station']}/{r['file']}", species=set(r["species"]), dt=r["dt"] or "",
                                status="auto" if r["species"] else "new", ai=None, agree=None, det_state=0, duration=None, size=None, src_dir=None, src_id=None, src_rel=None,
                                labels=";".join(r.raw) or None))
        elif scan_folders:                     # プロジェクトも種名の CSV の行もない調査回: フォルダを見て動画を並べる（種名なし。新しい調査回など。NAS では遅いので設定で）
            for st in _tree_stations(root, se):
                if stop and stop():
                    break
                if progress:
                    progress(i, len(want), f"{s['name']}/{se}/{st}")
                for fn in _tree_station_files(root, se, st):
                    out.append(dict(season=se, station=st, file=fn, rel=f"{se}/{st}/{fn}", species=set(), dt="", status="new", ai=None, agree=None, det_state=0,
                                    duration=None, size=None, src_dir=None, src_id=None, src_rel=None, labels=None))
        yield se, out, None


def _cat_rows_by_season(project, s: dict, progress=None, stop=None, code_names: dict | None = None):
    """build_catalog 用: 親フォルダ参照の行をカタログの tuple（CAT_INSERT の順）で調査回ごとに yield する。
    種名の CSV の行は LabelRow から直接 tuple を作る（dict を経由すると 120 万行で 10 秒余計にかかる）"""
    root = norm(s.get("root") or "")
    name = s["name"]
    if not root or not os.path.isdir(root):
        return
    want = source_seasons(s)
    lab = _labels(project, s, (lambda a, b, msg: progress(0, len(want), f"種名の CSV: {msg}")) if progress else None, stop)
    lm = label_map(project)
    scan_folders = bool(project.setting("catalog_scan_folders"))
    by_season = lab["by_season"]
    for i, se in enumerate(want):
        if stop and stop():
            break
        if progress:
            progress(i, len(want), f"{name}/{se}")
        pdir = tree_season_project(root, se)
        src = None
        if pdir:
            try:
                src = open_source(pdir)
            except Exception as e:
                log.warning("調査回のプロジェクト %s を開けません: %s", pdir, e)
        out: list[tuple] = []
        if src is not None:
            try:
                hum, hum_raw = _human_codes(project, src, lm, code_names, f"{name}/{se}")
                for r in src.db.q("SELECT id, rel_path, station, name, COALESCE(datetime_corrected, datetime) AS dt, status, auto_json, agree, det_state, duration, size FROM items WHERE missing=0"):
                    ai = None
                    if r["auto_json"]:
                        try:
                            ai = ";".join(x["species"] for x in (json.loads(r["auto_json"]).get("species") or [])) or None
                        except Exception:
                            ai = None
                    out.append((name, r["id"], f"{se}/{r['rel_path']}", se, r["station"] or "", r["name"], r["dt"] or None, r["status"],
                                ";".join(sorted(set(hum.get(r["id"], [])))) or None, ai, r["agree"], r["det_state"] or 0, r["duration"], r["size"],
                                ";".join(sorted(set(hum_raw.get(r["id"], [])))) or None))
            finally:
                src.close()
        elif by_season.get(se):
            for d in by_season[se]:
                sp = d.species                                   # 対応表を当てた種コードの tuple（並べ替え済み）
                st = d.station
                out.append((name, None, f"{se}/{st}/{d.file}", se, st, d.file, d.dt or None, "auto" if sp else "new", ";".join(sp) or None, None, None, 0, None, None, ";".join(d.raw) or None))
        elif scan_folders:
            for st in _tree_stations(root, se):
                if stop and stop():
                    break
                if progress:
                    progress(i, len(want), f"{name}/{se}/{st}")
                for fn in _tree_station_files(root, se, st):
                    out.append((name, None, f"{se}/{st}/{fn}", se, st, fn, None, "new", None, None, None, 0, None, None, None))
        yield se, out


def _tree_rows(project, s: dict, seasons: list[str] | None = None, progress=None, stop=None, code_names: dict | None = None) -> tuple[list[dict], list[str]]:
    """_tree_rows_by_season を全部つないだもの（抽出など、全部を一度に見るとき用）→ (行, プロジェクトのある調査回)"""
    out: list[dict] = []
    proj_seasons: list[str] = []
    for se, rows, pdir in _tree_rows_by_season(project, s, seasons, progress, stop, code_names):
        out.extend(rows)
        if pdir:
            proj_seasons.append(se)
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
    s.pop("labels_paused", None)
    project.save()
    _LABELS.pop(name, None)
    if not wait:
        return dict(ok=True, labels=dict(pending=not labels_ready(project, s)))
    lab = _labels(project, s)
    return dict(ok=True, labels=dict(n=len(lab["rows"]), unmapped=lab["unmapped"][:20], species=lab["species"], files=[dict(path=f["path"], n=f["n"], error=f["error"]) for f in lab["files"]]))


_LABELS: dict = {}       # name → (CSV の署名, 対応表の署名, 読んで対応を当てたもの)
LABEL_ALIAS = {"season": ("season", "調査回", "期"), "station": ("station", "地点", "site"), "file": ("file", "filename", "name", "ファイル", "ファイル名"),
               "species": ("species", "種", "種名", "label"), "datetime": ("datetime", "date_time", "日時", "timestamp")}


# ---------- 種名の対応表（この横断プロジェクトで、参照先に出てくる種名の表記をどの種と見なすか） ----------
def label_map(project) -> dict[str, str]:
    """project.json の label_map: 表記（小文字）→ 種コード。"" は「使わない」（その表記は判定なしとして扱う）。
    表記は、種名の CSV の値（Kiji、B_Hiyodori など）か、調査回プロジェクトの判定の種コード（kiji など）"""
    lm = project.data.get("label_map") or {}
    return {str(k).strip().lower(): (str(v).strip().lower() if v else "") for k, v in lm.items() if str(k).strip()}


def species_sig(sl) -> str:
    """種リストのうち、表記の対応に効く部分（コード・名前・CSV 表記）の署名。「使う」の付け外しでは変わらない"""
    import hashlib
    rows = sorted((r["code"], r.get("name_ja") or "", r.get("name_en") or "", r.get("name_sci") or "", r.get("csv_label") or "") for r in sl.rows)
    return hashlib.md5(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def map_sig(project) -> str:
    """対応表 + 種リストの署名（これが変わると、種名の CSV の読み直しはせずに対応だけ当て直す。カタログは作り直しが要る）"""
    return json.dumps([sorted(label_map(project).items()), species_sig(project.species)], ensure_ascii=False)


def auto_code(project, label: str) -> str | None:
    """対応表を使わない自動の当たり: 種リストの CSV 表記・英名・和名・学名・コードとの完全一致 → 略称表 → 前方・部分一致"""
    sl = project.species
    return sl.code_for_label(label) or sl.guess_code(label)


def resolve_label(project, label: str, lm: dict | None = None) -> str | None:
    """種名の表記 → 種コード。対応表にあればそれ（"" なら None = 使わない）、なければ自動の当たり"""
    s = (label or "").strip()
    if not s:
        return None
    lm = label_map(project) if lm is None else lm
    k = s.lower()
    if k in lm:
        return lm[k] or None
    return auto_code(project, s)


def map_code(lm: dict, code: str | None) -> str | None:
    """調査回プロジェクトの判定の種コードに対応表を当てる（表記として登録されていればそれに。"" なら None）"""
    if not code:
        return None
    k = code.strip().lower()
    if k in lm:
        return lm[k] or None
    return code


def map_codes(lm: dict, codes) -> list[str]:
    out: list[str] = []
    for c in codes:
        m = map_code(lm, c)
        if m and m not in out:
            out.append(m)
    return out


def map_state(lm: dict, state: dict) -> dict:
    """item_state() の判定の種に対応表を当てる（同じ種に重なれば先のものだけ残す）。何も変わらなければ同じ dict を返す"""
    if not lm or not state:
        return state
    out, seen, changed = [], set(), False
    for j in state.get("judgements") or []:
        m = map_code(lm, j["species"])
        if m != j["species"]:
            changed = True
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(dict(j, species=m) if m != j["species"] else j)
    if not changed:
        return state
    st = dict(state, judgements=out)
    if not out and st.get("status") == "auto":
        st["status"] = "new"
    return st


def set_label_map(project, mapping: dict) -> dict:
    """対応表を保存する（表記 → 種コード。"" は使わない）。選んだ種が「使わない」なら「使う」にする。読んだ種名の CSV は読み直さず、対応だけ当て直す"""
    from .species import save_csv
    sl = project.species
    by = sl.by_code()
    lm: dict[str, str] = {}
    for k, v in (mapping or {}).items():
        k = str(k).strip().lower()
        if not k:
            continue
        v = str(v or "").strip().lower()
        if v and v not in by:
            raise ValueError(f"種リストにない種です: {v}")
        lm[k] = v
    project.data["label_map"] = lm
    project.data["label_map_at"] = now_iso()
    changed = False
    for v in set(lm.values()):
        r = by.get(v)
        if r and not r.get("fixed") and not r.get("enabled"):
            r["enabled"] = 1
            changed = True
    if changed:
        save_csv(sl.path, sl.rows)
    project.save()
    msig = map_sig(project)
    for name, (sig, _old, out) in list(_LABELS.items()):      # 読んであるものには、読み直さずに対応だけ当て直す（120 万行で 1 秒）
        _LABELS[name] = (sig, msig, _apply_map(project, out, reapply=True))
    return dict(ok=True, n=len(lm))


def label_candidates(project) -> dict:
    """対応表の画面の元: 参照先に出てくる種名の表記と、本数・出どころ・自動の当たり・いまの対応。
    表記 = 種名の CSV の値（kind = csv）と、調査回プロジェクトの人の判定の種コード（kind = code。親フォルダ参照の中の調査回プロジェクトはカタログを作ったときに控えたもの）。
    返り値 dict(rows=[dict(label, kind, n, sources={name: n}, auto, name_ja, name_en, mapped, current)], pending=[CSV をまだ読んでいない参照先], map, n_unresolved)"""
    lm = label_map(project)
    known = project.species.by_code()
    rows_by: dict[str, dict] = {}
    pending: list[str] = []

    def add(label: str, kind: str, n: int, source: str, names: tuple | None = None):
        k = label.strip().lower()
        d = rows_by.get(k)
        if d is None:
            d = rows_by[k] = dict(label=label, kind=kind, n=0, sources={}, auto=(label if (kind == "code" and label in known) else auto_code(project, label)), name_ja="", name_en="")
        d["n"] += n
        d["sources"][source] = d["sources"].get(source, 0) + n
        if names and not d["name_ja"] and not d["name_en"]:
            d["name_ja"], d["name_en"] = names
        if kind == "code" and d["kind"] == "csv":
            d["kind"] = "both"
    for s in project.sources():
        name = s["name"]
        if s.get("kind") == "tree":
            if not labels_ready(project, s):
                if label_files(s):
                    pending.append(name)
                continue
            lab = _LABELS[name][2]
            for label, n in lab["labels"].items():
                add(label, "csv", n, name)
        else:
            try:
                src = open_source(s["dir"])
            except Exception:
                continue
            try:
                sb = src.species.by_code()
                for r in src.db.q("SELECT j.species, COUNT(DISTINCT j.item_id) FROM judgements j JOIN items i ON i.id=j.item_id "
                                  "WHERE i.missing=0 AND j.species != 'area' AND (i.status='verified' OR j.origin IN ('csv','manual')) GROUP BY j.species"):
                    sr = sb.get(r[0]) or {}
                    add(r[0], "code", int(r[1]), name, (sr.get("name_ja") or "", sr.get("name_en") or ""))
            finally:
                src.close()
    for code, info in ((project.data.get("catalog") or {}).get("code_names") or {}).items():
        if code.lower() in rows_by:
            continue
        add(code, "code", int(info.get("n") or 0), info.get("source") or "", (info.get("ja") or "", info.get("en") or ""))
    out = []
    for k, d in rows_by.items():
        d["mapped"] = lm[k] if k in lm else None          # None = 対応表にない（自動）、"" = 使わない
        d["current"] = (lm[k] or None) if k in lm else d["auto"]
        out.append(d)
    out.sort(key=lambda d: (d["current"] is not None, -d["n"], d["label"].lower()))
    return dict(rows=out, pending=pending, map=lm, n_unresolved=sum(1 for d in out if d["current"] is None), n_labels=len(out))


def catalog_status(project) -> dict:
    """カタログの様子と、作り直しが要るか（参照先が増減した・対応表か種リストが変わった）"""
    cat = project.data.get("catalog") or {}
    if not cat:
        return dict(built=False, stale=False, reasons=[], n=0, built_at=None)
    reasons = []
    if sorted(cat.get("sources") or []) != sorted(s["name"] for s in project.sources()):
        reasons.append("sources")
    if project.db.scalar("SELECT 1 FROM catalog WHERE species IS NOT NULL AND labels IS NULL LIMIT 1"):
        reasons.append("old")                    # v61 以前に作ったカタログ（表記の列がない）。作り直すと以後は当て直しだけで済む
    if (cat.get("map_sig") or "") != map_sig(project):
        reasons.append("labels")
    return dict(built=True, stale=bool(reasons), reasons=reasons, n=cat.get("n") or 0, built_at=cat.get("built_at"), by_source=cat.get("by_source") or {})


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
    for se in source_seasons(s):
        try:
            for e in sorted(os.scandir(os.path.join(root, se)), key=lambda e: e.name):
                if e.is_file() and e.name.lower().endswith(".csv"):
                    out.append((norm(e.path), se))
        except OSError:
            pass
    return out


LABEL_CONTAINS = {"species": ("species", "種"), "file": ("file", "ファイル"), "season": ("season", "調査回"), "station": ("station", "地点"), "datetime": ("datetime", "日時", "timestamp")}


def label_columns(header: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """種名 CSV の見出しから列を決める → (役割 → 見出し, 役割 → 列番号)。大文字小文字は問わない。
    1. 別名との完全一致（LABEL_ALIAS: Species / 種 / 種名 / label、File / FileName / Name / ファイル名 …）
    2. 見つからない役割は、別名を含む見出し（Species_name、種名1、Video_File など。まだ使っていない列から）"""
    head = {(h or "").strip().lower(): i for i, h in enumerate(header)}
    cols: dict[str, str] = {}
    idx: dict[str, int] = {}
    for k, names in LABEL_ALIAS.items():
        for n in names:
            if n in head:
                cols[k] = header[head[n]]
                idx[k] = head[n]
                break
    for k, parts in LABEL_CONTAINS.items():
        if k in idx:
            continue
        for h, i in head.items():
            if i in idx.values() or not h:
                continue
            if any(pt in h for pt in parts):
                cols[k] = header[i]
                idx[k] = i
                break
    return cols, idx


class LabelRow:
    """種名 CSV の 1 行（動画 1 本）。dict のように r["season"] で引ける。数十万行あるので __slots__ で小さく"""
    __slots__ = ("season", "station", "file", "species", "dt", "raw")

    def __init__(self, season: str, station: str, file: str, species: tuple = (), dt: str = ""):
        self.season, self.station, self.file, self.species, self.dt = season, station, file, species, dt
        self.raw = species                      # 対応表を当てる前の表記の tuple（_apply_map が species を種コードにしたあとも残す）

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
        self.raw = self.species


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


def _parse_label_file(project, path: str, season_default: str, rows: dict, labels: Counter, progress=None, stop=None, intern: dict | None = None) -> dict:
    """1 つの種名 CSV を rows に足す。返り値 dict(path, n, cols, error)。
    行は LabelRow(season, station, file, species=tuple(表記...), dt)。種は CSV の表記のまま（種コードへの対応は _apply_map で当てる。対応表を変えても読み直さなくてよい）。
    labels に表記ごとの本数を数える。調査回・地点・種の組は同じ文字列オブジェクトを使い回す（数十万行でもメモリを抑える）"""
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
    cols, idx = label_columns(header)
    if "file" not in idx or "species" not in idx:
        return dict(path=path, n=0, cols=cols, error=f"File と Species の列がありません（見出し: {', '.join(h for h in header if h)[:120]}）")
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
        label = intern.setdefault(label, label)
        k = (intern.setdefault(se.lower(), se.lower()), intern.setdefault(st.lower(), st.lower()), fn.lower())
        d = rows.get(k)
        if d is None:
            d = rows[k] = LabelRow(se, st, fn)
            n += 1
            if i_dt is not None and i_dt < len(r) and r[i_dt]:
                d.dt = media.parse_dt_any(r[i_dt] or "", z) or ""
        if label:
            labels[label] += 1
            if label not in d["species"]:
                tup = d["species"] + (label,)
                d["species"] = intern.setdefault(tup, tup)
    if progress:
        progress(total, total, f"{label_fn}: {k_row:,} 行")
    return dict(path=path, n=n, cols=cols, error=None)


def _apply_map(project, raw: dict, reapply: bool = False) -> dict:
    """生の表記で読んだもの（rows の species は表記の tuple）に、対応表と自動の当たりを当てて species を種コードの tuple にする（rows はその場で書き換える）。
    表記の組ごとに一度だけ対応を決め、行ごとの仕事は辞書引きだけにする（120 万行で 2〜3 秒）。
    返り値 dict(rows, labels={表記: 本数}, code_of={表記: コード or None}, unmapped=[(表記, n)], species=[(code, n)], files, cols,
                by_season={調査回: [LabelRow]}, season_stats={調査回: dict(n, n_human, species=Counter)})"""
    lm = label_map(project)
    code_of = {lab: resolve_label(project, lab, lm) for lab in raw["labels"]}
    tup_of: dict[tuple, tuple] = {}                  # 表記の組 → 種コードの組
    unm_of: dict[tuple, tuple] = {}                  # 表記の組 → 対応しない表記
    by_season: dict[str, list] = defaultdict(list)
    pair_counts: Counter = Counter()                 # (調査回, 種コードの組) → 本数
    for d in raw["rows"].values():
        if reapply:                                  # 一度当てたものに当て直す（対応表を変えたとき。raw に表記の tuple が残っている）
            rw = d.raw
        else:
            rw = d.species                           # 読んだ直後（parse / pickle）は species に表記の tuple が入っている
            d.raw = rw
        tup = tup_of.get(rw)
        if tup is None:
            codes: list[str] = []
            unm: list[str] = []
            for lab in rw:
                c = code_of.get(lab)
                if c:
                    if c not in codes:
                        codes.append(c)
                else:
                    unm.append(lab)
            if "empty" in codes and len(codes) > 1:
                codes = [c for c in codes if c != "empty"]
            tup = tuple(sorted(codes))
            tup = tup_of.setdefault(rw, tup)
            unm_of[rw] = tuple(unm)
        d.species = tup
        by_season[d.season].append(d)
        pair_counts[(d.season, tup)] += 1
    unmapped: Counter = Counter()
    sp_counts: Counter = Counter()
    season_stats: dict[str, dict] = {}
    for (se, tup), n in pair_counts.items():
        st = season_stats.get(se)
        if st is None:
            st = season_stats[se] = dict(n=0, n_human=0, species=Counter())
        st["n"] += n
        if tup:
            st["n_human"] += n
            for c in tup:
                st["species"][c] += n
                sp_counts[c] += n
    raw_counts: Counter = Counter()
    for d in raw["rows"].values():
        if unm_of.get(d.raw):
            raw_counts[d.raw] += 1
    for rw, n in raw_counts.items():
        for lab in unm_of[rw]:
            unmapped[lab] += n
    return dict(rows=raw["rows"], labels=raw["labels"], code_of=code_of, unmapped=unmapped.most_common(), species=sp_counts.most_common(), files=raw["files"], cols=raw["cols"],
                by_season=dict(by_season), season_stats=season_stats)


def _labels_sig(project, files: list[tuple[str, str]]) -> tuple:
    """読んだ種名 CSV の署名（ファイルのパス・更新時刻・サイズ、日時の読み方）。種リストと対応表は含めない（生の表記で読むので）"""
    out = [("fmt", 2, bool(project.setting("z_is_utc")))]
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
    """種名 CSV を読んだものが手元（メモリか cache/labels）にあるか。あれば対応表を当ててメモリに載せる（1 秒ほど）。CSV は読まない"""
    import pickle
    files = label_files(s)
    sig = _labels_sig(project, files)
    msig = map_sig(project)
    hit = _LABELS.get(s["name"])
    if hit and hit[0] == sig and hit[1] == msig:
        return True
    cp = _labels_cache_path(project, s)
    if os.path.isfile(cp):
        try:
            with open(cp, "rb") as fh:
                csig, raw = pickle.load(fh)
            if csig == sig and isinstance(raw, dict) and "labels" in raw:
                _LABELS[s["name"]] = (sig, msig, _apply_map(project, raw))
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
    """親フォルダ参照の既存の種名 CSV（指定した 1 つ、または自動で見つけたもの全部）を読み、対応表を当てる。ファイルが変わっていなければ覚えたもの（メモリ → cache/labels の pickle。生の表記）を使う。
    返り値 dict(rows={(season, station, file) の小文字: LabelRow(season, station, file, species=tuple(code), dt)}, labels={表記: n}, code_of, unmapped=[(label, n)], species=[(code, n)], files=[...], cols)"""
    import pickle
    if labels_ready(project, s):
        return _LABELS[s["name"]][2]
    files = label_files(s)
    sig = _labels_sig(project, files)
    rows: dict[tuple, dict] = {}
    labels: Counter = Counter()
    intern: dict = {}
    used = []
    for path, se in files:
        if stop and stop():
            break
        used.append(_parse_label_file(project, path, se, rows, labels, progress, stop, intern))
    raw = dict(rows=rows, labels=dict(labels), files=used, cols=(used[0]["cols"] if used else {}))
    if stop and stop():
        return _apply_map(project, raw)              # 途中でやめたものは覚えない
    try:
        cp = _labels_cache_path(project, s)
        with open(cp + ".tmp", "wb") as fh:
            pickle.dump((sig, raw), fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(cp + ".tmp", cp)
    except Exception as e:
        log.warning("種名 CSV のキャッシュを書けません: %s", e)
    out = _apply_map(project, raw)
    _LABELS[s["name"]] = (sig, map_sig(project), out)
    return out


def _tree_seasons(root: str) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(root) if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in ("trapnote", "staytime"))
    except OSError:
        return []


def source_seasons(s: dict) -> list[str]:
    """親フォルダ参照の調査回（s["seasons"] があればその調査回だけ = 調査回フォルダを 1 つだけ登録したとき）"""
    root = norm(s.get("root") or "")
    all_se = _tree_seasons(root) if root else []
    only = s.get("seasons")
    if only:
        want = {str(x) for x in only}
        return [se for se in all_se if se in want]
    return all_se


def classify_folder(path: str) -> str:
    """フォルダの置き方を見分ける: station（動画が直下にある）/ season（直下のフォルダに動画がある = 調査回のフォルダ）/ parent（直下のフォルダの下にフォルダ = 親フォルダ）/ unknown"""
    from .paths import kind_of
    try:
        entries = [e for e in os.scandir(path) if not e.name.startswith(".") and e.name.lower() not in ("trapnote", "staytime")]
    except OSError:
        return "unknown"
    if any(e.is_file() and kind_of(e.name) for e in entries):
        return "station"
    has_sub = False
    for e in [x for x in entries if x.is_dir()][:20]:
        try:
            subs = [x for x in os.scandir(e.path) if not x.name.startswith(".") and x.name.lower() not in ("trapnote", "staytime")]
        except OSError:
            continue
        if any(x.is_file() and kind_of(x.name) for x in subs):
            return "season"
        if any(x.is_dir() for x in subs):
            has_sub = True
    return "parent" if has_sub else "unknown"


def _tree_stations(root: str, season: str) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(os.path.join(root, season)) if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in ("trapnote", "staytime"))
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
    seasons = source_seasons(s) if available else []
    n_in = int(project.db.scalar("SELECT COUNT(*) FROM items WHERE rel_path LIKE ?", (s["name"] + "/%",)) or 0)
    proj_seasons = [se for se in seasons if tree_season_project(root, se)] if available else []
    d = dict(name=s["name"], kind="tree", root=root, dir=None, project_name=s["name"], ok=True, available=available, seasons=seasons, project_seasons=proj_seasons, stations=[],
             n_items=None, n_verified=None, n_human=None, n_det=None, species=[], n_imported=n_in, labels_csv=s.get("labels_csv") or "", labels=None,
             only_seasons=list(s.get("seasons") or []))
    if available and not wait and not labels_ready(project, s):
        fi = labels_files_info(s)
        d["labels"] = dict(pending=True, files=[dict(path=f["path"], n=None, error=None) for f in fi], size=sum(f["size"] for f in fi), auto=not s.get("labels_csv")) if fi else dict(files=[], auto=not s.get("labels_csv"))
        return d
    if available:
        try:
            lab = _labels(project, s)
            here = set(seasons) - set(proj_seasons)                                     # プロジェクトのある調査回は CSV ではなくプロジェクトの判定を使う
            sp: Counter = Counter()
            n_here = n_human = 0
            for se, st in lab["season_stats"].items():                                   # この親フォルダにある調査回の分だけ数える（調査回ごとの集計は読んだときに作ってある）
                if se in here:
                    n_here += st["n"]
                    n_human += st["n_human"]
                    sp.update(st["species"])
            d["labels"] = dict(n=len(lab["rows"]), n_here=n_here, unmapped=lab["unmapped"][:20], n_unmapped=sum(n for _, n in lab["unmapped"]), n_unmapped_labels=len(lab["unmapped"]), n_labels=len(lab["labels"]), cols=lab["cols"],
                               files=[dict(path=f["path"], n=f["n"], error=f["error"]) for f in lab["files"]], auto=not s.get("labels_csv"),
                               seasons=sorted(se for se in lab["season_stats"] if se))
            d["species"] = [dict(code=c, n=n) for c, n in sp.most_common()]
            d["n_human"] = n_human
            d["n_items"] = n_here
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
    lm = label_map(project)
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
                for r in rows.values():
                    r["species"] = set(map_codes(lm, r["species"]))
                cands = [r for r in rows.values() if r["station"] not in exclude_st and r["species"]]
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
    lm = label_map(project)
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
                        st0 = map_state(lm, item_state(src.db, sid))      # 判定の種コードに対応表を当てる（書き戻し・再取得の比較もこの形で）
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
                                  (st0.get("status") or srow.get("status") or "new", srow.get("verified_by"), srow.get("verified_at"), srow.get("agree"), srow.get("recheck") or 0, now, nid))
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
CAT_INDEXES = {"idx_cat_season": "season", "idx_cat_station": "station", "idx_cat_species": "species", "idx_cat_status": "status", "idx_cat_item": "item_id", "idx_cat_labels": "labels"}
CAT_INSERT = "INSERT OR IGNORE INTO catalog(source, src_id, rel, season, station, name, datetime, status, species, ai, agree, det_state, duration, size, labels) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"


def build_catalog(project, progress=None, stop=None) -> dict:
    """参照先の全動画の目録を作り直す（メディアは持たない）。プロジェクト参照は items と判定から、親フォルダ参照は種名の CSV から。
    速さのため: 調査回ごとに流し込む（全部をメモリに持たない）、索引は落として最後に作り直す（索引を保ちながらの挿入は HDD では桁違いに遅い）、
    1 回のトランザクションで 5 万行、キャッシュを大きくする"""
    db = project.db
    by_src: dict[str, int] = {}
    code_names: dict[str, dict] = {}
    lm = label_map(project)
    msig = map_sig(project)
    conn = db.conn()
    with db.tx() as c:
        c.execute("DELETE FROM catalog")
        for ix in CAT_INDEXES:
            c.execute(f"DROP INDEX IF EXISTS {ix}")
    conn.execute("PRAGMA cache_size=-262144")
    buf: list[tuple] = []
    n_all = 0

    def flush(force: bool = False):
        nonlocal buf, n_all
        if not buf or (len(buf) < 50000 and not force):
            return
        with db.tx() as c:
            c.executemany(CAT_INSERT, buf)
        n_all += len(buf)
        buf = []
    try:
        for s in project.sources():
            if stop and stop():
                break
            name = s["name"]
            n_src = 0
            if progress:
                progress(0, 0, name)
            if s.get("kind") == "tree":
                for se, rows in _cat_rows_by_season(project, s, lambda a, b, msg: progress and progress(a, b, msg), stop, code_names):
                    if stop and stop():
                        break
                    buf.extend(rows)
                    n_src += len(rows)
                    flush()
                    if progress:
                        progress(n_all + len(buf), 0, f"{name}/{se}: {n_all + len(buf):,} 本")
            else:
                try:
                    src = open_source(s["dir"])
                except Exception as e:
                    log.warning("参照先 %s を開けません: %s", name, e)
                    continue
                try:
                    hum, hum_raw = _human_codes(project, src, lm, code_names, name)
                    for r in src.db.q("SELECT id, rel_path, season, station, name, COALESCE(datetime_corrected, datetime) AS dt, status, auto_json, agree, det_state, duration, size FROM items WHERE missing=0"):
                        ai = None
                        if r["auto_json"]:
                            try:
                                ai = ";".join(x["species"] for x in (json.loads(r["auto_json"]).get("species") or [])) or None
                            except Exception:
                                ai = None
                        buf.append((name, r["id"], r["rel_path"], r["season"] or name, r["station"], r["name"], r["dt"], r["status"],
                                    ";".join(sorted(set(hum.get(r["id"], [])))) or None, ai, r["agree"], r["det_state"], r["duration"], r["size"],
                                    ";".join(sorted(set(hum_raw.get(r["id"], [])))) or None))
                        n_src += 1
                        if len(buf) >= 50000:
                            flush()
                            if progress:
                                progress(n_all, 0, f"{name}: {n_all:,} 本")
                finally:
                    src.close()
            flush(True)
            by_src[name] = n_src
    finally:
        flush(True)
        if progress:
            progress(n_all, n_all, "索引を作っています")
        with db.tx() as c:
            for ix, cols in CAT_INDEXES.items():
                c.execute(f"CREATE INDEX IF NOT EXISTS {ix} ON catalog({cols})")
        conn.execute("PRAGMA cache_size=-65536")
    _catalog_mark_imported(project)
    n = int(db.scalar("SELECT COUNT(*) FROM catalog") or 0)
    done = not (stop and stop())
    project.update_data(catalog=dict(built_at=now_iso(), n=n, by_source=by_src, sources=[s["name"] for s in project.sources()] if done else [], map_sig=msig if done else "",
                                     code_names=code_names))
    return dict(n=n, by_source=by_src, code_names=code_names)


def remap_catalog(project, progress=None, stop=None) -> dict:
    """対応表（か種リスト）を変えたあと、カタログを作り直さずに species だけ当て直す（labels 列 = 元の表記から。表記の組ごとに UPDATE するので数十万行でも数秒）。
    参照先が変わったときは build_catalog が要る。返り値 dict(n_changed, n_combos)"""
    db = project.db
    lm = label_map(project)
    combos = [(r[0], int(r[1])) for r in db.q("SELECT DISTINCT labels, (src_id IS NOT NULL) FROM catalog WHERE labels IS NOT NULL")]
    n_changed = 0
    with db.tx() as c:                       # 書き換える列の索引は落としておき、最後に作り直す（HDD では索引を保ちながらの更新が遅い）
        c.execute("DROP INDEX IF EXISTS idx_cat_species")
        c.execute("DROP INDEX IF EXISTS idx_cat_status")
    for i, (raw, proj) in enumerate(combos):
        if stop and stop():
            break
        parts = [x for x in raw.split(";") if x]
        if proj:
            codes = sorted(map_codes(lm, parts))
        else:
            codes = []
            for lab in parts:
                c = resolve_label(project, lab, lm)
                if c and c not in codes:
                    codes.append(c)
            if "empty" in codes and len(codes) > 1:
                codes = [c for c in codes if c != "empty"]
            codes.sort()
        sp = ";".join(codes) or None
        if progress:
            progress(i, len(combos), raw[:60])
        with db.tx() as c:
            if proj:
                cur = c.execute("UPDATE catalog SET species=? WHERE labels=? AND src_id IS NOT NULL AND species IS NOT ?", (sp, raw, sp))
            else:
                cur = c.execute("UPDATE catalog SET species=?, status=? WHERE labels=? AND src_id IS NULL AND species IS NOT ?", (sp, "auto" if sp else "new", raw, sp))
            n_changed += cur.rowcount
    with db.tx() as c:
        c.execute("CREATE INDEX IF NOT EXISTS idx_cat_species ON catalog(species)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cat_status ON catalog(status)")
    if not (stop and stop()):
        cat = dict(project.data.get("catalog") or {})
        cat["map_sig"] = map_sig(project)
        project.update_data(catalog=cat)
    return dict(n_changed=n_changed, n_combos=len(combos))


def import_label_map(project, other_path: str, overwrite: bool = False) -> dict:
    """別のプロジェクト（横断でも調査回でも。project.json に label_map があるもの）から種名の対応表を持ってくる。
    overwrite=False ならこちらにない表記だけ足す、True なら相手の対応で上書き。対応先の種がこちらの種リストにないときは相手の種リストから写す（同じ英名・学名・和名があればそれ）。
    返り値 dict(n_added, n_updated, n_kept, species_added, map)"""
    from .project import PROJECT_JSON, read_json
    from .species import SpeciesList
    other_path = norm(other_path)
    pdir, _ = _project_at(other_path)
    if not pdir:
        raise ValueError(f"プロジェクトがありません: {other_path}")
    if key(pdir) == key(project.dir):
        raise ValueError("自分自身からは持ってこられません")
    odata = read_json(os.path.join(pdir, PROJECT_JSON), None) or {}
    omap = {str(k).strip().lower(): str(v or "").strip().lower() for k, v in (odata.get("label_map") or {}).items() if str(k).strip()}
    if not omap:
        raise ValueError("そのプロジェクトには対応表がありません")
    osp = SpeciesList(os.path.join(pdir, "species.csv")) if os.path.isfile(os.path.join(pdir, "species.csv")) else None
    mine = dict(label_map(project))
    by = project.species.by_code()
    added = updated = kept = 0
    species_added: list[str] = []
    code_of: dict[str, str] = {}                        # 相手のコード → こちらのコード（種を写したときに変わることがある）
    for k, v in omap.items():
        if k in mine and not overwrite:
            kept += 1
            continue
        code = v
        if v and v not in by:
            if v in code_of:
                code = code_of[v]
            else:
                row = (osp.by_code().get(v) if osp else None)
                if row is None:
                    continue                                # 相手の種リストにもない対応は飛ばす
                r = project.species.add(dict(row))
                code = r["code"]
                code_of[v] = code
                by = project.species.by_code()
                species_added.append(code)
        if k in mine:
            if mine[k] != code:
                updated += 1
        else:
            added += 1
        mine[k] = code
    r = set_label_map(project, mine)
    return dict(n_added=added, n_updated=updated, n_kept=kept, species_added=species_added, n=r.get("n", len(mine)), from_dir=pdir, species=r.get("species"))


def _other_cross(project, other_path: str):
    """別の横断プロジェクトのフォルダ → (プロジェクトのフォルダ, project.json の中身)"""
    from .project import PROJECT_JSON, read_json
    other_path = norm(other_path)
    pdir, pkind = _project_at(other_path)
    if not pdir or pkind != "cross":
        raise ValueError(f"横断プロジェクトではありません: {other_path}")
    if key(pdir) == key(project.dir):
        raise ValueError("自分自身からは持ってこられません")
    odata = read_json(os.path.join(pdir, PROJECT_JSON), None) or {}
    if not odata.get("catalog"):
        raise ValueError("そのプロジェクトにはまだカタログがありません")
    return pdir, odata


def prepare_catalog_import(project, other_path: str) -> dict:
    """カタログを持ってくる前の支度（開いているプロジェクトで、その場で）: 参照先（名前が重ならないもの）と対応表（こちらにない表記）を足し、
    こちらにカタログも対応表もなければ種リスト・追加項目も相手のものにする。参照先の名前が同じで場所が違うものがあれば止める（カタログの行は参照先の名前で結び付く）"""
    pdir, odata = _other_cross(project, other_path)
    mine = {s["name"]: s for s in project.sources()}
    added = []
    for s in odata.get("sources") or []:
        m = mine.get(s["name"])
        if m is None:
            project.data.setdefault("sources", []).append(dict(s))
            added.append(s["name"])
        else:
            same = (m.get("kind") or "project") == (s.get("kind") or "project") and key(m.get("root") or m.get("dir") or "") == key(s.get("root") or s.get("dir") or "")
            if not same:
                raise ValueError(f"参照先の名前が重なっていて場所が違います: {s['name']}（こちら {m.get('root') or m.get('dir')} / 相手 {s.get('root') or s.get('dir')}）")
    fresh = not project.data.get("catalog") and not (project.data.get("label_map") or {})
    lm = dict(project.data.get("label_map") or {})
    for k, v in (odata.get("label_map") or {}).items():
        lm.setdefault(k, v)
    project.data["label_map"] = lm
    if fresh:
        for fn in ("species.csv", "fields.json"):
            a, b = os.path.join(pdir, fn), os.path.join(project.dir, fn)
            if os.path.isfile(a):
                shutil.copy2(a, b)
        from .fields import Fields
        from .species import SpeciesList
        project.species = SpeciesList(os.path.join(project.dir, "species.csv"))
        project.fields = Fields(os.path.join(project.dir, "fields.json"))
    project.forget_source_roots()
    project.save()
    return dict(from_dir=pdir, sources_added=added, lists_copied=fresh)


def import_catalog_from(project, other_path: str, progress=None, stop=None) -> dict:
    """別の横断プロジェクトからカタログを持ってくる（作り直さずに済ませる。prepare_catalog_import のあとに裏で）:
    catalog 表の全行（取り込み済みの印はこちらの対象で付け直す）、種名 CSV の読み込みキャッシュ（cache/labels。署名は CSV のパス・更新時刻・大きさなのでそのまま使える）、カタログの記録"""
    pdir, odata = _other_cross(project, other_path)
    ocat = odata.get("catalog") or {}
    db = project.db
    conn = db.conn()
    odb = os.path.join(pdir, "data.sqlite")
    if progress:
        progress(0, 0, "カタログの行を写しています")
    with db.tx() as c:
        c.execute("DELETE FROM catalog")
        for ix in CAT_INDEXES:
            c.execute(f"DROP INDEX IF EXISTS {ix}")
    conn.execute("ATTACH DATABASE ? AS o", (odb,))
    try:
        ocols = {r[1] for r in conn.execute("PRAGMA o.table_info(catalog)")}
        cols = [c for c in ("source", "src_id", "rel", "season", "station", "name", "datetime", "status", "species", "ai", "agree", "det_state", "duration", "size", "labels") if c in ocols]
        with db.tx() as c:
            c.execute(f"INSERT OR IGNORE INTO catalog({', '.join(cols)}) SELECT {', '.join(cols)} FROM o.catalog")
    finally:
        conn.execute("DETACH DATABASE o")
    if progress:
        progress(0, 0, "索引を作っています")
    with db.tx() as c:
        for ix, cols_ in CAT_INDEXES.items():
            c.execute(f"CREATE INDEX IF NOT EXISTS {ix} ON catalog({cols_})")
    _catalog_mark_imported(project)
    n = int(db.scalar("SELECT COUNT(*) FROM catalog") or 0)
    n_cache = 0
    src_cache = os.path.join(pdir, "cache", "labels")
    if os.path.isdir(src_cache):
        dst_cache = project.cache_dir("labels")
        os.makedirs(dst_cache, exist_ok=True)
        for fn in os.listdir(src_cache):
            if fn.endswith(".pkl"):
                try:
                    shutil.copy2(os.path.join(src_cache, fn), os.path.join(dst_cache, fn))
                    n_cache += 1
                except OSError as e:
                    log.warning("種名 CSV のキャッシュを写せません %s: %s", fn, e)
    cat = dict(ocat)
    cat.update(n=n, sources=[s["name"] for s in project.sources()], map_sig=map_sig(project), imported_from=dict(dir=pdir, name=odata.get("name") or os.path.basename(pdir), at=now_iso()))
    project.update_data(catalog=cat)
    return dict(n=n, n_cache=n_cache, from_dir=pdir, stale=catalog_status(project)["reasons"])


def _catalog_mark_imported(project) -> None:
    """取り込み済みの対象に横断側の items.id を付ける（rel_path = 名前/rel で照合）。全行を書き換えず、消えた対象の印を外し、まだ印のない対象だけ付ける"""
    db = project.db
    with db.tx() as c:
        c.execute("UPDATE catalog SET item_id=NULL WHERE item_id IS NOT NULL AND item_id NOT IN (SELECT id FROM items)")
        rows = c.execute("SELECT id, rel_path FROM items WHERE id NOT IN (SELECT item_id FROM catalog WHERE item_id IS NOT NULL)").fetchall()
        for iid, rel_path in rows:
            name, _, rel = rel_path.partition("/")
            c.execute("UPDATE catalog SET item_id=? WHERE source=? AND rel=?", (iid, name, rel))


def batch_units(project, batches) -> dict:
    """組（gold など）の動画が占める単位 = 学習用の母集団から除くもの。取り込みの記録の選び方（unit）で決める:
    地点で選んだ組 → その地点（全調査回）、調査回で選んだ組 → その調査回（全地点）、それ以外 → 動画のある 地点 × 調査回 の組。
    返り値 dict(stations=set, seasons=set, cells=set("調査回/地点"), batches=[...])"""
    names = [str(b) for b in (batches or []) if str(b)]
    out = dict(stations=set(), seasons=set(), cells=set(), batches=names)
    if not names:
        return out
    units_of: dict[str, set] = defaultdict(set)
    for rec in project.data.get("imports") or []:
        if rec.get("batch") in names and rec.get("n", 0) > 0:
            u = (rec.get("params") or {}).get("unit") or ((rec.get("params") or {}).get("params") or {}).get("unit")
            units_of[rec["batch"]].add(u or "")
    db = project.db
    for b in names:
        rows = db.q("SELECT DISTINCT COALESCE(season, ''), COALESCE(station, '') FROM items WHERE batch=?", (b,))
        us = units_of.get(b) or {""}
        if "station" in us:
            out["stations"] |= {r[1] for r in rows if r[1]}
        if "season" in us:
            out["seasons"] |= {r[0] for r in rows if r[0]}
        if us - {"station", "season"}:
            out["cells"] |= {f"{r[0]}/{r[1]}" for r in rows if r[0] or r[1]}
    return out


def catalog_where(f: dict, project=None) -> tuple[str, list]:
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
    if project is not None and f.get("exclude_batches"):             # 評価用の組（gold）の地点・調査回を母集団から除く
        bu = batch_units(project, f["exclude_batches"])
        if bu["stations"]:
            sts = sorted(bu["stations"])
            w.append(f"(station IS NULL OR station NOT IN ({','.join('?' * len(sts))}))"); p += sts
        if bu["seasons"]:
            ses = sorted(bu["seasons"])
            w.append(f"(season IS NULL OR season NOT IN ({','.join('?' * len(ses))}))"); p += ses
        if bu["cells"]:                                             # 組が多いことがあるので、変数の上限に掛からないよう文字列で埋め込む
            lits = ",".join("'" + c.replace("'", "''") + "'" for c in sorted(bu["cells"]))
            w.append(f"(COALESCE(season, '') || '/' || COALESCE(station, '')) NOT IN ({lits})")
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
    where, p = catalog_where(f, project)
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
    excluded = None
    if f.get("exclude_batches"):
        bu = batch_units(project, f["exclude_batches"])
        excluded = dict(batches=bu["batches"], stations=len(bu["stations"]), seasons=len(bu["seasons"]), cells=len(bu["cells"]))
    return dict(total=total, imported=imported, by_source=by_source, by_status=by_status, seasons=seasons, stations=stations,
                pivot={k: dict(v) for k, v in pivot.items()}, by_species=by_species, built=project.data.get("catalog"), excluded=excluded)


CAT_SORTS = {"rel": "source, rel", "datetime": "COALESCE(datetime, ''), rel", "station": "station, COALESCE(datetime, ''), rel", "species": "species, rel", "status": "status, rel", "season": "season, station, rel"}


def catalog_rows(project, f: dict, sort: str = "rel", desc: bool = False, offset: int = 0, limit: int = 200) -> dict:
    db = project.db
    where, p = catalog_where(f, project)
    order = CAT_SORTS.get(sort or "rel", CAT_SORTS["rel"])
    if desc:
        order = ", ".join(x.strip() + " DESC" for x in order.split(","))
    rows = [dict(r) for r in db.q(f"SELECT id, source, src_id, rel, season, station, name, datetime, status, species, ai, agree, det_state, duration, item_id FROM catalog WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*p, int(limit), int(offset)))]
    total = int(db.scalar(f"SELECT COUNT(*) FROM catalog WHERE {where}", p) or 0)
    return dict(rows=rows, total=total, offset=int(offset))


SAMPLE_UNITS = ("all", "video", "species", "station", "season", "station_season")
_UNIT_KEY = {"station": "COALESCE(station, '')", "season": "COALESCE(season, '')", "station_season": "COALESCE(season, '') || '/' || COALESCE(station, '')"}


def _species_counts_sql(db, where: str, p: list) -> dict[str, int]:
    c: Counter = Counter()
    for r in db.q(f"SELECT species, COUNT(*) FROM catalog WHERE {where} GROUP BY species", p):
        for code in (r[0] or "(none)").split(";"):
            c[code] += r[1]
    return dict(c)


def _dt_ts(s: str) -> float | None:
    """"YYYY/MM/DD HH:MM:SS"（- 区切り、T 区切りも）→ 秒。数十万行でも速いように文字の切り出しだけで"""
    if not s or len(s) < 16:
        return None
    try:
        y, mo, d, h, mi = int(s[0:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16])
        sec = int(s[17:19]) if len(s) >= 19 and s[16] == ":" else 0
        return datetime(y, mo, d).timestamp() + h * 3600 + mi * 60 + sec
    except (ValueError, TypeError):
        return _parse_dt(s)


def _thin_rows(rows: list[tuple], gap_min: float) -> list[tuple]:
    """rows = (id, source, season, station, datetime, species)。同じ地点で gap_min 分以内に続く動画は 1 本だけ（時刻のないものはそのまま）"""
    if gap_min <= 0:
        return rows
    out, last = [], {}
    for r in sorted(rows, key=lambda x: (x[3] or "", x[4] or "", x[0])):
        ts = _dt_ts(r[4])
        if ts is None:
            out.append(r)
            continue
        prev = last.get(r[3])
        if prev is not None and ts - prev < gap_min * 60:
            continue
        last[r[3]] = ts
        out.append(r)
    return out


def catalog_sample(project, f: dict, params: dict) -> dict:
    """カタログの絞り込み（未取り込みのもの）から、乱数で選ぶ。params["unit"]:
      all             絞り込みの全部
      video           動画を n 本（同じ地点で gap_min 分以内の連続動画は 1 本に間引いてから）
      species         種ごとに n 本（既存の種名から。地点 × 調査回の上限 cap、間引き gap_min。species=[code...] で種を限る）
      station         地点を n か所選び、その地点の動画を全部（gold の地点など。参照先・調査回をまたいで同じ地点名は 1 か所）
      season          調査回を n 回選び、その調査回の動画を全部
      station_season  地点 × 調査回の組を n 組選び、その動画を全部
      （station / season / station_season は gap_min で連続動画を間引いてから、per_unit = 単位ごとの上限、total = 合計の上限で、選んだ単位の中から乱数で間引ける。gold の「地点 20・合計 3,000 本」など）
      （species は max_avail = 既存の本数がこれ以下の種だけ、で稀な種の補いができる）
    乱数の種 seed で同じ結果が再現できる。返り値 dict(ids, n, counts={code: n}, available={code: n}（種ごと）, units=[(名前, 選んだ本数)], units_pool=[(名前, 母数)], n_pool, n_pool_thin, n_units_available)"""
    db = project.db
    where, p = catalog_where(dict(f, imported="0"), project)
    unit = params.get("unit") or ("species" if params.get("n_per_species") else "all")
    if unit not in SAMPLE_UNITS:
        raise ValueError(f"選び方が分かりません: {unit}")
    n_want = int(params.get("n") or params.get("n_per_species") or 0)
    if unit == "all" or (n_want <= 0 and unit != "species"):
        ids = [r[0] for r in db.q(f"SELECT id FROM catalog WHERE {where}", p)]
        return dict(ids=ids, counts=_species_counts_sql(db, where, p), available={}, n=len(ids), units=[], n_units_available=0, unit="all")
    cap = int(params.get("cap") or 0)
    gap_min = float(params.get("gap_min") or 0)
    seed = int(params.get("seed") or 1)
    rng = random.Random(seed)
    if unit in _UNIT_KEY:                                   # 地点・調査回・その組: 数は SQL で数え、選んだ単位の行だけ読む
        kx = _UNIT_KEY[unit]
        per_unit = int(params.get("per_unit") or 0)         # 単位ごとの上限（0 で全部）
        total = int(params.get("total") or 0)               # 合計の上限（0 で上限なし。超えれば選んだ単位の動画から乱数で間引く）
        groups = {r[0]: r[1] for r in db.q(f"SELECT {kx} AS k, COUNT(*) FROM catalog WHERE {where} GROUP BY k", p)}
        keys = sorted(k for k in groups if k and k != "/")
        chosen = sorted(rng.sample(keys, min(n_want, len(keys))))
        by_unit: dict[str, list[tuple]] = defaultdict(list)          # 行は (id, source, season, station, datetime, species)（_thin_rows と同じ並び）
        for i0 in range(0, len(chosen), 500):
            part = chosen[i0:i0 + 500]
            qs = ",".join("?" * len(part))
            for r in db.q(f"SELECT id, {kx} AS k, species, source, season, station, datetime FROM catalog WHERE {where} AND {kx} IN ({qs}) ORDER BY id", [*p, *part]):
                by_unit[r[1]].append((r[0], r[3], r[4], r[5], r[6], r[2]))
        n_pool = sum(len(v) for v in by_unit.values())
        if gap_min > 0:                                              # 同じ地点で gap_min 分以内に続く動画は 1 本に（同じ個体の連続を数えないため）
            by_unit = defaultdict(list, {k: _thin_rows(v, gap_min) for k, v in by_unit.items()})
        n_pool_thin = sum(len(v) for v in by_unit.values())
        picked: list[tuple] = []
        for k in chosen:
            rows_k = by_unit.get(k, [])
            if per_unit and len(rows_k) > per_unit:
                rows_k = sorted(rng.sample(rows_k, per_unit))
            picked.extend(rows_k)
        if total and len(picked) > total:
            picked = sorted(rng.sample(picked, total))
        sel_by_unit: Counter = Counter()
        counts: Counter = Counter()
        pid = {r[0] for r in picked}
        for k, rows_k in by_unit.items():
            sel_by_unit[k] = sum(1 for r in rows_k if r[0] in pid)
        for r in picked:
            for code in (r[5] or "(none)").split(";"):
                counts[code] += 1
        return dict(ids=[r[0] for r in picked], counts=dict(counts), available={}, n=len(picked), units=[(k, sel_by_unit[k]) for k in chosen],
                    units_pool=[(k, groups[k]) for k in chosen], n_pool=n_pool, n_pool_thin=n_pool_thin, n_units_available=len(keys), unit=unit, gap_min=gap_min)
    rows = [tuple(r) for r in db.q(f"SELECT id, source, season, station, datetime, species FROM catalog WHERE {where}" + (" AND species IS NOT NULL" if unit == "species" else ""), p)]
    if unit == "video":
        cands = _thin_rows(rows, gap_min)
        cands.sort(key=lambda r: r[0])
        rng.shuffle(cands)
        picked = cands[:n_want]
        cnt = lambda rs: dict(Counter(code for r in rs for code in (r[5] or "(none)").split(";")))
        return dict(ids=[r[0] for r in picked], counts=cnt(picked), available=cnt(cands), n=len(picked), units=[], n_units_available=len(cands), unit=unit,
                    n_pool=len(rows), n_pool_thin=len(cands), gap_min=gap_min)
    # unit == species
    n_per = n_want
    want_sp = set(params.get("species") or [])
    max_avail = int(params.get("max_avail") or 0)         # 既存の本数がこれ以下の種だけ（稀な種の補い用。0 で全部の種）
    pool: dict[str, list[tuple]] = defaultdict(list)
    thinned = _thin_rows(rows, gap_min)
    for r in thinned:
        for code in r[5].split(";"):
            if not want_sp or code in want_sp:
                pool[code].append(r)
    if max_avail:
        pool = defaultdict(list, {c: v for c, v in pool.items() if len(v) <= max_avail})
    ids = []
    seen: set[int] = set()
    counts2: dict[str, int] = {}
    for code in sorted(pool):
        cands = list(pool[code])
        cands.sort(key=lambda r: r[0])
        rng.shuffle(cands)
        per_cell: dict[tuple, int] = defaultdict(int)
        n_sp = 0
        for r in cands:
            if n_per and n_sp >= n_per:
                break
            cell = (r[1], r[3], r[2])
            if cap and per_cell[cell] >= cap:
                continue
            if r[0] in seen:
                continue
            per_cell[cell] += 1
            seen.add(r[0]); ids.append(r[0]); n_sp += 1
        counts2[code] = n_sp
    return dict(ids=ids, counts=counts2, available={k: len(v) for k, v in pool.items()}, n=len(ids), units=[], n_units_available=len(pool), unit=unit,
                n_pool=len(rows), n_pool_thin=len(thinned), gap_min=gap_min)


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


def export_catalog(project, path: str, f: dict | None = None, long: bool = False) -> dict:
    """カタログ（参照先の全動画と判定）を 1 つの CSV に。
    long=False: 1 行 = 1 動画。Source, Season, Station, File, DateTime, Status, Species（複数は ; 区切り）, AI, Agree, Imported, RelPathInSource
    long=True : 1 行 = 1 動画 × 1 種（調査回ごとのデータを 1 つにまとめる用。PRM_N の CSV と同じ Season, Station, File, DateTime, Species が先頭。
                Season は調査回のフォルダ名。種のない動画は Species 空で 1 行。続けて Status, Source, AI, Imported, RelPathInSource）"""
    db = project.db
    where, p = catalog_where(f or {}, project)
    sl = project.species
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = n_rows = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        if long:
            w.writerow(["Season", "Station", "File", "DateTime", "Species", "Status", "Source", "AI", "Imported", "RelPathInSource"])
        else:
            w.writerow(["Source", "Season", "Station", "File", "DateTime", "Status", "Species", "AI", "Agree", "Imported", "RelPathInSource"])
        for r in db.q(f"SELECT source, season, station, name, datetime, status, species, ai, agree, item_id, rel FROM catalog WHERE {where} ORDER BY source, rel", p):
            ai = ";".join(sl.csv_label(c) for c in (r[7] or "").split(";") if c)
            if long:
                codes = [c for c in (r[6] or "").split(";") if c] or [""]
                for c in codes:
                    w.writerow([r[1] or "", r[2] or "", r[3], r[4] or "", sl.csv_label(c) if c else "", r[5] or "", r[0], ai, 1 if r[9] else 0, r[10]])
                    n_rows += 1
            else:
                w.writerow([r[0], r[1] or "", r[2] or "", r[3], r[4] or "", r[5] or "", ";".join(sl.csv_label(c) for c in (r[6] or "").split(";") if c), ai,
                            {1: "match", 2: "partial", 3: "mismatch"}.get(r[8], ""), 1 if r[9] else 0, r[10]])
                n_rows += 1
            n += 1
    return dict(path=path, n=n, n_rows=n_rows)


# ---------- 学習と評価（組 = batch を単位に。gold など評価用の組と、round1 … の学習用の組） ----------
def eval_batches(project) -> list[str]:
    """評価用の組（学習には使わず、モデルの一致率を見るためだけの組。gold など）"""
    return [str(x) for x in (project.data.get("eval_batches") or []) if str(x)]


def set_eval_batches(project, names) -> list[str]:
    have = {r[0] or "" for r in project.db.q("SELECT DISTINCT batch FROM items")}
    names = [str(x) for x in (names or []) if str(x) and str(x) in have]
    project.update_data(eval_batches=names)
    return names


def batches_info(project) -> list[dict]:
    """組ごとの様子: 本数、確認済み、AI 判定済み、AI と不一致（確認済みのうち）、要確認、評価用か、選び方（取り込みの記録から）"""
    db = project.db
    ev = set(eval_batches(project))
    units: dict[str, list] = defaultdict(list)
    for rec in project.data.get("imports") or []:
        if rec.get("n", 0) > 0 and rec.get("batch") is not None:
            prm = rec.get("params") or {}
            u = prm.get("unit") or (prm.get("params") or {}).get("unit") or prm.get("mode") or ""
            units[rec["batch"]].append(dict(unit=u, n=rec.get("n"), n_units=len(prm.get("units") or [])))
    out = []
    for r in db.q("SELECT COALESCE(batch, ''), COUNT(*), SUM(status='verified'), SUM(det_state=1 AND auto_json IS NOT NULL), SUM(status='verified' AND agree=3), "
                  "SUM(status='verified' AND agree=2), SUM(recheck=1), SUM(status='verified' AND det_state=1 AND cls_state=1 AND train_exclude=0) FROM items GROUP BY COALESCE(batch, '') ORDER BY 1"):
        out.append(dict(batch=r[0], n=r[1], n_verified=r[2] or 0, n_ai=r[3] or 0, n_disagree=r[4] or 0, n_partial=r[5] or 0, n_recheck=r[6] or 0, n_ready=r[7] or 0,
                        eval=r[0] in ev, imports=units.get(r[0]) or []))
    return out


def _compare_sets(pairs: list[tuple[set[str], list[str]]], order: dict[str, int] | None = None) -> dict:
    """[(人の判定の集合, 比べる判定の列)] → 一致率。train.evaluate_items と同じ形: n, exact（集合が同じ）, partial（重なる）, first（比べる側の第 1 種が人の判定に入る）, low（比べる側なし）,
    by_species（人の第 1 種ごと）, matrix（人の第 1 種 × 比べる側の第 1 種。なしは "(low)"）"""
    from .judge import compare_codes
    n = exact = partial = first = low = 0
    by: dict[str, dict] = {}
    matrix: dict[str, dict[str, int]] = {}
    for human, other in pairs:
        if not human:
            continue
        n += 1
        h1 = sorted(human)[0]                      # 複数種のときは第 1 種（コード順）で集計
        d = by.setdefault(h1, dict(n=0, exact=0, first=0, low=0))
        d["n"] += 1
        other = [c for c in other if c and c != "area"]
        if not other:
            low += 1
            d["low"] += 1
            matrix.setdefault(h1, {})["(low)"] = matrix.setdefault(h1, {}).get("(low)", 0) + 1
            continue
        a = compare_codes(human, other)
        if a == 1:
            exact += 1
            d["exact"] += 1
        if a in (1, 2):
            partial += 1
        if other[0] in human:
            first += 1
            d["first"] += 1
        matrix.setdefault(h1, {})[other[0]] = matrix.setdefault(h1, {}).get(other[0], 0) + 1
    key = (lambda c: (c == "(low)", order.get(c, len(order)), c)) if order else (lambda c: (c == "(low)", c))
    by = dict(sorted(by.items(), key=lambda x: key(x[0])))
    matrix = {h: dict(sorted(row.items(), key=lambda x: key(x[0]))) for h, row in sorted(matrix.items(), key=lambda x: key(x[0]))}
    cols = sorted({c for row in matrix.values() for c in row} | set(matrix.keys()), key=key)
    return dict(n=n, exact=exact, partial=partial, first=first, low=low, by_species=by, matrix=matrix, cols=cols)


def batch_report(project, batch: str) -> dict:
    """組（gold など）の評価: 目視（確認済みの判定）を正解として、
    existing = 既存の判定（カタログの種 = 調査回のプロジェクトの判定か種名の CSV。手動の判定の誤り率）、
    ai       = いまの AI の判定（items.auto_json。SpeciesNet か学習したモデル）を比べる。確認済みの動画だけが対象"""
    from .detect import species_order
    from .judge import human_codes
    from .species import FIXED_CODES
    db = project.db
    rows = db.q("SELECT i.id, i.status, i.auto_json, c.species AS existing FROM items i LEFT JOIN catalog c ON c.item_id = i.id WHERE COALESCE(i.batch, '') = ? AND i.missing = 0", (batch,))
    n_all = len(rows)
    ver = [r for r in rows if r["status"] == "verified"]
    ids = [r["id"] for r in ver]
    js: dict[int, list[dict]] = {i: [] for i in ids}
    for i0 in range(0, len(ids), 900):
        part = ids[i0:i0 + 900]
        for j in db.q(f"SELECT item_id, species, origin FROM judgements WHERE item_id IN ({','.join('?' * len(part))})", part):
            js[j["item_id"]].append(dict(species=j["species"], origin=j["origin"]))
    enabled = {r["code"] for r in project.species.rows if r.get("enabled")} | set(FIXED_CODES)
    pairs_ex: list = []
    pairs_ai: list = []
    n_ai = n_existing = 0
    models: Counter = Counter()
    for r in ver:
        human = human_codes("verified", js[r["id"]])
        if not human:
            continue
        ex = [c for c in (r["existing"] or "").split(";") if c]
        if ex:
            n_existing += 1
        pairs_ex.append((human, ex))
        ai: list[str] = []
        if r["auto_json"]:
            try:
                d = json.loads(r["auto_json"])
                for x in d.get("species", []):
                    code = x["species"] if x["species"] in enabled else "unknown"
                    if code not in ai:
                        ai.append(code)
                if d.get("model"):
                    models[str(d["model"])] += 1
            except Exception:
                ai = []
        if ai:
            n_ai += 1
        pairs_ai.append((human, ai))
    order = species_order(project)
    return dict(batch=batch, n=n_all, n_verified=len(ver), n_existing=n_existing, n_ai=n_ai, models=dict(models.most_common(3)),
                existing=_compare_sets(pairs_ex, order), ai=_compare_sets(pairs_ai, order), eval=batch in eval_batches(project))


# ---------- 取り込みを取り消す（対象から外す。動画本体には触らない） ----------
def remove_items(project, batch: str | None = None, f: dict | None = None, all_items: bool = False, progress=None, stop=None) -> dict:
    """組（batch）か絞り込みか全部の対象を横断プロジェクトから外す。判定・枠・特徴量・枠の画像も消え、カタログの「取り込み済み」の印が外れる。参照先の動画には触らない"""
    from .judge import build_where, delete_items
    db = project.db
    if all_items:
        ids = [r[0] for r in db.q("SELECT id FROM items")]
    elif batch is not None:
        ids = [r[0] for r in db.q("SELECT id FROM items WHERE COALESCE(batch, '') = ?", (batch,))]
    else:
        where, p = build_where(dict(f or {}, include_missing=True))
        ids = [r[0] for r in db.q(f"SELECT id FROM items WHERE {where}", p)]
    n = 0
    for i0 in range(0, len(ids), 2000):
        if stop and stop():
            break
        r = delete_items(project, ids[i0:i0 + 2000], False)
        n += r["n"]
        if progress:
            progress(n, len(ids), "")
    project.data.setdefault("imports", []).append(dict(at=now_iso(), batch=batch if batch is not None else ("*" if all_items else ""), params=dict(mode="remove", filters=f or {}), n=-n, counts={}, by_source={}))
    project.data["eval_batches"] = [b for b in eval_batches(project) if db.scalar("SELECT 1 FROM items WHERE batch=? LIMIT 1", (b,))]      # なくなった組の「評価用」の印は消す
    project.save()
    return dict(n=n, n_requested=len(ids))


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
    lm = label_map(project)
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
                there = map_state(lm, item_state(src.db, r["src_id"]))      # 参照先の判定は対応表を当てた形で比べる
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
    lm = label_map(project)
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
                there = map_state(lm, item_state(src.db, r["src_id"]))      # 参照先の判定は対応表を当てた形で比べる・取り込む
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


def export_subset(project, ids: list[int], out_dir: str, copy_videos_too: bool = True, progress=None, stop=None, project_dirname: str = "", workers: int = 3) -> dict:
    """選んだ対象を、動画と判定ごと別のフォルダへ写して、そこに新しい（調査回型の）プロジェクトを作る: <out_dir>/<相対パス> に動画、<out_dir>/<project_dirname> に
    種リスト・追加項目・設定・対象（メタデータ・判定・追加項目の値・AI の判定・枠・特徴量・枠の画像）。配布や手元の複製、別の PC での作業に。
    project_dirname は自分で決められる（空なら TrapNote）。TrapNote 以外の名前のときは project.json に root_is_parent を付け、動画のフォルダ（out_dir）を開けば見つかるようにする。
    out_dir に既にプロジェクトがあれば、そこに足す（同じ相対パスの対象は飛ばす）。どのプロジェクト（調査回・横断）からでも使える"""
    from .fcopy import CopyCancelled, Throughput, copy_many, copy_to
    from .judge import item_state
    from .project import PROJECT_DIRNAME, Project, find_project_for_root, is_project_dir
    from .train import crop_path, features_path
    out_dir = norm(out_dir)
    project_dirname = (project_dirname or "").strip() or PROJECT_DIRNAME
    if project_dirname != safe_name(project_dirname) or project_dirname in (".", ".."):
        raise ValueError(f"プロジェクトのフォルダ名に使えない文字があります: {project_dirname}")
    if os.path.dirname(out_dir.rstrip("/")) in ("", out_dir.rstrip("/")):
        raise ValueError("ドライブの直下には写せません（<写す先>/TrapNote がアプリのフォルダと重なりかねない）。フォルダを 1 つ作って指定してください（例: H:/Copies/gold_sub）")
    for base in (project.root, project.dir):
        if base and (key(out_dir) == key(base) or key(out_dir).startswith(key(base).rstrip("/") + "/")):
            raise ValueError("写す先に、いまのプロジェクトのフォルダ（動画のフォルダとその中）は指定できません")
    os.makedirs(out_dir, exist_ok=True)
    pdir = os.path.join(out_dir, project_dirname)
    have = find_project_for_root(out_dir)                              # 既にそのフォルダにプロジェクトがあれば（名前が違っても）そこに足す
    if have and key(have) != key(pdir):
        pdir = have
    fresh = not is_project_dir(pdir)
    dst = Project(pdir, root=out_dir, create=True)
    try:
        if fresh:                                                    # 種リスト・追加項目・設定は元のプロジェクトから
            dst.close()
            for fn in ("species.csv", "fields.json"):
                src_f = os.path.join(project.dir, fn)
                if os.path.isfile(src_f):
                    shutil.copyfile(src_f, os.path.join(pdir, fn))
            dst = Project(pdir, root=out_dir, create=True)
            keep = {k: v for k, v in (project.data.get("settings") or {}).items() if k not in ("classifier", "catalog_scan_folders", "cross_purpose")}
            dst.update_settings(keep)
            dst.data["name"] = os.path.basename(out_dir) or dst.name
            dst.data["exported_from"] = dict(project=project.name, dir=project.dir, at=now_iso())
            if os.path.basename(pdir) != PROJECT_DIRNAME:
                dst.data["root_is_parent"] = True                     # 名前を付けたプロジェクト: ルートは親（動画のフォルダ）。開くときは動画のフォルダでも見つかる
            dst.save()
        db, ddb = project.db, dst.db
        cols = [r["name"] for r in db.q("PRAGMA table_info(items)")]
        dcols = {r["name"] for r in ddb.q("PRAGMA table_info(items)")}
        copy_cols = [c for c in cols if c in dcols and c not in ITEM_COPY_SKIP - {"batch"}]
        now = now_iso()
        counts = dict(n=0, miss=0, skip=0)
        tp = Throughput()
        ids = [int(x) for x in ids]
        total = len(ids)

        def report(rel: str) -> None:
            if progress:
                k = counts["n"] + counts["miss"] + counts["skip"]
                progress(k, total, f"{rel} · {tp.text()}" if tp.bytes else rel)

        def tasks():
            """写す対象（写してあるものは飛ばす）。DB はこのスレッドだけが触る"""
            for sid in ids:
                srow = db.q1("SELECT * FROM items WHERE id=?", (sid,))
                if srow is None:
                    continue
                srow = dict(srow)
                rel = srow["rel_path"]
                if ddb.scalar("SELECT 1 FROM items WHERE rel_key=? LIMIT 1", (rel_key(rel),)):       # 写してあるものは飛ばす（動画も触らない）
                    counts["skip"] += 1
                    report(rel)
                    continue
                yield (sid, srow, rel)

        def do_copy(task):
            """動画を <out_dir>/<相対パス> に写す（横断なら <参照先>/<参照先での相対パス>）。並列のスレッドで。返り値: "ok" / "missing" / "same"（同じ大きさのものが既にある）"""
            sid, srow, rel = task
            if not copy_videos_too:
                return "ok"
            src_f = project.abs_path(rel)
            dst_f = os.path.join(out_dir, *rel.split("/"))
            try:
                size = os.path.getsize(src_f)
            except OSError:
                return "missing"
            if os.path.isfile(dst_f) and os.path.getsize(dst_f) == size:
                return "same"
            tp.add(copy_to(src_f, dst_f, stop))
            return "ok"

        def record(task, res) -> None:
            """写し終えた対象を新しいプロジェクトに記録（判定・追加項目の値・AI の判定・枠・特徴量・枠の画像）"""
            sid, srow, rel = task
            if isinstance(res, CopyCancelled):
                return
            if isinstance(res, BaseException):
                raise res
            if res == "missing":
                counts["miss"] += 1
                report(rel)
                return
            with ddb.tx() as c:
                vals = {k: srow.get(k) for k in copy_cols}
                vals.update(rel_path=rel, rel_key=rel_key(rel), folder=os.path.dirname(rel), src_dir=None, src_id=None, src_rel=None, updated_at=now,
                            orig_rel_path=srow.get("orig_rel_path"), orig_size=srow.get("orig_size"), archived_at=srow.get("archived_at"))
                vals = {k: v for k, v in vals.items() if k in dcols}
                names = list(vals.keys())
                cur = c.execute(f"INSERT OR IGNORE INTO items({', '.join(names)}) VALUES({', '.join('?' * len(names))})", [vals[k] for k in names])
                if cur.rowcount == 0:
                    counts["skip"] += 1
                    report(rel)
                    return
                nid = cur.lastrowid
                st0 = item_state(db, sid)                                   # 判定・追加項目の値・メモ・担当・状態
                _apply_state(c, nid, st0, None)
                c.execute("UPDATE items SET status=?, verified_by=?, verified_at=?, agree=?, recheck=?, updated_at=? WHERE id=?",
                          (st0.get("status") or srow.get("status") or "new", srow.get("verified_by"), srow.get("verified_at"), srow.get("agree"), srow.get("recheck") or 0, now, nid))
                c.executemany("INSERT INTO auto_results(item_id, species, confidence, model, created_at) VALUES(?,?,?,?,?)",
                              [(nid, a["species"], a["confidence"], a["model"], a["created_at"]) for a in db.q("SELECT species, confidence, model, created_at FROM auto_results WHERE item_id=? ORDER BY id", (sid,))])
                c.executemany("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES(?,?,?,?,?,?,?,?,?,?)",
                              [(nid, d["t_sec"], d["category"], d["conf"], d["x"], d["y"], d["w"], d["h"], d["species"], d["sp_conf"])
                               for d in db.q("SELECT t_sec, category, conf, x, y, w, h, species, sp_conf FROM detections WHERE item_id=? ORDER BY id", (sid,))])
                fa, fb = features_path(project, rel), features_path(dst, rel)
                if os.path.isfile(fa) and not os.path.isfile(fb):
                    try:
                        shutil.copyfile(fa, fb)
                    except OSError as e:
                        log.warning("特徴量を写せません %s: %s", fa, e)
                ca, cb = os.path.dirname(crop_path(project, rel, 0)), os.path.dirname(crop_path(dst, rel, 0))
                if os.path.isdir(ca) and not os.path.isdir(cb):
                    try:
                        shutil.copytree(ca, cb)
                    except OSError as e:
                        log.warning("枠の画像を写せません %s: %s", ca, e)
                counts["n"] += 1
            report(rel)

        # 動画は workers 本ずつ並列に写す（NAS からは 1 本ずつより速い）。「止める」は写している途中でも効く（.part を消す）。写し終えた分は残り、やり直せば続きから
        stopped = copy_many(tasks(), do_copy, record, workers=workers if copy_videos_too else 1, stop=stop)
        n, n_miss, n_skip = counts["n"], counts["miss"], counts["skip"]
        return dict(n=n, n_missing=n_miss, n_skipped=n_skip, out_dir=out_dir, project_dir=dst.dir, fresh=fresh, stopped=stopped, n_left=len(ids) - n - n_miss - n_skip,
                    bytes=tp.bytes, seconds=round(tp.seconds, 1), mb_s=round(tp.mb_s, 1), speed=tp.text() if tp.bytes else "")
    finally:
        dst.close()


def copy_videos(project, out_dir: str, f: dict | None = None, progress=None, stop=None, workers: int = 3) -> dict:
    """対象の動画を out_dir/<参照先>/<参照先での相対パス> に写す（研究用の手元の複製や配布に）。workers 本ずつ並列、OS のコピーで"""
    from .fcopy import CopyCancelled, Throughput, copy_many, copy_to
    from .judge import build_where
    where, p = build_where(dict(f or {}))
    rows = project.db.q(f"SELECT id, rel_path FROM items WHERE {where} ORDER BY rel_path", p)
    counts = dict(ok=0, miss=0)
    tp = Throughput()

    def do_copy(r):
        src = project.abs_path(r["rel_path"])
        dst = os.path.join(out_dir, *r["rel_path"].split("/"))
        try:
            size = os.path.getsize(src)
        except OSError:
            return "missing"
        if os.path.isfile(dst) and os.path.getsize(dst) == size:
            return "same"
        tp.add(copy_to(src, dst, stop))
        return "ok"

    def done(r, res):
        if isinstance(res, CopyCancelled):
            return
        if isinstance(res, BaseException):
            raise res
        counts["miss" if res == "missing" else "ok"] += 1
        if progress and (counts["ok"] + counts["miss"]) % 5 == 0:
            progress(counts["ok"] + counts["miss"], len(rows), f"{r['rel_path']} · {tp.text()}" if tp.bytes else r["rel_path"])
    stopped = copy_many(rows, do_copy, done, workers=workers, stop=stop)
    return dict(n=counts["ok"], n_missing=counts["miss"], out_dir=norm(out_dir), stopped=stopped, bytes=tp.bytes, seconds=round(tp.seconds, 1), mb_s=round(tp.mb_s, 1), speed=tp.text() if tp.bytes else "")
