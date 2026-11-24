# 調べる前（prep_record.json）と比べて、元の 6 つのプロジェクトが変わっていないかを見る。読むだけ。
import json, os, sqlite3, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
rec = json.load(open(os.path.join(HERE, "prep_record.json"), encoding="utf-8"))
ok = True
for n, r in rec.items():
    src = f"F:/Copy/{n}/TrapNote"
    st = os.stat(src + "/data.sqlite")
    mt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
    con = sqlite3.connect("file:" + src + "/data.sqlite?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    jobs = [dict(x) for x in con.execute("SELECT id, kind, state, started_at FROM jobs ORDER BY id DESC LIMIT 3")]
    n_ver = con.execute("SELECT COUNT(*) FROM items WHERE status='verified' AND missing=0").fetchone()[0]
    con.close()
    nf = len([f for f in os.listdir(src + "/cache/features") if f.endswith(".npz")])
    pj = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(src + "/project.json")))
    newest = max(os.path.getmtime(os.path.join(src, "cache", "features", f)) for f in os.listdir(src + "/cache/features") if f.endswith(".npz"))
    same = (mt, st.st_size, n_ver, nf, jobs) == (r["db_mtime"], r["db_size"], r["n_verified"], r["n_npz"], r["jobs"])
    ok = ok and same
    print(n, "同じ" if same else "違う", "| data.sqlite", mt, st.st_size, "| project.json", pj, "| npz", nf, "いちばん新しい npz", time.strftime("%m-%d %H:%M:%S", time.localtime(newest)),
          "| 動いている処理", [j for j in jobs if j["state"] == "running"])
    models = sorted(os.listdir(src + "/models")) if os.path.isdir(src + "/models") else []
    print("    models:", models)
print("全部同じ" if ok else "違いがある")
