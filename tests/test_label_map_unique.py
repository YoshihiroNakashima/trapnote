"""SpeciesNet のラベル → 種コードの対応（detect.build_label_map）。
属・科だけのラベルは、その属・科で種リストに使う種が 1 つだけならその種に寄せる。哺乳類（綱）や齧歯目（目）の行があっても寄せる（92 回目までは、
哺乳類の行があると「muntjac species」などが哺乳類に入っていた）"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from trapnote.detect import build_label_map  # noqa: E402

LABELS = [
    "u01;mammalia;cetartiodactyla;cervidae;muntiacus;reevesi;reeves' muntjac",
    "u02;mammalia;cetartiodactyla;cervidae;muntiacus;;muntjac species",
    "u03;mammalia;cetartiodactyla;cervidae;;;cervidae family",
    "u04;mammalia;cetartiodactyla;cervidae;cervus;nippon;sika deer",
    "u05;mammalia;cetartiodactyla;cervidae;cervus;;cervus species",
    "u06;mammalia;cetartiodactyla;cervidae;cervus;elaphus;red deer",
    "u07;mammalia;cetartiodactyla;suidae;;;suidae family",
    "u08;mammalia;cetartiodactyla;suidae;sus;scrofa;wild boar",
    "u09;mammalia;carnivora;mustelidae;;;weasel family",
    "u10;mammalia;carnivora;mustelidae;meles;;meles species",
    "u11;mammalia;carnivora;mustelidae;martes;melampus;japanese marten",
    "u12;mammalia;carnivora;mustelidae;mustela;itatsi;japanese weasel",
    "u13;mammalia;rodentia;sciuridae;;;sciuridae family",
    "u14;mammalia;rodentia;sciuridae;sciurus;lis;japanese squirrel",
    "u15;mammalia;rodentia;muridae;rattus;;rattus species",
    "u16;mammalia;;;;;mammal",
    "u17;aves;;;;;bird",
    "u18;;;;;;blank",
    "u19;mammalia;primates;hominidae;homo;sapiens;human",
]


def _rows(**enabled):
    base = [
        ("deer", "cervus nippon", "種"), ("muntjac", "muntiacus reevesi", "種"), ("boar", "sus scrofa", "種"),
        ("badger", "meles", "種"), ("marten", "martes", "種"), ("weasel", "mustela", "種"), ("squirrel", "sciurus", "種"),
        ("mustelidae", "mustelidae", "科"), ("cervidae", "cervidae", "科"), ("suidae_fam", "suidae", "科"),
        ("rodent", "rodentia", "目"), ("mammal", "mammalia", "綱"), ("bird", "aves", "綱"),
    ]
    off = {"mustelidae", "cervidae", "suidae_fam"}
    return [dict(code=c, speciesnet=sn, rank=rk, enabled=enabled.get(c, c not in off)) for c, sn, rk in base]


def _map(rows):
    out = build_label_map(LABELS, rows)
    return {lab.split(";")[6]: code for lab, code in zip(LABELS, out)}


def test_unique_genus_and_family_beat_class_row():
    m = _map(_rows())
    assert m["reeves' muntjac"] == "muntjac" and m["sika deer"] == "deer" and m["wild boar"] == "boar"
    assert m["muntjac species"] == "muntjac"          # 属で唯一（以前は哺乳類）
    assert m["cervus species"] == "deer"
    assert m["suidae family"] == "boar"               # 科で唯一
    assert m["sciuridae family"] == "squirrel"        # 科で唯一（齧歯目の行より優先）
    assert m["cervidae family"] == "mammal"           # シカ科にはニホンジカとキョン → 決められないので上位の行
    assert m["weasel family"] == "mammal"             # イタチ科にはアナグマ・テン・イタチ
    assert m["red deer"] == "mammal"                  # 同じ属でも別の種のラベルは寄せない
    assert m["meles species"] == "badger"             # 属の行（meles）にそのまま当たる
    assert m["rattus species"] == "rodent"
    assert m["mammal"] == "mammal" and m["bird"] == "bird" and m["blank"] == "empty" and m["human"] == "human"


def test_explicit_family_row_and_without_class_row():
    m = _map(_rows(mustelidae=True, suidae_fam=True, cervidae=True))
    assert m["weasel family"] == "mustelidae"         # 使う科の行があればそれ
    assert m["suidae family"] == "suidae_fam"         # 科の行を使うと決めたなら、唯一の種より科の行
    assert m["cervidae family"] == "cervidae"
    assert m["muntjac species"] == "muntjac"          # 属で唯一なら、科の行より種
    m2 = _map(_rows(mammal=False))
    assert m2["muntjac species"] == "muntjac" and m2["suidae family"] == "boar"
    assert m2["cervidae family"] == "unknown"         # 当たる行がない
