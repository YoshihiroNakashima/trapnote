"""フォルダ名のパターン（例 {season}/{station}/{file}）から地点などを抜き出す。
相対パスの後ろから段ごとに当てる（手前に余分なフォルダがあってもよい）。段の中は {名前}、*（何でも）、文字そのまま を組み合わせられる
（例 {station}_{camera}/{file}）。{file} はファイル名の段"""
from __future__ import annotations

import re

TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}|\*|[^{}*]+")
KNOWN = ("season", "station", "file")


def compile_pattern(pattern: str) -> list[re.Pattern]:
    segs = [s for s in (pattern or "").replace("\\", "/").split("/") if s.strip()]
    out = []
    for seg in segs:
        parts = []
        for m in TOKEN.finditer(seg):
            t = m.group(0)
            if t.startswith("{"):
                parts.append(f"(?P<{m.group(1)}>.+?)")
            elif t == "*":
                parts.append(".*?")
            else:
                parts.append(re.escape(t))
        out.append(re.compile("^" + "".join(parts) + "$", re.IGNORECASE))
    return out


def names_in(pattern: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", pattern or "")]


def extract(rel_path: str, compiled: list[re.Pattern]) -> dict:
    """相対パスにパターンを当てて {名前: 値} を返す。合わない段の値は空"""
    segs = [s for s in rel_path.replace("\\", "/").split("/") if s]
    out: dict[str, str] = {}
    n = len(compiled)
    if n == 0:
        return out
    tail = segs[-n:] if len(segs) >= n else [""] * (n - len(segs)) + segs
    for pat, seg in zip(compiled, tail):
        m = pat.match(seg) if seg else None
        if m:
            for k, v in m.groupdict().items():
                out[k] = (v or "").strip()
        else:
            for k in pat.groupindex:
                out.setdefault(k, "")
    if "file" in out:
        del out["file"]
    return out


def preview(pattern: str, rel_paths: list[str], n: int = 8) -> list[dict]:
    comp = compile_pattern(pattern)
    rows = []
    for rp in rel_paths[:n]:
        rows.append(dict(rel_path=rp, **extract(rp, comp)))
    return rows
