"""30 万件の対象を入れたプロジェクトで、画面が呼ぶ主な問い合わせの時間を測る（動画は置かず DB だけ）"""
import json, os, random, shutil, sys, time
sys.path.insert(0, "/home/claude/work/TrapNote")
os.environ["TRAPNOTE_DATA_DIR"] = "/home/claude/work/bench/data"
from trapnote.project import Project
from trapnote import judge, train

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300000
ROOT = "/home/claude/work/bench/root"
shutil.rmtree(ROOT, ignore_errors=True); os.makedirs(ROOT)
p = Project(os.path.join(ROOT, "TrapNote"), root=ROOT, create=True)
db = p.db
rng = random.Random(1)
SP = ["deer", "boar", "raccoon_dog", "macaque", "hare", "empty", "badger", "civet", "marten", "unknown"]
t0 = time.time()
with db.tx() as c:
    items, judg, auto, dets = [], [], [], []
    for i in range(N):
        season = f"N2{rng.randint(1, 3)}0{rng.randint(1, 4)}01"
        st = f"BSN{rng.randint(1, 300):04d}"
        folder = f"{season}/{st}"
        name = f"IMG_{i:07d}.MP4"
        rel = f"{folder}/{name}"
        dt = f"2025/{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d} {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00"
        r = rng.random()
        status = "verified" if r < 0.4 else ("auto" if r < 0.8 else "new")
        det = 1 if status != "new" else 0
        sp = rng.choice(SP)
        auto_json = json.dumps(dict(model="speciesnet", species=[dict(species=sp, confidence=0.9)])) if det else None
        agree = (1 if rng.random() < 0.8 else 3) if (det and status == "verified") else None
        items.append((rel, rel.lower(), name, "video", folder, 20_000_000, 1.7e9, season, st, 20.0, 1920, 1080, 30.0, 600, "h264", "aac", "mp4", dt, "metadata", 1, 0, status,
                      "tester" if status == "verified" else None, det, '{"animal": true}' if det else None, det, None, "low" if (det and rng.random() < 0.05) else None, 0, auto_json, agree, 0, 0, 1 if det else 0))
    c.executemany("INSERT INTO items (rel_path, rel_key, name, kind, folder, size, mtime, season, station, duration, width, height, fps, n_frames, vcodec, acodec, container, datetime, datetime_source, meta_state, missing, status, verified_by, det_state, det_json, cls_state, cls_json, flag, favorite, auto_json, agree, recheck, train_exclude, n_boxes) VALUES (" + ",".join("?" * 34) + ")", items)
    rows = c.execute("SELECT id, status, det_state, auto_json FROM items").fetchall()
    for r in rows:
        if r[1] == "verified":
            judg.append((r[0], rng.choice(SP[:9]), "manual"))
        if r[2]:
            auto.append((r[0], json.loads(r[3])["species"][0]["species"], 0.9, "speciesnet"))
            dets.append((r[0], 0.5, "animal", 0.9, 0.1, 0.1, 0.3, 0.3, json.loads(r[3])["species"][0]["species"], 0.9))
            dets.append((r[0], 1.0, "animal", 0.8, 0.2, 0.2, 0.3, 0.3, json.loads(r[3])["species"][0]["species"], 0.8))
    cols_j = [x[1] for x in c.execute("PRAGMA table_info(judgements)").fetchall()]
    cols_a = [x[1] for x in c.execute("PRAGMA table_info(auto_results)").fetchall()]
    cols_d = [x[1] for x in c.execute("PRAGMA table_info(detections)").fetchall()]
    print("judgements cols:", cols_j); print("auto_results cols:", cols_a); print("detections cols:", cols_d)
    c.executemany("INSERT INTO judgements (item_id, species, origin) VALUES (?,?,?)", judg)
    c.executemany("INSERT INTO auto_results (item_id, species, confidence, model) VALUES (?,?,?,?)", auto)
    c.executemany("INSERT INTO detections (item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES (?,?,?,?,?,?,?,?,?,?)", dets)
print(f"inserted {N} items, {len(judg)} judgements, {len(dets)} detections in {time.time() - t0:.1f}s; db size {os.path.getsize(os.path.join(ROOT, 'TrapNote', 'data.sqlite')) / 1e6:.0f} MB")

def tm(label, fn, n=3):
    best = 1e9
    for _ in range(n):
        t = time.time(); r = fn(); best = min(best, time.time() - t)
    print(f"{label:48s} {best * 1000:8.0f} ms")
    return r
tm("facets（絞り込みの候補と件数）", lambda: judge.facets(db))
tm("list_items 先頭 200 件（地点・日時順）", lambda: judge.list_items(db, {}, "station", False, 0, 200))
tm("list_items 25 万件目から 200 件", lambda: judge.list_items(db, {}, "station", False, 250000, 200))
tm("list_items 日時順 先頭", lambda: judge.list_items(db, {}, "datetime", True, 0, 200))
tm("件数（絞り込みなし）", lambda: db.scalar("SELECT COUNT(*) FROM items WHERE " + judge.build_where({})[0], tuple(judge.build_where({})[1])))
f = dict(station="BSN0012", status="verified")
w, pa = judge.build_where(f)
tm("件数（地点 + 確認済み）", lambda: db.scalar(f"SELECT COUNT(*) FROM items WHERE {w}", tuple(pa)))
tm("list_items（地点 + 確認済み）", lambda: judge.list_items(db, f, "datetime", False, 0, 200))
f2 = dict(species="deer")
w2, pa2 = judge.build_where(f2)
tm("件数（種で絞る）", lambda: db.scalar(f"SELECT COUNT(*) FROM items WHERE {w2}", tuple(pa2)))
tm("list_items（種で絞る）", lambda: judge.list_items(db, f2, "station", False, 0, 200))
f3 = dict(text="IMG_01234")
w3, pa3 = judge.build_where(f3)
tm("件数（名前で検索）", lambda: db.scalar(f"SELECT COUNT(*) FROM items WHERE {w3}", tuple(pa3)))
tm("list_ids 全部（全部選択用、上限 20 万）", lambda: judge.list_ids(db, {}, "station", False))
tm("plan_counts（追加学習の材料の数）", lambda: train.plan_counts(p))
tm("update_agree 全部", lambda: judge.update_agree(db), n=1)
tm("detail 1 件", lambda: judge.detail(db, 150000))
p.close()
