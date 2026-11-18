"""TextMeshPro font-asset surgery: make baked STATIC TMP fonts render from
their (swapped) source TTF.

Root cause this fixes (e2e-fix14): swapping the embedded TTF of a game's
legacy `Font` objects does NOT change what TextMeshPro draws. TMP renders
through `TMP_FontAsset` MonoBehaviours whose baked glyph/character tables
(m_AtlasPopulationMode = Static) serve pre-rendered glyphs from static atlas
textures — the swapped Font bytes are never consulted.

Transform: set m_AtlasPopulationMode = Dynamic and drop the baked
glyph/character tables, so TMP rasterizes glyphs on demand from the asset's
m_SourceFontFile (a legacy `Font` whose m_FontData the font stage already
replaced). Everything else is preserved byte-exact.

Serialized layout of a TMP_FontAsset MonoBehaviour payload (typetree-less
IL2CPP build, TMP 3.x "1.1.0" asset format). Recovered EMPIRICALLY from the
four real assets of the example IL2CPP game (sharedassets0
path_id 967/970/971, sharedassets1 path_id 81) and cross-validated: the same
parameter-free parser consumes all four payloads to their tables with sane
values. Field offsets below are relative to the payload start and computed
from the parsed lengths — never hardcoded:

    m_GameObject   PPtr (i32 fileID + i64 pathID)      @0   (12 B)
    m_Enabled      i32                                 @12
    m_Script       PPtr                                @16  (12 B)
    m_Name         aligned string                      @28
    m_Version      aligned string  ("1.1.0")
    m_AtlasPopulationMode  i32   <- SET TO 1 (Dynamic)
    m_FaceInfo     aligned str (family), aligned str (style),
                   19 x u32 numerics (pointSize f32, scale f32,
                   unitsPerEM u32, line metrics f32 ...)
    2 x u32        (unknown, preserved: 112/110/111/2 then 0)
    str            guid1, u32 0, str guid2
    2 x u32 0
    7 x u32        (font-asset creation settings)
    str            character set (may be empty)
    str            guid3
    3 x u32 0
    u32            tag (4165/4169 on the real assets)
    u32            flag (0 or 1)
    m_SourceFontFile   PPtr (i32 fileID, i64 pathID)   <- legacy Font
    3 x u32
    m_GlyphTable       i32 count + count x 52 B records
                       [u32 index][5 x f32 metrics][4 x i32 atlas rect]
                       [f32 scale][u16 atlasIndex][u16 0][u32 0]
    m_CharacterTable   i32 count + count x 16 B records
                       [u32 elementType][u32 unicode][u32 glyphIndex][f32 scale]
    m_AtlasTextures    i32 count + count x PPtr(12 B)
    m_AtlasTextureIndex i32
    3 x u32        (multi-atlas / feature / clear-on-build bools)
    m_AtlasWidth/Height/Padding/RenderMode  4 x i32
    m_UsedGlyphRects   i32 count + count x 16 B
    m_FreeGlyphRects   i32 count + count x 16 B
    ... tail (kerning, feature table, fallbacks, creation settings, font
    weights) — opaque but preserved byte-exact.

Evidence-based keep/skip decisions (from the TMP 3.0.6 runtime source):
  * m_AtlasTextures is KEPT: the dynamic path reads atlasTextures[0]
    (`atlasTexture` getter throws on an empty list) and rasterizes new
    glyphs into that same texture. With empty character tables no baked
    pixel is ever referenced, so keeping it is both required and harmless.
  * m_FreeGlyphRects / m_UsedGlyphRects are KEPT: FontEngine.TryAddGlyphsTo
    Texture packs new glyphs into the free rects of the same-sized atlas.
  * Assets whose m_SourceFontFile is null (pathID == 0) used to be SKIPPED
    outright: a Dynamic asset with no source font can rasterize nothing,
    which would blank text that currently renders fine from its baked tables.
    e2e-fix15 first ASSIGNED a candidate (the file's first legacy `Font`) by
    writing a PPtr into the arithmetically located source-field offset —
    which fix16 reverted: for null-source assets that offset has no value
    signature and cannot be verified, so the write was deemed unsafe.
    e2e-fix17 resolves this properly: a REAL UnityPy TypeTree for the
    TMP_FontAsset layout (see parse_with_typetree) locates the source field
    by SCHEMA instead of arithmetic, cross-validated against the
    signature-verified anchors (967: source @360 = path_id 933, mode @68)
    and a byte-exact prefix round-trip on every real asset. The typed
    transform is now the primary path and null-source assets with a
    same-file Font candidate are transformed (971-class); files with NO Font
    objects still skip their null-source assets (e.g. the DSEG14 clock font
    in sharedassets1): with no candidate there is nothing to point at, and
    force-clearing a digit-only font's tables would re-render its glyphs
    from a CJK face. The arithmetic path remains as a fallback.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field

import UnityPy

from .files import assert_inside, assert_not_nested, find_asset_files, find_data_dir

TMP_SCRIPT_CLASS = "TMP_FontAsset"

# glyph record: idx + 5 floats + 4 ints + scale + u16 + pad + u32 = 52 B
_GLYPH_RECORD = 52
# character record: elementType + unicode + glyphIndex + scale = 16 B
_CHAR_RECORD = 16
_PPTR = 12  # i32 fileID + i64 pathID


class TmpFontError(ValueError):
    """A TMP_FontAsset payload does not match the supported layout."""


class TmpNoSourceFontError(TmpFontError):
    """The TMP asset has no m_SourceFontFile (pathID == 0): making it
    dynamic would blank its text, so it must stay static."""


def _u32(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 4], "little")


def _i32(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 4], "little", signed=True)


def _i64(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 8], "little", signed=True)


def _f32(buf: bytes, off: int) -> float:
    import struct

    return struct.unpack_from("<f", buf, off)[0]


def _align4(off: int) -> int:
    return (off + 3) & ~3


def _rd_str(buf: bytes, off: int) -> tuple[str, int]:
    if off + 4 > len(buf):
        raise TmpFontError(f"string length prefix at {off} out of range")
    n = _i32(buf, off)
    if n < 0 or off + 4 + n > len(buf):
        raise TmpFontError(f"invalid string length {n} at {off}")
    s = buf[off + 4 : off + 4 + n].decode("utf-8", "replace")
    return s, _align4(off + 4 + n)


def _rd_pptr(buf: bytes, off: int) -> tuple[int, int, int]:
    if off + _PPTR > len(buf):
        raise TmpFontError(f"PPtr at {off} out of range")
    return _i32(buf, off), _i64(buf, off + 4), off + _PPTR


def analyze_tmp_font_asset(payload: bytes | bytearray) -> dict:
    """Parse a TMP_FontAsset MonoBehaviour payload.

    Returns a dict with ``ok`` plus every recovered field and its offset.
    ``ok=False`` (with a ``reason``) for payloads that are not TMP_FontAsset
    or do not match the supported layout — never raises for foreign data.
    """
    buf = bytes(payload)
    info: dict = {"ok": False, "payload_size": len(buf)}
    try:
        if len(buf) < 28 + 4:
            info["reason"] = "payload too short"
            return info
        # MonoBehaviour header
        go_file_id, go_path_id, off = _rd_pptr(buf, 0)
        enabled = _i32(buf, off)
        sc_file_id, sc_path_id, off = _rd_pptr(buf, off + 4)
        if enabled not in (0, 1):
            info["reason"] = f"bad m_Enabled {enabled}"
            return info
        name, off = _rd_str(buf, off)
        version, off = _rd_str(buf, off)
        mode_off = off
        mode = _i32(buf, mode_off)
        off += 4
        info.update(
            name=name,
            version=version,
            mode_offset=mode_off,
            atlas_population_mode=mode,
            game_object_pptr=(go_file_id, go_path_id),
            script_pptr=(sc_file_id, sc_path_id),
        )
        if mode not in (0, 1, 2):
            info["reason"] = f"bad m_AtlasPopulationMode {mode}"
            return info
        if version != "1.1.0":
            info["reason"] = f"unsupported asset version {version!r}"
            return info

        # FaceInfo: family, style, 19 u32 numerics
        family, off = _rd_str(buf, off)
        style, off = _rd_str(buf, off)
        faceinfo_off = off
        numerics = [_u32(buf, off + 4 * i) for i in range(19)]
        off += 19 * 4
        point_size = _f32(buf, faceinfo_off)
        scale = _f32(buf, faceinfo_off + 4)
        units_per_em = numerics[2]
        if not (0.0 < point_size <= 512.0) or not (0.0 < scale <= 64.0):
            info["reason"] = f"implausible face metrics pt={point_size} scale={scale}"
            return info
        if units_per_em != 0 and not (16 <= units_per_em <= 65536):
            info["reason"] = f"implausible unitsPerEM {units_per_em}"
            return info
        info.update(family_name=family, style_name=style, point_size=point_size,
                    faceinfo_numerics=numerics)

        # fixed unknown block: 2 u32 (second is 0 on all known assets)
        tag_a = _u32(buf, off)
        zero_a = _u32(buf, off + 4)
        if zero_a != 0:
            info["reason"] = "layout drift: expected 0 after unknown tag"
            return info
        off += 8

        # two GUID strings with an int 0 between
        guid1, off = _rd_str(buf, off)
        if _u32(buf, off) != 0:
            info["reason"] = "layout drift: expected 0 between guid strings"
            return info
        guid2, off = _rd_str(buf, off + 4)

        # two zero u32s
        if _u32(buf, off) != 0 or _u32(buf, off + 4) != 0:
            info["reason"] = "layout drift: expected two zero u32s"
            return info
        off += 8

        # 7 creation-setting u32s (pointSize, ..., atlas width/height, mode)
        creation = [_u32(buf, off + 4 * i) for i in range(7)]
        off += 7 * 4

        # character-set string (often empty) + guid3
        charset, off = _rd_str(buf, off)
        guid3, off = _rd_str(buf, off)

        # three zero u32s
        if any(_u32(buf, off + 4 * i) != 0 for i in range(3)):
            info["reason"] = "layout drift: expected three zero u32s"
            return info
        off += 12

        # tag + flag + source font PPtr
        tag = _u32(buf, off)
        flag = _u32(buf, off + 4)
        src_pptr_off = off + 8
        src_file_id, src_path_id, off = _rd_pptr(buf, src_pptr_off)

        # 3 more u32s before the glyph table
        pre_table = [_u32(buf, off + 4 * i) for i in range(3)]
        off += 12

        info.update(
            guid1=guid1, guid2=guid2, guid3=guid3, charset=charset,
            creation_numerics=creation, tag=tag, flag=flag,
            pre_table_numerics=pre_table,
            source_font_pptr=(src_file_id, src_path_id),
            source_font_offset=src_pptr_off,
        )

        # glyph table
        glyph_count_off = off
        glyph_count = _i32(buf, glyph_count_off)
        if not (0 <= glyph_count <= 1_000_000):
            info["reason"] = f"implausible glyph count {glyph_count}"
            return info
        g_off = glyph_count_off + 4
        good = 0
        for i in range(glyph_count):
            r = g_off + i * _GLYPH_RECORD
            if r + _GLYPH_RECORD > len(buf):
                info["reason"] = "glyph table overruns payload"
                return info
            idx = _u32(buf, r)
            floats = [_f32(buf, r + 4 + 4 * k) for k in range(5)]
            rects = [_i32(buf, r + 24 + 4 * k) for k in range(4)]
            sc = _f32(buf, r + 40)
            if (
                all(-512.0 <= v <= 8192.0 for v in floats)
                and all(-1 <= v <= 8192 for v in rects)
                and 0.0 < sc <= 64.0
                and _u32(buf, r + 44) == 0
                and _u32(buf, r + 48) == 0
            ):
                good += 1
        g_end = g_off + glyph_count * _GLYPH_RECORD
        if glyph_count and good < 0.95 * glyph_count:
            info["reason"] = f"glyph records implausible ({good}/{glyph_count})"
            return info

        # character table
        char_count_off = g_end
        char_count = _i32(buf, char_count_off)
        if not (0 <= char_count <= 1_000_000):
            info["reason"] = f"implausible character count {char_count}"
            return info
        c_off = char_count_off + 4
        good = 0
        for i in range(char_count):
            r = c_off + i * _CHAR_RECORD
            if r + _CHAR_RECORD > len(buf):
                info["reason"] = "character table overruns payload"
                return info
            et = _u32(buf, r)
            uni = _u32(buf, r + 4)
            gi = _u32(buf, r + 8)
            sc = _f32(buf, r + 12)
            if et in (0, 1) and uni <= 0x10FFFF and gi <= 0xFFFFFF and 0.0 < sc <= 64.0:
                good += 1
        c_end = c_off + char_count * _CHAR_RECORD
        if char_count and good < 0.95 * char_count:
            info["reason"] = f"character records implausible ({good}/{char_count})"
            return info
        if c_end > len(buf):
            info["reason"] = "character table overruns payload"
            return info

        # atlas texture list
        atlas_count = _i32(buf, c_end)
        if not (0 <= atlas_count <= 64):
            info["reason"] = f"implausible atlas texture count {atlas_count}"
            return info
        atlas_pptrs = []
        a_off = c_end + 4
        for _ in range(atlas_count):
            fid, pid, a_off = _rd_pptr(buf, a_off)
            atlas_pptrs.append((fid, pid))
        if a_off + 4 > len(buf):
            info["reason"] = "atlas list overruns payload"
            return info
        atlas_index = _i32(buf, a_off)
        a_off += 4
        flags3 = [_u32(buf, a_off + 4 * i) for i in range(3)]
        a_off += 12
        atlas_w, atlas_h, atlas_pad, atlas_render = (
            _i32(buf, a_off + 4 * i) for i in range(4)
        )
        if not (0 < atlas_w <= 16384 and 0 < atlas_h <= 16384):
            info["reason"] = f"implausible atlas size {atlas_w}x{atlas_h}"
            return info
        a_off += 16

        # used / free glyph-rect lists (16 B per GlyphRect: x, y, w, h)
        def _rect_list(o: int) -> tuple[int, int, int]:
            cnt = _i32(buf, o)
            if not (0 <= cnt <= 1_000_000):
                raise TmpFontError(f"implausible glyph-rect count {cnt}")
            end = o + 4 + cnt * 16
            if end > len(buf):
                raise TmpFontError("glyph-rect list overruns payload")
            return cnt, o + 4, end

        used_count, used_off, a_off = _rect_list(a_off)
        free_count, free_off, a_off = _rect_list(a_off)

        # Rect sanity (e2e-fix16): every rect must fit inside the atlas. On
        # runtime-grown assets the sequential walk can drift — rects like
        # (2, 46, 252, 61864) against a 4096px atlas mean this parse is NOT
        # the real layout, and transforming on it would corrupt the asset.
        for label, cnt, off in (("used", used_count, used_off),
                                ("free", free_count, free_off)):
            for k in range(cnt):
                x, y, w, h = _i32(buf, off + 16 * k), _i32(buf, off + 16 * k + 4), \
                    _i32(buf, off + 16 * k + 8), _i32(buf, off + 16 * k + 12)
                if not (0 <= x <= atlas_w and 0 <= y <= atlas_h
                        and 0 <= w <= atlas_w and 0 <= h <= atlas_h):
                    raise TmpFontError(
                        f"implausible {label} glyph-rect {k}: ({x}, {y}, {w}, {h}) "
                        f"against atlas {atlas_w}x{atlas_h}"
                    )

        info.update(
            ok=True,
            glyph_count=glyph_count,
            glyph_table_offset=glyph_count_off,
            char_count=char_count,
            char_table_offset=char_count_off,
            tables_end=c_end,
            atlas_texture_count=atlas_count,
            atlas_texture_pptrs=atlas_pptrs,
            atlas_texture_index=atlas_index,
            atlas_flags=flags3,
            atlas_width=atlas_w,
            atlas_height=atlas_h,
            atlas_padding=atlas_pad,
            atlas_render_mode=atlas_render,
            used_glyph_rects=used_count,
            free_glyph_rects=free_count,
            parsed_end=a_off,
        )
        return info
    except TmpFontError as e:
        info["reason"] = str(e)
        return info
    except Exception as e:  # noqa: BLE001 — foreign payloads must not crash scans
        info["reason"] = f"{type(e).__name__}: {e}"
        return info


def make_tmp_font_dynamic(
    payload: bytes | bytearray,
    *,
    allow_missing_source: bool = False,
    source_font_path_id: int | None = None,
) -> bytes:
    """Return a transformed payload: Dynamic mode, empty baked tables.

    The glyph/character record blocks are removed and their counts set to 0;
    the population mode int becomes 1 (Dynamic). Every other byte — header,
    face info, source-font PPtr, atlas texture list, glyph-rect lists, tail —
    is preserved exactly.

    A null m_SourceFontFile (pathID == 0) is resolved with
    ``source_font_path_id``: when a candidate is provided, a PPtr
    (fileID=0, pathID=<candidate>) is written into the located source-field
    offset, so the Dynamic asset rasterizes from that legacy Font. Without a
    candidate the call raises TmpNoSourceFontError (the pre-fix15 behavior)
    unless ``allow_missing_source`` is set.

    Raises TmpFontError for payloads that do not match the recovered layout.
    """
    info = analyze_tmp_font_asset(payload)
    if not info.get("ok"):
        raise TmpFontError(f"not a supported TMP_FontAsset payload: {info.get('reason')}")
    if info["atlas_population_mode"] != 0:
        # Dynamic/DynamicOS already: nothing baked to clear, idempotent no-op.
        return bytes(payload)
    src_file_id, src_path_id = info["source_font_pptr"]
    if src_path_id == 0 and source_font_path_id is None and not allow_missing_source:
        raise TmpNoSourceFontError(
            f"TMP asset {info['name']!r} has no m_SourceFontFile; making it "
            "dynamic would blank its rendered text"
        )
    buf = bytes(payload)
    mode_off = info["mode_offset"]
    glyph_count_off = info["glyph_table_offset"]
    tables_end = info["tables_end"]
    out = bytearray(buf)
    if src_path_id == 0 and source_font_path_id is not None:
        # e2e-fix15: assign the candidate source — a fixed-width PPtr write
        # into the offset the parser located; everything stays byte-aligned.
        src_off = info["source_font_offset"]
        out[src_off : src_off + 4] = (0).to_bytes(4, "little")
        out[src_off + 4 : src_off + 12] = int(source_font_path_id).to_bytes(
            8, "little", signed=True
        )
    # 1) Dynamic population mode
    out[mode_off : mode_off + 4] = (1).to_bytes(4, "little")
    # 2) drop glyph records + character records: both table counts remain as
    #    explicit zeros, everything between them and the atlas list is gone.
    # 3) reset the ATLAS to factory-dynamic state (blank-text fix, e2e-fix16):
    #    a baked atlas is fully allocated, so kept free-rect lists leave the
    #    packer no room and no glyph can ever be added -> empty text. A fresh
    #    dynamic asset has ZERO atlas textures and ZERO rects; TMP then
    #    creates its own runtime atlas and rasterizes every glyph from the
    #    (swapped) source font. Atlas index/flags and width/height/render
    #    settings are kept so runtime creation uses the same dimensions.
    atlas_count = info["atlas_texture_count"]
    atlas_index_off = tables_end + 4 + atlas_count * 12
    head = bytes(out[:glyph_count_off])
    out = bytearray(head)
    out += b"\x00" * 12                              # glyph/char/atlas counts = 0
    out += buf[atlas_index_off:]                     # atlas index + flags + dims
                                                     # + validated rect lists + tail
    return bytes(out)


# ---------------------------------------------------------------------------
# e2e-fix17: schema-driven transform on a real UnityPy TypeTree
# ---------------------------------------------------------------------------
#
# The arithmetic transform above locates m_SourceFontFile by walking the
# recovered field sequence — a computation that cannot be VERIFIED for
# assets whose source field is null (no value signature to confirm the
# offset; the fix15 lesson). fix17 instead builds a REAL UnityPy TypeTree
# for the TMP_FontAsset serialized layout, so every field is located by the
# schema and the transform re-serializes the modeled prefix through UnityPy
# TypeTreeHelper. Everything after the modeled prefix (kerning tables,
# legacy face info, creation settings, font weights, ...) is carried as an
# opaque byte tail and preserved byte-exact, exactly like the arithmetic
# path.
#
# The typetree is VALIDATED against the real game's four TMP assets before
# it is trusted:
#   1. every modeled field lands at the same byte offset the arithmetic
#      parser computes (signature-verified anchors: 967 m_AtlasPopulationMode
#      @68, m_SourceFontFile @360 = (0, 933));
#   2. re-serializing the parsed prefix reproduces the original bytes
#      BYTE-EXACT (967: all 62,896 prefix bytes incl. 640 glyphs + chars);
#   3. the typed transform's output is byte-identical to the arithmetic
#      transform's for every verifiable asset.
# (tests/test_tmpfont.py::test_typetree_anchors_and_offsets_real et al.)
#
# Field NAMES follow the TMP 3.0.6 sources where the mapping is certain
# (github.com/needle-mirror/com.unity.textmeshpro tag 3.0.6,
# Scripts/Runtime/TMP_FontAsset.cs); this game's TMP build serializes a
# related but older layout of the same "1.1.0" asset format (e.g.
# m_SourceFontFile sits after the creation-settings block, not before
# m_AtlasPopulationMode), so unknown slots carry neutral names. Only TYPES
# and ORDER drive the byte layout — both pinned by the validation above.

_TT_ALIGN = 0x4000  # UnityPy TypeTreeHelper.kAlignBytes ("align after field")

# FaceInfo: 2 aligned strings + 19 fixed-width numerics (recovered layout;
# the leading triple is pointSize f32 / scale f32 / unitsPerEM u32).
_FACEINFO_NUMERICS = (
    ("float", "m_PointSize"), ("float", "m_Scale"), ("UInt32", "m_UnitsPerEM"),
    ("float", "m_LineHeight"), ("float", "m_AscentLine"), ("float", "m_CapHeight"),
    ("float", "m_Baseline"), ("float", "m_DescentLine"), ("float", "m_Underline"),
    ("float", "m_UnderlineThickness"), ("float", "m_Strikethrough"),
    ("float", "m_StrikethroughThickness"), ("float", "m_SuperscriptOffset"),
    ("float", "m_SuperscriptSize"), ("float", "m_SubscriptOffset"),
    ("float", "m_SubscriptSize"), ("float", "m_Unknown0"), ("float", "m_Unknown1"),
    ("float", "m_Unknown2"),
)
# Glyph record: 52 B (index + 5 metric floats + atlas rect + scale +
# atlasIndex u16 + pad u16 + reserved u32).
_GLYPH_FIELDS = (
    ("UInt32", "m_Index"),
    ("float", "m_Width"), ("float", "m_Height"), ("float", "m_BearingX"),
    ("float", "m_BearingY"), ("float", "m_Advance"),
    ("SInt32", "m_RectX"), ("SInt32", "m_RectY"),
    ("SInt32", "m_RectWidth"), ("SInt32", "m_RectHeight"),
    ("float", "m_Scale"), ("UInt16", "m_AtlasIndex"),
    ("UInt16", "m_Padding"), ("UInt32", "m_Reserved"),
)
# Character record: 16 B.
_CHAR_FIELDS = (
    ("UInt32", "m_ElementType"), ("UInt32", "m_Unicode"),
    ("UInt32", "m_GlyphIndex"), ("float", "m_Scale"),
)
# GlyphRect record: 16 B.
_RECT_FIELDS = (
    ("SInt32", "m_X"), ("SInt32", "m_Y"), ("SInt32", "m_Width"), ("SInt32", "m_Height"),
)

# The modeled prefix, in serialized order. kind: pptr / sint32 / string /
# faceinfo / glyphs / chars / pptrs / rects.
_PREFIX_SPEC = (
    ("m_GameObject", "pptr"),
    ("m_Enabled", "sint32"),
    ("m_Script", "pptr"),
    ("m_Name", "string"),
    ("m_Version", "string"),
    ("m_AtlasPopulationMode", "sint32"),
    ("m_FaceInfo", "faceinfo"),
    ("m_HashCode", "sint32"),            # 112/110/111/2 on the real assets
    ("m_Reserved0", "sint32"),
    ("m_SourceFontFileGUID", "string"),  # 32-char source TTF GUID
    ("m_Reserved1", "sint32"),
    ("m_CreationSourceGUID", "string"),  # 32-char GUID (== source GUID on the
                                         # stock Liberation assets)
    ("m_Reserved2", "sint32"),
    ("m_Reserved3", "sint32"),
    ("m_CreationPointSize", "sint32"),
    ("m_CreationPadding", "sint32"),
    ("m_CreationUnknown0", "sint32"),
    ("m_CreationUnknown1", "sint32"),
    ("m_CreationAtlasWidth", "sint32"),
    ("m_CreationAtlasHeight", "sint32"),
    ("m_CreationRenderMode", "sint32"),
    ("m_CharacterSet", "string"),        # often a range list, or empty
    ("m_CreationGUID", "string"),        # 32-char GUID
    ("m_Reserved4", "sint32"),
    ("m_Reserved5", "sint32"),
    ("m_Reserved6", "sint32"),
    ("m_LegacyAtlasRenderMode", "sint32"),  # 4165/4169 on the real assets
    ("m_Flag", "sint32"),
    ("m_SourceFontFile", "pptr"),        # <- legacy Font (the fix target)
    ("m_Reserved7", "sint32"),
    ("m_Reserved8", "sint32"),
    ("m_Reserved9", "sint32"),
    ("m_GlyphTable", "glyphs"),
    ("m_CharacterTable", "chars"),
    ("m_AtlasTextures", "pptrs"),
    ("m_AtlasTextureIndex", "sint32"),
    ("m_IsMultiAtlasTexturesEnabled", "sint32"),  # 3 flag slots (values 0/1
    ("m_ClearDynamicDataOnBuild", "sint32"),      # on every real asset)
    ("m_IsUsingDynamicTextures", "sint32"),
    ("m_AtlasWidth", "sint32"),
    ("m_AtlasHeight", "sint32"),
    ("m_AtlasPadding", "sint32"),
    ("m_AtlasRenderMode", "sint32"),
    ("m_UsedGlyphRects", "rects"),
    ("m_FreeGlyphRects", "rects"),
)

_TT_ROOT: list = []  # single-entry cache for the built tree


def _tt_node(m_type: str, m_name: str, children=None, align: bool = False):
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode

    return TypeTreeNode(
        m_Level=1, m_Type=m_type, m_Name=m_name, m_ByteSize=0, m_Version=0,
        m_Children=list(children or []),
        m_MetaFlag=_TT_ALIGN if align else None,
    )


def _tt_pptr(name: str):
    return _tt_node("PPtr<Object>", name, [
        _tt_node("SInt32", "m_FileID"),
        _tt_node("SInt64", "m_PathID"),
    ])


def _tt_vector(name: str, element):
    return _tt_node("vector", name, [
        _tt_node("Array", "Array", [_tt_node("SInt32", "size"), element], align=True),
    ])


def _prefix_typetree():
    """Build (once) the UnityPy TypeTreeNode tree for the TMP_FontAsset
    serialized prefix defined by _PREFIX_SPEC."""
    if not _TT_ROOT:
        children = []
        for name, kind in _PREFIX_SPEC:
            if kind == "pptr":
                children.append(_tt_pptr(name))
            elif kind == "sint32":
                children.append(_tt_node("SInt32", name))
            elif kind == "string":
                children.append(_tt_node("string", name, align=True))
            elif kind == "faceinfo":
                children.append(_tt_node("FaceInfo", name, [
                    _tt_node("string", "m_FamilyName", align=True),
                    _tt_node("string", "m_StyleName", align=True),
                ] + [_tt_node(t, n) for t, n in _FACEINFO_NUMERICS]))
            elif kind == "glyphs":
                children.append(_tt_vector(name, _tt_node(
                    "Glyph", "data", [_tt_node(t, n) for t, n in _GLYPH_FIELDS])))
            elif kind == "chars":
                children.append(_tt_vector(name, _tt_node(
                    "TMP_Character", "data", [_tt_node(t, n) for t, n in _CHAR_FIELDS])))
            elif kind == "pptrs":
                children.append(_tt_vector(name, _tt_pptr("data")))
            elif kind == "rects":
                children.append(_tt_vector(name, _tt_node(
                    "GlyphRect", "data", [_tt_node(t, n) for t, n in _RECT_FIELDS])))
            else:  # pragma: no cover — spec is a module constant
                raise ValueError(f"unknown prefix spec kind {kind!r}")
        from UnityPy.helpers.TypeTreeNode import TypeTreeNode

        _TT_ROOT.append(TypeTreeNode(
            m_Level=0, m_Type="MonoBehaviour", m_Name="TMP_FontAsset",
            m_ByteSize=0, m_Version=0, m_Children=children,
        ))
    return _TT_ROOT[0]


def serialize_typetree_prefix(fields: dict) -> bytes:
    """Serialize the modeled prefix fields back to bytes via the typetree."""
    from UnityPy.helpers.TypeTreeHelper import write_value, TypeTreeConfig
    from UnityPy.streams.EndianBinaryWriter import EndianBinaryWriter

    root = _prefix_typetree()
    writer = EndianBinaryWriter(endian="<")
    config = TypeTreeConfig(as_dict=True)
    for child in root.m_Children:
        write_value(fields[child.m_Name], child, writer, config)
    return writer.bytes


def _validate_prefix_fields(fields: dict) -> None:
    """Semantic guards on a typetree-parsed TMP_FontAsset payload.

    Mirrors the arithmetic analyzer's plausibility checks: a schema parse of
    foreign data can still 'succeed' with garbage values, and transforming
    on it would corrupt the asset (e2e-fix16 lesson).
    """
    if fields["m_Enabled"] not in (0, 1):
        raise TmpFontError(f"bad m_Enabled {fields['m_Enabled']}")
    if fields["m_Version"] != "1.1.0":
        raise TmpFontError(f"unsupported asset version {fields['m_Version']!r}")
    mode = fields["m_AtlasPopulationMode"]
    if mode not in (0, 1, 2):
        raise TmpFontError(f"bad m_AtlasPopulationMode {mode}")
    face = fields["m_FaceInfo"]
    point_size, scale, upem = face["m_PointSize"], face["m_Scale"], face["m_UnitsPerEM"]
    if not (0.0 < point_size <= 512.0) or not (0.0 < scale <= 64.0):
        raise TmpFontError(f"implausible face metrics pt={point_size} scale={scale}")
    if upem != 0 and not (16 <= upem <= 65536):
        raise TmpFontError(f"implausible unitsPerEM {upem}")
    glyphs, chars = fields["m_GlyphTable"], fields["m_CharacterTable"]
    if len(glyphs) > 1_000_000 or len(chars) > 1_000_000:
        raise TmpFontError("implausible glyph/character table counts")
    good = 0
    for g in glyphs:
        metrics = (g["m_Width"], g["m_Height"], g["m_BearingX"], g["m_BearingY"], g["m_Advance"])
        rect = (g["m_RectX"], g["m_RectY"], g["m_RectWidth"], g["m_RectHeight"])
        if (
            all(-512.0 <= v <= 8192.0 for v in metrics)
            and all(-1 <= v <= 8192 for v in rect)
            and 0.0 < g["m_Scale"] <= 64.0
            and g["m_Padding"] == 0 and g["m_Reserved"] == 0
        ):
            good += 1
    if glyphs and good < 0.95 * len(glyphs):
        raise TmpFontError(f"glyph records implausible ({good}/{len(glyphs)})")
    good = 0
    for c in chars:
        if (
            c["m_ElementType"] in (0, 1) and c["m_Unicode"] <= 0x10FFFF
            and c["m_GlyphIndex"] <= 0xFFFFFF and 0.0 < c["m_Scale"] <= 64.0
        ):
            good += 1
    if chars and good < 0.95 * len(chars):
        raise TmpFontError(f"character records implausible ({good}/{len(chars)})")
    atlas = fields["m_AtlasTextures"]
    if len(atlas) > 64:
        raise TmpFontError(f"implausible atlas texture count {len(atlas)}")
    aw, ah = fields["m_AtlasWidth"], fields["m_AtlasHeight"]
    if not (0 < aw <= 16384 and 0 < ah <= 16384):
        raise TmpFontError(f"implausible atlas size {aw}x{ah}")
    # e2e-fix16 rect guard: every rect must fit inside the atlas — rects
    # like (2, 46, 252, 61864) against a 4096px atlas mean this parse is NOT
    # the real layout, and transforming on it would corrupt the asset.
    for label, rects in (("used", fields["m_UsedGlyphRects"]),
                         ("free", fields["m_FreeGlyphRects"])):
        for k, r in enumerate(rects):
            x, y, rw, rh = r["m_X"], r["m_Y"], r["m_Width"], r["m_Height"]
            if not (0 <= x <= aw and 0 <= y <= ah and 0 <= rw <= aw and 0 <= rh <= ah):
                raise TmpFontError(
                    f"implausible {label} glyph-rect {k}: ({x}, {y}, {rw}, {rh}) "
                    f"against atlas {aw}x{ah}"
                )


def parse_with_typetree(payload: bytes | bytearray) -> dict:
    """Parse a TMP_FontAsset MonoBehaviour payload with the real typetree.

    Returns a dict with ``ok`` plus the same recovered fields/offsets as
    :func:`analyze_tmp_font_asset` (so callers can treat both uniformly) and
    additionally:
      fields   name -> parsed value for every modeled prefix field
      offsets  name -> byte offset of every modeled prefix field
      tail     bytes after the modeled prefix (opaque, preserved byte-exact)

    ``ok=False`` (with a ``reason``) for payloads that do not match — never
    raises for foreign data.
    """
    from UnityPy.helpers.TypeTreeHelper import read_value, TypeTreeConfig
    from UnityPy.streams.EndianBinaryReader import EndianBinaryReader

    buf = bytes(payload)
    info: dict = {"ok": False, "payload_size": len(buf), "via": "typetree"}
    try:
        if len(buf) < 32:
            info["reason"] = "payload too short"
            return info
        root = _prefix_typetree()
        reader = EndianBinaryReader(buf, endian="<")
        config = TypeTreeConfig(as_dict=True)
        fields: dict = {}
        offsets: dict = {}
        for child in root.m_Children:
            offsets[child.m_Name] = reader.Position
            fields[child.m_Name] = read_value(child, reader, config)
        end = reader.Position
        if not (0 < end <= len(buf)):
            info["reason"] = f"typetree prefix ends outside payload ({end})"
            return info
        _validate_prefix_fields(fields)
        face = fields["m_FaceInfo"]
        src = fields["m_SourceFontFile"]
        mode = fields["m_AtlasPopulationMode"]
        info.update(
            ok=True,
            name=fields["m_Name"],
            version=fields["m_Version"],
            atlas_population_mode=mode,
            mode_offset=offsets["m_AtlasPopulationMode"],
            game_object_pptr=(fields["m_GameObject"]["m_FileID"],
                              fields["m_GameObject"]["m_PathID"]),
            script_pptr=(fields["m_Script"]["m_FileID"], fields["m_Script"]["m_PathID"]),
            family_name=face["m_FamilyName"],
            style_name=face["m_StyleName"],
            point_size=face["m_PointSize"],
            faceinfo_numerics=[face[n] for _, n in _FACEINFO_NUMERICS],
            source_font_pptr=(src["m_FileID"], src["m_PathID"]),
            source_font_offset=offsets["m_SourceFontFile"],
            glyph_count=len(fields["m_GlyphTable"]),
            glyph_table_offset=offsets["m_GlyphTable"],
            char_count=len(fields["m_CharacterTable"]),
            char_table_offset=offsets["m_CharacterTable"],
            tables_end=offsets["m_AtlasTextures"],
            atlas_texture_count=len(fields["m_AtlasTextures"]),
            atlas_texture_pptrs=[(p["m_FileID"], p["m_PathID"]) for p in fields["m_AtlasTextures"]],
            atlas_texture_index=fields["m_AtlasTextureIndex"],
            atlas_flags=[fields["m_IsMultiAtlasTexturesEnabled"],
                         fields["m_ClearDynamicDataOnBuild"],
                         fields["m_IsUsingDynamicTextures"]],
            atlas_width=fields["m_AtlasWidth"],
            atlas_height=fields["m_AtlasHeight"],
            atlas_padding=fields["m_AtlasPadding"],
            atlas_render_mode=fields["m_AtlasRenderMode"],
            used_glyph_rects=len(fields["m_UsedGlyphRects"]),
            free_glyph_rects=len(fields["m_FreeGlyphRects"]),
            prefix_end=end,
            parsed_end=end,
            fields=fields,
            offsets=offsets,
            tail=buf[end:],
        )
        return info
    except TmpFontError as e:
        info["reason"] = str(e)
        return info
    except Exception as e:  # noqa: BLE001 — foreign payloads must not crash scans
        info["reason"] = f"{type(e).__name__}: {e}"
        return info


def make_tmp_font_dynamic_typed(
    payload: bytes | bytearray,
    *,
    allow_missing_source: bool = False,
    source_font_path_id: int | None = None,
) -> bytes:
    """:func:`make_tmp_font_dynamic` on the typetree path (e2e-fix17).

    Parses the payload with :func:`parse_with_typetree` (schema-located
    fields — the source-field offset no longer relies on unverifiable
    arithmetic), mutates the parsed values (Dynamic population mode, empty
    baked glyph/character tables, factory-dynamic empty atlas texture list,
    optional source-font assignment) and re-serializes the prefix through
    UnityPy's typetree writer. The unmodeled tail is appended byte-exact.

    For payloads that pass validation the output is byte-identical to the
    arithmetic transform (verified on the real game assets); payloads the
    typetree rejects raise TmpFontError so the caller can fall back.
    """
    info = parse_with_typetree(payload)
    if not info.get("ok"):
        raise TmpFontError(
            f"not a supported TMP_FontAsset payload: {info.get('reason')}"
        )
    if info["atlas_population_mode"] != 0:
        # Dynamic/DynamicOS already: nothing baked to clear, idempotent no-op.
        return bytes(payload)
    src_file_id, src_path_id = info["source_font_pptr"]
    if src_path_id == 0 and source_font_path_id is None and not allow_missing_source:
        raise TmpNoSourceFontError(
            f"TMP asset {info['name']!r} has no m_SourceFontFile; making it "
            "dynamic would blank its rendered text"
        )
    fields = dict(info["fields"])
    fields["m_AtlasPopulationMode"] = 1
    if src_path_id == 0 and source_font_path_id is not None:
        fields["m_SourceFontFile"] = {
            "m_FileID": 0,
            "m_PathID": int(source_font_path_id),
        }
    # factory-dynamic reset (e2e-fix16 semantics, now schema-driven): drop
    # the baked tables and the baked atlas list so TMP creates its own
    # runtime atlas and rasterizes every glyph from the source font.
    fields["m_GlyphTable"] = []
    fields["m_CharacterTable"] = []
    fields["m_AtlasTextures"] = []
    return serialize_typetree_prefix(fields) + info["tail"]


# ---------------------------------------------------------------------------
# file-level helper
# ---------------------------------------------------------------------------


@dataclass
class TmpAssetEntry:
    file: str            # absolute path of the serialized file
    path_id: int
    name: str
    mode: int
    glyph_count: int
    char_count: int
    has_source_font: bool
    identified_by: str   # "script" | "shape"


@dataclass
class TmpScanResult:
    entries: list[TmpAssetEntry] = field(default_factory=list)
    skipped_unknown_layout: list[dict] = field(default_factory=list)
    unresolved_scripts: list[dict] = field(default_factory=list)


def _read_script_classes(env) -> dict[int, str]:
    """path_id -> m_ClassName for every MonoScript in an env."""
    out: dict[int, str] = {}
    for obj in env.objects:
        if obj.type.name != "MonoScript":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:  # noqa: BLE001 — engine type; failures are exotic
            continue
        cls = tree.get("m_ClassName") or ""
        if cls:
            out[obj.path_id] = cls
    return out


def _resolve_script_map(
    env, file_path: str, game_dir: str, cache: dict
) -> dict[int, str]:
    """Map path_id -> MonoScript class for the MonoBehaviours of `env`.

    Resolves the m_Script PPtr (fileID = externals index + 1) against the
    external serialized files (typically globalgamemanagers.assets). Files
    that cannot be resolved yield an empty map; the caller then falls back
    to payload-shape identification and reports the file. `cache` is keyed
    on the RESOLVED EXTERNAL path so a shared script bank (globalgamemanagers
    .assets) is loaded once for the whole game, not once per scanned file.
    """
    key = os.path.abspath(file_path)
    if key in cache.setdefault("#files", {}):
        return cache["#files"][key]
    classes: dict[int, str] = {}
    try:
        externals = list(getattr(env.file, "externals", None) or [])
        for ext in externals:
            ext_path = getattr(ext, "path", "") or ""
            base = os.path.basename(ext_path.replace("\\", "/"))
            if not base or base.startswith("archive:"):
                continue
            # candidates: relative to the file's dir, the game dir and the
            # *_Data dir (externals carry paths like "globalgamemanagers.assets"
            # or "Resources/unity_builtin_extra").
            seen: dict[int, str] | None = None
            for root in (
                os.path.dirname(file_path),
                game_dir,
                find_data_dir(game_dir) or game_dir,
            ):
                cand = os.path.normpath(os.path.join(root, base))
                if not os.path.isfile(cand) or os.path.abspath(cand) == key:
                    continue
                ck = os.path.abspath(cand)
                if ck not in cache:
                    try:
                        cache[ck] = _read_script_classes(UnityPy.load(cand))
                    except Exception:  # noqa: BLE001
                        cache[ck] = {}
                seen = cache[ck] or None
                if seen:
                    break
            if seen:
                classes.update(seen)
    except Exception:  # noqa: BLE001
        classes = {}
    cache["#files"][key] = classes
    return classes


def scan_tmp_font_assets(game_dir: str, *, files=None) -> TmpScanResult:
    """Find every TMP_FontAsset MonoBehaviour across the game's files."""
    game_dir = os.path.abspath(game_dir)
    res = TmpScanResult()
    cache: dict = {}
    asset_files = files if files is not None else find_asset_files(game_dir)
    for af in asset_files:
        try:
            env = UnityPy.load(af.path)
        except Exception:  # noqa: BLE001
            continue
        script_map = _resolve_script_map(env, af.path, game_dir, cache)
        resolved_any = bool(script_map)
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                raw = obj.get_raw_data()
            except Exception:  # noqa: BLE001
                continue
            if len(raw) < 32:
                continue
            info = analyze_tmp_font_asset(raw)
            # Identification: m_Script PPtr -> MonoScript class name when the
            # external file resolves; payload-shape analysis as fallback for
            # files whose externals cannot be resolved (e.g. some bundles).
            by_script = False
            if len(raw) >= 28:
                fid = _i32(raw, 16)
                pid = _i64(raw, 20)
                if fid > 0 and resolved_any:
                    cls = script_map.get(pid)
                    by_script = cls is not None
                    if by_script and cls != TMP_SCRIPT_CLASS:
                        continue
                    if not by_script and pid in script_map:
                        continue  # resolvable and not TMP: definitively other
                elif fid == 0 and pid in script_map:
                    by_script = script_map[pid] == TMP_SCRIPT_CLASS
                    if not by_script:
                        continue
            if by_script or (info.get("ok") and info.get("version") == "1.1.0"):
                if not info.get("ok"):
                    res.skipped_unknown_layout.append(
                        {
                            "file": af.rel.replace("\\", "/"),
                            "path_id": obj.path_id,
                            "reason": info.get("reason"),
                        }
                    )
                    continue
                fid, pid = info["source_font_pptr"]
                res.entries.append(
                    TmpAssetEntry(
                        file=af.path,
                        path_id=obj.path_id,
                        name=info.get("name") or "",
                        mode=info["atlas_population_mode"],
                        glyph_count=info["glyph_count"],
                        char_count=info["char_count"],
                        has_source_font=pid != 0,
                        identified_by="script" if by_script else "shape",
                    )
                )
            elif info.get("ok") is False and not by_script and len(raw) >= 28:
                fid = _i32(raw, 16)
                if fid > 0 and not resolved_any:
                    res.unresolved_scripts.append(
                        {"file": af.rel.replace("\\", "/"), "path_id": obj.path_id}
                    )
    return res


def make_game_fonts_dynamic(
    game_dir: str,
    out_dir: str,
    *,
    patch_mode: bool = False,
    in_place: bool = False,
    progress=None,
) -> dict:
    """Make every resolvable TMP_FontAsset render from its source font.

    Mirrors ``fonts.replace_source_font`` staging semantics:
      * ``patch_mode=True`` copies nothing: assets are read from ``game_dir``
        (pass the original game so unstaged files are covered), except that
        an already-written ``out_dir/<rel>`` (an earlier stage of this patch
        run — e.g. the font swap) is used INSTEAD so earlier edits never get
        reverted. Only modified files land in ``out_dir``.
      * ``in_place=True`` requires ``game_dir == out_dir`` and rewrites the
        files in place (used on an already-patched output copy).

    Assets whose m_SourceFontFile is null get the file's FIRST legacy `Font`
    object (lowest path_id, sorted) assigned as their source and are made
    dynamic (e2e-fix15 idea, safe since e2e-fix17: the source field is
    located by the typetree, not unverifiable arithmetic); when the file has
    NO Font objects they are still skipped (nothing to point at — e.g. a
    digit-only font asset sharing no file with a swap candidate). Payloads
    that do not match the supported layout are skipped as before.
    """
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if in_place:
        if game_dir != out_dir:
            raise ValueError(
                f"in_place=True requires game_dir == out_dir "
                f"(got {game_dir!r} vs {out_dir!r})"
            )
    elif patch_mode:
        assert_not_nested(game_dir, out_dir, "out_dir")

    scan = scan_tmp_font_assets(game_dir)
    by_file: dict[str, list[TmpAssetEntry]] = defaultdict(list)
    for e in scan.entries:
        by_file[e.file].append(e)

    made_dynamic = 0
    already_dynamic = 0
    skipped_no_source = 0
    sources_assigned = 0
    changed_files = 0
    exported: list[str] = []
    details: list[dict] = []

    for i, (abs_file, entries) in enumerate(sorted(by_file.items())):
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        # stage-order rule (e2e-fix13/14): build on this run's earlier output
        # when present — the font stage may already have written this very
        # file (swapped Font bytes) and the TMP transform must not revert it.
        src_file = abs_file
        if patch_mode and os.path.isfile(out_file):
            src_file = out_file
        env = UnityPy.load(src_file)
        target_ids = {e.path_id for e in entries}
        # e2e-fix15/17: source-assignment candidates — the legacy Font objects
        # of THIS file, lowest path_id first. Font objects survive the font
        # swap (their bytes are replaced in place), so a patched file still
        # lists them; picking one keeps the TMP asset inside its own file's
        # reference space (fileID=0 = same serialized file).
        font_pids = sorted(
            o.path_id for o in env.objects if o.type.name == "Font"
        )
        changed = False
        for obj in env.objects:
            if obj.path_id not in target_ids:
                continue
            entry = next(e for e in entries if e.path_id == obj.path_id)
            try:
                raw = obj.get_raw_data()
            except Exception:  # noqa: BLE001
                continue
            if entry.mode != 0:
                already_dynamic += 1
                details.append({"file": rel.replace("\\", "/"), "path_id": entry.path_id,
                                "name": entry.name, "action": "already_dynamic"})
                continue
            candidate: int | None = None
            if not entry.has_source_font:
                # e2e-fix17: assign the file's first Font via the TYPETREE
                # path — the source field is schema-located, resolving the
                # fix16 unverifiable-offset hazard. No Font in the file:
                # nothing to point at, skip (DSEG14-class digit fonts stay
                # static rather than re-rendering from a CJK face).
                if not font_pids:
                    skipped_no_source += 1
                    details.append({"file": rel.replace("\\", "/"), "path_id": entry.path_id,
                                    "name": entry.name, "action": "skipped_no_source"})
                    continue
                candidate = font_pids[0]
            # typetree-first (fix17); the arithmetic path stays as fallback
            # for payloads the typetree rejects.
            new_payload: bytes | None = None
            via = None
            try:
                new_payload = make_tmp_font_dynamic_typed(
                    raw, source_font_path_id=candidate
                )
                via = "typetree"
            except TmpNoSourceFontError:
                skipped_no_source += 1
                details.append({"file": rel.replace("\\", "/"), "path_id": entry.path_id,
                                "name": entry.name, "action": "skipped_no_source"})
                continue
            except TmpFontError:
                new_payload = None
            if new_payload is None:
                new_payload = make_tmp_font_dynamic(raw, source_font_path_id=candidate)
                via = "arithmetic"
            obj.set_raw_data(new_payload)
            made_dynamic += 1
            changed = True
            detail = {"file": rel.replace("\\", "/"), "path_id": entry.path_id,
                      "name": entry.name, "action": "made_dynamic", "via": via}
            if candidate is not None:
                sources_assigned += 1
                detail["assigned_source_path_id"] = candidate
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
        "made_dynamic": made_dynamic,
        "already_dynamic": already_dynamic,
        "skipped_no_source": skipped_no_source,
        "sources_assigned": sources_assigned,
        "skipped_unknown_layout": len(scan.skipped_unknown_layout),
        "files_modified": changed_files,
        "exported_files": sorted(exported),
        "details": details,
        "unknown_layout": scan.skipped_unknown_layout,
        "unresolved_script_files": sorted(
            {u["file"] for u in scan.unresolved_scripts}
        ),
    }
