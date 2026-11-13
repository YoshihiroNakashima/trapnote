"""動画ファイルだけを別のフォルダへ書き出す（一覧の右クリック「動画ファイルを書き出す…」）。判定や TrapNote フォルダは写さない（それは cross.export_subset「別のフォルダへ写す」）。

- keep_tree = True: <書き出し先>/<相対パス>（プロジェクトの階層のまま。横断プロジェクトでは先頭が参照先の名前）
- keep_tree = False: <書き出し先>/<フォルダ名>_<ファイル名>（階層をたたむ。フォルダ名は相対フォルダの全部を _ でつないだもの: ST01/2025 → ST01_2025_VID.AVI。
  ルート直下のものはファイル名のまま。同じ名前になるものには _2, _3 を付ける）
- convert = True: ブラウザがそのまま再生できない動画（MJPEG の AVI など）は H.264 の MP4 に変換して書き出す（「別のフォルダへ写す」と同じ。NVENC があればそれで）。
  変換できないものはそのまま写す。
同じ大きさのファイルが既にあれば飛ばす（やり直すと続きから）。並列に写す（copy_many、設定 copy_workers）"""
from __future__ import annotations

import logging
import os

from . import media
from .fcopy import CopyCancelled, Throughput, copy_many, copy_to

log = logging.getLogger(__name__)


def flat_name(rel: str) -> str:
    """階層をたたんだときのファイル名: 相対フォルダの各段を _ でつないで、ファイル名の前に付ける"""
    parts = [x for x in rel.replace("\\", "/").split("/") if x]
    return "_".join(parts)


def plan_names(rels: list[str], keep_tree: bool) -> dict[str, str]:
    """相対パス → 書き出し先での相対パス。たたむときに同じ名前になるものは _2, _3 を付ける（大文字小文字は同じとみなす）"""
    out: dict[str, str] = {}
    used: set[str] = set()
    for rel in rels:
        if keep_tree:
            out[rel] = rel
            continue
        name = flat_name(rel)
        stem, ext = os.path.splitext(name)
        cand, k = name, 1
        while cand.lower() in used:
            k += 1
            cand = f"{stem}_{k}{ext}"
        used.add(cand.lower())
        out[rel] = cand
    return out


def _mp4_name(name: str) -> str:
    stem, ext = os.path.splitext(name)
    return stem + (".MP4" if ext and ext.isupper() else ".mp4")


def wants_mp4(convert, kind, vcodec, acodec, container) -> bool:
    """MP4 に変換して写すか（動画の書き出しの窓の「MP4 に変換」。確認事項 125 回目）。
    convert: False = 変換しない / True か "unplayable" = ブラウザがそのまま再生できない動画だけ / "all" = MP4 でない動画は全部。
    すでに H.264 の MP4 になっている動画は、"all" でもそのまま写す（変換し直すと、画質が落ちるだけなので）"""
    if not convert or kind != "video":
        return False
    if not media.direct_playable(vcodec, acodec, container):
        return True
    return convert == "all" and ("." + (container or "").lower().lstrip(".")) != ".mp4"


LIST_NAME = "video_list.csv"
LIST_COLS = ["OutFile", "SourceFolder", "SourceFile", "Season", "Station", "DateTime", "Species", "Count", "Status"]


def write_list_csv(project, out_dir: str, pairs: list[tuple]) -> dict:
    """書き出した動画の一覧（video_list.csv）を out_dir に書く（動画の書き出しには、必ず付ける。確認事項 125 回目）。1 行 = 動画 1 本。
    pairs = [(対象の id か None, もとの行（rel_path。目録の行なら abs・season・station も）, 書き出し先での相対パス)]。
    列: OutFile（書き出し先でのファイル名）, SourceFolder・SourceFile（もとのフォルダとファイル名）, Season（調査回）, Station（地点）, DateTime（修正後があればそれ）,
    Species（種。CSV 表記。2 種以上は ; でつなぐ）, Count（個体数。種と同じ順に ; でつなぐ）, Status。
    階層をたたんだり MP4 に変換したりしてファイル名が変わっても、もとの動画との対応が残る。
    同じ書き出し先にもう一度書き出したときは、前の一覧に足す（同じ OutFile の行は、新しいもので置き換える）"""
    import csv
    path = os.path.join(out_dir, LIST_NAME)
    rows: dict[str, list] = {}
    if os.path.isfile(path):                             # 前の一覧（続きから書き出したとき）
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rd = csv.reader(fh)
                if next(rd, None) == LIST_COLS:
                    for r in rd:
                        if r:
                            rows[r[0]] = (r + [""] * len(LIST_COLS))[:len(LIST_COLS)]
        except Exception as e:
            log.warning("前の一覧を読めません %s: %s", path, e)
    db = project.db
    sp = project.species.by_code()
    ids = [int(p[0]) for p in pairs if p[0] is not None]
    items: dict[int, dict] = {}
    js: dict[int, list[dict]] = {}
    cnt: dict[int, str] = {}
    for i0 in range(0, len(ids), 900):
        part = ids[i0:i0 + 900]
        qs = ",".join("?" * len(part))
        for r in db.q(f"SELECT id, rel_path, season, station, datetime, datetime_corrected, status FROM items WHERE id IN ({qs})", part):
            items[r["id"]] = dict(r)
        for j in db.q(f"SELECT id, item_id, species FROM judgements WHERE item_id IN ({qs}) ORDER BY id", part):
            js.setdefault(j["item_id"], []).append(dict(j))
        for v in db.q(f"SELECT judgement_id, value FROM field_values WHERE item_id IN ({qs}) AND field_key='count' AND judgement_id IS NOT NULL AND seq=0", part):
            cnt[v["judgement_id"]] = v["value"] or ""
    n = 0
    for iid, src, dst_rel in pairs:
        rel = src.get("rel_path") or ""
        src_abs = src.get("abs") or project.abs_path(rel)
        it = items.get(int(iid)) if iid is not None else None
        jl = js.get(int(iid), []) if iid is not None else []
        rows[dst_rel] = [dst_rel, os.path.dirname(src_abs), os.path.basename(rel),
                         (it or src).get("season") or "", (it or src).get("station") or "",
                         ((it or {}).get("datetime_corrected") or (it or src).get("datetime") or ""),
                         ";".join(sp.get(j["species"], {}).get("csv_label") or j["species"] for j in jl),
                         ";".join(cnt.get(j["id"], "") for j in jl) if any(cnt.get(j["id"]) for j in jl) else "",
                         (it or {}).get("status") or ""]
        n += 1
    os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(LIST_COLS)
        for k in sorted(rows):
            w.writerow(rows[k])
    os.replace(tmp, path)
    return dict(path=path, rows=len(rows), added=n)


def export_files(project, ids: list[int], out_dir: str, keep_tree: bool = True, convert: bool = False, progress=None, stop=None,
                 workers: int = 3, ffmpeg: str | None = None, ffprobe: str | None = None, crf: int = 19, rows: list[dict] | None = None) -> dict:
    """ids の動画・画像ファイルを out_dir へ写す。rows を渡せば（横断の目録から: cross.catalog_files）ids の代わりにその行を写す（rel_path・abs・kind、codec は None でよい）。返り値: n（写した）, n_same（あったので飛ばした）, n_missing, n_converted, n_convert_failed, stopped, n_left, out_dir, speed, seconds, encoder, files（書き出し先の相対パス）"""
    out_dir = os.path.abspath(out_dir)
    if convert:
        if not ffmpeg:
            ffmpeg, ffprobe = media.find_ffmpeg("")
        if not ffmpeg:
            raise RuntimeError("ffmpeg がありません（設定で場所を指定するか、「MP4 に変換して書き出す」を外してください）")
    use_nvenc = bool(convert and media.has_nvenc(ffmpeg))
    db = project.db
    if rows is not None:
        rows = [dict(r) for r in rows]
    else:
        rows = []
        for i0 in range(0, len(ids), 900):
            part = [int(x) for x in ids[i0:i0 + 900]]
            got = {r["id"]: dict(r) for r in db.q(f"SELECT id, rel_path, kind, vcodec, acodec, container, duration, datetime FROM items WHERE id IN ({','.join('?' * len(part))})", part)}
            rows.extend(got[i] for i in part if i in got)
    rows.sort(key=lambda r: r["rel_path"])
    names = plan_names([r["rel_path"] for r in rows], keep_tree)
    counts = dict(n=0, same=0, miss=0, conv=0, conv_fail=0)
    files: list[str] = []
    tp = Throughput()
    total = len(rows)

    def report(rel: str) -> None:
        if progress:
            progress(counts["n"] + counts["same"] + counts["miss"], total, f"{rel} · {tp.text()}" if tp.bytes else rel)

    pairs: list[tuple] = []      # 一覧の CSV 用: (id, もとの行, 書き出し先での相対パス)

    def want_convert(r) -> bool:
        return wants_mp4(convert, r.get("kind"), r.get("vcodec"), r.get("acodec"), r.get("container"))

    def do_copy(r):
        rel = r["rel_path"]
        src_f = r.get("abs") or project.abs_path(rel)
        try:
            size = os.path.getsize(src_f)
        except OSError:
            return "missing"
        dst_rel = names[rel]
        if convert and r.get("kind") == "video" and not r.get("vcodec"):           # 目録の行は codec が分からない: 変換するかを決めるために見る
            try:
                m0 = media.probe(src_f, ffmpeg, ffprobe, bool(project.setting("z_is_utc")), background=True)
                r.update(vcodec=m0.get("vcodec"), acodec=m0.get("acodec"), container=m0.get("container"), duration=r.get("duration") or m0.get("duration"))
            except Exception as e:
                log.warning("codec を調べられません %s: %s", rel, e)
        if want_convert(r):
            new_rel = _mp4_name(dst_rel)
            dst_f = os.path.join(out_dir, *new_rel.split("/"))
            try:
                if os.path.isfile(dst_f) and os.path.getsize(dst_f) > 0:
                    return ("same", new_rel)
                if stop and stop():
                    raise CopyCancelled()
                os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                media.archive_encode(ffmpeg, src_f, dst_f, use_nvenc, crf, r.get("datetime"), background=True)
                try:
                    st = os.stat(src_f)
                    os.utime(dst_f, (st.st_atime, st.st_mtime))
                except OSError:
                    pass
                tp.add(size)
                m = media.probe(dst_f, ffmpeg, ffprobe, bool(project.setting("z_is_utc")), background=True)
                od, nd = r.get("duration"), m.get("duration")
                if not m.get("width") or (od and nd and abs(od - nd) > max(0.5, 0.02 * od)):
                    try:
                        os.remove(dst_f)
                    except OSError:
                        pass
                    raise RuntimeError(f"検算に通りません（長さ {od} → {nd}）")
                return ("conv", new_rel)
            except CopyCancelled:
                raise
            except Exception as e:
                log.warning("MP4 に変換できないのでそのまま写します %s: %s", rel, e)
                counts["conv_fail"] += 1
        dst_f = os.path.join(out_dir, *dst_rel.split("/"))
        if os.path.isfile(dst_f) and os.path.getsize(dst_f) == size:
            return ("same", dst_rel)
        tp.add(copy_to(src_f, dst_f, stop))
        return ("ok", dst_rel)

    def on_done(r, res) -> None:
        if isinstance(res, CopyCancelled):
            return
        if isinstance(res, BaseException):
            log.warning("書き出せません %s: %s", r["rel_path"], res)
            counts["miss"] += 1
        elif res == "missing":
            counts["miss"] += 1
        else:
            st, dst_rel = res
            files.append(dst_rel)
            pairs.append((r.get("id"), r, dst_rel))
            if st == "same":
                counts["same"] += 1
            else:
                counts["n"] += 1
                if st == "conv":
                    counts["conv"] += 1
        report(r["rel_path"])

    os.makedirs(out_dir, exist_ok=True)
    stopped = copy_many(rows, do_copy, on_done, workers=workers, stop=stop)
    done = counts["n"] + counts["same"] + counts["miss"]
    list_csv = ""
    if pairs:                                            # 一覧の CSV は必ず付ける（途中で止めても、写し終えた分を書く）
        try:
            list_csv = write_list_csv(project, out_dir, pairs)["path"]
        except Exception as e:
            log.warning("一覧の CSV を書けません %s: %s", out_dir, e)
    return dict(n=counts["n"], n_same=counts["same"], n_missing=counts["miss"], n_converted=counts["conv"], n_convert_failed=counts["conv_fail"],
                stopped=stopped, n_left=max(0, total - done), out_dir=out_dir, speed=tp.text() if tp.bytes else "", seconds=tp.seconds,
                encoder=("nvenc" if use_nvenc else "libx264") if convert else "", files=sorted(files), keep_tree=keep_tree, list_csv=list_csv)


_FRONT_PS = r"""
$folder = $env:TRAPNOTE_REVEAL_DIR.TrimEnd('\')
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$sh = New-Object -ComObject Shell.Application
for ($i = 0; $i -lt 40; $i++) {
  foreach ($w in @($sh.Windows())) {
    try { $p = ([uri]$w.LocationURL).LocalPath.TrimEnd('\') } catch { continue }
    if ($p -ieq $folder) {
      $h = [IntPtr]$w.HWND
      if ([W]::IsIconic($h)) { [W]::ShowWindow($h, 9) | Out-Null }
      [W]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [W]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
      [W]::SetForegroundWindow($h) | Out-Null
      exit 0
    }
  }
  Start-Sleep -Milliseconds 100
}
exit 1
"""


def _front_explorer(folder: str) -> None:
    """Windows: そのフォルダのエクスプローラーの窓を前に出す（裏のスレッドで。別のプロセスから開いた窓は TrapNote の窓の後ろに出る・既に開いていれば前に出てこないので、
    PowerShell で Shell.Application から窓を探して SetForegroundWindow。Alt を一瞬押すのは前面化の制限（foreground lock）を通すための常套手段）"""
    import subprocess
    try:
        env = dict(os.environ, TRAPNOTE_REVEAL_DIR=folder.replace("/", "\\"))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _FRONT_PS], env=env, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.debug("エクスプローラーの窓を前に出せません %s: %s", folder, e)


def reveal(path: str) -> bool:
    """ファイルのあるフォルダを OS のファイル管理（エクスプローラー / Finder）で開き、そのファイルを選んだ状態にする。ファイルがなければフォルダだけ開く。返り値: 開けたか。
    Windows では開いた窓（既に開いていた窓）を前に出す（_front_explorer）。TrapNote の窓（Edge / Chrome のアプリの窓）の後ろに出て「何も起きない」ように見えていた"""
    import subprocess
    import sys
    import threading
    path = os.path.abspath(path)
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not os.path.isdir(folder):
        return False
    try:
        if sys.platform.startswith("win"):
            # フォルダは explorer /select,<中の最初のファイル> で開く。os.startfile(フォルダ) だと窓が 1 秒ほどで消えて（既にある窓に吸われて）何も起きないように見えた。
            # /select, なら新しい窓が前に出る（実測）。中が空ならフォルダそのもの
            sel = path if os.path.isfile(path) else None
            if sel is None:
                try:
                    names = sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n)))
                    sel = os.path.join(folder, names[0]) if names else None
                except OSError:
                    sel = None
            if sel:
                subprocess.Popen(["explorer", "/select,", sel.replace("/", "\\")])
            else:
                subprocess.Popen(["explorer", folder.replace("/", "\\")])
            threading.Thread(target=_front_explorer, args=(folder,), daemon=True, name="reveal-front").start()
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception as e:
        log.warning("フォルダを開けません %s: %s", path, e)
        return False
