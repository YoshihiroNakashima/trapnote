"""Camtrap DP（Camera Trap Data Package）形式への書き出し（確認事項 140 回目）

Camtrap DP は、カメラトラップのデータをやり取りするための標準の形式（TDWG。https://camtrap-dp.tdwg.org）。
1 つのフォルダに、datapackage.json（データの説明）と 3 つの表（deployments.csv = カメラの設置、media.csv = 動画・静止画、observations.csv = 観察）を書く。
版は 1.0.2（2025-06 公開）。表の列の定義（名前・型・制約）は、公式の table schema から写したもので、順番も同じ
（TABLES。tests/test_camtrapdp.py が、2026-11-16 に公式の定義から取った照合値と比べる）。

TrapNote の記録との対応
  deployments  1 行 = 調査回 × 地点（deploymentID = <調査回>_<地点>。調査回がなければ地点だけ。地点を選んでいないプロジェクトでは、地点の代わりにフォルダ）。
               緯度・経度は、地点の表（<プロジェクト>/stations.csv。書き出しの窓で CSV から読み込む）から。
               開始・終了は、地点の表にあればそれ。なければ、その地点の最初と最後の動画の日時（そのことを deploymentComments に書く）
  media        1 行 = 動画・静止画 1 つ。mediaID = 相対パス、timestamp = 撮影日時（時計のずれを直していれば、直したあと）＋ 時差、
               filePath = 動画のフォルダ（ルート）からの相対パス、filePublic = false
  observations 1 行 = 動画 1 本 × 種 1 つ（observationLevel = event。動画 1 本を 1 つの event とみなす。eventID = mediaID、eventStart = 撮影日時、eventEnd = ＋ 動画の長さ）。
               種 → animal ＋ scientificName（種リストの学名）、動物なし → blank、人 → human、車 → vehicle、不明 → unknown、
               有効範囲 → human ＋ cameraSetupType = calibration。判定のない動画 → unclassified（要確認の印があれば unknown）。
               確認済み・CSV 由来の判定 → classificationMethod = human、AI の判定のまま → machine（確からしさを classificationProbability に）。
               追加項目の 個体数・性別・行動・成長段階・個体 ID は count・sex・behavior・lifeStage・individualID へ（_role。キーか名前で見分ける）、
               ほかの項目は observationTags（key:value）へ、自由記述は comments へ
  枠（bbox）は書かない（枠は YOLO 形式か MegaDetector の JSON で書き出す）
  datapackage.json の description（説明）と licenses（データと、動画・静止画のライセンス）は任意。窓で入れたときだけ書く（確認事項 141 回目。
               R のパッケージ camtrapdp の write_eml() = GBIF 向けの説明のファイルを作る関数は、説明とデータのライセンスがないと止まる）

決まり（確認事項 148 回目。CRAN の R2camtrapdp と比べて足したもの）
  必須の項目が足りなければ、書き出さない  書き出す前に precheck() で調べ、足りない項目（作成者、調査地点の決め方、撮影の方法、地点の緯度・経度 など）を
               どの項目か・どの地点かまで並べて返す。1 つでもあれば、ファイルは 1 つも書かない（前は、書いてから「足りないもの」を知らせていた）。
               書いたものは、置く前に定義と照合し（check_package）、合わなければ置かない
  observationID は必ず 1 行に 1 つ  observationID = <mediaID>#<種のコード>。1 本の動画に 2 種が写っていれば 2 行で、ID は別（種のコードが違う）。
               個体数・性別・成長段階・個体 ID を ＋ で何件も記録してあれば、記録ごとに 1 行（<mediaID>#<種のコード>#<番号>。Camtrap DP は、
               性別や成長段階が違う個体を別の行にする）。R2camtrapdp は、ID を動画ごとに付けると 2 種の動画で ID が重なり、表を作れない。ここでは重ならない
  値の中の改行は空白にする  メモなどに改行があると、行ごとに読む道具や表計算ソフトで行がずれる
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta

from . import VERSION, media
from .export import _iter_items, _sniff
from .judge import build_where
from .species import read_text_any

log = logging.getLogger(__name__)

DP_VERSION = "1.0.2"
BASE_URL = f"https://raw.githubusercontent.com/tdwg/camtrap-dp/{DP_VERSION}/"
MISSING = ("", "NA", "NaN", "nan")
SAMPLING = ("simpleRandom", "systematicRandom", "clusteredRandom", "experimental", "targeted", "opportunistic")
CAPTURE = ("activityDetection", "timeLapse")
LICENSES = {       # 窓で選べるライセンス（GBIF が受け付ける 3 つ）: name → (title, path)
    "CC0-1.0": ("Creative Commons Zero v1.0 Universal", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "CC-BY-4.0": ("Creative Commons Attribution 4.0 International", "https://creativecommons.org/licenses/by/4.0/"),
    "CC-BY-NC-4.0": ("Creative Commons Attribution Non Commercial 4.0 International", "https://creativecommons.org/licenses/by-nc/4.0/"),
}
_DT_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})$")
_TZ_RE = re.compile(r"^(Z|[+-]\d{2}:\d{2})$")


def _f(name: str, typ: str, fmt: str | None = None, required: bool = False, unique: bool = False, enum=None, pattern: str | None = None,
       minimum=None, maximum=None) -> dict:
    """列の定義 1 つ（公式の table schema の name・type・format と constraints を、この順に持つ）"""
    d: dict = dict(name=name, type=typ)
    if fmt:
        d["format"] = fmt
    if required:
        d["required"] = True
    if unique:
        d["unique"] = True
    if enum:
        d["enum"] = list(enum)
    if pattern:
        d["pattern"] = pattern
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


TABLES: dict[str, list[dict]] = {
    "deployments": [
        _f("deploymentID", "string", required=True, unique=True),
        _f("locationID", "string"),
        _f("locationName", "string"),
        _f("latitude", "number", required=True, minimum=-90, maximum=90),
        _f("longitude", "number", required=True, minimum=-180, maximum=180),
        _f("coordinateUncertainty", "integer", minimum=1),
        _f("deploymentStart", "datetime", _DT_FORMAT, required=True),
        _f("deploymentEnd", "datetime", _DT_FORMAT, required=True),
        _f("setupBy", "string"),
        _f("cameraID", "string"),
        _f("cameraModel", "string"),
        _f("cameraDelay", "integer", minimum=0),
        _f("cameraHeight", "number", minimum=0),
        _f("cameraDepth", "number", minimum=0),
        _f("cameraTilt", "integer", minimum=-90, maximum=90),
        _f("cameraHeading", "integer", minimum=0, maximum=360),
        _f("detectionDistance", "number", minimum=0),
        _f("timestampIssues", "boolean"),
        _f("baitUse", "boolean"),
        _f("featureType", "string", enum=("roadPaved", "roadDirt", "trailHiking", "trailGame", "roadUnderpass", "roadOverpass", "roadBridge", "culvert", "burrow",
                                         "nestSite", "carcass", "waterSource", "fruitingTree")),
        _f("habitat", "string"),
        _f("deploymentGroups", "string"),
        _f("deploymentTags", "string"),
        _f("deploymentComments", "string"),
    ],
    "media": [
        _f("mediaID", "string", required=True, unique=True),
        _f("deploymentID", "string", required=True),
        _f("captureMethod", "string", enum=CAPTURE),
        _f("timestamp", "datetime", _DT_FORMAT, required=True),
        _f("filePath", "string", required=True, pattern=r"^(?=^[^./~])(^((?!\.{2}).)*$).*$"),
        _f("filePublic", "boolean", required=True),
        _f("fileName", "string"),
        _f("fileMediatype", "string", required=True, pattern=r"^(image|video|audio)/.*$"),
        _f("exifData", "any"),
        _f("favorite", "boolean"),
        _f("mediaComments", "string"),
    ],
    "observations": [
        _f("observationID", "string", required=True, unique=True),
        _f("deploymentID", "string", required=True),
        _f("mediaID", "string"),
        _f("eventID", "string"),
        _f("eventStart", "datetime", _DT_FORMAT, required=True),
        _f("eventEnd", "datetime", _DT_FORMAT, required=True),
        _f("observationLevel", "string", required=True, enum=("media", "event")),
        _f("observationType", "string", required=True, enum=("animal", "human", "vehicle", "blank", "unknown", "unclassified")),
        _f("cameraSetupType", "string", enum=("setup", "calibration")),
        _f("scientificName", "string"),
        _f("count", "integer", minimum=1),
        _f("lifeStage", "string", enum=("adult", "subadult", "juvenile")),
        _f("sex", "string", enum=("female", "male")),
        _f("behavior", "string"),
        _f("individualID", "string"),
        _f("individualPositionRadius", "number", minimum=0),
        _f("individualPositionAngle", "number", minimum=-90, maximum=90),
        _f("individualSpeed", "number", minimum=0),
        _f("bboxX", "number", minimum=0, maximum=1),
        _f("bboxY", "number", minimum=0, maximum=1),
        _f("bboxWidth", "number", minimum=1e-15, maximum=1),
        _f("bboxHeight", "number", minimum=1e-15, maximum=1),
        _f("classificationMethod", "string", enum=("human", "machine")),
        _f("classifiedBy", "string"),
        _f("classificationTimestamp", "datetime", _DT_FORMAT),
        _f("classificationProbability", "number", minimum=0, maximum=1),
        _f("observationTags", "string"),
        _f("observationComments", "string"),
    ],
}
FOREIGN_KEYS = {"media": [("deploymentID", "deployments", "deploymentID")],
                "observations": [("deploymentID", "deployments", "deploymentID"), ("mediaID", "media", "mediaID")]}
COLS = {t: [f["name"] for f in fs] for t, fs in TABLES.items()}


def schema_digest(table: str) -> str:
    """表の定義の照合値（公式の table schema の fields から name・type・format・constraints を同じ順に取り出した JSON の SHA-256。試験で公式のものと比べる）"""
    return hashlib.sha256(json.dumps(TABLES[table], separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


MEDIATYPES = {".avi": "video/x-msvideo", ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".m4v": "video/x-m4v", ".mpg": "video/mpeg",
              ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv", ".mts": "video/mp2t", ".m2ts": "video/mp2t", ".3gp": "video/3gpp", ".webm": "video/webm",
              ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
RANKS = {"種": "species", "亜種": "subspecies", "属": "genus", "科": "family", "目": "order", "綱": "class", "門": "phylum", "界": "kingdom"}
SEX = {"オス": "male", "雄": "male", "♂": "male", "male": "male", "m": "male", "メス": "female", "雌": "female", "♀": "female", "female": "female", "f": "female"}
SEX_UNKNOWN = ("不明", "unknown", "?", "？", "-", "ー", "na", "n/a")       # 性別が分からない、という値。Camtrap DP では sex を空にする（タグにも残さない）
LIFE_STAGE = {"adult": "adult", "成獣": "adult", "成体": "adult", "成鳥": "adult", "おとな": "adult", "大人": "adult",
              "subadult": "subadult", "sub-adult": "subadult", "亜成獣": "subadult", "亜成体": "subadult", "若獣": "subadult", "若鳥": "subadult",
              "juvenile": "juvenile", "幼獣": "juvenile", "幼体": "juvenile", "幼鳥": "juvenile", "子": "juvenile", "仔": "juvenile", "子ども": "juvenile", "こども": "juvenile"}
# 追加項目 → observations の列。項目のキーか名前（日本語・英語。大文字小文字、空白、_ は見ない）で見分ける
_ROLE_NAMES = {"count": ("count", "個体数", "頭数"),
               "sex": ("sex", "性別", "雌雄"),
               "behavior": ("behaviour", "behavior", "行動"),
               "lifeStage": ("lifestage", "age", "ageclass", "成長段階", "齢", "齢クラス", "齢区分", "年齢", "ライフステージ"),
               "individualID": ("individualid", "individual", "個体id", "個体識別", "個体名", "個体番号")}
_ROLE_OF = {n: role for role, names in _ROLE_NAMES.items() for n in names}
_PATH_RE = re.compile(r"^(?=^[^./~])(^((?!\.{2}).)*$).*$")        # media.filePath の決まり（. / ~ で始まらない、.. を含まない）


# ---------- 日時と時差 ----------
def local_offset() -> str:
    """この PC の時差（+09:00 の形）"""
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    sec = int(off.total_seconds())
    sign = "+" if sec >= 0 else "-"
    sec = abs(sec)
    return f"{sign}{sec // 3600:02d}:{sec % 3600 // 60:02d}"


def norm_offset(tz: str | None) -> str | None:
    """時差の書き方をそろえる（+9、+0900、+09:00、UTC、Z → +09:00 か Z）。読めなければ None"""
    s = (tz or "").strip().upper().replace("UTC", "").replace("GMT", "").strip()
    if s in ("", "Z"):
        return "Z" if (tz or "").strip() else None
    m = re.match(r"^([+-])(\d{1,2})(?::?(\d{2}))?$", s)
    if not m:
        return None
    hh, mm = int(m.group(2)), int(m.group(3) or 0)
    if hh > 14 or mm > 59:
        return None
    return f"{m.group(1)}{hh:02d}:{mm:02d}"


def iso(dt: str | None, tz: str, add_sec: float = 0.0) -> str | None:
    """TrapNote の日時（YYYY/MM/DD HH:MM:SS）→ Camtrap DP の日時（YYYY-MM-DDThh:mm:ss＋時差）。add_sec を足せる（動画の終わり）"""
    if not dt:
        return None
    try:
        d = datetime.strptime(dt, media.DT_FMT)
    except ValueError:
        return None
    if add_sec:
        d += timedelta(seconds=int(round(add_sec)))
    return d.strftime("%Y-%m-%dT%H:%M:%S") + tz


def _stamp(s: str | None) -> str:
    """記録の時刻（verified_at など。ISO で時差つきのはず）を Camtrap DP の形に。合わなければ空"""
    s = (s or "").strip()
    if _DT_RE.match(s):
        return s
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})$", s)
    return m.group(1) + m.group(2) if m else ""


# ---------- 地点の表（座標など） ----------
STATION_COLS = ["Season", "Station", "Latitude", "Longitude", "Start", "End"]
PASS_COLS = ["coordinateUncertainty", "setupBy", "cameraID", "cameraModel", "cameraDelay", "cameraHeight", "cameraDepth", "cameraTilt", "cameraHeading",
             "detectionDistance", "baitUse", "featureType", "habitat", "deploymentTags", "deploymentComments"]
_SYN = {"Season": ("season", "調査回", "round", "surveyround", "session"),
        "Station": ("station", "地点", "地点名", "stationid", "stationname", "locationname", "locationid", "location"),
        "Latitude": ("latitude", "lat", "緯度", "decimallatitude"),
        "Longitude": ("longitude", "lon", "lng", "long", "経度", "decimallongitude"),
        "Start": ("start", "deploymentstart", "設置", "設置日", "設置日時", "開始", "開始日", "開始日時", "setup", "setupdate"),
        "End": ("end", "deploymentend", "回収", "回収日", "回収日時", "終了", "終了日", "終了日時", "retrieval", "pickup")}
_HEAD = {s: canon for canon, syns in _SYN.items() for s in syns}
_HEAD.update({c.lower(): c for c in PASS_COLS})


def _hkey(h: str) -> str:
    return re.sub(r"[\s_\-()（）]+", "", (h or "").strip().lower())


def stations_path(project) -> str:
    return os.path.join(project.dir, "stations.csv")


def load_stations(project) -> list[dict]:
    """プロジェクトに保存してある地点の表（なければ空）"""
    p = stations_path(project)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except (OSError, csv.Error) as e:
        log.warning("地点の表を読めません %s: %s", p, e)
        return []


def _station_index(rows: list[dict]) -> dict[tuple, dict]:
    return {((r.get("Season") or "").strip().lower(), (r.get("Station") or "").strip().lower()): r for r in rows if (r.get("Station") or "").strip()}


def _station_row(idx: dict[tuple, dict], season: str, loc: str) -> dict:
    return idx.get((season.lower(), loc.lower())) or idx.get(("", loc.lower())) or {}


def _num(v) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _coords(row: dict) -> tuple[float, float] | None:
    la, lo = _num(row.get("Latitude")), _num(row.get("Longitude"))
    if la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def import_stations(project, path: str) -> dict:
    """地点の表の CSV（地点、緯度、経度。あれば 調査回、設置と回収の日時、Camtrap DP の deployments の列）を読んで、プロジェクトに保存する（前のものは置き換える）。
    列の名前は、日本語・英語・Camtrap DP の名前のどれでもよい（大文字小文字、空白、_ は見ない）"""
    text = read_text_any(path)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=_sniff(text)))
    out, bad = [], []
    for n, r in enumerate(rows, start=2):
        d = {c: "" for c in STATION_COLS + PASS_COLS}
        for k, v in r.items():
            c = _HEAD.get(_hkey(k))
            if c and (v or "").strip():
                d[c] = v.strip()
        if not d["Station"]:
            if any((v or "").strip() for v in r.values()):
                bad.append(dict(line=n, why="station"))
            continue
        if (d["Latitude"] or d["Longitude"]) and _coords(d) is None:
            bad.append(dict(line=n, station=d["Station"], why="coords"))
            d["Latitude"] = d["Longitude"] = ""
        for c in ("Start", "End"):
            if d[c]:
                p = media.parse_dt_any(d[c])
                if p is None:
                    bad.append(dict(line=n, station=d["Station"], why=c.lower()))
                    d[c] = ""
                else:
                    d[c] = p[:10] + " 23:59:59" if c == "End" and re.match(r"^\d{4}\D\d{1,2}\D\d{1,2}$", d[c]) else p     # 日付だけの回収日は、その日の終わりまで
        out.append(d)
    if not out:
        raise ValueError("地点の列（Station / 地点）のある行がありません")
    tmp = stations_path(project) + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=STATION_COLS + PASS_COLS)
        w.writeheader()
        w.writerows(out)
    os.replace(tmp, stations_path(project))
    res = info(project)
    res.update(rows=len(out), bad=bad[:20], n_bad=len(bad))
    return res


def _loc(season: str | None, station: str | None, folder: str | None) -> tuple[str, str, str]:
    """(調査回, 地点（なければフォルダ、それもなければ all）, deploymentID)"""
    se = (season or "").strip()
    loc = (station or "").strip() or (folder or "").strip().strip("/") or "all"
    return se, loc, f"{se}_{loc}" if se else loc


def deployments_of(project) -> dict[str, dict]:
    """プロジェクトの全部の動画から、調査回 × 地点ごとの様子: deploymentID → dict(season, loc, n, first, last（最後の動画の終わり）)"""
    out: dict[str, dict] = {}
    for r in project.db.q("SELECT season, station, folder, COALESCE(datetime_corrected, datetime) AS dt, duration FROM items WHERE missing=0"):
        se, loc, did = _loc(r["season"], r["station"], r["folder"])
        d = out.setdefault(did, dict(season=se, loc=loc, n=0, first=None, last=None))
        d["n"] += 1
        dt = r["dt"]
        if not dt:
            continue
        end = media.shift_dt(dt, int(round(r["duration"] or 0))) or dt
        if d["first"] is None or dt < d["first"]:
            d["first"] = dt
        if d["last"] is None or end > d["last"]:
            d["last"] = end
    return out


def info(project, user_name: str = "") -> dict:
    """書き出しの窓に出すもの: 覚えている説明（題名・作成者など）と、地点の表の様子"""
    deps = deployments_of(project)
    idx = _station_index(load_stations(project))
    with_c = [d for d in deps.values() if _coords(_station_row(idx, d["season"], d["loc"]))]
    names = sorted({d["loc"] for d in deps.values()})
    known = {k[1] for k in idx}
    saved = dict(project.data.get("camtrap") or {})
    meta = dict(title=saved.get("title") or project.name, contributor=saved.get("contributor") or user_name or "", organization=saved.get("organization") or "",
                email=saved.get("email") or "", sampling=saved.get("sampling") or "", capture=saved.get("capture") or ["activityDetection"],
                individuals=bool(saved.get("individuals")), tz=saved.get("tz") or local_offset(), description=saved.get("description") or "",
                license_data=saved.get("license_data") or "", license_media=saved.get("license_media") or "")
    return dict(version=DP_VERSION, licenses=list(LICENSES), meta=meta, n_deployments=len(deps), n_with_coords=len(with_c), n_stations=len(names),
                without_coords=sorted({d["loc"] for d in deps.values()} - {d["loc"] for d in with_c})[:8],
                stations_file=stations_path(project) if idx else "", n_table_rows=len(idx),
                not_in_project=sorted(k for k in known if k not in {n.lower() for n in names})[:8], sampling=list(SAMPLING), capture=list(CAPTURE))


def write_template(project, path: str) -> dict:
    """地点の表のひな形を書く（地点ごとに 1 行。もう読み込んである値は入れておく）。FirstVideo・LastVideo・Videos は参考で、読み込むときは見ない"""
    deps = deployments_of(project)
    idx = _station_index(load_stations(project))
    seasons = {d["season"] for d in deps.values()}
    per_season = len(seasons) > 1               # 調査回が 2 つ以上あるプロジェクト（統合など）では、調査回 × 地点で 1 行
    rows: dict[tuple, dict] = {}
    for d in deps.values():
        k = (d["season"] if per_season else "", d["loc"])
        r = rows.setdefault(k, dict(n=0, first=None, last=None))
        r["n"] += d["n"]
        if d["first"] and (r["first"] is None or d["first"] < r["first"]):
            r["first"] = d["first"]
        if d["last"] and (r["last"] is None or d["last"] > r["last"]):
            r["last"] = d["last"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(STATION_COLS + ["cameraModel", "cameraHeight", "habitat", "FirstVideo", "LastVideo", "Videos"])
        for (se, loc), r in sorted(rows.items()):
            s = _station_row(idx, se, loc)
            w.writerow([se, loc, s.get("Latitude", ""), s.get("Longitude", ""), s.get("Start", ""), s.get("End", ""),
                        s.get("cameraModel", ""), s.get("cameraHeight", ""), s.get("habitat", ""), r["first"] or "", r["last"] or "", r["n"]])
    os.replace(tmp, path)
    return dict(path=path, rows=len(rows))


# ---------- 説明と追加項目 ----------
def _clean_meta(meta: dict | None, project, user_name: str = "") -> tuple[dict, list[dict]]:
    """窓で入れた説明をそろえる。返り値は (そろえたもの, 足りない必須の項目の並び)。足りない項目があっても例外は出さない
    （足りないものを全部まとめて知らせるため。precheck が地点の座標などと合わせて返す）。一覧にないライセンスだけは ValueError"""
    m = dict(meta or {})
    miss: list[dict] = []
    tz_raw = str(m.get("tz") or "").strip()
    tz = norm_offset(tz_raw) if tz_raw else local_offset()
    if tz is None:                             # 書いてあるが読めない: どの時刻にも時差が要る（deploymentStart、timestamp、eventStart …）
        miss.append(dict(key="tz", field="deploymentStart / timestamp / eventStart", names=[tz_raw]))
        tz = local_offset()
    cap = [c for c in (m.get("capture") or []) if c in CAPTURE]
    if not cap:
        if m.get("capture") is None:           # 渡されなかった（窓を通さない呼び出し）: 既定
            cap = ["activityDetection"]
        else:                                  # 窓で両方のチェックを外した
            miss.append(dict(key="capture", field="datapackage.json: project.captureMethod"))
    out = dict(title=(m.get("title") or "").strip() or project.name, contributor=(m.get("contributor") or "").strip() or (user_name or "").strip(),
               organization=(m.get("organization") or "").strip(), email=(m.get("email") or "").strip(), sampling=(m.get("sampling") or "").strip(),
               capture=cap, individuals=bool(m.get("individuals")), tz=tz, description=(m.get("description") or "").strip(),
               license_data=(m.get("license_data") or "").strip(), license_media=(m.get("license_media") or "").strip())
    if not out["contributor"]:
        miss.insert(0, dict(key="contributor", field="datapackage.json: contributors.title"))
    if out["sampling"] not in SAMPLING:
        miss.insert(1 if not out["contributor"] else 0, dict(key="sampling", field="datapackage.json: project.samplingDesign"))
        out["sampling"] = ""
    for k in ("license_data", "license_media"):
        if out[k] and out[k] not in LICENSES:
            raise ValueError("ライセンスは、一覧から選んでください")
    return out, miss


def _one_line(s: str) -> str:
    """値の中の改行を空白 1 つにする（改行のある値は CSV としては正しいが、行ごとに読む道具や表計算ソフトで行がずれる）"""
    return re.sub(r"[ \t]*[\r\n]+[ \t]*", " ", s).strip() if ("\n" in s or "\r" in s) else s


def _cells(vals: list) -> list:
    return [_one_line(v) if isinstance(v, str) else v for v in vals]


def _tag(k: str, v: str) -> str:
    return f"{k}:{str(v).replace('|', '/').strip()}"


def _role(x: dict) -> str | None:
    """追加項目 → observations の列の名前（count / sex / behavior / lifeStage / individualID）。あてはまらなければ None"""
    for s in (x.get("key"), x.get("label_en"), x.get("label_ja")):
        r = _ROLE_OF.get(_hkey(s or ""))
        if r:
            return r
    return None


def _to_col(role: str, v: str) -> str | None:
    """値を、その列に書ける形にする。書けない値は None（タグに残す）。性別の「不明」は空文字（書かない。タグにも残さない）"""
    s = v.strip()
    if role == "count":
        x = _num(s)
        return str(int(x)) if x is not None and x >= 1 and x == int(x) else None
    if role == "sex":
        return SEX.get(s.lower()) or ("" if s.lower() in SEX_UNKNOWN else None)
    if role == "lifeStage":
        return LIFE_STAGE.get(s.lower())
    return s.replace("|", "/") or None             # individualID


def _fields_of(vals: dict[str, dict[int, str]], defs: list[dict]) -> tuple[list[str], list[str]]:
    """対象ごとの追加項目の値を、(タグ（key:value）, 自由記述) に分ける"""
    tags: list[str] = []
    notes: list[str] = []
    for x in defs:
        vs = [v.strip() for _, v in sorted((vals.get(x["key"]) or {}).items()) if (v or "").strip()]
        if x["type"] == "text":
            notes += [f"{x['key']}: {v}" for v in vs]
        else:
            tags += [_tag(x["key"], v) for v in vs]
    return tags, notes


def _species_fields(vals: dict[str, dict[int, str]], defs: list[dict]) -> tuple[list[dict], str, list[str], list[str]]:
    """種の行の追加項目の値を、(記録の並び, behavior, タグ, 自由記述) に分ける。
    記録 = dict(count, sex, lifeStage, individualID のうち、値のあるもの)。＋ で何件も記録してあれば、記録の番号（seq）ごとに 1 つ
    （Camtrap DP は、性別や成長段階が違う個体を別の行にする）。1 件だけ、またはこれらの項目に値がなければ、記録は 1 つ"""
    recs: dict[int, dict] = {}
    beh: list[str] = []
    tags: list[str] = []
    notes: list[str] = []
    for x in defs:
        vs = [(s, v.strip()) for s, v in sorted((vals.get(x["key"]) or {}).items()) if (v or "").strip()]
        if not vs:
            continue
        role = _role(x)
        if role == "behavior":
            beh += [v.replace("|", "/") for _, v in vs]
        elif role:
            for s, v in vs:
                c = _to_col(role, v)
                if c is None or role in recs.get(s, {}):         # その列に書けない値、または同じ列にあたる項目が 2 つあるときの 2 つ目: タグに残す
                    tags.append(_tag(x["key"], v))
                elif c:
                    recs.setdefault(s, {})[role] = c
        elif x["type"] == "text":
            notes += [f"{x['key']}: {v}" for _, v in vs]
        else:
            tags += [_tag(x["key"], v) for _, v in vs]
    return [recs[s] for s in sorted(recs)] or [{}], " | ".join(dict.fromkeys(beh)), tags, notes


def _slug(s: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9._-]+", "-", (s or "").lower())).strip("-.") or "trapnote-export"


def _deployment_row(did: str, d: dict, idx: dict[tuple, dict], tz: str) -> tuple[dict, tuple[float, float] | None]:
    """deployments の 1 行（列の名前 → 値）と座標（なければ None）。d = dict(season, loc, first, last)。
    地点の表（idx）の値を入れる。設置・回収の日時が表になければ、その地点の最初と最後の動画の日時（そのことを deploymentComments に書く）"""
    s = _station_row(idx, d["season"], d["loc"])
    row = {c: "" for c in COLS["deployments"]}
    row.update(deploymentID=did, locationID=d["loc"], locationName=d["loc"], deploymentGroups=f"season:{d['season']}" if d["season"] else "")
    for c in PASS_COLS:
        if (s.get(c) or "").strip():
            v = s[c].strip()
            row[c] = {"1": "true", "0": "false", "yes": "true", "no": "false"}.get(v.lower(), v.lower()) if c == "baitUse" else v
    c2 = _coords(s)
    if c2:
        row["latitude"], row["longitude"] = repr(c2[0]), repr(c2[1])
    st, en = s.get("Start") or "", s.get("End") or ""
    row["deploymentStart"] = iso(st or d["first"], tz) or ""
    row["deploymentEnd"] = iso(en or d["last"], tz) or ""
    if not (st and en):
        note = "deploymentStart/deploymentEnd taken from the first/last media file (setup and retrieval times were not recorded)"
        row["deploymentComments"] = f"{row['deploymentComments']} | {note}" if row["deploymentComments"] else note
    return {k: _one_line(v) for k, v in row.items()}, c2


# ---------- 書き出す前の検査 ----------
_DT_SQL = "NULLIF(COALESCE(items.datetime_corrected, items.datetime), '')"
NAMES_MAX = 30                                 # 足りない地点などの名前を、いくつまで返すか（残りは数だけ）


def _names(xs, limit: int = NAMES_MAX) -> dict:
    xs = sorted(dict.fromkeys(xs))
    return dict(n=len(xs), names=xs[:limit], more=max(0, len(xs) - limit))


def precheck(project, f: dict | None = None, meta: dict | None = None, user_name: str = "") -> dict:
    """書き出す前の検査（ファイルは書かない）。Camtrap DP の必須の項目のうち足りないものを missing に、知らせておくことを warnings に並べる。
    missing が 1 つでもあれば（ok = False）、export_camtrapdp は書き出さない。
    missing の要素 = dict(key, field = Camtrap DP での項目の名前, n, names = 足りない地点などの名前（NAMES_MAX 個まで）, more = 残りの数)
      key: contributor（作成者の名前がない）/ sampling（調査地点の決め方を選んでいない）/ capture（撮影の方法を選んでいない）/ tz（時差が読めない）/
           coords（緯度・経度のない地点）/ media（日時のある動画・静止画が 1 つもない）/ deployment_id（別の地点が同じ deploymentID になる）/
           value（地点の表の値が列の決まりに合わない。kind = type / enum / range / required、names = 「地点: 値」）
    warnings の要素 = dict(key, n, names, more)
      key: no_time（日時がなく、書き出されない動画・静止画。names は例）/ no_sci（学名のない種。scientificName が空になる）/
           outside（地点の表の設置〜回収の期間の外に動画がある地点）/ reversed（回収が設置より前の地点）"""
    m, missing = _clean_meta(meta, project, user_name)
    tz = m["tz"]
    db = project.db
    where, p = build_where(f or {})
    p = tuple(p)
    idx = _station_index(load_stations(project))
    used: dict[str, dict] = {}
    pairs: dict[str, set] = {}
    n_items = n_no_time = 0
    for r in db.q(f"SELECT items.season AS season, items.station AS station, items.folder AS folder, COUNT(*) AS n, COUNT({_DT_SQL}) AS n_time, "
                  f"MIN({_DT_SQL}) AS first, MAX({_DT_SQL}) AS last FROM items WHERE {where} GROUP BY items.season, items.station, items.folder", p):
        n_items += r["n"]
        n_no_time += r["n"] - r["n_time"]
        if not r["n_time"]:
            continue
        se, loc, did = _loc(r["season"], r["station"], r["folder"])
        pairs.setdefault(did, set()).add((se, loc))
        d = used.setdefault(did, dict(season=se, loc=loc, n=0, first=r["first"], last=r["last"]))
        d["n"] += r["n_time"]
        d["first"], d["last"] = min(d["first"], r["first"]), max(d["last"], r["last"])
    if not used:
        missing.append(dict(key="media", field="media.csv: timestamp", n=n_no_time, names=[], more=0))
    dup = [did for did, ps in pairs.items() if len(ps) > 1]
    if dup:
        missing.append(dict(key="deployment_id", field="deployments.csv: deploymentID", **_names(dup)))
    no_coords, outside, rev = [], [], []
    bad_vals: dict[tuple, list] = {}
    for did, d in used.items():
        row, c2 = _deployment_row(did, d, idx, tz)
        if c2 is None:
            no_coords.append(d["loc"])
        for fd in TABLES["deployments"]:
            nm, v = fd["name"], row[fd["name"]]
            if v in MISSING:
                if fd.get("required") and nm not in ("latitude", "longitude"):
                    bad_vals.setdefault((nm, "required"), []).append(d["loc"])
                continue
            why = _check_value(fd, v)
            if why:
                bad_vals.setdefault((nm, why), []).append(f"{d['loc']}: {v}")
        s = _station_row(idx, d["season"], d["loc"])
        st, en = s.get("Start") or "", s.get("End") or ""
        if st and en and en < st:
            rev.append(d["loc"])
        elif (st and d["first"] < st) or (en and d["last"] > en):
            outside.append(d["loc"])
    if no_coords:
        missing.append(dict(key="coords", field="deployments.csv: latitude / longitude", **_names(no_coords)))
    for (nm, why), xs in bad_vals.items():
        missing.append(dict(key="value", field=f"deployments.csv: {nm}", kind=why, **_names(xs)))
    warnings: list[dict] = []
    if n_no_time:
        ex = [r["rel_path"] for r in db.q(f"SELECT items.rel_path AS rel_path FROM items WHERE {where} AND {_DT_SQL} IS NULL ORDER BY items.rel_path LIMIT 5", p)]
        warnings.append(dict(key="no_time", n=n_no_time, names=ex, more=max(0, n_no_time - len(ex))))
    sp = project.species.by_code()
    no_sci = []
    for r in db.q(f"SELECT species, COUNT(*) AS n FROM judgements WHERE item_id IN (SELECT items.id FROM items WHERE {where} AND {_DT_SQL} IS NOT NULL) GROUP BY species", p):
        s = sp.get(r["species"], {})
        if not s.get("fixed") and not (s.get("name_sci") or "").strip():
            no_sci.append(f"{s.get('name_en') or s.get('name_ja') or r['species']} ({r['n']})")
    if no_sci:
        warnings.append(dict(key="no_sci", **_names(no_sci)))
    if outside:
        warnings.append(dict(key="outside", **_names(outside)))
    if rev:
        warnings.append(dict(key="reversed", **_names(rev)))
    return dict(ok=not missing, missing=missing, warnings=warnings, n_items=n_items, n_media=n_items - n_no_time, n_deployments=len(used), version=DP_VERSION, meta=m)


# ---------- 書き出し ----------
FILES = ("deployments.csv", "media.csv", "observations.csv", "datapackage.json")


def export_camtrapdp(project, out_dir: str, f: dict | None = None, meta: dict | None = None, user_name: str = "", progress=None) -> dict:
    """Camtrap DP 1.0.2 のパッケージを out_dir に書く。
    先に precheck で必須の項目を調べ、足りなければ何も書かずに dict(blocked=True, missing=[…]) を返す（out_dir も作らない）。
    書いたものは、置く前に定義と照合する（check_package）。合わなければ置かずに消し、dict(blocked=True, problems=[…]) を返す
    （out_dir に前の書き出しがあれば、それは変えない）。だから、定義に合わないパッケージが out_dir に残ることはない。
    置けたときの返り値は、件数と warnings（precheck と同じ形。知らせておくこと）で、valid は True"""
    pc = precheck(project, f, meta, user_name)
    m = pc["meta"]
    project.update_data(camtrap=m)             # 次に開いたときのために、入れた説明を覚えておく（足りない項目があるときも）
    if not pc["ok"]:
        return dict(out_dir=out_dir, version=DP_VERSION, blocked=True, valid=False, missing=pc["missing"], warnings=pc["warnings"], problems=[])
    tz = m["tz"]
    db = project.db
    sp = project.species.by_code()
    fdefs = project.fields.fields
    item_defs = [x for x in fdefs if x["scope"] == "item"]
    sp_defs = [x for x in fdefs if x["scope"] == "species"]
    deps = deployments_of(project)
    idx = _station_index(load_stations(project))
    cap1 = m["capture"][0] if len(m["capture"]) == 1 else ""
    made_dir = not os.path.isdir(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    tmp = {n: os.path.join(out_dir, n + ".tmp") for n in FILES}
    used: dict[str, dict] = {}
    taxa: dict[str, dict] = {}
    no_sci: dict[str, int] = {}
    bad_path: list[str] = []
    n_media = n_obs = n_no_time = 0
    try:
        with open(tmp["media.csv"], "w", encoding="utf-8", newline="") as fm, open(tmp["observations.csv"], "w", encoding="utf-8", newline="") as fo:
            wm, wo = csv.writer(fm, lineterminator="\n"), csv.writer(fo, lineterminator="\n")
            wm.writerow(COLS["media"])
            wo.writerow(COLS["observations"])
            for k, (it, js, fv, _auto) in enumerate(_iter_items(db, f or {})):
                if progress and k % 500 == 0:
                    progress(k)
                dt = it.get("datetime_corrected") or it.get("datetime")
                start = iso(dt, tz)
                if not start:
                    n_no_time += 1
                    continue
                mid = it["rel_path"]
                if not _PATH_RE.match(mid) or "\n" in mid or "\r" in mid:      # filePath の決まりに合わない名前（.. を含むなど）: 書かない（知らせる）
                    bad_path.append(mid)
                    continue
                end = iso(dt, tz, float(it.get("duration") or 0)) or start
                se, loc, did = _loc(it.get("season"), it.get("station"), it.get("folder"))
                used.setdefault(did, deps.get(did) or dict(season=se, loc=loc, n=0, first=dt, last=dt))
                itags, inotes = _fields_of(fv.get((it["id"], None), {}), item_defs)
                comments = " / ".join([x for x in [(it.get("note") or "").strip()] if x] + inotes)
                ext = os.path.splitext(it["rel_path"])[1].lower()
                wm.writerow(_cells([mid, did, cap1, start, it["rel_path"], "false", it["name"], MEDIATYPES.get(ext, ("image/" if it["kind"] == "image" else "video/") + ext.lstrip(".")),
                                    "", "true" if it.get("favorite") else "false", comments]))
                n_media += 1
                verified = it["status"] == "verified"
                try:
                    auto_j = json.loads(it["auto_json"]) if it.get("auto_json") else {}
                except (ValueError, TypeError):
                    auto_j = {}
                oids: set = set()                          # この動画でもう使った observationID（observationID は表の中で 1 つだけ。重ねない）

                def obs(code: str, typ: str, **kw) -> None:
                    nonlocal n_obs
                    oid, n2 = f"{mid}#{code}", 1
                    while oid in oids:                     # 念のため: 同じ動画で同じ ID になるなら、番号を付けて別の ID にする（行を捨てない）
                        n2 += 1
                        oid = f"{mid}#{code}~{n2}"
                    oids.add(oid)
                    row = {c: "" for c in COLS["observations"]}
                    row.update(observationID=oid, deploymentID=did, eventID=mid, eventStart=start, eventEnd=end, observationLevel="event", observationType=typ)
                    row.update({k2: v for k2, v in kw.items() if v not in (None, "")})
                    wo.writerow(_cells([row[c] for c in COLS["observations"]]))
                    n_obs += 1
                if not js:
                    if it.get("flag") == "low":            # AI が動物を見つけたが、種を付けなかった（要確認）
                        obs("none", "unknown", classificationMethod="machine", classifiedBy=auto_j.get("model") or "", classificationTimestamp=_stamp(auto_j.get("at")),
                            observationTags=" | ".join(itags))
                    else:
                        obs("none", "unclassified", observationTags=" | ".join(itags))
                    continue
                for j in js:
                    code = j["species"]
                    human = verified or j.get("origin") in ("csv", "manual")
                    recs, beh, tags, notes = _species_fields(fv.get((it["id"], j["id"]), {}), sp_defs)
                    kw = dict(observationTags=" | ".join(itags + tags), observationComments=" / ".join(notes))
                    if human:
                        kw.update(classificationMethod="human", classifiedBy=(it.get("verified_by") if verified else j.get("created_by")) or "",
                                  classificationTimestamp=_stamp(it.get("verified_at") if verified else j.get("created_at")))
                    else:
                        p = j.get("confidence")
                        kw.update(classificationMethod="machine", classifiedBy=j.get("model") or auto_j.get("model") or "", classificationTimestamp=_stamp(j.get("created_at")),
                                  classificationProbability=f"{min(1.0, max(0.0, float(p))):.3f}" if p is not None else "")
                    if code == "empty":
                        obs(code, "blank", **kw)
                    elif code in ("human", "vehicle", "unknown"):
                        obs(code, code, **kw)
                    elif code == "area":                   # 有効範囲の確認用に撮った動画（人がカメラの前を歩く）
                        obs(code, "human", cameraSetupType="calibration", **kw)
                    else:
                        s = sp.get(code, {})
                        sci = (s.get("name_sci") or "").strip()
                        if sci:
                            t = taxa.setdefault(sci, dict(scientificName=sci))
                            rank = RANKS.get((s.get("rank") or "").strip())
                            if rank:
                                t["taxonRank"] = rank
                            vn = {k2: v for k2, v in (("eng", (s.get("name_en") or "").strip()), ("jpn", (s.get("name_ja") or "").strip())) if v}
                            if vn:
                                t["vernacularNames"] = vn
                        else:                              # 学名のない種: 名前を comments に残す（何の種かが分からなくならないように）
                            label = s.get("name_en") or s.get("name_ja") or code
                            no_sci[label] = no_sci.get(label, 0) + 1
                            kw["observationComments"] = " / ".join([f"species: {label}"] + notes)
                        for n, rec in enumerate(recs, start=1):       # 記録が 2 件以上なら、記録ごとに 1 行（ID は <mediaID>#<種>#<番号>）
                            obs(code if len(recs) == 1 else f"{code}#{n}", "animal", scientificName=sci, behavior=beh, **rec, **kw)
        # deployments: 書き出した動画のある地点だけ
        starts, ends, pts = [], [], []
        with open(tmp["deployments.csv"], "w", encoding="utf-8", newline="") as fd:
            wd = csv.writer(fd, lineterminator="\n")
            wd.writerow(COLS["deployments"])
            for did, d in sorted(used.items()):
                row, c2 = _deployment_row(did, d, idx, tz)
                if c2:
                    pts.append(c2)
                if row["deploymentStart"]:
                    starts.append(row["deploymentStart"][:10])
                if row["deploymentEnd"]:
                    ends.append(row["deploymentEnd"][:10])
                wd.writerow([row[c] for c in COLS["deployments"]])
        # datapackage.json
        contributor = dict(title=m["contributor"], role="contact")
        if m["email"]:
            contributor["email"] = m["email"]
        if m["organization"]:
            contributor["organization"] = m["organization"]
        pkg: dict = dict(
            resources=[dict(name=n, path=n + ".csv", profile="tabular-data-resource", format="csv", mediatype="text/csv", encoding="utf-8", schema=f"{BASE_URL}{n}-table-schema.json")
                       for n in ("deployments", "media", "observations")],
            profile=BASE_URL + "camtrap-dp-profile.json", name=_slug(m["title"]), created=datetime.now().astimezone().isoformat(timespec="seconds"),
            title=m["title"], contributors=[contributor], sources=[dict(title="TrapNote", version=VERSION)],
            project=dict(title=m["title"], samplingDesign=m["sampling"], captureMethod=m["capture"], individualAnimals=m["individuals"], observationLevel=["event"]))
        if m["description"]:                       # 説明とライセンスは任意。入れたときだけ書く
            pkg["description"] = m["description"]
        lic = [dict(name=m[k], path=LICENSES[m[k]][1], title=LICENSES[m[k]][0], scope=scope) for k, scope in (("license_data", "data"), ("license_media", "media")) if m[k]]
        if lic:
            pkg["licenses"] = lic
        if pts:
            la0, la1, lo0, lo1 = min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)
            pkg["spatial"] = dict(type="Polygon", bbox=[lo0, la0, lo1, la1], coordinates=[[[lo0, la0], [lo1, la0], [lo1, la1], [lo0, la1], [lo0, la0]]])
        if starts and ends:
            pkg["temporal"] = dict(start=min(starts), end=max(ends))
        pkg["taxonomic"] = [taxa[k] for k in sorted(taxa)]
        with open(tmp["datapackage.json"], "w", encoding="utf-8") as fh:
            json.dump(pkg, fh, ensure_ascii=False, indent=2)
        problems = check_package(out_dir, suffix=".tmp")       # 置く前に、定義と照合する
        if not problems:
            for n in FILES:
                os.replace(tmp[n], os.path.join(out_dir, n))
    finally:
        for path in tmp.values():                  # 置かなかったもの（合わなかった、途中で失敗した）は残さない
            try:
                os.remove(path)
            except OSError:
                pass
        if made_dir:                               # この回に作ったフォルダが空のままなら、消す（何も置かなかった）
            try:
                os.rmdir(out_dir)
            except OSError:
                pass
    warnings = [w for w in pc["warnings"] if w["key"] not in ("no_time", "no_sci")]
    if n_no_time:
        warnings.insert(0, next((w for w in pc["warnings"] if w["key"] == "no_time"), dict(key="no_time", n=n_no_time, names=[], more=n_no_time)))
    if no_sci:
        warnings.append(dict(key="no_sci", **_names([f"{k} ({v})" for k, v in sorted(no_sci.items(), key=lambda x: -x[1])])))
    if bad_path:
        warnings.append(dict(key="bad_path", n=len(bad_path), names=bad_path[:5], more=max(0, len(bad_path) - 5)))
    res = dict(out_dir=out_dir, version=DP_VERSION, deployments=len(used), media=n_media, observations=n_obs, no_time=n_no_time, no_coords=0,
               no_sci=[dict(name=k, n=v) for k, v in sorted(no_sci.items(), key=lambda x: -x[1])][:10], taxa=len(taxa), warnings=warnings,
               problems=problems, valid=not problems, blocked=bool(problems), missing=[])
    if problems:
        log.warning("Camtrap DP: 書いたものが定義に合わないので、置きませんでした: %s", problems[:5])
    return res


# ---------- 検査（書き出したものが、定義に合っているか） ----------
def _check_value(fd: dict, v: str) -> str | None:
    """値 1 つを列の定義と比べる。合わなければ、合わない点（type / enum / pattern / range）"""
    t = fd["type"]
    try:
        if t == "integer":
            x = int(v)
        elif t == "number":
            x = float(v)
        elif t == "boolean":
            if v not in ("true", "false"):
                return "type"
            return None
        elif t == "datetime":
            if not _DT_RE.match(v):
                return "type"
            datetime.strptime(v.replace("Z", "+0000") if v.endswith("Z") else v, _DT_FORMAT)
            return None
        else:
            if "enum" in fd and v not in fd["enum"]:
                return "enum"
            if "pattern" in fd and not re.match(fd["pattern"], v):
                return "pattern"
            return None
    except ValueError:
        return "type"
    if ("minimum" in fd and x < fd["minimum"]) or ("maximum" in fd and x > fd["maximum"]):
        return "range"
    return None


def check_package(out_dir: str, suffix: str = "") -> list[dict]:
    """書き出したパッケージを、Camtrap DP 1.0.2 の定義（TABLES と、パッケージの必須の項目）と比べる。
    suffix = ファイルの名前の後ろに付いているもの（書き出しの途中のファイルは <名前>.tmp。置く前に照合するため）。
    返り値は、合わない点の並び: dict(where = "表.列" か "datapackage.項目", kind = required / type / enum / pattern / range / unique / foreign / header / missing,
    n = 件数, example = 合わない値の例, rows = 合わない行（CSV の行の番号。見出しが 1 行目。はじめの 5 つ）)"""
    out: list[dict] = []

    def add(where: str, kind: str, n: int = 1, example: str = "", rows: list | None = None) -> None:
        out.append(dict(where=where, kind=kind, n=n, example=str(example)[:80], rows=list(rows or [])))
    try:
        with open(os.path.join(out_dir, "datapackage.json" + suffix), encoding="utf-8") as fh:
            pkg = json.load(fh)
    except (OSError, ValueError):
        add("datapackage.json", "missing")
        pkg = {}
    for k in ("resources", "profile", "created", "contributors", "project", "spatial", "temporal", "taxonomic"):
        if k not in pkg or pkg[k] in (None, "", []) and k != "taxonomic":
            add(f"datapackage.{k}", "required")
    if pkg.get("profile") and pkg["profile"] != BASE_URL + "camtrap-dp-profile.json":
        add("datapackage.profile", "enum", example=pkg["profile"])
    for c in pkg.get("contributors") or []:
        if not (c.get("title") or "").strip():
            add("datapackage.contributors.title", "required")
        if c.get("role") and c["role"] not in ("contact", "principalInvestigator", "rightsHolder", "publisher", "contributor"):
            add("datapackage.contributors.role", "enum", example=c["role"])
    if "description" in pkg and not isinstance(pkg["description"], str):
        add("datapackage.description", "type")
    for li in pkg.get("licenses") or []:
        if not ((li.get("name") or "").strip() or (li.get("path") or "").strip()):
            add("datapackage.licenses.name", "required")
        elif li.get("name") and not re.match(r"^[-a-zA-Z0-9._]+$", li["name"]):
            add("datapackage.licenses.name", "pattern", example=li["name"])
        if li.get("scope") not in ("data", "media"):
            add("datapackage.licenses.scope", "enum" if li.get("scope") else "required", example=li.get("scope") or "")
    pr = pkg.get("project") or {}
    if pr:
        for k in ("title", "samplingDesign", "captureMethod", "individualAnimals", "observationLevel"):
            if k not in pr or pr[k] in (None, "", []):
                add(f"datapackage.project.{k}", "required")
        if pr.get("samplingDesign") and pr["samplingDesign"] not in SAMPLING:
            add("datapackage.project.samplingDesign", "enum", example=pr["samplingDesign"])
        if any(c not in CAPTURE for c in pr.get("captureMethod") or []):
            add("datapackage.project.captureMethod", "enum")
        if not isinstance(pr.get("individualAnimals"), bool):
            add("datapackage.project.individualAnimals", "type")
        if any(c not in ("media", "event") for c in pr.get("observationLevel") or []):
            add("datapackage.project.observationLevel", "enum")
    tp = pkg.get("temporal") or {}
    for k in ("start", "end"):
        if tp and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(tp.get(k) or "")):
            add(f"datapackage.temporal.{k}", "type", example=tp.get(k) or "")
    if pkg.get("spatial") and not pkg["spatial"].get("type"):
        add("datapackage.spatial", "type")
    for t in pkg.get("taxonomic") or []:
        if not (t.get("scientificName") or "").strip():
            add("datapackage.taxonomic.scientificName", "required")
    res = {r.get("name"): r for r in pkg.get("resources") or []}
    keys: dict[str, set] = {}
    for name in ("deployments", "media", "observations"):
        r = res.get(name)
        if not r:
            add(f"datapackage.resources.{name}", "required")
        elif r.get("profile") != "tabular-data-resource" or r.get("schema") != f"{BASE_URL}{name}-table-schema.json":
            add(f"datapackage.resources.{name}", "enum")
        path = os.path.join(out_dir, ((r or {}).get("path") or name + ".csv") + suffix)
        if not os.path.isfile(path):
            add(f"{name}.csv", "missing")
            continue
        fds = TABLES[name]
        bad: dict[tuple, list] = {}             # (列, 合わない点) → [件数, 合わない行（はじめの 5 つ）, 値の例]
        seen: dict[str, set] = {f["name"]: set() for f in fds if f.get("unique")}
        fks = FOREIGN_KEYS.get(name, [])
        mine: set = set()

        def note(nm: str, why: str, line: int, v: str) -> None:
            b = bad.setdefault((nm, why), [0, [], ""])
            b[0] += 1
            if len(b[1]) < 5:
                b[1].append(line)
            b[2] = b[2] or v
        with open(path, encoding="utf-8", newline="") as fh:
            rd = csv.reader(fh)
            header = next(rd, [])
            if header != COLS[name]:
                add(f"{name}.csv", "header", example=",".join(header)[:80])
                continue
            for row in rd:
                line = rd.line_num               # 見出しが 1 行目。値の中に改行があれば、その行の終わりの番号
                for fd, v in zip(fds, row):
                    nm = fd["name"]
                    if v in MISSING:
                        if fd.get("required"):
                            note(nm, "required", line, "")
                        continue
                    why = _check_value(fd, v)
                    if why:
                        note(nm, why, line, v)
                    if nm in seen:
                        if v in seen[nm]:
                            note(nm, "unique", line, v)
                        seen[nm].add(v)
                    for col, ref, _rc in fks:
                        if col == nm and v not in keys.get(ref, set()):
                            note(nm, "foreign", line, v)
                mine.add(row[0] if row else "")
        keys[name] = mine
        for (nm, why), (n, lines, ex) in bad.items():
            add(f"{name}.{nm}", why, n, ex, lines)
    return out
