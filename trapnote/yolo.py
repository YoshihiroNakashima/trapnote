"""YOLO 形式の学習データ（<種>/images/*.png|jpg + <種>/labels/*.txt）を TrapNote のプロジェクトとして読む

フォルダの作り（例 F:\\Data）
  Boar/images/N180712_BSN0870_IMG0047_img450.png     … 1 枚 = 動画の 1 フレーム
  Boar/labels/N180712_BSN0870_IMG0047_img450.txt     … YOLO の枠: 「class cx cy w h」（0〜1 の正規化。class はこのフォルダでは 0 = 動物）
  Back/images/...                                     … 動物なし（labels なし）
読み方
  1. そのフォルダをプロジェクトとして開き、走査で画像を登録する（通常の流れ）
  2. 「YOLO ラベルを読む」: 最上位フォルダ名 → 種（対応表は画面で直せる）を確認済みの判定にし、labels の枠を検出枠（detections）として入れる。
     画像名の N180712_BSN0870_IMG0047_img450 から 季節 N180712・地点 BSN0870 を付ける（パターンに合えば）
枠は MegaDetector の枠と同じ表に入るので、重ね描きも、第 3 段階の追加学習（枠の切り出し）もそのまま使える"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

from .db import now_iso
from .paths import abs_of

log = logging.getLogger(__name__)
ITEM_RE = re.compile(r"^(?P<folder>[^/]+)/images/(?P<name>[^/]+)$")
NAME_RE = re.compile(r"^(?P<season>[A-Za-z]\d{6})_(?P<station>[A-Za-z]+\d+)_(?P<video>.+?)_img(?P<frame>\d+)$")
BACK_NAMES = {"back", "background", "empty", "blank", "none", "negative"}
BIRD_HINTS = ("kiji", "hato", "tsugumi", "shirohara", "phasianidae", "bird", "aves", "tori", "mokin", "sagi", "kirei")


def _guess(sl, folder: str) -> str | None:
    f = folder.strip().lower()
    if f in BACK_NAMES:
        return "empty"
    code = sl.guess_code(folder)
    if code:
        return code
    rows = sl.by_code()
    if any(h in f for h in BIRD_HINTS) and "bird" in rows:
        return "bird"
    return None


def preview(project) -> dict:
    """最上位フォルダごとの画像数・ラベル数と、種の当たり（画面の対応表の元）"""
    db = project.db
    rows = db.q("SELECT rel_path FROM items WHERE kind='image' AND missing=0")
    folders: Counter = Counter()
    for r in rows:
        m = ITEM_RE.match(r["rel_path"])
        if m:
            folders[m.group("folder")] += 1
    out = []
    for folder, n in sorted(folders.items()):
        ldir = os.path.join(project.root, folder, "labels")
        n_labels = len([f for f in os.listdir(ldir) if f.lower().endswith(".txt")]) if os.path.isdir(ldir) else 0
        out.append(dict(folder=folder, n_images=n, n_labels=n_labels, guess=_guess(project.species, folder)))
    n_named = sum(1 for r in rows if ITEM_RE.match(r["rel_path"]) and NAME_RE.match(os.path.splitext(os.path.basename(r["rel_path"]))[0]))
    return dict(folders=out, n_images=len(rows), n_matching=sum(folders.values()), n_named=n_named)


def parse_label_file(path: str) -> list[tuple[float, float, float, float, float, int]]:
    """YOLO の txt → [(x, y, w, h, conf, class)]（x, y は左上、0〜1）"""
    boxes = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 5:
                    continue
                try:
                    cls = int(float(parts[0]))
                    cx, cy, w, h = (float(v) for v in parts[1:5])
                    conf = float(parts[5]) if len(parts) > 5 else 1.0
                except ValueError:
                    continue
                x, y = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
                w, h = min(w, 1.0 - x), min(h, 1.0 - y)
                if w <= 0 or h <= 0:
                    continue
                boxes.append((round(x, 4), round(y, 4), round(w, 4), round(h, 4), round(min(1.0, max(0.0, conf)), 4), cls))
    except OSError:
        pass
    return boxes


def import_yolo(project, mapping: dict[str, str | None], by: str | None, as_verified: bool = True, set_station: bool = True,
                overwrite_verified: bool = False, progress=None) -> dict:
    """mapping = {フォルダ名: 種コード or None（読まない）}。返り値は件数"""
    from .judge import update_agree
    db = project.db
    if set_station and project.setting("pattern") in ("{station}/{file}", "{season}/{station}/{file}", ""):
        project.update_settings(dict(pattern="{species_folder}/{sub}/{file}"))      # 季節・地点は画像名から付けるので、フォルダのパターンでは付けない
    rows = db.q("SELECT id, rel_path, status FROM items WHERE kind='image' AND missing=0")
    n_items = n_boxes = n_skipped = n_noboxes = n_station = 0
    by = by or "yolo"
    ids = []
    with db.tx() as cur:
        for k, r in enumerate(rows):
            if progress and k % 200 == 0:
                progress(k, len(rows))
            m = ITEM_RE.match(r["rel_path"])
            if not m:
                continue
            folder, name = m.group("folder"), m.group("name")
            if folder not in mapping or mapping[folder] is None:
                continue
            code = mapping[folder]
            if r["status"] == "verified" and not overwrite_verified:
                n_skipped += 1
                continue
            stem = os.path.splitext(name)[0]
            boxes = parse_label_file(os.path.join(project.root, folder, "labels", stem + ".txt"))
            # 検出枠（MegaDetector の枠と同じ表）
            cur.execute("DELETE FROM detections WHERE item_id=?", (r["id"],))
            for x, y, w, h, conf, cls in boxes:
                cur.execute("INSERT INTO detections(item_id, t_sec, category, conf, x, y, w, h, species, sp_conf) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (r["id"], 0.0, "animal", conf, x, y, w, h, code if code not in ("empty", "human", "vehicle", "area") else None, 1.0 if code not in ("empty", "human", "vehicle", "area") else None))
            summary = dict(n_frames=1, animal_frames=1 if boxes else 0, person_frames=0, vehicle_frames=0,
                           max_animal=max([b[4] for b in boxes], default=0.0), max_person=0.0, max_vehicle=0.0,
                           max_area=round(max([b[2] * b[3] for b in boxes], default=0.0), 4), animal_threshold=0.2, person_threshold=0.2,
                           animal=bool(boxes), person=False, vehicle=False, model="yolo-labels", at=now_iso())
            cur.execute("UPDATE items SET det_state=1, det_json=?, cls_state=0, cls_json=NULL, meta_error=NULL WHERE id=?", (json.dumps(summary, ensure_ascii=False), r["id"]))
            n_boxes += len(boxes)
            if not boxes:
                n_noboxes += 1
            # 判定（フォルダ名の種。確認済み）
            cur.execute("DELETE FROM judgements WHERE item_id=?", (r["id"],))
            cur.execute("DELETE FROM field_values WHERE item_id=? AND judgement_id IS NOT NULL", (r["id"],))
            cur.execute("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                        (r["id"], code, None, "manual" if as_verified else "csv", "yolo:" + folder, now_iso(), by))
            if as_verified:
                cur.execute("UPDATE items SET status='verified', verified_by=?, verified_at=?, updated_at=? WHERE id=?", (by, now_iso(), now_iso(), r["id"]))
            else:
                cur.execute("UPDATE items SET status='auto', verified_by=NULL, verified_at=NULL, updated_at=? WHERE id=?", (now_iso(), r["id"]))
            # 画像名から季節・地点
            if set_station:
                nm = NAME_RE.match(stem)
                if nm:
                    cur.execute("UPDATE items SET season=?, station=?, pattern_json=? WHERE id=?",
                                (nm.group("season"), nm.group("station"), json.dumps(dict(video=nm.group("video"), frame=nm.group("frame"), species_folder=folder), ensure_ascii=False), r["id"]))
                    n_station += 1
                else:                                 # 名前から取れない画像は、フォルダのパターンで付いた "images" などの地点を消す
                    cur.execute("UPDATE items SET season=NULL, station=NULL, pattern_json=? WHERE id=?", (json.dumps(dict(species_folder=folder), ensure_ascii=False), r["id"]))
            n_items += 1
            ids.append(r["id"])
    update_agree(db, ids)
    return dict(items=n_items, boxes=n_boxes, no_boxes=n_noboxes, skipped_verified=n_skipped, station_set=n_station)
