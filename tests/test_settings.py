"""アプリの設定（config.Settings）の丸め"""
from __future__ import annotations


def test_settings_brightness(tmp_path, monkeypatch):
    """明るさ（brightness）は 0.5〜3.0 に丸める。無いもの・壊れたものは 1.0"""
    from trapnote import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    assert config.Settings().get("brightness") == 1.0
    for raw, want in ((2.5, 2.5), (9, 3.0), (0.1, 0.5), ("1.7", 1.7), ("x", 1.0), (None, 1.0)):
        config.write_json(f, {"brightness": raw})
        assert config.Settings().get("brightness") == want, raw
    s = config.Settings()
    assert s.update({"brightness": 4})["brightness"] == 3.0
    assert s.update({"brightness": 0.8})["brightness"] == 0.8
