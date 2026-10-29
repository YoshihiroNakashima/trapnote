"""TrapNote: カメラトラップのデータ管理アプリ（サーバー側）

areacheck（有効範囲の確認）と同じ作り: Python の aiohttp サーバーがローカルで待ち受け、ブラウザに画面を出す。
"""
APP_NAME = "TrapNote"
APP_ID = "trapnote"
VERSION = "0.3.0"
SCHEMA_VERSION = 10         # data.sqlite の版。上げたときは db.migrate() に移行を書く


def _build_stamp():
    """プログラムの更新時刻（trapnote/*.py、app.py、static/* の最新）。起動時に動いているサーバーと比べ、
    違えば止めて起動し直す（版の数字を変え忘れても古いサーバーが残らないように。areacheck と同じ）"""
    import glob as _glob
    import os as _os
    import time as _time
    here = _os.path.dirname(_os.path.abspath(__file__))
    app = _os.path.dirname(here)
    files = (_glob.glob(_os.path.join(here, "*.py")) + [_os.path.join(app, "app.py")]
             + _glob.glob(_os.path.join(app, "static", "*")) + _glob.glob(_os.path.join(app, "resources", "*")))
    try:
        t = max(_os.path.getmtime(f) for f in files if _os.path.isfile(f))
    except (ValueError, OSError):
        return "0", ""
    return str(int(t)), _time.strftime("%Y-%m-%d %H:%M", _time.localtime(t))


BUILD, BUILD_TIME = _build_stamp()
