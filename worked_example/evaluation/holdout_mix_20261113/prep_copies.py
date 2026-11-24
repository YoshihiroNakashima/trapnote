# 材料の 6 つのプロジェクトの、小さなファイル（project.json・species.csv・fields.json）とデータベースの写しを、このフォルダの proj/ に作る。
# 元のプロジェクトは読むだけ（データベースは読み取り専用で開いて、SQLite の backup で写す）。特徴量（cache/features）は写さず、あとで元の場所から読む。
import json, os, shutil, sqlite3, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = ["First_200", "Empty_2000", "Empty_Second_4000", "Empty_Third_4000", "Second_500_2", "Third_500"]
rec = {}
for n in NAMES:
    src = f"F:/Copy/{n}/TrapNote"
    dst = os.path.join(HERE, "proj", n, "TrapNote")
    os.makedirs(dst, exist_ok=True)
    for fn in ("project.json", "species.csv", "fields.json"):
        if os.path.isfile(os.path.join(src, fn)):
            shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))
    dbp = os.path.join(dst, "data.sqlite")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(dbp + ext):
            os.remove(dbp + ext)
    st = os.stat(src + "/data.sqlite")
    con = sqlite3.connect("file:" + src + "/data.sqlite?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    jobs = [dict(r) for r in con.execute("SELECT id, kind, state, started_at FROM jobs ORDER BY id DESC LIMIT 3")]
    out = sqlite3.connect(dbp)
    con.backup(out)
    out.close()
    n_ver = con.execute("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0").fetchone()[0]
    n_ready = con.execute("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0 AND det_state=1 AND cls_state=1").fetchone()[0]
    con.close()
    nf = len([f for f in os.listdir(src + "/cache/features") if f.endswith(".npz")])
    rec[n] = dict(db_mtime=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)), db_size=st.st_size, n_verified=n_ver, n_ready=n_ready, n_npz=nf, jobs=jobs)
    print(n, rec[n]["db_mtime"], st.st_size, "確認済み", n_ver, "学習に使える", n_ready, "npz", nf)
    for j in jobs:
        print("    ", j)
json.dump(rec, open(os.path.join(HERE, "prep_record.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
