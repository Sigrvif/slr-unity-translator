"""Latin advance scaling: the swapped CJK face prices Latin wider than the
pixel fonts eroge bubbles were designed around; auto-sized boxes inflate.
The scaler edits the TTF's hmtx advances for ASCII only (shapes untouched).
"""
import io
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from fontTools.ttLib import TTFont

from ut.fonts import scale_latin_advances

SLR = r"<dev-root>/SLR Translator\www\core\SEP\SLRCondensedCJK-Regular.ttf"
has_slr = pytest.mark.skipif(not os.path.isfile(SLR), reason="SLR font not present")


def _advances(ttf_bytes, chars="nWiあ"):
    f = TTFont(io.BytesIO(ttf_bytes))
    cmap = f.getBestCmap()
    hmtx = f["hmtx"]
    return {c: hmtx.metrics[cmap.get(ord(c))][0] for c in chars if cmap.get(ord(c))}


@has_slr
def test_scales_latin_only():
    data = open(SLR, "rb").read()
    new, count = scale_latin_advances(data, 0.6)
    assert count >= 90
    adv = _advances(new)
    orig = _advances(data)
    for ch in ("n", "W", "i"):
        assert adv[ch] == max(1, round(orig[ch] * 0.6)), (ch, orig[ch], adv[ch])
    assert adv["あ"] == orig["あ"]           # non-Latin untouched


@has_slr
def test_scale_one_is_identity():
    data = open(SLR, "rb").read()
    new, count = scale_latin_advances(data, 1.0)
    assert count == 0
    assert _advances(new) == _advances(data)


@has_slr
def test_floor_keeps_advance_positive():
    data = open(SLR, "rb").read()
    new, _count = scale_latin_advances(data, 0.001)
    adv = _advances(new, "ni")
    assert all(v >= 1 for v in adv.values())
