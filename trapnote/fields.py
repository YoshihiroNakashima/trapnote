"""追加項目の定義（<プロジェクト>/TrapNote/fields.json）

項目 = dict(key, label_ja, label_en, type, scope, repeat, group, options, default, required, order)
  type: select（1 つ）/ multiselect / check / number / text / time（動画内の秒）
  scope: item（対象ごと）/ species（種の行ごと）
  repeat: True なら ＋ で何件でも記録できる。同じ group の repeat 項目は 1 組として 1 行に並ぶ（例: 行動 + 開始秒 + 終了秒）
"""
from __future__ import annotations

import os
import re

from .config import read_json, write_json

TYPES = ("select", "multiselect", "check", "number", "text", "time")

DEFAULT_FIELDS = [
    dict(key="count", label_ja="個体数", label_en="Count", type="number", scope="species", repeat=False, group="",
         options=[], default="1", required=False),
    dict(key="behaviour", label_ja="行動", label_en="Behaviour", type="select", scope="species", repeat=True, group="behaviour",
         options=["移動", "採食", "休息", "警戒", "その他"], default="", required=False),
    dict(key="behaviour_start", label_ja="開始（秒）", label_en="Start (s)", type="time", scope="species", repeat=True, group="behaviour",
         options=[], default="", required=False),
    dict(key="behaviour_end", label_ja="終了（秒）", label_en="End (s)", type="time", scope="species", repeat=True, group="behaviour",
         options=[], default="", required=False),
    dict(key="sex", label_ja="性別", label_en="Sex", type="select", scope="species", repeat=False, group="",
         options=["不明", "オス", "メス"], default="", required=False),
    dict(key="juvenile", label_ja="幼獣あり", label_en="Juvenile present", type="check", scope="species", repeat=False, group="",
         options=[], default="", required=False),
    dict(key="memo", label_ja="メモ", label_en="Memo", type="text", scope="item", repeat=False, group="",
         options=[], default="", required=False),
]


def _norm(f: dict, i: int) -> dict:
    key = re.sub(r"[^A-Za-z0-9_]+", "_", str(f.get("key") or f.get("label_en") or f.get("label_ja") or f"field{i}").strip()).strip("_") or f"field{i}"
    t = f.get("type") if f.get("type") in TYPES else "text"
    opts = f.get("options") or []
    if isinstance(opts, str):
        opts = [o.strip() for o in re.split(r"[,\n、]", opts) if o.strip()]
    return dict(key=key, label_ja=str(f.get("label_ja") or key), label_en=str(f.get("label_en") or f.get("label_ja") or key),
                type=t, scope="species" if f.get("scope") == "species" else "item",
                repeat=bool(f.get("repeat")), group=str(f.get("group") or ""),
                options=[str(o) for o in opts], default=str(f.get("default") or ""), required=bool(f.get("required")),
                help=str(f.get("help") or ""), order=int(f.get("order") if f.get("order") is not None else i))


class Fields:
    def __init__(self, path: str):
        self.path = path
        d = read_json(path, None)
        if isinstance(d, dict) and isinstance(d.get("fields"), list):
            self.fields = [_norm(f, i) for i, f in enumerate(d["fields"])]
        else:
            self.fields = [_norm(f, i) for i, f in enumerate(DEFAULT_FIELDS)]
            if not os.path.isfile(path):
                self.save()
        self._dedupe()

    def _dedupe(self) -> None:
        seen, out = set(), []
        for f in self.fields:
            if f["key"] in seen:
                continue
            seen.add(f["key"])
            out.append(f)
        self.fields = sorted(out, key=lambda f: f["order"])

    def save(self) -> None:
        write_json(self.path, dict(fields=self.fields))

    def update(self, fields: list[dict]) -> None:
        self.fields = [_norm(f, i) for i, f in enumerate(fields)]
        self._dedupe()
        self.save()

    def by_key(self) -> dict[str, dict]:
        return {f["key"]: f for f in self.fields}

    def of_scope(self, scope: str) -> list[dict]:
        return [f for f in self.fields if f["scope"] == scope]

    def to_dict(self) -> dict:
        return dict(fields=self.fields)
