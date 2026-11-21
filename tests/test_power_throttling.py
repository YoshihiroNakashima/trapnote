"""Windows の電力の絞り込み（Power Throttling）から、サーバーのプロセスを外す（確認事項 151 回目）:
paths.no_power_throttling が「絞らない」の指定にすること、Windows 以外では何もしないこと、設定 full_speed の既定（True）と False が残ること、
app.py が、設定 full_speed が True のときだけ呼ぶこと"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from trapnote import paths  # noqa: E402

WIN = sys.platform.startswith("win")


def _child(code: str) -> str:
    """別のプロセスで流す（試験のプロセス自身の指定を変えないため。指定は子プロセスに引き継がれないので、子はいつも「Windows まかせ」から始まる）"""
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.skipif(not WIN, reason="Windows だけ")
def test_no_power_throttling_sets_no_throttle():
    out = _child("from trapnote import paths; print(paths.power_throttling_state(), paths.no_power_throttling(), paths.power_throttling_state())")
    if "None" in out:
        pytest.skip("この Windows は ProcessPowerThrottling を読めない")
    assert out == "(0, 0) True (1, 0)"        # 新しいプロセスは Windows まかせ → 外したあとは「絞らない」（ControlMask だけが立つ）


@pytest.mark.skipif(not WIN, reason="Windows だけ")
def test_power_throttling_not_inherited_by_child():
    """子プロセス（ffmpeg など）には引き継がれない = 子は、これまでどおり Windows まかせ"""
    code = ("import subprocess, sys\n"
            "from trapnote import paths\n"
            "paths.no_power_throttling()\n"
            "r = subprocess.run([sys.executable, '-c', 'from trapnote import paths; print(paths.power_throttling_state())'], capture_output=True, text=True)\n"
            "print(paths.power_throttling_state(), r.stdout.strip())\n")
    out = _child(code)
    if "None" in out:
        pytest.skip("この Windows は ProcessPowerThrottling を読めない")
    assert out == "(1, 0) (0, 0)"


@pytest.mark.skipif(WIN, reason="Windows 以外")
def test_no_power_throttling_is_noop_elsewhere():
    assert paths.no_power_throttling() is False
    assert paths.power_throttling_state() is None


def test_full_speed_setting(tmp_path, monkeypatch):
    """設定 full_speed: 既定は True。False にすれば残る（Windows まかせに戻すための切り替え）。壊れた値は真偽に丸める"""
    from trapnote import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    assert config.DEFAULTS["full_speed"] is True
    assert config.Settings().get("full_speed") is True
    config.write_json(f, {"full_speed": False})
    assert config.Settings().get("full_speed") is False
    s = config.Settings()
    assert s.update({"full_speed": True})["full_speed"] is True
    assert config.read_json(f, {}).get("full_speed") is True
    config.write_json(f, {"full_speed": 0})
    assert config.Settings().get("full_speed") is False


def test_app_calls_it_only_when_full_speed():
    """app.py は、サーバーを起動する前に、設定 full_speed が True のときだけ外す（ショートカットを作るだけのときは呼ばない = その分岐より後ろにある）"""
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    i_short = src.index("return make_shortcut(ed)")
    i_if = src.index('if session.settings.get("full_speed", True):')
    i_call = src.index("no_power_throttling():")
    i_serve = src.index("asyncio.run(serve(")
    assert i_short < i_if < i_call < i_serve
