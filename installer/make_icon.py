"""TrapNote のアイコンを、もとの絵（JPEG など）から作る（確認事項 148 回目）。

使い方（TrapNote のフォルダで。conda 環境 trapnote の Python で）:
    python installer/make_icon.py <もとの絵>                  resources/trapnote.ico と static/icon-32.png、static/icon-256.png を書く
    python installer/make_icon.py <もとの絵> --preview <フォルダ>   書く代わりに、大きさごとの見え方を 1 枚の PNG にして <フォルダ> に置く

もとの絵は、利用者が描いたトラの絵（CCI20211228.jpg。2314×3448）。絵そのものはリポジトリに入れない（入れるのは、できたアイコンだけ）。
作り方:
  - 絵から正方形を 2 つ切り出す。頭の全体（HEAD。48 ピクセル以上のアイコン用）と、目・鼻・口のまわり（FACE。32 ピクセル以下用。
    小さいアイコンに頭の全体を入れると、何の絵か分からなくなる）。切り出す場所は、絵の幅に対する割合で決めてある
  - 角を丸くする（外は透明）。紙の白はそのまま残す
  - できるもの:
      resources/trapnote.ico   16〜256 ピクセルの 7 つの大きさ。デスクトップのショートカット（app.py --make-shortcut）とインストーラの exe のアイコン
      static/icon-32.png、static/icon-256.png   画面のタブと、アプリの窓（タスクバー）のアイコン（index.html、search.html）
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
# 切り出す正方形: (左, 上, 一辺) を、絵の幅に対する割合で
HEAD = (0.053, 0.096, 0.915)
FACE = (0.190, 0.300, 0.640)
SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL = 32                       # この大きさ以下は FACE、それより大きければ HEAD
RADIUS = 0.18                    # 角の丸み（一辺に対する割合）


def crop(im: Image.Image, box: tuple[float, float, float]) -> Image.Image:
    w = im.width
    x, y, s = (int(round(v * w)) for v in box)
    if x < 0 or y < 0 or x + s > im.width or y + s > im.height:
        raise SystemExit(f"切り出す場所が絵の外に出ます: {box}（絵は {im.width}×{im.height}）")
    return im.crop((x, y, x + s, y + s))


def tile(sq: Image.Image, size: int) -> Image.Image:
    """正方形の絵を size ピクセルにし、角を丸くする（外は透明）。縮めるときは、いったん 4 倍の大きさで角を描いてなめらかにする"""
    im = sq.convert("RGB").resize((size, size), Image.LANCZOS).convert("RGBA")
    k = 4
    mask = Image.new("L", (size * k, size * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size * k - 1, size * k - 1], radius=int(size * k * RADIUS), fill=255)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), mask.resize((size, size), Image.LANCZOS)))
    return im


def icons(src: str) -> dict[int, Image.Image]:
    im = Image.open(src).convert("RGB")
    head, face = crop(im, HEAD), crop(im, FACE)
    return {s: tile(face if s <= SMALL else head, s) for s in SIZES}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--preview", help="アイコンを書かずに、見え方を確かめる PNG をこのフォルダに置く")
    a = ap.parse_args()
    ics = icons(a.image)
    if a.preview:
        os.makedirs(a.preview, exist_ok=True)
        for name, bg in (("light", (240, 240, 240, 255)), ("dark", (32, 32, 32, 255))):       # 明るい背景と暗い背景（タスクバー）での見え方
            sheet = Image.new("RGBA", (sum(SIZES) + 12 * (len(SIZES) + 1), max(SIZES) + 24), bg)
            x = 12
            for s in SIZES:
                sheet.alpha_composite(ics[s], (x, 12))
                x += s + 12
            sheet.save(os.path.join(a.preview, f"icon_preview_{name}.png"))
        big = Image.new("RGBA", (16 * 8 + 24 * 8 + 32 * 8 + 48, 32 * 8 + 24), (240, 240, 240, 255))    # 小さい 3 つを 8 倍に拡大（細部を見る）
        x = 12
        for s in (16, 24, 32):
            big.alpha_composite(ics[s].resize((s * 8, s * 8), Image.NEAREST), (x, 12))
            x += s * 8 + 12
        big.save(os.path.join(a.preview, "icon_preview_small_x8.png"))
        print("見え方の PNG を置きました:", a.preview)
        return 0
    ico = os.path.join(APP, "resources", "trapnote.ico")
    ics[256].save(ico, format="ICO", sizes=[(s, s) for s in SIZES], append_images=[ics[s] for s in SIZES if s != 256])
    back = Image.open(ico)
    got = sorted(back.info.get("sizes") or [])
    if got != sorted((s, s) for s in SIZES):
        raise SystemExit(f"ICO に入った大きさが違います: {got}")
    for s in (16, 32):                          # 小さい大きさに、FACE の絵が入っていること（Pillow が 256 から縮め直していないこと）
        back.size = (s, s)
        back.load()
        if back.convert("RGBA").tobytes() != ics[s].tobytes():
            raise SystemExit(f"ICO の {s} ピクセルの絵が、作ったものと違います（Pillow の版が古い？）")
    ics[32].save(os.path.join(APP, "static", "icon-32.png"), optimize=True)
    ics[256].save(os.path.join(APP, "static", "icon-256.png"), optimize=True)
    for p in (ico, os.path.join(APP, "static", "icon-32.png"), os.path.join(APP, "static", "icon-256.png")):
        print(f"書きました: {p}  {os.path.getsize(p)} バイト")
    return 0


if __name__ == "__main__":
    sys.exit(main())
