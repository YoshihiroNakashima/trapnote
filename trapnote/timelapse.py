"""Timelapse2 のテンプレート（.tdb）とデータ（.ddb）の取り込み。どちらも SQLite

テンプレート（TemplateTable）: Type（RelativePath / File / DateTime / DeleteFlag / FixedChoice / Note / Flag / Counter、古い版では Folder / Date / Time / UtcOffset / ImageQuality）、
  DefaultValue、Label、DataLabel、Tooltip、List（JSON の ChoiceListNonEmpty、古い版は A|B|C）、ControlOrder
  → Species の FixedChoice は種リストに、Star の Flag はお気に入りに、Remarks の Note はメモに、ほかの Note / Flag / Counter / FixedChoice は追加項目（対象ごと）にする
データ（DataTable）: 1 行 = 1 ファイル。RelativePath + File で対象を引き、Species を判定、DateTime を日時、Star をお気に入り、Remarks をメモ、ほかを追加項目の値にする"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter

from . import media
from .db import now_iso
from .paths import abs_of, norm, rel_key, rel_of
from .species import FIXED_CODES

log = logging.getLogger(__name__)
SKIP_TYPES = {"RelativePath", "File", "DateTime", "DeleteFlag", "Folder", "Date", "Time", "UtcOffset", "ImageQuality"}
TYPE_MAP = {"Note": "text", "Flag": "check", "Counter": "number", "FixedChoice": "select", "MultiLine": "text", "DecimalAny": "number",
            "IntegerAny": "number", "IntegerPositive": "number", "DecimalPositive": "number", "MultiChoice": "multiselect", "AlphaNumeric": "text"}
NON_SPECIES_CHOICES = {"multispecies"}       # 種ではない選択肢（Multispecies の自由記述で扱う）
FLAG_PREFIX = "flag:"                        # 対応表のキー: Flag の項目は "flag:<DataLabel>"（選択肢の値と混ざらないように）
FIELD_KEEP = "__field__"                     # 対応表の値: Flag を種ではなく追加項目（チェック）のまま取り込む


def _open(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{norm(path)}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _choices(lst: str) -> list[str]:
    s = (lst or "").strip()
    if not s:
        return []
    if s.startswith("{"):
        try:
            d = json.loads(s)
            return [str(x) for x in (d.get("ChoiceListNonEmpty") or []) if str(x).strip()]
        except Exception:
            pass
    return [x for x in (p.strip() for p in s.split("|")) if x and x != "<EMPTY>"]


def read_template(path: str) -> dict:
    """テンプレートの控え（.tdb か .ddb）。controls は ControlOrder 順"""
    c = _open(path)
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM TemplateTable ORDER BY ControlOrder, Id")]
        ver = None
        try:
            ver = c.execute("SELECT VersionCompatabily FROM TemplateInfo").fetchone()
            ver = ver[0] if ver else None
        except Exception:
            try:
                ver = c.execute("SELECT VersionCompatabily FROM ImageSetTable").fetchone()
                ver = ver[0] if ver else None
            except Exception:
                pass
        has_data = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='DataTable'").fetchone())
        n_data = c.execute("SELECT COUNT(*) FROM DataTable").fetchone()[0] if has_data else 0
        root = None
        if has_data:
            try:
                root = c.execute("SELECT RootFolder FROM ImageSetTable").fetchone()[0]
            except Exception:
                pass
    finally:
        c.close()
    controls = []
    for r in rows:
        ctl = dict(type=r.get("Type"), label=r.get("Label") or r.get("DataLabel"), data_label=r.get("DataLabel"), default=r.get("DefaultValue") or "",
                   tooltip=r.get("Tooltip") or "", choices=_choices(r.get("List")), visible=str(r.get("Visible")).lower() != "false", order=r.get("ControlOrder"))
        ctl["role"] = _role(ctl)
        controls.append(ctl)
    return dict(path=norm(path), version=ver, controls=controls, has_data=has_data, n_data=n_data, root_folder=root)


def _role(ctl: dict) -> str:
    t, dl = ctl["type"], (ctl["data_label"] or "").lower()
    if t in SKIP_TYPES:
        return "skip"
    if t == "FixedChoice" and dl == "species":
        return "species"
    if t == "Flag" and dl == "star":
        return "favorite"
    if t == "Flag" and dl != "deleteflag":
        return "flag"                    # 種ごとのチェック（Deer = TRUE のような作り）か、ただのチェック項目かは対応表で選ぶ
    if t == "Note" and dl == "remarks":
        return "note"
    if t == "Note" and dl == "multispecies":
        return "multispecies"
    return "field"


def field_from_control(ctl: dict) -> dict:
    t = TYPE_MAP.get(ctl["type"], "text")
    default = ctl["default"]
    if t == "text" and re.fullmatch(r"-?\d+(\.\d+)?", str(default).strip()):      # 既定が数（999.9 など）の Note は数値の項目に
        t = "number"
    if t == "check":
        default = "1" if str(default).lower() == "true" else ""
    return dict(key=ctl["data_label"], label_ja=ctl["label"], label_en=ctl["label"], type=t, scope="item", repeat=False, group="",
                options=ctl["choices"] if t in ("select", "multiselect") else [], default=default, required=False, help=ctl["tooltip"])


def import_template(project, tpl: dict, mapping: dict | None = None, only_mapped: bool = False) -> dict:
    """種リストと追加項目にテンプレートを足す（既にあるものは残す）。mapping は Species の選択肢 → 種コード（"" なら使わない、"__new__" なら登録）。
    only_mapped なら、対応表で選んだ種（と新しく登録した種）だけを「使う」にし、ほかの種は「使わない」にする（右の欄にはその地域で撮れる種だけが出る）"""
    sl, fields = project.species, project.fields
    mapping = {k.strip().lower(): v for k, v in (mapping or {}).items()}
    n_sp = n_fd = n_removed = 0
    species_added = []
    used: set[str] = set()
    labels = []
    for ctl in tpl["controls"]:
        if ctl["role"] == "species":
            labels += list(ctl["choices"])
    for lab in mapping:                           # データにだけ出てくる値も対応表に載っている（Flag の項目は別に扱う）
        if not lab.startswith(FLAG_PREFIX) and lab not in [x.lower() for x in labels]:
            labels.append(lab)
    for label in labels:
        if not label or label.lower() in NON_SPECIES_CHOICES:
            continue
        mp = mapping.get(label.lower())
        if mp is not None and mp != "__new__":
            if mp:
                used.add(mp)                      # 固定項目や既存の種に寄せる（"" は使わない）
            continue
        code = sl.code_for_label(label) or (sl.guess_code(label) if mp is None else None)
        if code:
            used.add(code)
            continue
        row = sl.add(dict(name_en=label, csv_label=label, rank="", enabled=1))
        species_added.append(row["code"])
        used.add(row["code"])
        n_sp += 1
    for ctl in tpl["controls"]:
        if ctl["role"] == "species":
            continue
        role = ctl["role"]
        if role == "flag":
            mp = mapping.get(FLAG_PREFIX + ctl["data_label"].lower())
            if mp is None or mp == FIELD_KEEP:
                role = "field"                # 対応表で選んでいない / 追加項目のまま
            elif ctl["data_label"] in fields.by_key():
                # 前の取り込みで追加項目（チェック）になっていたものを種（または使わない）にする: 項目の定義と値を消す
                fields.update([f for f in fields.fields if f["key"] != ctl["data_label"]])
                project.db.x("DELETE FROM field_values WHERE field_key=?", (ctl["data_label"],))
                n_removed += 1
            if role == "flag" and mp == "":
                continue                      # 使わない
            elif role == "flag" and mp == "__new__":
                name = ctl["label"] or ctl["data_label"]
                code = sl.code_for_label(name)
                if not code:
                    row = sl.add(dict(name_en=name, csv_label=name, rank="", enabled=1))
                    code = row["code"]
                    species_added.append(code)
                    n_sp += 1
                used.add(code)
                continue
            elif role == "flag":
                used.add(mp)                  # 種（または固定項目）のチェックとして読む
                continue
        if role == "field":
            have = fields.by_key()
            if ctl["data_label"] in have:
                continue
            fields.update(fields.fields + [field_from_control(ctl)])
            n_fd += 1
    if only_mapped:
        sl.set_enabled([c for c in used if c in {r["code"] for r in sl.rows}])
    return dict(species_added=n_sp, species_codes=species_added, fields_added=n_fd, fields_removed=n_removed, used=sorted(used))


def _flag(v) -> str:
    return "1" if str(v).strip().lower() == "true" else ""


def _split_species(text: str, sl, code_of=None) -> tuple[list[str], list[str]]:
    """Multispecies の自由記述（例 "Deer, Boar"）から種のコードを拾う。拾えなかった語も返す"""
    codes, unknown = [], []
    for tok in re.split(r"[,\s;/+&]+", text or ""):
        tok = tok.strip()
        if not tok:
            continue
        code = code_of(tok) if code_of else sl.code_for_label(tok)
        if code and code not in codes:
            codes.append(code)
        elif not code:
            unknown.append(tok)
    return codes, unknown


def guess_prefix(project, ddb_path: str, root_folder: str | None) -> str:
    """DataTable の RelativePath の前に付けるフォルダ（.ddb が Season フォルダにあり、TrapNote のルートがその親のとき）"""
    p = norm(ddb_path)
    rel = rel_of(os.path.dirname(p), project.root) if project.root else p
    if rel != os.path.dirname(p) and not os.path.isabs(rel):
        return rel.strip("/")
    name = root_folder or os.path.basename(os.path.dirname(p))
    if name and project.db.scalar("SELECT COUNT(*) FROM items WHERE folder = ? OR folder LIKE ?", (name, name + "/%")):
        return name
    return ""


def preview(project, path: str) -> dict:
    tpl = read_template(path)
    out = dict(template=tpl, prefix=guess_prefix(project, path, tpl.get("root_folder")) if project else "")
    if tpl["has_data"]:
        c = _open(path)
        try:
            sp_col = next((x["data_label"] for x in tpl["controls"] if x["role"] == "species"), None)
            if sp_col:
                out["species_counts"] = [dict(value=r[0], count=r[1]) for r in c.execute(f'SELECT "{sp_col}", COUNT(*) FROM DataTable GROUP BY 1 ORDER BY 2 DESC')]
            out["folders"] = [r[0] for r in c.execute("SELECT DISTINCT RelativePath FROM DataTable ORDER BY 1 LIMIT 50")]
            out["sample"] = [dict(r) for r in c.execute("SELECT * FROM DataTable ORDER BY Id LIMIT 3")]
        finally:
            c.close()
        sl = project.species if project else None
        if sl and out.get("species_counts"):
            for d in out["species_counts"]:
                d["code"] = sl.code_for_label(d["value"]) if d["value"].lower() not in NON_SPECIES_CHOICES else "(multi)"
    # Species の選択肢（テンプレート）＋データにだけ出てくる値 → 種コードの当たり（画面の対応表の既定値）
    sl = project.species if project else None
    counts = {d["value"]: d["count"] for d in out.get("species_counts", [])}
    sp_ctl = next((x for x in tpl["controls"] if x["role"] == "species"), None)
    labels = list(sp_ctl["choices"]) if sp_ctl else []
    for v in counts:
        if v and v not in labels:
            labels.append(v)
    rows = []
    for lab in labels:
        if not lab or lab.lower() in NON_SPECIES_CHOICES:
            continue
        exact = sl.code_for_label(lab) if sl else None
        guess = exact or (sl.guess_code(lab) if sl else None)
        rows.append(dict(key=lab, label=lab, kind="choice", n=counts.get(lab, 0), exact=exact, guess=guess, in_template=bool(sp_ctl and lab in sp_ctl["choices"])))
    # Flag（チェック）の項目: 「Deer = TRUE」のように種ごとのチェックで記録するテンプレート向け。当たりがなければ既定は「追加項目のまま」
    flag_ctls = [x for x in tpl["controls"] if x["role"] == "flag"]
    if flag_ctls:
        true_counts = {}
        if tpl["has_data"]:
            c = _open(path)
            try:
                for x in flag_ctls:
                    try:
                        true_counts[x["data_label"]] = c.execute(f'SELECT COUNT(*) FROM DataTable WHERE LOWER("{x["data_label"]}") = \'true\'').fetchone()[0]
                    except Exception:
                        true_counts[x["data_label"]] = 0
            finally:
                c.close()
        for x in flag_ctls:
            name = x["label"] or x["data_label"]
            exact = sl.code_for_label(name) or sl.code_for_label(x["data_label"]) if sl else None
            guess = exact or ((sl.guess_code(name) or sl.guess_code(x["data_label"])) if sl else None)
            rows.append(dict(key=FLAG_PREFIX + x["data_label"], label=f"{name} (Flag)" if name == x["data_label"] else f"{name} [{x['data_label']}] (Flag)",
                             kind="flag", n=true_counts.get(x["data_label"], 0), exact=exact, guess=guess, in_template=True))
    out["species_map"] = rows
    return out


def import_data(project, path: str, prefix: str, as_verified: bool, by: str | None, overwrite_verified: bool = False, mapping: dict | None = None) -> dict:
    """DataTable を取り込む。種は判定（由来 timelapse。as_verified なら確認済み）、DateTime は日時（出所 csv）、Star はお気に入り、Remarks はメモ、ほかは追加項目の値"""
    db = project.db
    sl, fields = project.species, project.fields
    tpl = read_template(path)
    z = bool(project.setting("z_is_utc"))
    mapping = {k.strip().lower(): v for k, v in (mapping or {}).items()}

    def code_of(label: str):
        mp = mapping.get(label.lower())
        if mp is not None and mp != "__new__":
            return mp or None                 # "" = 使わない
        return sl.code_for_label(label) or sl.guess_code(label)

    sp_col = next((x["data_label"] for x in tpl["controls"] if x["role"] == "species"), None)
    multi_col = next((x["data_label"] for x in tpl["controls"] if x["role"] == "multispecies"), None)
    fav_col = next((x["data_label"] for x in tpl["controls"] if x["role"] == "favorite"), None)
    note_col = next((x["data_label"] for x in tpl["controls"] if x["role"] == "note"), None)
    field_ctls = [x for x in tpl["controls"] if x["role"] in ("field", "flag") and x["data_label"] in fields.by_key()]
    # 種ごとのチェック（Deer = TRUE）: 対応表で種に対応させた Flag
    flag_species: list[tuple[str, str]] = []            # (DataLabel, 種コード)
    for x in tpl["controls"]:
        if x["role"] != "flag":
            continue
        mp = mapping.get(FLAG_PREFIX + x["data_label"].lower())
        if mp is None or mp in ("", FIELD_KEEP):
            continue
        code = sl.code_for_label(x["label"] or x["data_label"]) if mp == "__new__" else mp
        if code:
            flag_species.append((x["data_label"], code))
    fdefs = fields.by_key()
    keymap: dict = {}
    stemmap: dict = {}
    for r in db.q("SELECT id, rel_key, rel_path, orig_rel_path, status FROM items"):
        keymap[r["rel_key"]] = (r["id"], r["status"])
        if r["orig_rel_path"]:
            keymap.setdefault(rel_key(r["orig_rel_path"]), (r["id"], r["status"]))
        stem = rel_key(os.path.splitext(r["rel_path"])[0])
        stemmap[stem] = None if stem in stemmap else (r["id"], r["status"])
    prefix = (prefix or "").replace("\\", "/").strip("/")
    c = _open(path)
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM DataTable ORDER BY Id")]
    finally:
        c.close()
    n_items = n_skipped = n_fav = n_note = n_fields = 0
    unmatched = Counter()
    unknown_labels = Counter()
    with db.tx() as cur:
        for r in rows:
            rp = "/".join(x for x in ((r.get("RelativePath") or "").replace("\\", "/").strip("/"), (r.get("File") or "").strip()) if x)
            if prefix:
                rp = f"{prefix}/{rp}"
            hit = keymap.get(rel_key(rp)) or stemmap.get(rel_key(os.path.splitext(rp)[0]))
            if hit is None:
                unmatched[rp] += 1
                continue
            iid, status = hit
            if status == "verified" and not overwrite_verified:
                n_skipped += 1
                continue
            # 種
            codes = []
            if sp_col:
                label = (r.get(sp_col) or "").strip()
                if label.lower() in NON_SPECIES_CHOICES:
                    more, unk = _split_species(r.get(multi_col) or "", sl, code_of) if multi_col else ([], [])
                    codes += [x for x in more if x not in codes]
                    for u in unk:
                        unknown_labels[u] += 1
                elif label:
                    code = code_of(label)
                    if code:
                        codes.append(code)
                    elif mapping.get(label.lower()) is None:
                        unknown_labels[label] += 1
            for dl, code in flag_species:
                if _flag(r.get(dl)) and code not in codes:
                    codes.append(code)
            if "empty" in codes and len(codes) > 1:
                codes = [x for x in codes if x != "empty"]
            cur.execute("DELETE FROM judgements WHERE item_id=?", (iid,))
            cur.execute("DELETE FROM field_values WHERE item_id=?", (iid,))
            for code in codes:
                cur.execute("INSERT INTO judgements(item_id, species, confidence, origin, model, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                            (iid, code, None, "manual" if as_verified else "csv", os.path.basename(path), now_iso(), by))
            # 日時
            dt = media.parse_dt_any(r.get("DateTime") or "", z)
            if dt and not dt.startswith("1900/"):
                cur.execute("UPDATE items SET datetime=?, datetime_source='csv' WHERE id=?", (dt, iid))
            # お気に入り・メモ・印
            if fav_col and _flag(r.get(fav_col)):
                cur.execute("UPDATE items SET favorite=1 WHERE id=?", (iid,))
                n_fav += 1
            if note_col and (r.get(note_col) or "").strip():
                cur.execute("UPDATE items SET note=? WHERE id=?", ((r.get(note_col) or "").strip(), iid))
                n_note += 1
            if _flag(r.get("DeleteFlag")):
                cur.execute("UPDATE items SET flag='delete' WHERE id=?", (iid,))
            # 追加項目（既定値と同じものは入れない）
            for ctl in field_ctls:
                f = fdefs[ctl["data_label"]]
                v = r.get(ctl["data_label"])
                v = _flag(v) if f["type"] == "check" else ("" if v is None else str(v).strip())
                if v == "" or v == (f.get("default") or ""):
                    continue
                if f["type"] == "number" and v in ("999", "999.9"):
                    continue
                cur.execute("INSERT INTO field_values(item_id, judgement_id, field_key, seq, value) VALUES(?,?,?,?,?)", (iid, None, f["key"], 0, v))
                n_fields += 1
            if as_verified and codes:
                cur.execute("UPDATE items SET status='verified', verified_by=?, verified_at=?, updated_at=? WHERE id=?", (by, now_iso(), now_iso(), iid))
            else:
                cur.execute("UPDATE items SET status=?, verified_by=NULL, verified_at=NULL, updated_at=? WHERE id=?", ("auto" if codes else "new", now_iso(), iid))
            n_items += 1
    from .judge import update_agree
    update_agree(db)
    return dict(rows=len(rows), items=n_items, skipped_verified=n_skipped, favorites=n_fav, notes=n_note, field_values=n_fields,
                unmatched_paths=len(unmatched), unmatched_path_examples=[p for p, _ in unmatched.most_common(5)],
                unmatched_labels=[dict(value=v, count=n) for v, n in unknown_labels.most_common(50)])


# ---------- 書き出し（Timelapse2 の .tdb / .ddb を作る） ----------
TL_VERSION = "2.3.0.4"
TL_STD_LABELS = {"RelativePath", "File", "DateTime", "DeleteFlag", "Species", "Multispecies", "Star", "Remarks"}


def _data_label(name: str, used: set[str]) -> str:
    """Timelapse の DataLabel（英数字と _、先頭は英字）。重なれば番号を付ける"""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_") or "Field"
    if not re.match(r"[A-Za-z]", s):
        s = "F_" + s
    base, n = s, 2
    while s in used:
        s = f"{base}{n}"
        n += 1
    used.add(s)
    return s


def _tl_bool(v) -> str:
    return "true" if str(v).strip().lower() in ("1", "true", "yes", "on") else "false"


def _fmt_dt(s: str | None) -> str:
    """TrapNote の 2026/10/20 19:05:21 → Timelapse の 2026-10-20 19:05:21"""
    if not s:
        return "1900-01-01 12:00:00"
    return s.replace("/", "-")[:19]


def build_export_template(project, species_style: str = "choice", include_fields: bool = True, lang: str = "ja") -> dict:
    """書き出す TemplateTable の行（Timelapse の control）を組み立てる。返り値 dict(controls, species_labels, label_of(code → Species の値), field_cols)"""
    from .species import FIXED
    sl = project.species
    rows = [r for r in sl.rows if r.get("enabled")] + [dict(f, fixed=True) for f in FIXED]
    seen: set[str] = set()
    label_of: dict[str, str] = {}
    labels: list[str] = []
    for r in rows:
        lab = (r.get("csv_label") or r.get("name_en") or r.get("code") or "").strip() or r.get("code")
        if lab in seen:
            lab = r.get("code")
        seen.add(lab)
        label_of[r["code"]] = lab
        labels.append(lab)
    used = set(TL_STD_LABELS)
    controls: list[dict] = []

    def add(type_, data_label, label, default="", tooltip="", width="100", copyable="false", visible="true", lst=""):
        controls.append(dict(Type=type_, DefaultValue=default, Label=label, DataLabel=data_label, Tooltip=tooltip, TXTBOXWIDTH=width, Copyable=copyable, Visible=visible, List=lst))
    add("RelativePath", "RelativePath", "RelativePath", "", "Path from the folder containing the template and image data files to the file", "100", "false")
    add("File", "File", "File", "", "The file name", "100", "false")
    add("DateTime", "DateTime", "DateTime", "1900-01-01 12:00:00", "Date and time taken (Year-Month-Day Hours:Minutes:Seconds)", "160", "false")
    add("DeleteFlag", "DeleteFlag", "Delete?", "false", "Mark a file as one to be deleted. You can then confirm deletion through the Edit Menu", "20", "false", "false")
    add("FixedChoice", "Species", "Species", "", "Species (first species when several; see Multispecies)", "100", "true", "true",
        json.dumps(dict(IncludeEmptyChoice=True, ChoiceListNonEmpty=labels), ensure_ascii=False))
    add("Note", "Multispecies", "Multispecies", "", "Second and later species, separated by ; (TrapNote)", "100", "false")
    flag_cols: dict[str, str] = {}
    if species_style == "flags":
        for r in rows:
            if r.get("fixed"):
                continue
            dl = _data_label(label_of[r["code"]], used)
            flag_cols[r["code"]] = dl
            add("Flag", dl, label_of[r["code"]], "false", f"{r.get('name_ja') or ''} {r.get('name_sci') or ''}".strip() or "species present", "20", "false")
    add("Flag", "Star", "Star", "false", "Favourite (TrapNote)", "20", "false")
    add("Note", "Remarks", "Remarks", "", "Note (TrapNote)", "100", "false")
    field_cols: list[dict] = []
    if include_fields:
        groups_done: set[str] = set()
        for fd in project.fields.fields:
            lab = fd.get("label_ja" if lang == "ja" else "label_en") or fd["key"]
            tip = (fd.get("help") or "") + (" (per species: first species)" if fd["scope"] == "species" else "")
            if fd.get("repeat"):
                g = fd.get("group") or fd["key"]
                if g in groups_done:                      # 組の 2 つ目以降（開始・終了など）は最初の項目の列に "値 (開始-終了)" で入る
                    field_cols.append(dict(key=fd["key"], scope=fd["scope"], repeat=True, group=g, data_label="", kind="note", type=fd["type"]))
                    continue
                groups_done.add(g)
                dl = _data_label(fd["key"], used)
                add("Note", dl, lab, "", tip + " records separated by ;", "100", "false")
                kind = "note"
                field_cols.append(dict(key=fd["key"], scope=fd["scope"], repeat=True, group=g, data_label=dl, kind=kind, type=fd["type"]))
                continue
            dl = _data_label(fd["key"], used)
            if fd["type"] == "number":
                add("Counter", dl, lab, str(fd.get("default") or "0") if str(fd.get("default") or "").strip().lstrip("-").isdigit() else "0", tip, "30", "false")
                kind = "counter"
            elif fd["type"] == "select":
                add("FixedChoice", dl, lab, fd.get("default") or "", tip, "100", "true", "true",
                    json.dumps(dict(IncludeEmptyChoice=True, ChoiceListNonEmpty=list(fd.get("options") or [])), ensure_ascii=False))
                kind = "choice"
            elif fd["type"] == "check":
                add("Flag", dl, lab, _tl_bool(fd.get("default")), tip, "20", "false")
                kind = "flag"
            else:
                add("Note", dl, lab, fd.get("default") or "", tip, "100", "false")
                kind = "note"
            field_cols.append(dict(key=fd["key"], scope=fd["scope"], repeat=False, group="", data_label=dl, kind=kind, type=fd["type"]))
    for i, c in enumerate(controls):
        c["ControlOrder"] = i + 1
        c["SpreadsheetOrder"] = i + 1
    return dict(controls=controls, labels=labels, label_of=label_of, flag_cols=flag_cols, field_cols=field_cols)


def _create_template_table(c: sqlite3.Connection, controls: list[dict]) -> None:
    c.execute("CREATE TABLE TemplateTable ( \nId INTEGER PRIMARY KEY AUTOINCREMENT, \nControlOrder  INTEGER , \nSpreadsheetOrder  INTEGER , \nType TEXT, \nDefaultValue TEXT, \nLabel TEXT, \nDataLabel TEXT, \nTooltip TEXT, \nTXTBOXWIDTH TEXT, \nCopyable TEXT, \nVisible TEXT, \nList TEXT )")
    for ctl in controls:
        c.execute("INSERT INTO TemplateTable(ControlOrder, SpreadsheetOrder, Type, DefaultValue, Label, DataLabel, Tooltip, TXTBOXWIDTH, Copyable, Visible, List) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (ctl["ControlOrder"], ctl["SpreadsheetOrder"], ctl["Type"], ctl["DefaultValue"], ctl["Label"], ctl["DataLabel"], ctl["Tooltip"], ctl["TXTBOXWIDTH"], ctl["Copyable"], ctl["Visible"], ctl["List"]))


def export_timelapse(project, ddb_path: str, f: dict | None = None, species_style: str = "choice", include_fields: bool = True,
                     use_corrected: bool = True, lang: str = "ja") -> dict:
    """Timelapse2 の .tdb（テンプレート）と .ddb（データ）を書く。.ddb と同じ名前の .tdb を隣に作る。
    RelativePath は .ddb を置いたフォルダからの相対パス（Timelapse の約束）なので、.ddb はメディアのルート（かその中のフォルダ）に置く。
    その外にある対象は書けないので飛ばす。動画 1 本 = 1 行: 種が複数なら Species に第 1 種、残りは Multispecies（flags なら種ごとの Flag も）。
    種ごとの追加項目は第 1 種のもの。＋の記録は "値 (開始-終了); ..." の文字に"""
    from .export import _iter_items, _join
    db = project.db
    tpl = build_export_template(project, species_style, include_fields, lang)
    ddb_path = norm(ddb_path)
    if not ddb_path.lower().endswith(".ddb"):
        ddb_path += ".ddb"
    tdb_path = ddb_path[:-4] + ".tdb"
    base_dir = os.path.dirname(ddb_path)
    os.makedirs(base_dir, exist_ok=True)
    for pth in (ddb_path, tdb_path):
        if os.path.exists(pth):
            os.remove(pth)
    # .tdb
    t = sqlite3.connect(tdb_path)
    try:
        _create_template_table(t, tpl["controls"])
        t.execute("CREATE TABLE TemplateInfo ( \nVersionCompatabily TEXT DEFAULT '2.3.0.0' )")
        t.execute("INSERT INTO TemplateInfo(VersionCompatabily) VALUES(?)", (TL_VERSION,))
        t.commit()
    finally:
        t.close()
    # .ddb
    custom = [c for c in tpl["controls"] if c["Type"] not in ("RelativePath", "File", "DateTime", "DeleteFlag")]
    cols_sql = ", ".join([f"[{c['DataLabel']}] TEXT  DEFAULT '{c['DefaultValue'].replace(chr(39), chr(39) * 2)}'" for c in custom])
    d = sqlite3.connect(ddb_path)
    n_rows = n_skip = 0
    try:
        _create_template_table(d, tpl["controls"])
        d.execute(f"CREATE TABLE \"DataTable\" ( Id INTEGER  PRIMARY KEY, RelativePath TEXT  DEFAULT '', File TEXT  DEFAULT '', DateTime DATETIME  DEFAULT '1900-01-01 12:00:00', DeleteFlag TEXT  DEFAULT 'false', {cols_sql} )")
        counters = [c for c in custom if c["Type"] == "Counter"]
        d.execute("CREATE TABLE \"MarkersTable\" ( Id INTEGER  PRIMARY KEY " + "".join(f", [{c['DataLabel']}] TEXT DEFAULT '[]'" for c in counters) + ",  FOREIGN KEY  ( Id )  References DataTable ( Id )  ON Delete Cascade  )")
        d.execute("CREATE TABLE \"ImageSetTable\" ( Id INTEGER    PRIMARY KEY, Log TEXT   DEFAULT 'Add text here', Row TEXT, VersionCompatabily TEXT, SortTerms TEXT, QuickPasteTerms TEXT, BBDisplayThreshold REAL   DEFAULT '-1', RootFolder TEXT   DEFAULT '', SearchTerms TEXT   DEFAULT '{}' )")
        sort_terms = json.dumps([dict(DataLabel="RelativePath", DisplayLabel="Relative Path (folder)", ControlType="RelativePath", IsAscending="true"),
                                 dict(DataLabel="DateTime", DisplayLabel="DateTime", ControlType="DateTime", IsAscending="true")])
        d.execute("INSERT INTO ImageSetTable(Id, Log, Row, VersionCompatabily, SortTerms, QuickPasteTerms, BBDisplayThreshold, RootFolder, SearchTerms) VALUES(1, 'Add text here', '0', ?, ?, '[]', -1, ?, '{}')",
                  (TL_VERSION, sort_terms, os.path.basename(base_dir)))
        sp = project.species.by_code()
        label_of = tpl["label_of"]
        fcols = tpl["field_cols"]
        col_names = ["RelativePath", "File", "DateTime", "DeleteFlag"] + [c["DataLabel"] for c in custom]
        ins = f"INSERT INTO DataTable({', '.join('[' + c + ']' for c in col_names)}) VALUES({', '.join('?' * len(col_names))})"
        batch = []
        for it, js, fv, auto in _iter_items(db, f or {}):
            ab = abs_of(it["rel_path"], project.root)
            rel = rel_of(ab, base_dir)
            if os.path.isabs(rel) or rel.startswith("..") or rel == ab:    # .ddb のフォルダの外
                n_skip += 1
                continue
            folder = os.path.dirname(rel).replace("/", "\\")        # Timelapse（Windows）は \ 区切り
            codes = [j["species"] for j in js]
            codes_sp = [c for c in codes if c != "area"] or codes
            first = codes_sp[0] if codes_sp else None
            vals = {c["DataLabel"]: c["DefaultValue"] for c in custom}
            vals["Species"] = label_of.get(first, first) if first else ""
            vals["Multispecies"] = ";".join(label_of.get(c, c) for c in codes_sp[1:])
            for code, dl in tpl["flag_cols"].items():
                vals[dl] = "true" if code in codes else "false"
            vals["Star"] = _tl_bool(it.get("favorite"))
            vals["Remarks"] = it.get("note") or ""
            ifv = fv.get((it["id"], None), {})
            sfv = fv.get((it["id"], js[0]["id"]), {}) if js else {}
            if first is not None:
                jf = next((j for j in js if j["species"] == first), js[0])
                sfv = fv.get((it["id"], jf["id"]), {})
            groups: dict[str, list[dict]] = {}
            for fc in fcols:
                store = sfv if fc["scope"] == "species" else ifv
                if fc["repeat"]:
                    groups.setdefault(fc["group"] or fc["key"], []).append(fc)
                    continue
                v = _join(store.get(fc["key"]))
                if fc["kind"] == "flag":
                    vals[fc["data_label"]] = _tl_bool(v) if v != "" else _tl_bool(vals.get(fc["data_label"]))
                elif fc["kind"] == "counter":
                    vals[fc["data_label"]] = v if v.strip().lstrip("-").isdigit() else vals.get(fc["data_label"], "0")
                elif v != "":
                    vals[fc["data_label"]] = v
            for gname, members in groups.items():         # ＋の記録: 組の 1 行ごとに "行動 (開始-終了)" を ; でつなぐ
                store = sfv if members[0]["scope"] == "species" else ifv
                n = max([len(store.get(m["key"]) or {}) for m in members], default=0)
                parts = []
                for i in range(n):
                    cells = [(store.get(m["key"]) or {}).get(i, "") for m in members]
                    if not any(cells):
                        continue
                    head, rest = cells[0], [x for x in cells[1:] if x != ""]
                    parts.append(head + (f" ({'-'.join(rest)})" if rest else ""))
                if members[0]["data_label"]:
                    vals[members[0]["data_label"]] = "; ".join(parts)
            dt = (it.get("datetime_corrected") if use_corrected else None) or it.get("datetime")
            row = [folder, os.path.basename(rel), _fmt_dt(dt), "true" if it.get("flag") == "delete" else "false"] + [vals.get(c["DataLabel"], c["DefaultValue"]) for c in custom]
            batch.append(row)
            n_rows += 1
            if len(batch) >= 500:
                d.executemany(ins, batch)
                batch = []
        if batch:
            d.executemany(ins, batch)
        d.commit()
    finally:
        d.close()
    return dict(ddb=ddb_path, tdb=tdb_path, rows=n_rows, skipped=n_skip, species=tpl["labels"], fields=[c["data_label"] for c in fcols if c["data_label"]])
