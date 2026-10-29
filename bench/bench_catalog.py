"""横断プロジェクト: 親フォルダ参照 + 種名の CSV（N 行。動画は置かない）で、CSV の読み込み・対応の当て直し・カタログの作成・対応表の変更後の当て直しの時間を測る。
python bench/bench_catalog.py 1200000"""
import os, random, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trapnote import cross
from trapnote.project import Project

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1_200_000
BASE = "/home/claude/work/bench/cat"
shutil.rmtree(BASE, ignore_errors=True)
root = os.path.join(BASE, "nas", "PRM_N")
rng = random.Random(1)
seasons = [f"N{y}{m:02d}01" for y in range(18, 26) for m in (1, 4, 7, 10)]      # 32 調査回
stations = [f"BSN{i:04d}" for i in range(1, 209)]                                  # 208 地点
for se in seasons:
    for st in stations:
        os.makedirs(os.path.join(root, se, st), exist_ok=True)
labels = ["Deer"] * 30 + ["Boar"] * 15 + ["RaccoonDog"] * 8 + ["Civet"] * 5 + ["Badger"] * 4 + ["Kiji"] * 3 + ["Hiyodori"] * 2 + ["Shirohara", "Karasu", "AngleChange", "Setup"] + ["Empty"] * 25 + ["Human"] * 4 + ["Monkey"] * 3 + ["Hare"] * 3
t0 = time.time()
with open(os.path.join(root, "labels.csv"), "w", encoding="utf-8", newline="") as fh:
    fh.write("Season,Station,File,Species,DateTime\n")
    for i in range(N):
        se, st = rng.choice(seasons), rng.choice(stations)
        fh.write(f"{se},{st},IMG_{i:07d}.AVI,{rng.choice(labels)},2024/01/28 {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00\n")
print(f"CSV {N:,} 行を書いた: {time.time() - t0:.1f} s")
place = os.path.join(BASE, "Studies", "gold")
os.makedirs(place)
p = Project(os.path.join(place, "TrapNote"), root=place, create=True, kind="cross")
cross.add_sources_under(p, root)
s = cross.source_by_name(p, "PRM_N")

def lap(msg, f):
    t = time.time()
    r = f()
    print(f"{msg}: {time.time() - t:.1f} s")
    return r

lab = lap("CSV を読む（_labels）", lambda: cross._labels(p, s))
print("  表記", len(lab["labels"]), "行", len(lab["rows"]))
cross._LABELS.clear()
lap("pickle から戻して対応を当てる（labels_ready）", lambda: cross.labels_ready(p, s))
r = lap("カタログを作る（build_catalog）", lambda: cross.build_catalog(p))
print("  ", r["n"])
lap("対応表を保存（set_label_map）", lambda: cross.set_label_map(p, {"kiji": "bird", "hiyodori": "bird", "shirohara": "bird", "karasu": "bird", "anglechange": "", "setup": ""}))
print("  stale:", cross.catalog_status(p))
if hasattr(cross, "remap_catalog"):
    r = lap("対応を当て直す（remap_catalog）", lambda: cross.remap_catalog(p))
    print("  ", r, cross.catalog_status(p)["stale"])
lap("参照先の表（sources_info）", lambda: cross.sources_info(p))
lap("対応表の候補（label_candidates）", lambda: cross.label_candidates(p))
lap("カタログの集計（catalog_counts）", lambda: cross.catalog_counts(p, {}))
r = lap("ランダム: 地点 20（catalog_sample）", lambda: cross.catalog_sample(p, {}, dict(unit="station", n=20, seed=1)))
print("  ", r["n"], r["n_units_available"])
r = lap("ランダム: 種ごと 200", lambda: cross.catalog_sample(p, {}, dict(unit="species", n=200, cap=5, gap_min=10, seed=1)))
print("  ", r["n"])
p.close()
