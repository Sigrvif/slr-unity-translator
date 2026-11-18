"""Offline TMP glyph-atlas re-baking (e2e-fix19, SDF encoder reworked in
e2e-fix20).

The dynamic-conversion approach (ut/tmpfont.py, e2e-fix14..17) is DEAD for
this player: three controlled exports proved this TMP build does NOT
re-rasterize glyphs from m_SourceFontFile at runtime — dynamic-converted
assets render blank while static ones serve their baked glyphs. The only way
to re-font a static TMP asset is therefore to REPLACE what it serves:

  1. rasterize every needed code point from an arbitrary TTF at the asset's
     original point size,
  2. encode the pixels the way the ORIGINAL atlas does (a signed distance
     field for SDF render modes, plain alpha otherwise),
  3. skyline-pack the glyph cells into the EXISTING atlas page dimensions,
  4. rebuild the m_GlyphTable / m_CharacterTable blocks (+ used/free rect
     lists) and the font-dependent FaceInfo numerics — keeping the payload
     in STATIC population mode — via the same typetree splice ut/tmpfont.py
     validated (schema-located fields, opaque byte tail preserved),
  5. write the new pages into the referenced atlas Texture2D objects
     (UnityPy set_image, with the field merged into the ALPHA channel —
     see replace_atlas_textures: Alpha8 atlases encode from the image's
     alpha only, and an L->RGBA convert bakes a constant 255, the
     "solid white blob" bug fixed in e2e-fix20).

The player then renders text in the new font with NO runtime re-rendering.

Derivations (from the real game's assets — sharedassets0 967 "NotoSansJP-
Regular SDF" pt64/pad8/atlas 4096²/render 4165, 971 "LiberationSans SDF"
pt86/pad9/atlas 1024²/render 4169, both texture format Alpha8):

* Glyph record (52 B): the 5 metric floats are, in order, WIDTH, HEIGHT,
  BEARING-X, BEARING-Y, ADVANCE — all in PIXELS at the asset's point size.
  Evidence: on 971 every value is an integer and every atlas rect equals
  (W, H) exactly (250/250); 'A' has BY=59 == cap height, 'a' BY=46 ≈
  x-height with 1px overshoot, 'j' bottom lands at -18px ≈ hhea descent;
  advances match FreeType's hinted metrics of a metric-compatible face
  (Arial) for 230/250 glyphs (the outliers are the game's Liberation
  build differing from modern Arial by 1-4px). On runtime-grown 967 the
  values are 26.6-fixed fractions (e.g. advance 14.34375 = 918/64) — the
  FreeType pixel pipeline, unrounded. WE EMIT the Font-Asset-Creator
  convention: integer pixel metrics (round()), like the stock 971.
* Atlas rect = the TIGHT glyph bitmap (x, y, W, H); y counts from the
  BOTTOM of the texture (Unity UV space). Evidence: per-glyph contrast
  between the rect interior and its padding ring scores 69.3 (y-from-
  bottom) vs 9.6 (y-from-top) on 971, and 98.3 vs 36.9 on 967. The SDF
  padding skirt lives OUTSIDE the recorded rect, in the cell margin (the
  packer reserves (W + 2*pad, H + 2*pad) and the encoded tile puts the
  glyph bbox at (pad, pad) inside that cell — skirt == pad on all sides).
* Atlas encoding: a real signed distance field with the edge at 128,
  normalized over the padding: value = clamp(round(128 + d * 128 / pad))
  where d is the signed distance to the glyph OUTLINE in atlas pixels,
  positive INSIDE the glyph shape (the stroke ring of an 'o'; the counter
  is OUTSIDE the shape and encodes dark). Evidence: straight-edge
  profiles through 971/967 cross 128 exactly at the recorded rect boundary
  ('A' top edge: crossing at row 862 = rect top), rise ~12.7-14.2/px
  (formula: 128/9 = 14.2 for 971), and stroke-interior plateaus (~172 on
  971) match d ≈ (172-128)*9/128 = 3.1px for 6px-wide stems. e2e-fix19's
  pseudo-SDF (Chebyshev erosion rings of the binarized mask + AA-ring
  refinement) reproduced crossings but distorted diagonals; e2e-fix20
  computes the field properly: rasterize at 4x supersample, binarize,
  two-pass chamfer (3,4) distance transforms of the mask and its inverse,
  d = (inside - outside) / 3 / 4 atlas px, box-average to atlas
  resolution, encode. Plain-alpha atlases (GlyphRenderMode Smooth = 0/1)
  still get the raw AA coverage.
* FaceInfo numerics (19 slots after family/style): slot 3 = LineHeight =
  (hhea.ascender - hhea.descender + hhea.lineGap) * pt / upem (967:
  1448*64/1000 = 92.672 exact; 971: 2355*86/2048 = 98.8916 exact),
  slot 4 = AscentLine = hhea.ascender * pt/upem (both exact), slot 8 =
  DescentLine = hhea.descender * pt/upem (negative; both exact; slots 9
  and 11 duplicate ascent/descent), slots 13/14 = post.underlinePosition
  / post.underlineThickness scaled (971: -292u/150u -> -12.26/6.30 exact),
  slots 5/6 = round(OS/2 sCapHeight / sxHeight * pt/upem) (971: x-height
  1062u*86/2048 = 44.6 -> 45.0 as stored; 967: cap 734*64/1000 = 47.0).
  WE UPDATE exactly those slots (3/4/5/6/8/9/11/13/14/16) from the new
  font and PRESERVE the rest (0/1/2/7/10/12/15/17/18: point size, scale,
  unitsPerEM, baseline 0, the 0.5 constants and the un-identified
  sub/superscript sizes 14.0/18.0/24.0), plus family/style names — the
  game caches nothing about them at runtime and the swap stays
  conservative.

Charset: the ORIGINAL character table's unicode set ∪ ASCII 0x20-0x7E ∪
extra characters. Code points the TTF cannot provide are SKIPPED and
counted (TMP has no runtime fallback in static mode, so coverage is
reported per asset). Packing: skyline bottom-left over (w+2*pad, h+2*pad)
cells; v1 requires everything to fit into the EXISTING page count — when
it does not, FontBakeError is raised so the caller can shrink the point
size or the charset (the CLI then keeps that asset's original bytes).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field

import UnityPy

from .files import assert_inside, assert_not_nested
from . import tmpfont

# GlyphRenderMode values that bake a signed distance field. Everything else
# (Smooth=0 / SmoothHinted=1) renders plain anti-aliased coverage.
_SDF_RENDER_MODES = {4164, 4165, 4166, 4167, 4168, 4169}

# FaceInfo numeric slots (per tmpfont._FACEINFO_NUMERICS) recomputed from the
# new font, and the font metric each is derived from. Values are the SCALED
# pixel values (font units * point_size / unitsPerEm), matching how the
# original assets' values decode (see module docstring derivation table).
#   slot (tmpfont name)      derivation
#   m_LineHeight             hhea ascender - descender + lineGap
#   m_AscentLine             hhea ascender
#   m_CapHeight              round(OS/2 sCapHeight)   (when present)
#   m_Baseline (slot 6)      round(OS/2 sxHeight)     (when present; the slot
#                            NAME is tmpfont's neutral guess — the real
#                            assets store the x-height there)
#   m_Underline (slot 8)     hhea descender (negative; the slot NAME is
#                            tmpfont's neutral guess — both real assets store
#                            the descent value there, and slot 7 stays 0.0)
#   m_UnderlineThickness     == AscentLine (dup on both real assets)
#   m_StrikethroughThickness == DescentLine (dup)
#   m_SuperscriptSize        post.underlinePosition (negative)
#   m_SubscriptOffset        post.underlineThickness
#   m_Unknown0               == m_SubscriptOffset (dup)
_FACEINFO_DERIVED = (
    "m_LineHeight", "m_AscentLine", "m_CapHeight", "m_Baseline",
    "m_Underline", "m_UnderlineThickness", "m_StrikethroughThickness",
    "m_SuperscriptSize", "m_SubscriptOffset", "m_Unknown0",
)

_CJK_MIN = 0x2E80  # first CJK block (Radicals); kana/kanji/fullwidth above


class FontBakeError(ValueError):
    """Atlas baking cannot proceed for this font/charset/size combination."""


# ---------------------------------------------------------------------------
# font metrics (fontTools) + rasterization (PIL/FreeType)
# ---------------------------------------------------------------------------


def _font_units_metrics(ttf_path: str) -> dict:
    """Font-unit metrics of a TTF via fontTools (pure parsing, no numpy)."""
    from fontTools.ttLib import TTFont

    try:
        f = TTFont(ttf_path, fontNumber=0, lazy=True)
    except Exception as e:  # noqa: BLE001 — caller turns this into a skip
        raise FontBakeError(f"cannot read font {ttf_path!r}: {e}") from e
    head = f["head"]
    hhea = f["hhea"]
    os2 = f.get("OS/2")
    post = f.get("post")
    cmap = f.getBestCmap() if f.get("cmap") else {}
    upem = head.unitsPerEm or 1000
    return {
        "upem": upem,
        "ascent": hhea.ascent,
        "descent": hhea.descent,
        "line_gap": hhea.lineGap,
        "cap_height": getattr(os2, "sCapHeight", None) if os2 else None,
        "x_height": getattr(os2, "sxHeight", None) if os2 else None,
        "underline_position": post.underlinePosition if post else None,
        "underline_thickness": post.underlineThickness if post else None,
        "cmap": cmap,
    }


def _faceinfo_updates(metrics: dict, point_size: float) -> dict:
    """FaceInfo numeric slots recomputed from the new font at `point_size`.

    Returns {tmpfont faceinfo field name -> new float}, only for slots whose
    source metric exists in the font. Derivation table in the module doc.
    """
    upem = metrics["upem"] or 1000
    scale = float(point_size) / upem
    asc = metrics["ascent"] * scale
    desc = metrics["descent"] * scale
    out = {
        "m_LineHeight": (metrics["ascent"] - metrics["descent"] + metrics["line_gap"]) * scale,
        "m_AscentLine": asc,
        "m_Underline": desc,
        # duplicates observed on both real assets
        "m_UnderlineThickness": asc,
        "m_StrikethroughThickness": desc,
    }
    if metrics.get("cap_height"):
        out["m_CapHeight"] = float(round(metrics["cap_height"] * scale))
    if metrics.get("x_height"):
        out["m_Baseline"] = float(round(metrics["x_height"] * scale))
    if metrics.get("underline_position") is not None:
        out["m_SuperscriptSize"] = metrics["underline_position"] * scale
    if metrics.get("underline_thickness") is not None:
        sub = metrics["underline_thickness"] * scale
        out["m_SubscriptOffset"] = sub
        out["m_Unknown0"] = sub
    return out


def _rasterize_glyph(font, ch: str, spread: int) -> dict:
    """Render one code point with PIL/FreeType at the font's pixel size.

    Returns {width, height, bearing_x, bearing_y, advance, mask} where mask
    is the anti-aliased 'L' tile of size (w + 2*spread, h + 2*spread) with
    the glyph bitmap at offset (spread, spread), or mask=None for blank
    glyphs (space-like). Metrics follow the original-asset conventions
    derived in the module docstring: integer pixels, bearing_y measured UP
    from the baseline to the bitmap TOP.
    """
    from PIL import Image, ImageDraw

    ascent, _descent = font.getmetrics()
    # generous tile: ±1em around the pen so any ink (negative lsb, tall
    # ascenders, deep descenders) lands inside
    margin = font.size + spread
    tile_w = tile_h = font.size * 2 + spread * 2 + 4
    tile = Image.new("L", (tile_w, tile_h), 0)
    draw = ImageDraw.Draw(tile)
    draw.text((margin, margin), ch, font=font, fill=255)  # anchor "la"
    bbox = tile.getbbox()
    advance = font.getlength(ch)
    if bbox is None:
        return {
            "width": 0.0, "height": 0.0, "bearing_x": 0.0, "bearing_y": 0.0,
            "advance": float(round(advance)), "mask": None,
        }
    x0, y0, x1, y1 = bbox
    mask = tile.crop((x0 - spread, y0 - spread, x1 + spread, y1 + spread))
    return {
        "width": float(x1 - x0),
        "height": float(y1 - y0),
        "bearing_x": float(x0 - margin),
        "bearing_y": float(ascent - (y0 - margin)),
        "advance": float(round(advance)),
        "mask": mask,
    }


# ---------------------------------------------------------------------------
# true SDF field (e2e-fix20): supersampled raster + chamfer distance
# ---------------------------------------------------------------------------

# Glyph outlines are rasterized at SS x the asset point size, binarized and
# the chamfer distances are averaged back down — ~1/SS px effective edge
# precision (2 gray levels at pad 9), and hinting distortion at the huge
# raster size shrinks to the same scale.
_SDF_SUPERSAMPLE = 4

# Chamfer chunk budget (batch cells): bounds the float32 temporaries to a
# few tens of MB regardless of charset size.
_SDF_BATCH_CELLS = 1 << 22


def _chamfer_dist_batch(masks):
    """Two-pass chamfer distance transform (Borgefors (3,4) weights), batched.

    `masks` is a (G, H, W) bool array; returns float32 (G, H, W) where every
    True cell holds the distance to the nearest False cell in RAW weight
    units (orthogonal 3, diagonal 4 — divide by 3 for pixels; the (3,4)
    chamfer underestimates Euclidean diagonals by a flat 5.7%, an order of
    magnitude below SDF8's gray-step size) and False cells hold 0.

    The raster scan's row-to-row relaxation is sequential, but every row
    update is one vectorized op across the whole (G, W) batch: the
    intra-row propagation uses the running-prefix identity
    min_{j<=i}(g[j] + k*(i-j)) = k*i + cummin(g[j] - k*j)   (and its mirror).
    """
    import numpy as np

    masks = np.asarray(masks, dtype=bool)
    _g, h, w = masks.shape
    big = np.float32(4 * (h + w))
    d = np.where(masks, big, np.float32(0)).astype(np.float32)
    cols = np.arange(w, dtype=np.float32)
    w3 = np.float32(3)
    w4 = np.float32(4)

    def shift_prev(x):  # out[c] = x[c - 1]; first column -> big
        out = np.empty_like(x)
        out[:, 1:] = x[:, :-1]
        out[:, 0] = big
        return out

    def shift_next(x):  # out[c] = x[c + 1]; last column -> big
        out = np.empty_like(x)
        out[:, :-1] = x[:, 1:]
        out[:, -1] = big
        return out

    for r in range(h):  # forward pass: up (3), up-left/up-right (4), left (3)
        if r:
            top = d[:, r - 1]
            m = np.minimum(d[:, r], top + w3)
            m = np.minimum(m, shift_prev(top) + w4)
            m = np.minimum(m, shift_next(top) + w4)
            d[:, r] = m
        row = d[:, r] - w3 * cols
        np.minimum.accumulate(row, axis=1, out=row)
        d[:, r] = row + w3 * cols
    for r in range(h - 1, -1, -1):  # backward: down (3), diagonals (4), right (3)
        if r < h - 1:
            bot = d[:, r + 1]
            m = np.minimum(d[:, r], bot + w3)
            m = np.minimum(m, shift_prev(bot) + w4)
            m = np.minimum(m, shift_next(bot) + w4)
            d[:, r] = m
        row = d[:, r] + w3 * cols
        row = np.minimum.accumulate(row[:, ::-1], axis=1)[:, ::-1]
        d[:, r] = row - w3 * cols
    return d


def _ss_glyph_mask(font_ss, ch: str, w: int, h: int, spread: int, ss: int):
    """Supersampled binary mask of `ch` in an exact
    (ss*(h + 2*spread), ss*(w + 2*spread)) grid with the ink bbox anchored
    at (ss*spread, ss*spread) and RESCALED to exactly ss*(w, h).

    The recorded glyph rect comes from the 1x hinted render (the original
    assets' convention — their SDF crosses 128 at the rect boundary), while
    a 4x hinted render can differ by a pixel or two per side. Drawing twice
    (probe -> ink offset from the pen -> redraw anchored at the skirt)
    pins the top/left; cropping the ink bbox and resizing it to ss*(w, h)
    conforms all four edges to the recorded rect, so the field's 128-
    crossing lands on the rect on every side (±0.25px), like the original
    atlas.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    probe = Image.new("L", (font_ss.size * 3, font_ss.size * 3), 0)
    pen = font_ss.size  # 1em of left excursion, like _rasterize_glyph's margin
    ImageDraw.Draw(probe).text((pen, pen), ch, font=font_ss, fill=255)
    bbox = probe.getbbox()
    if bbox is None:  # 1x had ink; a blank ss render would be a FreeType anomaly
        return np.zeros((ss * (h + 2 * spread), ss * (w + 2 * spread)), dtype=bool)
    off_x, off_y = bbox[0] - pen, bbox[1] - pen
    tile = Image.new("L", (ss * (w + 2 * spread), ss * (h + 2 * spread)), 0)
    t = ss * spread
    ImageDraw.Draw(tile).text((t - off_x, t - off_y), ch, font=font_ss, fill=255)
    ink_bbox = tile.getbbox()
    if ink_bbox is not None:
        ink = tile.crop(ink_bbox).resize((ss * w, ss * h), Image.LANCZOS)
        tile.paste(ink, (t, t))
    return np.asarray(tile, np.uint8) >= 128


def _sdf_encode_batch(masks, pad: int, ss: int):
    """Encode supersampled binary glyph masks into TMP SDF8 'L' tiles.

    Per mask: d = (chamfer(mask) - chamfer(~mask)) / (3*ss) is the signed
    distance to the glyph outline in ATLAS pixels, positive inside the glyph
    SHAPE (an 'o''s stroke ring — its counter is outside the shape and goes
    negative). The field is box-averaged down to atlas resolution (each
    atlas pixel = the mean over its ss x ss block, a pixel-centered sample
    of a smooth field) and encoded as clamp(round(128 + d * 128 / pad)):
    128 exactly on the outline, bright plateaus deep in strokes, -> 0 in
    open space — the original assets' derived profile.

    Input masks may differ in shape; returns one 'L' image per mask, sized
    (h + 2*spread, w + 2*spread) — the same contract _rasterize_glyph's AA
    tiles follow, so the packer's crop logic is unchanged.
    """
    import numpy as np
    from PIL import Image

    slope = 128.0 / max(1, pad)
    out = []
    i = 0
    while i < len(masks):
        h0, w0 = masks[i].shape
        take = max(1, _SDF_BATCH_CELLS // (h0 * w0))
        chunk = masks[i:i + take]
        i += take
        H = max(m.shape[0] for m in chunk)
        W = max(m.shape[1] for m in chunk)
        batch = np.zeros((len(chunk), H, W), dtype=bool)
        for k, m in enumerate(chunk):
            batch[k, :m.shape[0], :m.shape[1]] = m
        din = _chamfer_dist_batch(batch)
        dout = _chamfer_dist_batch(~batch)
        # signed distance, box-averaged down to atlas resolution (each atlas
        # pixel = the mean over its ss x ss block), in atlas px, + inside
        g, hs, ws = din.shape
        field = (din - dout).reshape(g, hs // ss, ss, ws // ss, ss)
        field = field.mean(axis=(2, 4)) / np.float32(3 * ss)
        vals = np.clip(np.rint(128.0 + field * slope), 0, 255).astype(np.uint8)
        for k, m in enumerate(chunk):
            th, tw = m.shape[0] // ss, m.shape[1] // ss
            out.append(Image.fromarray(vals[k, :th, :tw], "L"))
    return out


# ---------------------------------------------------------------------------
# packing
# ---------------------------------------------------------------------------


def _skyline_pack(sizes: list[tuple[int, int]], atlas_w: int, atlas_h: int):
    """Pack (w, h) cells bottom-left (skyline), heights pre-sorted desc.

    Returns (positions, free_rects) with positions[i] = (x, y_top) in
    TOP-DOWN image space, or (None, None) when anything does not fit.
    Free rects are the leftover skyline strips (x, y_top, w, h), top-down.
    """
    skyline = [[0, atlas_w, 0]]  # [x, width, y]
    positions: list[tuple[int, int]] = []
    for w, h in sizes:
        best = None  # (y_after, x, seg_idx)
        for i in range(len(skyline)):
            x = skyline[i][0]
            if x + w > atlas_w:
                continue
            y = 0
            remaining = w
            j = i
            while remaining > 0 and j < len(skyline):
                y = max(y, skyline[j][2])
                remaining -= skyline[j][1]
                j += 1
            if remaining > 0 or y + h > atlas_h:
                continue
            if best is None or (y, x) < best[:2]:
                best = (y, x, i)
        if best is None:
            return None, None
        y, x, _i = best
        positions.append((x, y))
        # splice the skyline: raise [x, x+w) to y+h, trim/split segments
        new = []
        for sx, sw, sy in skyline:
            if sx + sw <= x or sx >= x + w:
                new.append([sx, sw, sy])
            else:
                if sx < x:
                    new.append([sx, x - sx, sy])
                if sx + sw > x + w:
                    new.append([x + w, sx + sw - (x + w), sy])
        new.append([x, w, y + h])
        new.sort()
        merged = []
        for seg in new:
            if merged and merged[-1][0] + merged[-1][1] == seg[0] and merged[-1][2] == seg[2]:
                merged[-1][1] += seg[1]
            else:
                merged.append(seg)
        skyline = merged
    free = [(sx, sy, sw, atlas_h - sy) for sx, sw, sy in skyline if sw > 0 and atlas_h - sy > 0]
    return positions, free


# ---------------------------------------------------------------------------
# public API 1: bake a set of code points into atlas pages
# ---------------------------------------------------------------------------


def bake_atlas(
    ttf_path: str,
    char_codes,
    point_size: float,
    atlas_w: int,
    atlas_h: int,
    padding: int,
    render_mode_hint: int | None = None,
) -> dict:
    """Rasterize `char_codes` from `ttf_path` and pack them into an atlas.

    Returns:
        pages       list of PIL 'L' images (one per atlas page; v1 packs into
                    a single page sized atlas_w x atlas_h)
        glyphs      glyph-record dicts in tmpfont typetree field naming
                    (m_Index, m_Width ... m_RectY bottom-up, m_Scale, ...)
        chars       character-record dicts (m_ElementType 1, m_Unicode,
                    m_GlyphIndex, m_Scale 1.0)
        free_rects  leftover space as (x, y_top, w, h) strips, top-down
        coverage    {requested, baked, missing: [code points]}
        metrics     the fontTools unit metrics used
        faceinfo    {_FACEINFO_DERIVED slot -> value} at point_size
        sdf         whether the pages are SDF-encoded

    Raises FontBakeError when the cells do not fit the single page or the
    font file cannot be read.
    """
    from PIL import Image, ImageFont

    metrics = _font_units_metrics(ttf_path)
    cmap = metrics["cmap"]
    faceinfo = _faceinfo_updates(metrics, point_size)

    codes = sorted({int(c) for c in char_codes})
    font = ImageFont.truetype(ttf_path, size=int(round(point_size)))
    sdf = render_mode_hint is None or render_mode_hint in _SDF_RENDER_MODES
    pad = max(1, int(padding))
    spread = min(pad + 2, 250)
    font_ss = None
    if sdf:
        font_ss = ImageFont.truetype(
            ttf_path, size=int(round(point_size)) * _SDF_SUPERSAMPLE)

    cells: list[dict] = []
    missing: list[int] = []
    for cp in codes:
        if 0xD800 <= cp <= 0xDFFF:
            missing.append(cp)  # lone surrogates are not renderable
            continue
        if cp not in cmap:
            missing.append(cp)
            continue
        r = _rasterize_glyph(font, chr(cp), spread=spread)
        cells.append({"cp": cp, **r})

    sized = [c for c in cells if c["width"] > 0 and c["height"] > 0]
    sized.sort(key=lambda c: (-c["height"], -c["width"]))
    cell_sizes = [(int(c["width"]) + 2 * pad, int(c["height"]) + 2 * pad) for c in sized]
    positions, free = _skyline_pack(cell_sizes, atlas_w, atlas_h)
    if positions is None:
        raise FontBakeError(
            f"charset of {len(cell_sizes)} glyphs at point size {point_size:g} "
            f"does not fit a {atlas_w}x{atlas_h} atlas (padding {pad}); "
            "reduce the point size or the character set"
        )

    # paint the page (top-down image space), emit bottom-up rects. The
    # encoded tile carries (w + 2*spread, h + 2*spread) with the glyph bbox
    # at (spread, spread); the cell wants `pad` of skirt, so the tile is
    # trimmed to the cell before pasting (the trimmed skirt pixels sit
    # >= pad from the glyph edge where the SDF has saturated).
    fields = None
    if sdf:
        ss_masks = [
            _ss_glyph_mask(font_ss, chr(c["cp"]), int(c["width"]),
                           int(c["height"]), spread, _SDF_SUPERSAMPLE)
            for c in sized
        ]
        fields = iter(_sdf_encode_batch(ss_masks, pad, _SDF_SUPERSAMPLE))
    page = Image.new("L", (atlas_w, atlas_h), 0)
    for cell, (x, y_top) in zip(sized, positions):
        w, h = int(cell["width"]), int(cell["height"])
        enc = next(fields) if sdf else cell["mask"]
        cw, chh = w + 2 * pad, h + 2 * pad
        tile = enc.crop((spread - pad, spread - pad, spread - pad + cw, spread - pad + chh))
        page.paste(tile, (x, y_top))
        cell["_rect"] = (x + pad, atlas_h - (y_top + pad) - h, w, h)
    for cell in cells:
        if cell["width"] == 0 or cell["height"] == 0:
            cell["_rect"] = (0, 0, 0, 0)

    glyphs = []
    index_of: dict[int, int] = {}
    for i, cell in enumerate(cells, start=1):
        rx, ry, rw, rh = cell["_rect"]
        glyphs.append({
            "m_Index": i,
            "m_Width": cell["width"], "m_Height": cell["height"],
            "m_BearingX": cell["bearing_x"], "m_BearingY": cell["bearing_y"],
            "m_Advance": cell["advance"],
            "m_RectX": rx, "m_RectY": ry, "m_RectWidth": rw, "m_RectHeight": rh,
            "m_Scale": 1.0, "m_AtlasIndex": 0, "m_Padding": 0, "m_Reserved": 0,
        })
        index_of[cell["cp"]] = i
    chars = [
        {"m_ElementType": 1, "m_Unicode": cell["cp"],
         "m_GlyphIndex": index_of[cell["cp"]], "m_Scale": 1.0}
        for cell in cells
    ]
    return {
        "pages": [page],
        "glyphs": glyphs,
        "chars": chars,
        "free_rects": free,
        "coverage": {"requested": len(codes), "baked": len(cells), "missing": missing},
        "metrics": metrics,
        "faceinfo": faceinfo,
        "sdf": sdf,
    }


# ---------------------------------------------------------------------------
# public API 2: rebuild a TMP_FontAsset payload around a baked atlas
# ---------------------------------------------------------------------------


def bake_tmp_font_asset(payload: bytes | bytearray, ttf_path: str, *, extra_chars: str = ""):
    """Replace the baked glyph/character tables + atlas pages of a STATIC
    TMP_FontAsset with ones generated from `ttf_path`.

    Charset = the ORIGINAL character table's unicode set ∪ ASCII 0x20-0x7E ∪
    `extra_chars`. Mode STAYS STATIC (0) — a baked replacement, not a dynamic
    conversion — and the atlas page count/PPtrs are kept (the bake packs into
    the EXISTING page geometry). FaceInfo slots listed in _FACEINFO_DERIVED
    are recomputed from the new font; everything else in the payload prefix
    plus the opaque tail is preserved byte-exact via tmpfont's typetree.

    Returns (new_payload, page_images, info) where info carries the bake
    result of :func:`bake_atlas` plus per-asset details (counts, coverage,
    updated faceinfo values). Raises TmpFontError for unsupported layouts and
    FontBakeError when the charset does not fit the existing atlas.
    """
    info = tmpfont.parse_with_typetree(payload)
    if not info.get("ok"):
        raise tmpfont.TmpFontError(
            f"not a supported TMP_FontAsset payload: {info.get('reason')}"
        )
    if info["atlas_population_mode"] != 0:
        raise tmpfont.TmpFontError(
            f"TMP asset {info['name']!r} is not static (mode "
            f"{info['atlas_population_mode']}): its glyphs re-render at runtime, "
            "nothing to bake"
        )
    orig = info["fields"]
    charset = {c["m_Unicode"] for c in orig["m_CharacterTable"]}
    charset |= set(range(0x20, 0x7F))
    charset |= {ord(c) for c in extra_chars}
    charset.discard(0)

    bake = bake_atlas(
        ttf_path,
        sorted(charset),
        info["point_size"],
        info["atlas_width"],
        info["atlas_height"],
        info["atlas_padding"],
        render_mode_hint=info["atlas_render_mode"],
    )

    fields = dict(orig)
    face = dict(orig["m_FaceInfo"])
    for key, value in bake["faceinfo"].items():
        face[key] = float(value)
    fields["m_FaceInfo"] = face
    fields["m_GlyphTable"] = bake["glyphs"]
    fields["m_CharacterTable"] = bake["chars"]
    # keep m_AtlasTextures (page PPtrs) as-is; rebuild the used/free rect
    # lists from the actual packing so the payload stays self-consistent
    atlas_h = info["atlas_height"]
    used = [
        {"m_X": g["m_RectX"], "m_Y": g["m_RectY"],
         "m_Width": g["m_RectWidth"], "m_Height": g["m_RectHeight"]}
        for g in bake["glyphs"] if g["m_RectWidth"] and g["m_RectHeight"]
    ]
    free = [
        {"m_X": x, "m_Y": atlas_h - y - fh, "m_Width": fw, "m_Height": fh}
        for (x, y, fw, fh) in bake["free_rects"]
    ]
    fields["m_UsedGlyphRects"] = used
    fields["m_FreeGlyphRects"] = free

    new_payload = tmpfont.serialize_typetree_prefix(fields) + info["tail"]
    out_info = {
        "name": info["name"],
        "point_size": info["point_size"],
        "atlas": (info["atlas_width"], info["atlas_height"], info["atlas_padding"]),
        "glyph_count": len(bake["glyphs"]),
        "char_count": len(bake["chars"]),
        "orig_glyph_count": info["glyph_count"],
        "orig_char_count": info["char_count"],
        "coverage": bake["coverage"],
        "faceinfo_updates": bake["faceinfo"],
        "sdf": bake["sdf"],
    }
    return new_payload, bake["pages"], out_info


# ---------------------------------------------------------------------------
# public API 3: write baked pages into the referenced atlas textures
# ---------------------------------------------------------------------------


def replace_atlas_textures(env, page_images, atlas_pptrs) -> int:
    """Set each referenced atlas Texture2D's image to the corresponding page.

    Uses UnityPy's read -> set_image -> save_typetree path, so the texture's
    ORIGINAL format (Alpha8 on the real assets) is re-encoded and
    streaming/streamdata handled by UnityPy. Returns the number of textures
    replaced.

    e2e-fix20: the page pixels are the SDF / coverage FIELD, and TMP samples
    it from the texture's ALPHA channel. UnityPy encodes Alpha8 from the
    passed image's alpha channel ONLY, so the previous `page.convert("RGBA")`
    — gray in RGB, alpha a constant 255 — baked a constant 255 atlas: every
    pixel "infinitely deep inside the stroke", rendering each glyph as a
    solid white blob (counters filled, glyphs merging into word blocks).
    The field is therefore merged into EVERY band (R=G=B=A=page), which is
    correct for Alpha8 (alpha) and for RGBA-style atlases alike.
    """
    from PIL import Image

    by_pid = {o.path_id: o for o in env.objects}
    replaced = 0
    for i, (fid, pid) in enumerate(atlas_pptrs):
        if fid != 0:
            raise FontBakeError(
                f"atlas texture {i} lives in another file (fileID {fid}); "
                "cross-file atlas pages are not supported"
            )
        obj = by_pid.get(pid)
        if obj is None or obj.type.name != "Texture2D":
            raise FontBakeError(f"atlas texture path_id {pid} not found in file")
        page = page_images[min(i, len(page_images) - 1)]
        if page.mode != "RGBA":
            gray = page if page.mode == "L" else page.convert("L")
            img = Image.merge("RGBA", (gray, gray, gray, gray))
        else:
            img = page
        d = obj.read()
        d.set_image(img)          # encodes into the texture's original format
        obj.save_typetree(d)      # persist typetree + image data
        replaced += 1
    return replaced


# ---------------------------------------------------------------------------
# public API 4: whole-game stage (mirrors tmpfont.make_game_fonts_dynamic)
# ---------------------------------------------------------------------------


def _has_cjk(unicodes) -> bool:
    return any(cp >= _CJK_MIN for cp in unicodes)


def bake_game_fonts(
    game_dir: str,
    out_dir: str,
    latin_ttf: str,
    cjk_ttf: str,
    *,
    extra_chars: str = "",
    patch_mode: bool = False,
    in_place: bool = False,
    progress=None,
) -> dict:
    """Re-bake the atlas + tables of every scannable static TMP_FontAsset.

    Role mapping: assets whose ORIGINAL baked charset contains CJK code
    points (>= U+2E80) are baked from `cjk_ttf`, the rest from `latin_ttf`.
    Staging semantics mirror tmpfont.make_game_fonts_dynamic: patch_mode
    reads sources from game_dir EXCEPT where an earlier stage of this run
    already wrote out_dir/<rel> (stage-order rule); in_place edits the copy
    at out_dir in place. Only files with baked assets are written.
    """
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if in_place and game_dir != out_dir:
        raise ValueError(
            f"in_place=True requires game_dir == out_dir "
            f"(got {game_dir!r} vs {out_dir!r})"
        )
    if not in_place:
        assert_not_nested(game_dir, out_dir, "out_dir")
    for label, path in (("latin_ttf", latin_ttf), ("cjk_ttf", cjk_ttf)):
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"font_bake {label} not found: {path!r}")

    scan = tmpfont.scan_tmp_font_assets(game_dir)
    by_file: dict[str, list] = defaultdict(list)
    for e in scan.entries:
        by_file[e.file].append(e)

    baked = 0
    already_dynamic = 0
    failed = 0
    textures_replaced = 0
    changed_files = 0
    exported: list[str] = []
    details: list[dict] = []

    for i, (abs_file, entries) in enumerate(sorted(by_file.items())):
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        src_file = abs_file
        if patch_mode and os.path.isfile(out_file):
            src_file = out_file  # stage-order rule (e2e-fix13)
        env = UnityPy.load(src_file)
        target_ids = {e.path_id for e in entries}
        changed = False
        for obj in env.objects:
            if obj.path_id not in target_ids:
                continue
            entry = next(e for e in entries if e.path_id == obj.path_id)
            detail = {"file": rel.replace("\\", "/"), "path_id": entry.path_id,
                      "name": entry.name}
            try:
                raw = obj.get_raw_data()
            except Exception:  # noqa: BLE001
                continue
            if entry.mode != 0:
                already_dynamic += 1
                detail["action"] = "already_dynamic"
                details.append(detail)
                continue
            unicodes: set[int] = set()
            t = tmpfont.parse_with_typetree(raw)
            if t.get("ok"):
                unicodes = {c["m_Unicode"] for c in t["fields"]["m_CharacterTable"]}
            ttf = cjk_ttf if _has_cjk(unicodes) else latin_ttf
            detail["ttf_role"] = "cjk" if ttf == cjk_ttf else "latin"
            try:
                new_payload, pages, binfo = bake_tmp_font_asset(raw, ttf, extra_chars=extra_chars)
            except (tmpfont.TmpFontError, FontBakeError) as e:
                failed += 1
                detail["action"] = "failed"
                detail["reason"] = str(e)
                details.append(detail)
                continue
            obj.set_raw_data(new_payload)
            n_tex = replace_atlas_textures(env, pages, t["atlas_texture_pptrs"])
            textures_replaced += n_tex
            baked += 1
            changed = True
            detail.update({
                "action": "baked",
                "glyphs": binfo["glyph_count"],
                "chars": binfo["char_count"],
                "coverage": binfo["coverage"],
                "atlas_textures_replaced": n_tex,
            })
            details.append(detail)
        if changed:
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                env.save(pack="lz4", out_path=td)
                for fname in os.listdir(td):
                    shutil.copy2(
                        os.path.join(td, fname),
                        os.path.join(os.path.dirname(out_file), fname),
                    )
            changed_files += 1
            exported.append(rel.replace("\\", "/"))
        if progress:
            progress(i + 1, len(by_file), rel)

    return {
        "tmp_assets_found": len(scan.entries),
        "fonts_baked": baked,
        "bake_failed": failed,
        "already_dynamic": already_dynamic,
        "atlas_textures_replaced": textures_replaced,
        "files_modified": changed_files,
        "exported_files": sorted(exported),
        "details": details,
        "skipped_unknown_layout": len(scan.skipped_unknown_layout),
    }
