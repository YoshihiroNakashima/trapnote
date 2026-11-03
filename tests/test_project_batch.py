"""別のプロジェクト（gold_station など。横断から「別のフォルダへ写す」で作ったもの）の動画を、カタログで探してその組として取り込む。
名前から 評価用・除く単位（station → 地点）を決め、判定はそのプロジェクトから写り、「除く組」で地点が母集団から外れる。取り込み済みのものは飛ばす"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote import cross  # noqa: E402
from trapnote.project import Project  # noqa: E402

from test_cross_study import make_nas, make_cross  # noqa: E402


def test_import_project_batch(tmp_path):
    root, rows = make_nas(tmp_path)
    a = make_cross(tmp_path)
    try:
        # 地点 BSN0812 の動画を gold として取り込み → 別のフォルダへ写す（gold_station）
        ids = [r["id"] for r in a.db.q("SELECT id FROM catalog WHERE station='BSN0812' ORDER BY id")]
        assert len(ids) == 16
        cross.catalog_import(a, ids, "gold")
        gold_dir = str(tmp_path / "Copy" / "gold_station")
        cross.export_subset(a, [r["id"] for r in a.db.q("SELECT id FROM items")], gold_dir, copy_videos_too=False)
    finally:
        a.close()
    g = Project(os.path.join(gold_dir, "TrapNote"))
    try:                                                              # gold 側で 2 本を目視で確定
        from trapnote.judge import save as save_judgement
        gids = [r["id"] for r in g.db.q("SELECT id FROM items ORDER BY id LIMIT 2")]
        for gid in gids:
            save_judgement(g.db, gid, dict(species=["deer"]), "me")
        assert g.db.scalar("SELECT COUNT(*) FROM items WHERE status='verified'") == 2
        rels = {r[0] for r in g.db.q("SELECT rel_path FROM items")}
    finally:
        g.close()
    assert all(r.startswith("PRM_N/") for r in rels)
    # 新しい横断プロジェクト（学習用）に同じ参照先・カタログ
    place = tmp_path / "Studies" / "train"
    place.mkdir(parents=True)
    b = Project(str(place / "TrapNote"), root=str(place), create=True, kind="cross")
    try:
        cross.add_sources_under(b, root)
        cross.build_catalog(b)
        with pytest.raises(ValueError):
            cross.import_project_batch(b, str(tmp_path / "Studies" / "gold"))      # 横断は組にできない
        with pytest.raises(ValueError):
            cross.import_project_batch(b, str(tmp_path / "nowhere"))
        m = cross.match_project_items(b, os.path.join(gold_dir, "TrapNote"))
        assert m["n_items"] == 16 and len(m["rows"]) == 16 and not m["unmatched"] and not m["already"]
        r = cross.import_project_batch(b, gold_dir)
        print(r)
        assert r["n"] == 16 and r["batch"] == "gold_station" and r["unit"] == "station" and r["eval"] is True and r["n_unmatched"] == 0
        assert cross.eval_batches(b) == ["gold_station"]
        assert b.db.scalar("SELECT COUNT(*) FROM items WHERE batch='gold_station'") == 16
        assert b.db.scalar("SELECT COUNT(*) FROM items WHERE batch='gold_station' AND status='verified'") == 2     # 判定が写る
        assert b.db.scalar("SELECT COUNT(*) FROM catalog WHERE item_id IS NOT NULL") == 16                        # カタログに取り込み済みの印
        assert {r[0] for r in b.db.q("SELECT rel_path FROM items")} == rels                                        # 相対パスは元の横断と同じ形
        assert b.db.scalar("SELECT COUNT(*) FROM items WHERE src_dir IS NOT NULL AND src_id IS NOT NULL") == 16    # 書き戻し・再取得の相手は gold プロジェクト
        # 除く組: 地点を丸ごと
        bu = cross.batch_units(b, ["gold_station"])
        assert bu["stations"] == {"BSN0812"} and not bu["seasons"] and not bu["cells"]
        assert cross.catalog_counts(b, dict(exclude_batches=["gold_station"]))["total"] == 48 - 16
        # もう一度 → 取り込み済みで飛ばす
        r2 = cross.import_project_batch(b, gold_dir, batch="again")
        assert r2["n"] == 0 and r2["n_already"] == 16 and r2["already"] == {"gold_station": 16}
        assert "again" not in cross.eval_batches(b)
        # 調査回のフォルダを 1 つのプロジェクトとして（rel が 地点/ファイル のとき: 調査回/rel で探す）
        sp = os.path.join(root, "N240415")
        sproj = Project(os.path.join(sp, "TrapNote"), root=sp, create=True)
        try:
            from trapnote import scan
            scan.register(sproj, scan.walk_media(sp, sproj.setting("skip_names")))
            assert sproj.db.scalar("SELECT COUNT(*) FROM items") == 24
        finally:
            sproj.close()
        r3 = cross.import_project_batch(b, sp, batch="season_gold", unit="season", make_eval=False)
        print(r3)
        assert r3["n"] == 24 - 8 and r3["n_already"] == 8 and r3["unit"] == "season" and r3["eval"] is False
        assert "season_gold" not in cross.eval_batches(b)
        assert cross.batch_units(b, ["season_gold"])["seasons"] == {"N240415"}
    finally:
        b.close()
    # 複数のプロジェクトを並べて 1 回で（4 の段の A）: gold_station と 調査回 N240415 と 存在しないフォルダ。失敗した分は残りを止めず error に
    place2 = tmp_path / "Studies" / "train2"
    place2.mkdir(parents=True)
    c = Project(str(place2 / "TrapNote"), root=str(place2), create=True, kind="cross")
    try:
        cross.add_sources_under(c, root)
        cross.build_catalog(c)
        msgs = []
        r = cross.import_project_batches(c, [dict(path=gold_dir), dict(path=sp, batch="", unit="", eval=None), dict(path=str(tmp_path / "nowhere"))],
                                         progress=lambda a, b_, m: msgs.append(m))
        print(r)
        assert r["n_ok"] == 2 and r["n_failed"] == 1 and r["n"] == 16 + (24 - 8) and r["batch"] == ""        # 複数なら一覧に出す組は決めない
        assert [x.get("batch") for x in r["results"]] == ["gold_station", "N240415", None]
        assert r["results"][0]["eval"] is True and r["results"][1]["eval"] is False and r["results"][1]["n_already"] == 8
        assert "プロジェクトが見つかりません" in r["results"][2]["error"]
        assert cross.eval_batches(c) == ["gold_station"]
        assert c.db.scalar("SELECT COUNT(*) FROM items WHERE batch='N240415'") == 16
        assert any(m.startswith("2 / 3: ") for m in msgs)
        msg = cross.project_batch_message(r)
        assert "gold_station → 組 gold_station: 16 本のうち 16 件" in msg and "評価用にしました" in msg and "N240415 → 組 N240415: 24 本のうち 16 件" in msg and "nowhere" in msg
        # 1 つだけなら、その組を一覧に出す（batch）。全部取り込み済みでも組の名前は返る
        r1 = cross.import_project_batches(c, [dict(path=gold_dir, batch="again")])
        assert r1["n"] == 0 and r1["n_ok"] == 1 and r1["batch"] == "again" and r1["n_already"] == 16
        assert cross.project_batch_message(dict(results=[])) == "取り込むプロジェクトがありません"
    finally:
        c.close()
