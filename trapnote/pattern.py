"""フォルダ名のパターン（例 {season}/{station}/{file}）から地点などを抜き出す。
相対パスの後ろから段ごとに当てる（手前に余分なフォルダがあってもよい）。段の中は {名前}、*（何でも）、文字そのまま を組み合わせられる
（例 {station}_{camera}/{file}）。{file} はファイル名の段。

先頭に ^ を付けたパターン（例 ^*/{station}）は、相対パスの先頭（ルートの直下）から、フォルダの段ごとに当てる（ファイル名の段には当てない。
余った下の段は見ない）。「地点のフォルダを選ぶ」画面（station.py）が作る形: ルートから何段目が地点か、で決めるので、地点の下のフォルダの深さが動画によって違ってもよい"""
from __future__ import annotations

import re

TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}|\*|[^{}*]+")
KNOWN = ("season", "station", "file")


class Compiled(list):
    """compile_pattern の返り値（段ごとの正規表現の列）。from_root = True なら、ルートの直下から当てる"""
    from_root = False


def compile_pattern(pattern: str) -> list[re.Pattern]:
    pattern = (pattern or "").strip()
    out = Compiled()
    if pattern.startswith("^"):
        out.from_root = True
        pattern = pattern[1:]
    segs = [s for s in pattern.replace("\\", "/").split("/") if s.strip()]
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
    if getattr(compiled, "from_root", False):            # ルートの直下から: フォルダの段だけに当てる（足りない段は空）
        dirs = segs[:-1]
        tail = dirs[:n] + [""] * max(0, n - len(dirs))
    else:
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
