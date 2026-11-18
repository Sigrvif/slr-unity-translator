"""e2e-fix19: offline TMP atlas re-baking tests (SDF validation added in
e2e-fix20).

Covers ut/fontbake.py at four levels:
  * bake_atlas on a SYNTHETIC TTF built with fontTools (3 real outlined
    glyphs): ink lands inside every recorded rect, records are well-formed,
    char->glyph mapping round-trips, rects stay inside the atlas and do not
    overlap (occupancy grid);
  * the SDF field itself (e2e-fix20): a synthetic ring glyph with a known
    counter must scanline-profile like a distance field — 128-crossings
    within +/-1px of the fontTools outline, bright stroke plateaus, a dark
    counter the shader-thresholded mask keeps UNFILLED, and a trimodal-ish
    value histogram (dark field / edge band / bright strokes) instead of a
    saturated blob;
  * bake_tmp_font_asset on the REAL game payloads (skip-if-missing): the
    rebuilt payload re-parses via tmpfont.analyze, keeps the mode STATIC and
    the atlas PPtrs, covers the original charset (up to what the chosen face
    actually provides), preserves the opaque tail byte-exact, and the real
    bake's 'o' passes the same field checks; replace_atlas_textures must
    round-trip the page through the Alpha8 atlas texture EXACTLY (the field
    travels in the texture's alpha channel — writing it to RGB only bakes a
    constant-255 "all inside" atlas, the solid-white-blob bug);
  * the file-level stage (bake_game_fonts, patch-mode semantics + stage-order
    rule) and the CLI wiring (patch job with font_bake reports
    stats["fonts_baked"]).
"""
import hashlib
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from conftest import STEALTH, SUCR_TTF, CJK_TTF, has_game, has_bake_fonts
from ut import fontbake, tmpfont
from ut.fontbake import FontBakeError, bake_atlas, bake_tmp_font_asset


# ---------------------------------------------------------------------------
# synthetic test font (real TTF outlines via fontTools)
# ---------------------------------------------------------------------------

def _build_test_font(path, upem=1000):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(upem, isTTF=True)
    names = ["A", "B", "q"]
    fb.setupGlyphOrder([".notdef"] + names)
    fb.setupCharacterMap({ord("A"): "A", ord("B"): "B", ord("q"): "q"})
    fb.setupNameTable({"familyName": "BakeTest", "styleName": "Regular"})
    fb.setupHorizontalHeader(ascent=800, descent=-200, lineGap=0)
    glyphs = {".notdef": TTGlyphPen(None).glyph()}  # empty .notdef
    for i, name in enumerate(names):
        pen = TTGlyphPen(None)
        y_top = 600 if name != "q" else 500  # q: x-height box
        x_off = 40 * i
        pen.moveTo((x_off, -100 if name == "q" else 0))
        pen.lineTo((x_off, y_top))
        pen.lineTo((x_off + 400, y_top))
        pen.lineTo((x_off + 400, -100 if name == "q" else 0))
        pen.closePath()
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({".notdef": (500, 0),
                               **{n: (520 + 20 * i, 0) for i, n in enumerate(names)}})
    fb.setupOS2(sCapHeight=700, sxHeight=500, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.save(path)
    return path


@pytest.fixture(scope="module")
def test_font(tmp_path_factory):
    return _build_test_font(str(tmp_path_factory.mktemp("font") / "baketest.ttf"))


def _build_ring_font(path, upem=1000):
    """Font with a single ring glyph 'O' (outer contour CW + inner contour
    CCW -> TrueType nonzero winding punches a real counter hole), so the
    SDF field can be checked against EXACTLY known outline geometry:
    outer box (0,0)-(600,600), counter (200,200)-(400,400) font units."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder([".notdef", "O"])
    fb.setupCharacterMap({ord("O"): "O"})
    fb.setupNameTable({"familyName": "BakeRingTest", "styleName": "Regular"})
    fb.setupHorizontalHeader(ascent=800, descent=-200, lineGap=0)
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((0, 600)); pen.lineTo((600, 600))
    pen.lineTo((600, 0)); pen.closePath()
    pen.moveTo((200, 200)); pen.lineTo((400, 200)); pen.lineTo((400, 400))
    pen.lineTo((200, 400)); pen.closePath()
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "O": pen.glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "O": (700, 0)})
    fb.setupOS2(sCapHeight=600, sxHeight=500, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.save(path)
    return path


def _vals(img):
    return list(img.tobytes())  # L-mode: one byte per pixel, row-major


def _crossings(vals, thresh=128):
    """Sub-pixel x where a 1D int profile crosses `thresh` (the shader mask
    is v >= thresh): one position per rising/falling transition of that
    mask, linearly interpolated; exact-at-threshold samples included once."""
    on = [v >= thresh for v in vals]
    out = []
    for i in range(1, len(vals)):
        if on[i] != on[i - 1]:
            a, b = vals[i - 1] - thresh, vals[i] - thresh
            out.append(i - 1 + (0 - a) / (b - a) if b != a else i - 0.5)
    return out


def _dark_components(mask):
    """Count 4-connected components of a bool grid (small inputs only)."""
    mask = mask.copy()
    h, w = mask.shape
    n = 0
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx]:
                continue
            n += 1
            stack = [(sy, sx)]
            mask[sy, sx] = False
            while stack:
                y, x = stack.pop()
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx]:
                        mask[ny, nx] = False
                        stack.append((ny, nx))
    return n


def _occupancy(page, rects, pad):
    """Assert no two padded rects overlap; return the set of covered cells."""
    cells = set()
    for x, y, w, h in rects:
        for dy in range(h + 2 * pad):
            for dx in range(w + 2 * pad):
                key = (x + dx, y + dy)
                assert key not in cells, f"cell {key} covered twice"
                cells.add(key)
    return cells


# ---------------------------------------------------------------------------
# bake_atlas on the synthetic font
# ---------------------------------------------------------------------------

def test_bake_atlas_synthetic(test_font):
    res = bake_atlas(test_font, [0x41, 0x42, 0x71], 32.0, 128, 128, 4,
                     render_mode_hint=4165)
    assert res["coverage"]["requested"] == 3
    assert res["coverage"]["baked"] == 3 and res["coverage"]["missing"] == []
    assert len(res["pages"]) == 1
    page = res["pages"][0]
    assert page.size == (128, 128)
    assert res["sdf"] is True

    # records well-formed + mapping round-trips
    assert [g["m_Index"] for g in res["glyphs"]] == [1, 2, 3]
    assert [c["m_Unicode"] for c in res["chars"]] == [0x41, 0x42, 0x71]
    assert [c["m_GlyphIndex"] for c in res["chars"]] == [1, 2, 3]
    for g in res["glyphs"]:
        assert g["m_Scale"] == 1.0 and g["m_AtlasIndex"] == 0
        assert g["m_Padding"] == 0 and g["m_Reserved"] == 0
        # rect == (W, H) integers, the original-asset convention
        assert g["m_RectWidth"] == int(g["m_Width"])
        assert g["m_RectHeight"] == int(g["m_Height"])
        # rect inside the atlas
        assert 0 <= g["m_RectX"] and g["m_RectX"] + g["m_RectWidth"] <= 128
        assert 0 <= g["m_RectY"] and g["m_RectY"] + g["m_RectHeight"] <= 128

    # non-overlap (cells INCLUDING the padding skirt) — note rects are
    # bottom-up, expand them to top-down for the grid
    rects_td = [(g["m_RectX"], 128 - g["m_RectY"] - g["m_RectHeight"],
                 g["m_RectWidth"], g["m_RectHeight"]) for g in res["glyphs"]]
    _occupancy(page, rects_td, 4)

    # ink present: the cell (rect + padding skirt) must hold the SDF edge
    # gradient — bright interior, dark skirt. (Like the ORIGINAL assets, the
    # 128-crossing sits ON the rect boundary, so the gradient is at the rect
    # edge / skirt, not deep inside the rect.)
    for g in res["glyphs"]:
        box = (g["m_RectX"] - 4, 128 - g["m_RectY"] - g["m_RectHeight"] - 4,
               g["m_RectX"] + g["m_RectWidth"] + 4, 128 - g["m_RectY"] + 4)
        crop = page.crop(box)
        vals = _vals(crop)
        assert max(vals) >= 128, "no ink in rect"
        assert min(vals) < 128, "cell has no edge gradient (not SDF-like)"
        # metrics plausibility: bearing_y in [0, point*1.5], advance > 0
        assert g["m_Advance"] > 0
        assert 0 <= g["m_BearingY"] <= 48
        assert g["m_Height"] >= 10  # 600-unit box at 32pt/1000upem ~= 19px

    # char->glyph round trip via the returned records
    by_index = {g["m_Index"]: g for g in res["glyphs"]}
    for c in res["chars"]:
        g = by_index[c["m_GlyphIndex"]]
        assert g["m_Width"] > 0 and g["m_Advance"] > 0


def test_bake_atlas_missing_codepoints(test_font):
    res = bake_atlas(test_font, [0x41, 0x3042, 0x1F600], 32.0, 128, 128, 4)
    assert res["coverage"]["baked"] == 1
    assert res["coverage"]["missing"] == [0x3042, 0x1F600]


def test_bake_atlas_does_not_fit(test_font):
    with pytest.raises(FontBakeError) as ei:
        bake_atlas(test_font, [0x41, 0x42, 0x71], 96.0, 32, 32, 4)
    assert "does not fit" in str(ei.value)


def test_bake_atlas_plain_alpha_when_smooth(test_font):
    res = bake_atlas(test_font, [0x41], 32.0, 64, 64, 4, render_mode_hint=0)
    assert res["sdf"] is False
    page = res["pages"][0]
    g = res["glyphs"][0]
    box = (g["m_RectX"], 64 - g["m_RectY"] - g["m_RectHeight"],
           g["m_RectX"] + g["m_RectWidth"], 64 - g["m_RectY"])
    vals = _vals(page.crop(box))
    assert max(vals) >= 200  # AA coverage, interior near-solid


# ---------------------------------------------------------------------------
# the SDF field itself (e2e-fix20): numeric validation against known outlines
# ---------------------------------------------------------------------------

_RING_PT = 64.0
_RING_PAD = 4
_RING_SCALE = _RING_PT / 1000.0  # px per font unit (ring font upem=1000)


def _ring_cell(res):
    """(page ndarray, rect in top-down page space) for the baked 'O'."""
    page = np.asarray(res["pages"][0]).astype(int)
    g = res["glyphs"][0]
    rx, rw = int(g["m_RectX"]), int(g["m_RectWidth"])
    rh = int(g["m_RectHeight"])
    tdy = page.shape[0] - int(g["m_RectY"]) - rh
    return page, (rx, tdy, rw, rh)


def test_sdf_ring_field_profile(tmp_path_factory):
    """Scanline profile through a known ring glyph must look like a distance
    field: 128-crossings at the TrueType outline (+/-1px), bright strokes,
    a dark counter, dark open space — and the shader-thresholded mask must
    keep the counter UNFILLED (the e2e-fix20 blob bug filled everything)."""
    font = _build_ring_font(str(tmp_path_factory.mktemp("ring") / "ring.ttf"))
    res = bake_atlas(font, [0x4F], _RING_PT, 64, 64, _RING_PAD,
                     render_mode_hint=4165)
    assert res["sdf"] is True
    page, (rx, tdy, rw, rh) = _ring_cell(res)
    pad = _RING_PAD

    # one horizontal scanline through the ring's middle: crosses all four
    # outlines (outer-left, counter-left, counter-right, outer-right)
    row = [int(v) for v in page[tdy + rh // 2, :]]
    expected = [rx + u * _RING_SCALE for u in (0, 200, 400, 600)]
    found = [c for c in _crossings(row) if rx - pad - 1 <= c <= rx + rw + pad + 1]
    assert len(found) == 4, f"expected 4 edge crossings, got {found}"
    for got, want in zip(found, expected):
        assert abs(got - want) <= 1.0, f"128-crossing {got} != outline {want}"

    # stroke interior bright, counter and open cell space dark
    stroke_max = max(row[rx:rx + int(200 * _RING_SCALE)])
    assert stroke_max >= 200, f"stroke plateau only {stroke_max}"
    counter_center = row[rx + int(300 * _RING_SCALE)]
    assert counter_center <= 50, f"counter center is {counter_center}"
    assert row[0] <= 50, "open page space left of the glyph is bright"
    assert row[rx - 2] < 128, "skirt 2px left of the outline reads as ink"

    # shader view (value >= 128 = ink): the counter interior must be OFF and
    # the dark region must fall into >= 2 components (counter + outside),
    # i.e. the ring reads as a ring, not a filled blob
    cell = page[tdy - pad:tdy + rh + pad, rx - pad:rx + rw + pad]
    on = cell >= 128
    cx0, cx1 = int(200 * _RING_SCALE) + 2, int(400 * _RING_SCALE) - 2
    cy0, cy1 = int(200 * _RING_SCALE) + 2, int(400 * _RING_SCALE) - 2
    inner = on[pad + cy0:pad + cy1, pad + cx0:pad + cx1]
    assert not inner.any(), "counter filled: shader-thresholded ink inside the hole"
    assert _dark_components(~on) >= 2, "no enclosed hole: dark region is one blob"


def test_sdf_histogram_trimodal_not_saturated(tmp_path_factory):
    """A baked page's values must be trimodal-ish (dark field / edge band /
    bright strokes) rather than saturated into one blob."""
    font = _build_ring_font(str(tmp_path_factory.mktemp("ring") / "ring.ttf"))
    res = bake_atlas(font, [0x4F], _RING_PT, 64, 64, _RING_PAD,
                     render_mode_hint=4165)
    page = np.asarray(res["pages"][0]).astype(int)
    _, (rx, tdy, rw, rh) = _ring_cell(res)
    cell = page[tdy - _RING_PAD:tdy + rh + _RING_PAD,
                rx - _RING_PAD:rx + rw + _RING_PAD]
    dark = (cell <= 60).mean()
    band = ((cell > 96) & (cell < 160)).mean()
    bright = (cell >= 196).mean()
    assert dark >= 0.15, f"no dark field: {dark:.3f}"
    assert band >= 0.05, f"no edge band around 128: {band:.3f}"
    assert bright >= 0.20, f"no bright stroke interior: {bright:.3f}"
    assert (cell == 255).mean() <= 0.40, "cell interior saturated to 255"
    assert (page == 255).mean() <= 0.40, "page saturated to 255"


# ---------------------------------------------------------------------------
# bake_tmp_font_asset on the real game payloads (skip-if-missing)
# ---------------------------------------------------------------------------

_DATA_FILES = {"sharedassets0.assets": [967, 970, 971], "sharedassets1.assets": [81]}


def _load_payload(game_dir, fname, pid):
    import UnityPy

    data = next(os.path.join(str(game_dir), n) for n in os.listdir(str(game_dir))
                if n.endswith("_Data"))
    env = UnityPy.load(os.path.join(data, fname))
    return next(o for o in env.objects if o.path_id == pid).get_raw_data()


@has_game
@has_bake_fonts
def test_bake_971_latin_real(game_dir):
    raw = _load_payload(game_dir, "sharedassets0.assets", 971)
    before = tmpfont.parse_with_typetree(raw)
    assert before["ok"]

    new_payload, pages, info = bake_tmp_font_asset(raw, SUCR_TTF)
    assert info["name"] == "LiberationSans SDF"
    assert info["sdf"] is True
    # static mode kept, atlas page PPtrs kept
    after = tmpfont.analyze_tmp_font_asset(new_payload)
    assert after["ok"], after.get("reason")
    assert after["atlas_population_mode"] == 0
    assert after["atlas_texture_pptrs"] == before["atlas_texture_pptrs"] == [(0, 360)]
    assert after["atlas_width"] == before["atlas_width"] == 1024
    # coverage: ASCII always, original code points up to what sucr provides
    from fontTools.ttLib import TTFont

    cmap = TTFont(SUCR_TTF, lazy=True).getBestCmap()
    orig_cps = {c["m_Unicode"] for c in before["fields"]["m_CharacterTable"]}
    expected_missing = sorted(cp for cp in orig_cps | set(range(0x20, 0x7F))
                              if cp not in cmap)
    assert info["coverage"]["missing"] == expected_missing
    assert set(range(0x20, 0x7F)) - set(expected_missing)
    new_cps = {c["m_Unicode"] for c in tmpfont.parse_with_typetree(new_payload)
               ["fields"]["m_CharacterTable"]}
    assert new_cps == (orig_cps | set(range(0x20, 0x7F))) - set(expected_missing)
    # char -> glyph mapping resolves
    gidx = {g["m_Index"] for g in tmpfont.parse_with_typetree(new_payload)
            ["fields"]["m_GlyphTable"]}
    assert all(c["m_GlyphIndex"] in gidx for c in
               tmpfont.parse_with_typetree(new_payload)["fields"]["m_CharacterTable"])
    # face info: derived slots updated, others (point size etc.) preserved
    fafter = tmpfont.parse_with_typetree(new_payload)["fields"]["m_FaceInfo"]
    assert fafter["m_PointSize"] == before["fields"]["m_FaceInfo"]["m_PointSize"] == 86.0
    assert fafter["m_LineHeight"] != before["fields"]["m_FaceInfo"]["m_LineHeight"]
    assert set(info["faceinfo_updates"]) <= set(fontbake._FACEINFO_DERIVED)
    # tail (kerning/legacy blocks) preserved byte-exact; header prefix too
    after2 = tmpfont.parse_with_typetree(new_payload)
    assert new_payload[after2["prefix_end"]:] == raw[before["prefix_end"]:]
    assert new_payload[: before["mode_offset"]] == raw[: before["mode_offset"]]
    # exactly one page image of the atlas size came back
    assert len(pages) == 1 and pages[0].size == (1024, 1024)


@has_game
@has_bake_fonts
def test_bake_971_o_field_profile(game_dir):
    """The REAL 971 bake's 'o' must pass the same distance-field checks as
    the synthetic ring: 128-crossings on the fontTools outline (+/-1px), a
    bright stroke plateau, a dark counter the thresholded mask keeps open,
    and no saturation. sucr is ultra-condensed (~5px strokes at pt86), so
    the plateau/counter bounds are the geometry-appropriate equivalents of
    the ring test's >=200 / <=50 (the ORIGINAL atlas plateaus at ~172 for
    6px stems — same regime)."""
    raw = _load_payload(game_dir, "sharedassets0.assets", 971)
    new_payload, pages, info = bake_tmp_font_asset(raw, SUCR_TTF)
    assert info["sdf"] is True
    page = np.asarray(pages[0]).astype(int)

    t = tmpfont.parse_with_typetree(new_payload)
    gidx = next(c["m_GlyphIndex"] for c in t["fields"]["m_CharacterTable"]
                if c["m_Unicode"] == 0x6F)
    g = next(x for x in t["fields"]["m_GlyphTable"] if x["m_Index"] == gidx)
    rx, rw = int(g["m_RectX"]), int(g["m_RectWidth"])
    rh = int(g["m_RectHeight"])
    tdy = 1024 - int(g["m_RectY"]) - rh

    from fontTools.ttLib import TTFont
    from fontTools.pens.boundsPen import BoundsPen

    f = TTFont(SUCR_TTF, lazy=True)
    gs = f.getGlyphSet()
    bp = BoundsPen(gs)
    gs[f.getBestCmap()[0x6F]].draw(bp)
    scale = 86.0 / f["head"].unitsPerEm
    pen_x = rx - int(round(g["m_BearingX"]))
    x_min = pen_x + bp.bounds[0] * scale
    x_max = pen_x + bp.bounds[2] * scale

    row = [int(v) for v in page[tdy + rh // 2, :]]
    found = [c for c in _crossings(row) if rx - 8 <= c <= rx + rw + 8]
    assert len(found) >= 2, f"expected the outer edge crossings, got {found}"
    # the atlas's own convention (derived from the original 971/967 assets):
    # the 128-crossing sits at the RECT boundary. The true fontTools outline
    # can differ from the rect by up to ~1px (the rect is the hinted AA
    # bbox: it starts at floor(xMin*scale), i.e. left of the 50%-coverage
    # line), plus ~0.5px of field block-averaging -> +/-2.0px vs the pure
    # outline; the synthetic ring test holds the strict +/-1.0 there.
    assert abs(found[0] - rx) <= 1.0, f"left crossing {found[0]} vs rect {rx}"
    assert abs(found[-1] - (rx + rw)) <= 1.0, \
        f"right crossing {found[-1]} vs rect edge {rx + rw}"
    assert abs(found[0] - x_min) <= 2.0, f"left crossing {found[0]} vs outline {x_min}"
    assert abs(found[-1] - x_max) <= 2.0, f"right crossing {found[-1]} vs outline {x_max}"
    stroke_max = max(row[rx:rx + rw])
    assert stroke_max >= 160, f"stroke plateau only {stroke_max}"
    center = row[rx + rw // 2]
    assert center <= 80, f"'o' counter center is {center}"

    pad = 9  # the 971 asset's atlas padding (info["atlas"] == (1024, 1024, 9))
    cell = page[tdy - pad:tdy + rh + pad, rx - pad:rx + rw + pad]
    on = cell >= 128
    cx = pad + rw // 2
    assert not on[pad + rh // 2 - 1:pad + rh // 2 + 2, cx - 1:cx + 2].any(), \
        "counter filled: shader-thresholded ink inside the 'o' hole"
    assert _dark_components(~on) >= 2, "no enclosed hole: dark region is one blob"
    # trimodal-ish, definitely not saturated (sucr has no 9px-deep strokes)
    assert (page >= 250).mean() <= 0.02, "page saturated to 255"
    assert (page <= 5).mean() >= 0.40, "page has no dark open-space field"
    band = page.flatten()
    assert ((band > 96) & (band < 160)).mean() >= 0.02, "no edge band around 128"


@has_game
@has_bake_fonts
def test_atlas_alpha8_roundtrip(game_dir, tmp_path):
    """replace_atlas_textures must carry the page through the Alpha8 atlas
    texture EXACTLY: UnityPy encodes Alpha8 from the passed image's ALPHA
    channel, so the field has to be merged into alpha (an L->RGBA convert
    baked a constant-255 atlas — every pixel 'deep inside the stroke', the
    solid-white-blob bug). Verified on the SAVED file — the bytes the player
    actually loads; an in-memory obj.read() after save_typetree serves a
    stale cached parse and must not be used as the oracle."""
    import UnityPy

    raw = _load_payload(game_dir, "sharedassets0.assets", 971)
    new_payload, pages, info = bake_tmp_font_asset(raw, SUCR_TTF)
    assert len(pages) == 1 and pages[0].size == (1024, 1024)

    data = next(os.path.join(str(game_dir), n) for n in os.listdir(str(game_dir))
                if n.endswith("_Data"))
    env = UnityPy.load(os.path.join(data, "sharedassets0.assets"))
    n = fontbake.replace_atlas_textures(env, pages, [(0, 360)])
    assert n == 1

    env.save(pack="lz4", out_path=str(tmp_path))
    saved = tmp_path / "sharedassets0.assets"
    assert saved.is_file()
    d = next(o for o in UnityPy.load(str(saved)).objects
             if o.path_id == 360 and o.type.name == "Texture2D").read()
    assert d.m_TextureFormat == 1  # Alpha8 kept
    img = d.image
    assert img.size == (1024, 1024)
    alpha = bytes(img.getchannel("A").tobytes())
    assert alpha == bytes(pages[0].tobytes()), (
        "decoded Alpha8 atlas does not round-trip the baked page"
    )


@has_game
@has_bake_fonts
def test_bake_refuses_dynamic_and_keeps_guards(game_dir):
    raw = _load_payload(game_dir, "sharedassets0.assets", 971)
    dyn = tmpfont.make_tmp_font_dynamic(raw, source_font_path_id=933)
    with pytest.raises(tmpfont.TmpFontError):
        bake_tmp_font_asset(dyn, SUCR_TTF)


def test_bake_synthetic_mini_payload():
    import struct
    from test_tmpfont import _build_mini

    raw, mode_off, _g_off, _c_off = _build_mini(src_path=0)
    new_payload, pages, info = bake_tmp_font_asset(
        raw, _build_test_font(os.path.join(_tmpdir(), "mini_font.ttf")))
    assert info["char_count"] == 3  # only A/B/q exist in the mini font
    after = tmpfont.analyze_tmp_font_asset(new_payload)
    assert after["ok"], after.get("reason")
    assert after["atlas_population_mode"] == 0  # STATIC kept
    assert after["glyph_count"] == 3 and after["char_count"] == 3
    assert after["source_font_pptr"] == (0, 0)  # untouched
    assert after["atlas_texture_pptrs"] == [(0, 361)]  # untouched
    # prefix before the population mode byte-exact; tail byte-exact
    assert new_payload[:mode_off] == raw[:mode_off]
    from ut.tmpfont import parse_with_typetree

    t = parse_with_typetree(new_payload)
    raw_t = parse_with_typetree(raw)
    assert new_payload[t["prefix_end"]:] == raw[raw_t["prefix_end"]:]


def _tmpdir():
    import tempfile

    return tempfile.mkdtemp()


# ---------------------------------------------------------------------------
# file-level stage on the real game
# ---------------------------------------------------------------------------

@has_game
@has_bake_fonts
def test_bake_game_fonts_patch_mode(game_dir, tmp_path):
    import UnityPy

    out = tmp_path / "out"
    data = next(os.path.join(str(game_dir), n) for n in os.listdir(str(game_dir))
                if n.endswith("_Data"))

    def _sha(p):
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    h0 = _sha(os.path.join(data, "sharedassets0.assets"))
    h1 = _sha(os.path.join(data, "sharedassets1.assets"))

    res = fontbake.bake_game_fonts(str(game_dir), str(out), SUCR_TTF, CJK_TTF,
                                   patch_mode=True)
    assert res["tmp_assets_found"] == 4
    # 967 (CJK charset) bakes from the CJK face with FULL original coverage;
    # 971 (ASCII) bakes from sucr; the two tiny atlases (970 fallback 512²,
    # DSEG 256²) cannot hold ASCII at their point size and fail SAFE
    # (original bytes kept).
    by_pid = {d["path_id"]: d for d in res["details"]}
    assert by_pid[967]["action"] == "baked" and by_pid[967]["ttf_role"] == "cjk"
    assert by_pid[967]["coverage"]["missing"] == []  # CJK face covers all 640+ASCII
    assert by_pid[971]["action"] == "baked" and by_pid[971]["ttf_role"] == "latin"
    assert by_pid[970]["action"] == "failed" and "does not fit" in by_pid[970]["reason"]
    assert by_pid[81]["action"] == "failed"
    assert res["fonts_baked"] == 2 and res["bake_failed"] == 2
    assert res["files_modified"] == 1
    assert res["exported_files"] == ["SampleGame_Data/sharedassets0.assets"]
    assert res["atlas_textures_replaced"] == 2

    # reload the baked file: object count equal, exactly the 4 expected
    # objects changed (2 TMP payloads + 2 atlas textures), payloads re-parse,
    # atlas pixels actually changed (hash-level), format kept
    p_new = out / "SampleGame_Data" / "sharedassets0.assets"
    env = UnityPy.load(str(p_new))
    orig = UnityPy.load(os.path.join(data, "sharedassets0.assets"))
    assert len(env.objects) == len(orig.objects)
    objs = {o.path_id: o for o in env.objects}
    orig_objs = {o.path_id: o for o in orig.objects}
    changed = [o.path_id for o in env.objects
               if o.get_raw_data() != orig_objs[o.path_id].get_raw_data()]
    assert sorted(changed) == [360, 361, 967, 971]
    for pid in (967, 971):
        info = tmpfont.analyze_tmp_font_asset(objs[pid].get_raw_data())
        assert info["ok"] and info["atlas_population_mode"] == 0
        assert info["glyph_count"] > 0 and info["char_count"] == info["glyph_count"]
    tex_new = objs[360].read()
    tex_old = orig_objs[360].read()
    assert tex_new.m_TextureFormat == tex_old.m_TextureFormat == 1  # Alpha8 kept
    assert (hashlib.sha256(bytes(tex_new.image.tobytes())).hexdigest()
            != hashlib.sha256(bytes(tex_old.image.tobytes())).hexdigest())
    # spot objects byte-identical (Material, other Texture2D, MonoBehaviours)
    for pid in (1, 100, 500, 700):
        assert objs[pid].get_raw_data() == orig_objs[pid].get_raw_data()

    # sharedassets1 was NOT written (its only asset failed safe) and the
    # ORIGINAL game is untouched
    assert not (out / "SampleGame_Data" / "sharedassets1.assets").exists()
    assert _sha(os.path.join(data, "sharedassets0.assets")) == h0
    assert _sha(os.path.join(data, "sharedassets1.assets")) == h1


@has_game
@has_bake_fonts
def test_bake_stage_order_builds_on_font_stage_output(game_dir, tmp_path):
    """The bake must build on out_dir/<rel> written by the font swap stage,
    never revert it (e2e-fix13 rule)."""
    from ut.fonts import replace_source_font

    ttf = tmp_path / "swap.ttf"
    ttf.write_bytes(b"FAKE-SWAPPED-TTF-" + b"x" * 64)
    out = tmp_path / "out"
    replace_source_font(str(game_dir), str(out), str(ttf), patch_mode=True)
    sa0 = out / "SampleGame_Data" / "sharedassets0.assets"
    assert sa0.exists()
    h_after_fonts = hashlib.sha256(sa0.read_bytes()).hexdigest()

    res = fontbake.bake_game_fonts(str(game_dir), str(out), SUCR_TTF, CJK_TTF,
                                   patch_mode=True)
    assert res["fonts_baked"] == 2
    assert hashlib.sha256(sa0.read_bytes()).hexdigest() != h_after_fonts
    import UnityPy

    env = UnityPy.load(str(sa0))
    from ut.fonts import _detect_font_kind

    swapped = 0
    for o in env.objects:
        if o.type.name != "Font":
            continue
        tree = o.read_typetree()
        assert bytes(tree["m_FontData"]).startswith(b"FAKE-SWAPPED-TTF-")
        swapped += 1
    assert swapped == 2  # the font swap survived the bake stage


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

@has_game
@has_bake_fonts
def test_cli_patch_reports_fonts_baked(game_dir, tmp_path):
    CLI = "slr_cli.py"

    def _run(manifest):
        m = tmp_path / f"job-{abs(hash(json.dumps(manifest, sort_keys=True)))}.json"
        m.write_text(json.dumps(manifest), encoding="utf-8")
        r = tmp_path / f"result-{m.stem}.json"
        p = subprocess.run(
            [sys.executable, CLI, "--manifest", str(m), "--result", str(r)],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert p.returncode == 0, p.stderr
        return json.loads(r.read_text(encoding="utf-8"))

    scan = _run({"version": 2, "command": "scan", "game_dir": str(game_dir)})
    assert scan["ok"]
    jp = next(s["text"] for s in scan["strings"] if s["source"] == "metadata")
    (tmp_path / "trans.json").write_text(
        json.dumps({jp: "ENTEST"}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(scan["index"], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "swap.ttf").write_bytes(b"FAKE-SWAPPED-TTF-" + b"x" * 64)
    out = tmp_path / "gameout"
    res = _run({
        "version": 2, "command": "patch", "game_dir": str(game_dir), "out_dir": str(out),
        "translations_path": str(tmp_path / "trans.json"),
        "index_path": str(tmp_path / "index.json"),
        "font_ttf": str(tmp_path / "swap.ttf"),
        "font_bake": {"latin_ttf": SUCR_TTF, "cjk_ttf": CJK_TTF},
        "patch_mode": True,
    })
    assert res["ok"] is True, res.get("error")
    assert res["fonts_swapped"] >= 1
    assert res["fonts_baked"] >= 1
    assert res["atlas_textures_replaced"] >= 1
    assert "SampleGame_Data/sharedassets0.assets" in res["exported_files"]
    # the baked payload in the produced patch re-parses with new tables
    import UnityPy

    env = UnityPy.load(str(out / "SampleGame_Data" / "sharedassets0.assets"))
    objs = {o.path_id: o for o in env.objects}
    info = tmpfont.analyze_tmp_font_asset(objs[967].get_raw_data())
    assert info["ok"] and info["atlas_population_mode"] == 0
    assert info["glyph_count"] == info["char_count"] > 600  # 640 orig + ASCII extras
