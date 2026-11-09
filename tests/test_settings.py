"""アプリの設定（config.Settings）の丸めと、明るさ（brightness）から補正（enhance）への読み替え"""
from __future__ import annotations


def test_settings_enhance(tmp_path, monkeypatch):
    """補正（enhance）は 0〜4 の整数に丸める。無いもの・壊れたものは 0（なし）"""
    from trapnote import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    assert config.Settings().get("enhance") == 0
    for raw, want in ((2, 2), (9, 4), (-1, 0), ("3", 3), ("x", 0), (None, 0)):
        config.write_json(f, {"enhance": raw})
        assert config.Settings().get("enhance") == want, raw
    s = config.Settings()
    assert s.update({"enhance": 4})["enhance"] == 4
    assert s.update({"enhance": 1})["enhance"] == 1
    assert "brightness" not in s.to_dict()


def test_settings_brightness_to_enhance(tmp_path, monkeypatch):
    """明るさ（以前の brightness）だけが保存されていれば補正の段に読み替える（×1.8 → 中）。補正が保存されていれば読み替えない"""
    from trapnote import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    for raw, want in ((1.0, 0), (0.5, 0), (1.2, 1), (1.5, 1), (1.8, 2), (2.0, 2), (2.5, 3), (3.0, 4), ("x", 0), (None, 0)):
        config.write_json(f, {"brightness": raw})
        assert config.Settings().get("enhance") == want, raw
    config.write_json(f, {"brightness": 3.0, "enhance": 1})
    assert config.Settings().get("enhance") == 1
    # 次に保存したら brightness は残らず、enhance が残る
    config.write_json(f, {"brightness": 1.8})
    s = config.Settings()
    s.update({"loop": True})
    saved = config.read_json(f, {})
    assert saved.get("enhance") == 2 and "brightness" not in saved
