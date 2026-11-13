"""横断の 4 の段「動画を選ぶ」まわり: 同じ動画を 2 回取り込まない（印が外れていても相対パスで見る。何も取り込まなければ記録も残さない）、
取り込み済みの本数（n_already）、組を消すと取り込みの記録も消える（drop_log）、取り込みの記録だけを消す、目録から動画ファイルだけを書き出す（取り込まない）"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from test_cross_study import make_cross, make_nas  # noqa: E402

from trapnote import cross, files_out  # noqa: E402


def test_import_twice_is_skipped(tmp_path):
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        total = int(p.db.scalar("SELECT COUNT(*) FROM catalog"))
        r = cross.catalog_sample(p, {}, dict(unit="station", n=1, seed=1))
        assert r["n"] and r["n_already"] == 0
        i1 = cross.catalog_import(p, r["ids"], "gold", record=dict(unit="station"))
        assert i1["n"] == r["n"] and i1["n_already"] == 0
        n_log = len(p.data["imports"])
        # 同じ行をもう一度（「取り込む」を 2 回押した・同じ乱数の種で選び直した）: 全部飛ばし、記録は増えない
        i2 = cross.catalog_import(p, r["ids"], "gold2", record=dict(unit="station"))
        assert i2["n"] == 0 and i2["n_already"] == r["n"] and len(p.data["imports"]) == n_log
        assert int(p.db.scalar("SELECT COUNT(*) FROM items")) == r["n"]
        # 目録の印が外れていても（作り直した直後など）、同じ相対パスの対象があれば飛ばし、印を付け直す
        p.db.x("UPDATE catalog SET item_id=NULL")
        i3 = cross.catalog_import(p, r["ids"], "gold3", record={})
        assert i3["n"] == 0 and i3["n_already"] == r["n"]
        assert int(p.db.scalar("SELECT COUNT(*) FROM catalog WHERE item_id IS NOT NULL")) == r["n"]
        assert int(p.db.scalar("SELECT COUNT(*) FROM (SELECT rel_path FROM items GROUP BY rel_path HAVING COUNT(*) > 1)")) == 0
        # 数えるとき: 取り込み済みは母集団から除き、除いた本数が出る。exclude_imported=False なら全部
        s2 = cross.catalog_sample(p, {}, dict(unit="all"))
        assert s2["n"] == total - r["n"] and s2["n_already"] == r["n"]
        s3 = cross.catalog_sample(p, {}, dict(unit="all"), exclude_imported=False)
        assert s3["n"] == total and s3["n_already"] == 0
        s4 = cross.catalog_sample(p, {"imported": "1"}, dict(unit="all"), exclude_imported=False)
        assert s4["n"] == r["n"]
        # 組を消す（取り込みの記録も）
        rr = cross.remove_items(p, batch="gold", drop_log=True)
        assert rr["n"] == r["n"] and int(p.db.scalar("SELECT COUNT(*) FROM items")) == 0
        assert not any((rec.get("batch") or "") == "gold" for rec in p.data["imports"])
        assert not any((rec.get("params") or {}).get("mode") == "remove" for rec in p.data["imports"])
        assert int(p.db.scalar("SELECT COUNT(*) FROM catalog WHERE item_id IS NOT NULL")) == 0
    finally:
        p.close()


def test_remove_import_records(tmp_path):
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        r = cross.catalog_sample(p, {}, dict(unit="station", n=2, seed=3))
        cross.catalog_import(p, r["ids"][:4], "a", record={})
        cross.catalog_import(p, r["ids"][4:8], "b", record={})
        cross.catalog_import(p, r["ids"][8:], "a", record={})
        recs = p.data["imports"]
        assert [x["batch"] for x in recs] == ["a", "b", "a"]
        assert cross.remove_import_records(p, index=1, at="wrong") == dict(n=0, n_left=3)      # 日時が合わなければ消さない（画面が古い）
        assert cross.remove_import_records(p, index=1, at=recs[1]["at"]) == dict(n=1, n_left=2)
        assert [x["batch"] for x in p.data["imports"]] == ["a", "a"]
        assert cross.remove_import_records(p, batch="a")["n_left"] == 0
        assert int(p.db.scalar("SELECT COUNT(*) FROM items")) == r["n"]                 # 記録を消しても動画・組はそのまま
        assert [b["batch"] for b in cross.batches_info(p)] == ["a", "b"]
    finally:
        p.close()


def test_catalog_files_out(tmp_path):
    """目録から選んだ動画のファイルだけを書き出す（取り込まない）: 階層のまま <参照先>/<調査回>/<地点>/<ファイル>、たたむと 参照先_調査回_地点_ファイル"""
    make_nas(tmp_path)
    p = make_cross(tmp_path)
    try:
        r = cross.catalog_sample(p, {"station": "BSN0812"}, dict(unit="video", n=3, seed=1))
        assert r["n"] == 3
        rows = cross.catalog_files(p, r["ids"])
        assert len(rows) == 3 and all(os.path.isfile(x["abs"]) for x in rows) and all(x["kind"] == "video" for x in rows)
        assert all(x["rel_path"].startswith("PRM_N/") and "/BSN0812/" in x["rel_path"] for x in rows)
        out = str(tmp_path / "out_tree")
        res = files_out.export_files(p, [], out, keep_tree=True, rows=rows)
        assert res["n"] == 3 and res["n_missing"] == 0
        for x in rows:
            assert os.path.isfile(os.path.join(out, *x["rel_path"].split("/")))
        out2 = str(tmp_path / "out_flat")
        res2 = files_out.export_files(p, [], out2, keep_tree=False, rows=rows)
        assert res2["n"] == 3
        names = sorted(x for x in os.listdir(out2) if x != files_out.LIST_NAME)        # 一覧の CSV（video_list.csv）は必ず付く（確認事項 129 回目）
        assert os.path.isfile(os.path.join(out2, files_out.LIST_NAME))
        assert all(n.startswith("PRM_N_N24") and "_BSN0812_IMG_" in n for n in names), names
        assert int(p.db.scalar("SELECT COUNT(*) FROM items")) == 0                     # 取り込んでいない
    finally:
        p.close()
