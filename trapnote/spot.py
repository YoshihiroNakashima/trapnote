"""抜き取りの確認（確認事項 125 回目で決めたもの。129 回目で作った）

「一括確定」の対象に、検出で枠が 1 つも付かなかった動画（検出済みで、枠の数が 0）が入っているとき、
その中から無作為に選んだ動画を人が目で確かめて、動物が映っていた数と、枠が付かなかった動画の全体での見積もりを出す。

- 抜き取る対象（母数）: 一括確定の対象（いまの絞り込みに入る「判定あり・未確認」）のうち、枠が付かなかった動画
- 抜き取る数: 既定は 100 本（母数がそれより少なければ全部）
- 結果: 確認した数、動物が映っていた数、人・車だけが映っていた数、割合の 95 % の上限（片側。正確な二項分布）、母数での見積もり
- 記録: project.json の spot_checks（終えた抜き取りの列）と spot_active（途中の抜き取り）。人の判定そのものは、ふつうの確認と同じに items / judgements に入る
"""
from __future__ import annotations

import math
import random

from .db import Database, now_iso
from .judge import build_where

DEFAULT_N = 100
TARGET = "items.status='auto' AND EXISTS (SELECT 1 FROM judgements j WHERE j.item_id=items.id)"       # 一括確定の対象（judge.bulk_verify と同じ）
UNBOXED = "items.det_state = 1 AND items.n_boxes = 0"                                                  # 検出済みで、枠が付かなかった
NOT_ANIMAL = ("empty", "area", "human", "vehicle")


def pool_ids(db: Database, f: dict) -> list[int]:
    """一括確定の対象のうち、枠が付かなかった動画の id（小さい順）"""
    where, p = build_where(f)
    return [r["id"] for r in db.q(f"SELECT id FROM items WHERE {where} AND {TARGET} AND {UNBOXED} ORDER BY id", p)]


def pool_count(db: Database, f: dict) -> int:
    where, p = build_where(f)
    return int(db.scalar(f"SELECT COUNT(*) FROM items WHERE {where} AND {TARGET} AND {UNBOXED}", p) or 0)


def upper_bound(k: int, n: int, conf: float = 0.95) -> float | None:
    """n 本のうち k 本に当たりがあったときの、割合の上限（片側 conf。正確な二項分布 = Clopper–Pearson の上側）。
    k = 0 なら 1 - (1 - conf)^(1/n)（n = 100 で約 3 %、300 で約 1 %）"""
    if n <= 0:
        return None
    if k >= n:
        return 1.0
    alpha = 1.0 - conf

    def cdf(p: float) -> float:          # P(X <= k)。対数で足す（n が大きくても桁あふれしない）
        if p <= 0.0:
            return 1.0
        lp, lq = math.log(p), math.log1p(-p)
        return math.fsum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq) for i in range(k + 1))

    lo, hi = k / n, 1.0
    for _ in range(60):                  # cdf は p について単調に減る → 2 分法
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def tally(db: Database, ids: list[int]) -> dict:
    """抜き取った動画のいまの様子: 確認した数、そのうち動物が映っていた数・人や車だけだった数・何も映っていなかった数"""
    n_done = n_animal = n_other = n_empty = n_missing = 0
    found = set()
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        qs = ",".join("?" * len(part))
        na = ",".join("?" * len(NOT_ANIMAL))
        rows = db.q(
            f"SELECT i.id, i.status, "
            f"EXISTS (SELECT 1 FROM judgements j WHERE j.item_id=i.id AND j.species NOT IN ({na})) AS animal, "
            f"EXISTS (SELECT 1 FROM judgements j WHERE j.item_id=i.id AND j.species IN ('human','vehicle')) AS other "
            f"FROM items i WHERE i.id IN ({qs})", (*NOT_ANIMAL, *part))
        for r in rows:
            found.add(r["id"])
            if r["status"] != "verified":
                continue
            n_done += 1
            if r["animal"]:
                n_animal += 1
            elif r["other"]:
                n_other += 1
            else:
                n_empty += 1
    n_missing = len([i for i in ids if i not in found])        # 抜き取ったあとで消された動画
    return dict(n_sample=len(ids) - n_missing, n_done=n_done, n_animal=n_animal, n_other=n_other, n_empty=n_empty, n_missing=n_missing)


def estimate(n_pool: int, n_done: int, n_animal: int) -> dict:
    """母数（枠が付かなかった動画の全部）での見積もり: 割合、その 95 % の上限、本数"""
    if n_done <= 0:
        return dict(share=None, share_hi=None, est=None, est_hi=None)
    share = n_animal / n_done
    hi = upper_bound(n_animal, n_done)
    return dict(share=share, share_hi=hi, est=int(round(n_pool * share)), est_hi=int(math.ceil(n_pool * hi - 1e-9)) if hi is not None else None)


def _view(db: Database, a: dict | None) -> dict | None:
    if not a:
        return None
    ids = [int(i) for i in a.get("ids") or []]
    t = tally(db, ids)
    out = dict(id=a.get("id"), started_at=a.get("started_at"), by=a.get("by"), n_pool=int(a.get("n_pool") or 0), n_asked=int(a.get("n_asked") or len(ids)),
               filters=a.get("filters") or {}, ids=ids, **t)
    out.update(estimate(out["n_pool"], t["n_done"], t["n_animal"]))
    return out


def status(project) -> dict:
    """いまの抜き取り（途中のもの）と、これまでの記録"""
    return dict(active=_view(project.db, project.data.get("spot_active")), history=list(project.data.get("spot_checks") or []), default_n=DEFAULT_N)


def start(project, f: dict, n: int | None, by: str, seed: int | None = None) -> dict:
    """抜き取りを始める: 母数から無作為に n 本を選んで、project.json の spot_active に書く。途中のものがあれば、始めない"""
    if project.data.get("spot_active"):
        raise ValueError("抜き取りの確認が途中です。結果を出して終えるか、やめてから始めてください")
    ids = pool_ids(project.db, f)
    if not ids:
        raise ValueError("抜き取る対象（一括確定の対象のうち、枠が付かなかった動画）がありません")
    n = max(1, int(n or DEFAULT_N))
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2 ** 31)
    pick = sorted(random.Random(seed).sample(ids, min(n, len(ids))))
    stamp = now_iso()
    project.data["spot_active"] = dict(id=stamp, started_at=stamp, by=by or "", filters=dict(f), n_pool=len(ids), n_asked=n, seed=seed, ids=pick)
    project.save()
    return status(project)


def finish(project, by: str) -> dict:
    """抜き取りを終えて、結果を記録する（確認した数が 0 でも、記録して終える）。返り値: dict(result=記録した結果, filters=始めたときの絞り込み)"""
    a = project.data.get("spot_active")
    if not a:
        raise ValueError("途中の抜き取りの確認がありません")
    v = _view(project.db, a)
    rec = dict(id=v["id"], started_at=v["started_at"], finished_at=now_iso(), by=by or v.get("by") or "", filters=v["filters"], seed=a.get("seed"),
               n_pool=v["n_pool"], n_asked=v["n_asked"], n_sample=v["n_sample"], n_done=v["n_done"], n_animal=v["n_animal"], n_other=v["n_other"],
               n_empty=v["n_empty"], n_missing=v["n_missing"], share=v["share"], share_hi=v["share_hi"], est=v["est"], est_hi=v["est_hi"], ids=v["ids"])
    project.data.setdefault("spot_checks", []).append(rec)
    project.data.pop("spot_active", None)
    project.save()
    return dict(result=rec, filters=rec["filters"])


def cancel(project) -> dict:
    """抜き取りをやめる（記録しない。確認した判定は、そのまま残る）"""
    a = project.data.pop("spot_active", None)
    if a:
        project.save()
    return dict(filters=(a or {}).get("filters") or {})
