"""古い版（スキーマ v10: catalog に labels 列がない）の data.sqlite を開けるか（v11 への移行）"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote.db import Database  # noqa: E402


def test_open_v10_catalog(tmp_path):
    path = str(tmp_path / "data.sqlite")
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO meta VALUES ('schema_version', '10');
    CREATE TABLE catalog (id INTEGER PRIMARY KEY, source TEXT NOT NULL, src_id INTEGER, rel TEXT NOT NULL, season TEXT, station TEXT, name TEXT NOT NULL,
      datetime TEXT, status TEXT, species TEXT, ai TEXT, agree INTEGER, det_state INTEGER, duration REAL, size INTEGER, item_id INTEGER, UNIQUE(source, rel));
    INSERT INTO catalog(source, rel, name, species) VALUES ('PRM_N', 'N240128/BSN0812/IMG_0001.AVI', 'IMG_0001.AVI', 'deer');
    """)
    c.commit(); c.close()
    db = Database(path)
    cols = {r["name"] for r in db.q("PRAGMA table_info(catalog)")}
    assert "labels" in cols
    assert db.get_meta("schema_version") == "11"
    assert db.q1("SELECT species, labels FROM catalog")["species"] == "deer"
    assert any(r["name"] == "idx_cat_labels" for r in db.q("PRAGMA index_list(catalog)"))
    db.close()
