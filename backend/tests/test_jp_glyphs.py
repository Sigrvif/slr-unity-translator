"""Gate tests for font-glyph characters (v2.6c).

JP games re-purpose box-drawing/arrows/symbols as font glyphs — moan marks,
emphasis, decorations (real case: あっ┻…んっ┻, where ┻ renders as the little
tremolo mark in a speech bubble). The strict text gate used to count them
against the 85%-normal ratio and rejected the SHORT strings wholesale, so
their rows never reached the grid.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from ut import jp


def test_glyph_lines_pass():
    assert jp.looks_like_text("あっ┻…んっ┻")
    assert jp.looks_like_text("あっ…┻")
    assert jp.looks_like_text("んっ┻♡")
    assert jp.looks_like_text("うわぁあああ↑↑")


def test_glyphs_alone_are_still_not_text():
    # the kana/kanji requirement is untouched: decoration-only noise stays out
    assert not jp.looks_like_text("┻┻┻┻")
    assert not jp.looks_like_text("→←↑↓⇔")
    assert not jp.looks_like_text("♠♣♥♦")


def test_garbage_ranges_still_rejected():
    assert not jp.looks_like_text("あっ\uE000んっ")      # private use
    assert not jp.looks_like_text("あっ\x01んっ")        # control
    assert not jp.looks_like_text("한글テスト")          # hangul


def test_raw_bubble_records_now_collected():
    """The real records from the game's typetree-less bubble-table
    MonoBehaviour (sharedassets4 #244) must pass the raw gate."""
    records = [
        "あっ┻…赤ちゃんできちゃうっ┻",
        "あっ┻…ダメっ┻",
        "あっ…┻",
        "あっ┻…んっ┻",
        "あっ┻…あん┻",
        "あっ┻…はぁっ┻",
        "あぁ…っ┻…んあっ┻",
    ]
    for s in records:
        assert jp.contains_japanese(s)
        assert jp.looks_like_text(s), repr(s)
