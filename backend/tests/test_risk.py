"""v2.8 risk tagging: discovery.string_risk rules + payload plumbing.

The tag scheme (SLR Translator's): "" display text and "green" code literals
translate by default (SLR's batch blacklist defaults skip yellow, red, gray,
gold and darkred only); "yellow" = likely safe, confirm first; "red" =
likely structural. The JS project builder ranks per unique text into the
row tags (red > yellow > "" > green, then the no-Japanese variants gray/
gold/darkred) — ranked in addon/tests/projectBuilder.test.js.
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # `import ut`

from ut.discovery import (  # noqa: E402
    DISPLAY_TT_FIELDS, RISK_RANK, _shape_risk, string_risk, worst_risk,
)


# --- per-source rules --------------------------------------------------------

def test_code_literals_are_green():
    # mono (Managed DLL #US heaps) and metadata (IL2CPP global-metadata.dat)
    # literals: the purpose-built writers make them confirmed-safe script
    # text — translated by default, never downgraded by content shape.
    assert string_risk("mono", "", "", "しかし、今日からは性処理のための肉便器となるのだ") == "green"
    assert string_risk("metadata", "", "", "研究施設へようこそ") == "green"
    # even a filename-shaped or table-shaped literal stays green
    assert string_risk("mono", "", "", "chara_a.png") == "green"


def test_typetree_display_fields_untagged():
    for leaf in DISPLAY_TT_FIELDS:
        assert string_risk("typetree", leaf, "", "こんにちは、先輩") == "", leaf


def test_typetree_non_display_is_yellow():
    assert string_risk("typetree", "m_TableData", "", "はじめての検査") == "yellow"
    assert string_risk("typetree", "narration", "", "…そうかもしれない") == "yellow"


def test_typetree_identifier_leaves_are_red():
    for leaf in ("m_Name", "name", "key", "cue", "event"):
        assert string_risk("typetree", leaf, "", "楓") == "red", leaf


def test_raw_is_dialogue_by_default():
    # raw JP records ARE the displayed text of typetree-less components —
    # the main dialogue of many games; untagged unless a shape flags them.
    assert string_risk("raw", "", "", "…さっさと射精してください") == ""


def test_textasset_textfile_lines_are_dialogue():
    assert string_risk("textasset", "", "L42", "おはよう") == ""
    assert string_risk("textfile", "", "L7", "行ってきます！") == ""


def test_textasset_textfile_json_csv_are_yellow():
    assert string_risk("textasset", "", "$.dialogue[3].text", "おはよう") == "yellow"
    assert string_risk("textasset", "", "r12c5", "楓") == "yellow"
    assert string_risk("textfile", "", "r0c1", "陽葵") == "yellow"


# --- shape upgrades ----------------------------------------------------------

def test_shape_table_row_is_red():
    # real case (a Mono game raw record): a quoted name-table row
    assert _shape_risk('"楓","陽葵","葵","結菜","陽菜"') == "red"
    assert _shape_risk("楓\t陽葵\t葵\t結菜") == "red"
    assert _shape_risk("ただの検査なんだよね？") == ""


def test_shape_asset_filename_is_red():
    assert _shape_risk("girl.psd（対応無し）") == "red"
    assert _shape_risk("bg_school.png") == "red"
    assert _shape_risk("テキスト") == ""


def test_shape_beats_yellow_but_not_green():
    assert string_risk("typetree", "narration", "", '"楓","陽葵","葵"') == "red"
    assert string_risk("raw", "", "", "chara_a.psd を読み込み") == "red"
    assert string_risk("mono", "", "", '"楓","陽葵"') == "green"


# --- ranking -----------------------------------------------------------------

def test_worst_risk_ranking():
    assert RISK_RANK == {"red": 3, "yellow": 2, "": 1, "green": 0}
    assert worst_risk(["green", "green"]) == "green"
    assert worst_risk(["green", ""]) == ""          # display occurrence dominates
    assert worst_risk(["", "yellow"]) == "yellow"
    assert worst_risk(["green", "red"]) == "red"
    assert worst_risk([]) == ""


# --- the tags ride the real scan payload -------------------------------------

def test_scan_payload_carries_risk(game_dir, has_game):
    from ut import discovery
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False,
                             scan_demosaic=False)
    assert rep.strings, "expected string hits"
    tagged = {h.risk for h in rep.strings}
    assert tagged <= {"", "green", "yellow", "red"}
    assert any(h.risk == "green" for h in rep.strings), \
        "the IL2CPP example game must carry metadata (green) literals"
    # every hit's risk is consistent with the rules
    for h in rep.strings:
        leaf = h.tokens[-1] if h.tokens else ""
        expect = string_risk(h.source, str(leaf) if h.source == "typetree" else "",
                             h.key or "", h.text)
        assert h.risk == expect, (h.source, h.field, h.text[:30])
