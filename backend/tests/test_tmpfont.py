"""e2e-fix14: TMP_FontAsset surgery tests.

Covers the byte-level analyzer/transform (analyze_tmp_font_asset /
make_tmp_font_dynamic) against the REAL game's four TMP assets (skip-if-
missing) plus a synthetic mini payload, the file-level stage
(make_game_fonts_dynamic) with the stage-order rule, and the CLI stat.
e2e-fix17 adds the typetree path (parse_with_typetree /
make_tmp_font_dynamic_typed): schema-located source field, validated by
signature-verified anchors + byte-exact prefix round-trips, with the
null-source LiberationSans SDF asset (971) now transformed via a same-file
Font assignment.
"""
import hashlib
import json
import os
import struct
import subprocess
import sys

import pytest

from conftest import STEALTH, has_game
from ut import tmpfont
from ut.tmpfont import analyze_tmp_font_asset, make_tmp_font_dynamic, make_tmp_font_dynamic_typed

# payload dumps of the four real TMP_FontAsset MonoBehaviours live inside the
# game's serialized files; locate them through UnityPy at need.
_DATA_FILES = {
    "sharedassets0.assets": [967, 970, 971],
    "sharedassets1.assets": [81],
}
_EXPECT = {
    967: dict(name="NotoSansJP-Regular SDF", glyphs=640, chars=640, src=933, atlas=361),
    970: dict(name="LiberationSans SDF - Fallback", glyphs=0, chars=0, src=934, atlas=359),
    971: dict(name="LiberationSans SDF", glyphs=250, chars=250, src=0, atlas=360),
    81: dict(name="DSEG14Modern-Regular SDF", glyphs=12, chars=12, src=0, atlas=41),
}


def _data_dir(game_dir):
    d = next(n for n in os.listdir(str(game_dir)) if n.endswith("_Data"))
    return os.path.join(str(game_dir), d)


def _load_payloads(game_dir):
    import UnityPy

    data = _data_dir(game_dir)
    out = {}
    for fname, pids in _DATA_FILES.items():
        env = UnityPy.load(os.path.join(data, fname))
        objs = {o.path_id: o for o in env.objects}
        for pid in pids:
            out[pid] = objs[pid].get_raw_data()
    return out


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ---------------------------------------------------------------------------
# analyzer on the real payloads
# ---------------------------------------------------------------------------

@has_game
@pytest.mark.skipif(STEALTH is None, reason="example game not present")
def test_analyze_real_tmp_assets(game_dir):
    payloads = _load_payloads(game_dir)
    assert sorted(payloads) == [81, 967, 970, 971]
    for pid, raw in payloads.items():
        info = analyze_tmp_font_asset(raw)
        exp = _EXPECT[pid]
        assert info["ok"], (pid, info.get("reason"))
        assert info["name"] == exp["name"]
        assert info["version"] == "1.1.0"
        assert info["atlas_population_mode"] == 0
        assert info["glyph_count"] == exp["glyphs"]
        assert info["char_count"] == exp["chars"]
        # every table count sits exactly where the parser says; the tables
        # must fit inside the payload and the parser must consume them all
        assert 0 < info["tables_end"] <= len(raw)
        assert 0 < info["parsed_end"] <= len(raw)
        assert info["source_font_pptr"][1] == exp["src"]
        assert info["atlas_texture_pptrs"] == [(0, exp["atlas"])]
        assert info["atlas_width"] * info["atlas_height"] > 0
        assert info["family_name"] and info["point_size"] > 0


# ---------------------------------------------------------------------------
# transform round trip on the real payloads
# ---------------------------------------------------------------------------

@has_game
def test_make_dynamic_roundtrip_real(game_dir):
    payloads = _load_payloads(game_dir)
    for pid in (967, 970):
        raw = payloads[pid]
        before = analyze_tmp_font_asset(raw)
        assert before["ok"]
        new = make_tmp_font_dynamic(raw)
        after = analyze_tmp_font_asset(new)
        assert after["ok"], after.get("reason")
        # mode flipped, tables emptied
        assert after["atlas_population_mode"] == 1
        assert after["glyph_count"] == 0 and after["char_count"] == 0
        # face info, source font intact; the baked atlas list is cleared so
        # TMP creates a fresh runtime atlas (e2e-fix16 factory-dynamic reset)
        assert after["name"] == before["name"]
        assert after["family_name"] == before["family_name"]
        assert after["point_size"] == before["point_size"]
        assert after["faceinfo_numerics"] == before["faceinfo_numerics"]
        assert after["source_font_pptr"] == before["source_font_pptr"]
        assert after["atlas_texture_pptrs"] == []
        assert after["atlas_width"] == before["atlas_width"]
        assert after["free_glyph_rects"] == before["free_glyph_rects"]
        # header (through m_Version) byte-exact; after the three zero counts
        # (glyph/char/atlas) the payload resumes at the atlas index field
        assert new[: before["mode_offset"]] == raw[: before["mode_offset"]]
        atlas_index_off = (before["tables_end"]
                           + 4 + before["atlas_texture_count"] * 12)
        assert new[after["glyph_table_offset"] + 12 :] == raw[atlas_index_off :]
        # smaller by exactly the dropped tables + atlas list (three 4-byte
        # counts remain: glyph/char/atlas); idempotent
        dropped = atlas_index_off - before["glyph_table_offset"] - 12
        assert len(new) == len(raw) - dropped
        assert make_tmp_font_dynamic(new) == new


@has_game
def test_no_source_font_refused(game_dir):
    payloads = _load_payloads(game_dir)
    for pid in (971, 81):
        with pytest.raises(tmpfont.TmpNoSourceFontError):
            make_tmp_font_dynamic(payloads[pid])


# ---------------------------------------------------------------------------
# e2e-fix15: null-source assets get a same-file Font ASSIGNED as their source
# ---------------------------------------------------------------------------

@has_game
def test_assign_source_font_on_real_payload(game_dir):
    """971 (LiberationSans SDF, the game's visible-text font, null source)
    becomes dynamic with the file's Font 933 written into m_SourceFontFile;
    the round trip re-parses cleanly and every other field is preserved."""
    raw = _load_payloads(game_dir)[971]
    before = analyze_tmp_font_asset(raw)
    assert before["ok"] and before["source_font_pptr"] == (0, 0)

    new = make_tmp_font_dynamic(raw, source_font_path_id=933)
    after = analyze_tmp_font_asset(new)
    assert after["ok"], after.get("reason")
    assert after["atlas_population_mode"] == 1
    assert after["glyph_count"] == 0 and after["char_count"] == 0
    # the assigned PPtr really landed (fileID=0, pathID=933)
    assert after["source_font_pptr"] == (0, 933)
    # everything except the source field + cleared tables is byte-exact
    assert after["name"] == before["name"]
    assert after["family_name"] == before["family_name"]
    assert after["point_size"] == before["point_size"]
    assert after["faceinfo_numerics"] == before["faceinfo_numerics"]
    assert after["atlas_texture_pptrs"] == []  # baked atlas list cleared
    assert after["atlas_width"] == before["atlas_width"]
    src_off = before["source_font_offset"]
    mode_off = before["mode_offset"]
    assert new[:mode_off] == raw[:mode_off]                    # header
    assert new[mode_off + 4 : src_off] == raw[mode_off + 4 : src_off]  # face info etc.
    assert new[src_off + 12 : src_off + 24] == raw[src_off + 12 : src_off + 24]
    # dropped tables + atlas list: three zero counts remain, then the payload
    # resumes at the atlas index field
    assert new[before["glyph_table_offset"] : before["glyph_table_offset"] + 12] == b"\x00" * 12
    atlas_index_off = (before["tables_end"]
                       + 4 + before["atlas_texture_count"] * 12)
    assert new[before["glyph_table_offset"] + 12 :] == raw[atlas_index_off :]
    dropped = atlas_index_off - before["glyph_table_offset"] - 12
    assert len(new) == len(raw) - dropped
    # idempotent: already-dynamic payloads stay untouched
    assert make_tmp_font_dynamic(new, source_font_path_id=999) == new


@has_game
def test_assign_source_refused_without_candidate(game_dir):
    """Without a candidate the pre-fix15 guard still fires, and a source that
    is NOT null is never overwritten."""
    payloads = _load_payloads(game_dir)
    with pytest.raises(tmpfont.TmpNoSourceFontError):
        make_tmp_font_dynamic(payloads[971])
    # 967 carries a real source (933): a candidate must not touch it
    raw = payloads[967]
    new = make_tmp_font_dynamic(raw, source_font_path_id=12345)
    info = analyze_tmp_font_asset(new)
    assert info["ok"] and info["source_font_pptr"] == (0, 933)


# ---------------------------------------------------------------------------
# e2e-fix17: typetree path — schema-located source field, byte-exact prefix
# ---------------------------------------------------------------------------

_OFFSET_KEYS = (
    "mode_offset", "source_font_offset", "glyph_table_offset",
    "char_table_offset", "tables_end", "parsed_end",
)
_VALUE_KEYS = (
    "name", "version", "atlas_population_mode", "glyph_count", "char_count",
    "source_font_pptr", "atlas_texture_count", "atlas_texture_pptrs",
    "atlas_texture_index", "atlas_flags", "atlas_width", "atlas_height",
    "atlas_padding", "atlas_render_mode", "used_glyph_rects",
    "free_glyph_rects",
)


@has_game
def test_typetree_anchors_and_offsets_real(game_dir):
    """The typetree parse must agree with the signature-verified anchors and
    with the arithmetic analyzer on every field offset and value."""
    from ut.tmpfont import parse_with_typetree, serialize_typetree_prefix

    payloads = _load_payloads(game_dir)
    for pid, raw in payloads.items():
        typed = parse_with_typetree(raw)
        arith = analyze_tmp_font_asset(raw)
        assert typed["ok"], (pid, typed.get("reason"))
        assert arith["ok"]
        for key in _OFFSET_KEYS:
            assert typed[key] == arith[key], (pid, key, typed[key], arith[key])
        for key in _VALUE_KEYS:
            assert typed[key] == arith[key], (pid, key, typed[key], arith[key])
        # byte-exact prefix round trip: re-serializing the parsed values
        # reproduces the original prefix (967: 62,896 bytes incl. 640 glyphs)
        assert serialize_typetree_prefix(typed["fields"]) == raw[: typed["prefix_end"]]
        # the tail (kerning tables, legacy face info, font weights, ...) is
        # carried opaquely and must span the rest of the payload exactly
        assert typed["prefix_end"] + len(typed["tail"]) == len(raw)
    # signature-verified ground truth (967 = NotoSansJP, source Font 933)
    t967 = parse_with_typetree(payloads[967])
    assert t967["mode_offset"] == 68
    assert t967["source_font_offset"] == 360
    assert t967["source_font_pptr"] == (0, 933)
    assert t967["name"] == "NotoSansJP-Regular SDF"
    assert (t967["glyph_count"], t967["char_count"]) == (640, 640)
    assert t967["atlas_texture_pptrs"] == [(0, 361)]
    # 971 (null source): the typetree lands on the same offset the D-block
    # structure implies (== the arithmetic parser's)
    t971 = parse_with_typetree(payloads[971])
    assert t971["source_font_offset"] == 408
    assert t971["source_font_pptr"] == (0, 0)
    assert t971["name"] == "LiberationSans SDF"


@has_game
def test_typetree_transform_971_assigns_source(game_dir):
    """971 (LiberationSans SDF, null source, the game's default UI font)
    becomes dynamic with source Font 933 assigned via the typetree; the
    output is byte-identical to the arithmetic transform (whose offset the
    typetree independently confirms) and the tail is preserved byte-exact."""
    from ut.tmpfont import make_tmp_font_dynamic_typed, parse_with_typetree

    raw = _load_payloads(game_dir)[971]
    before = parse_with_typetree(raw)
    assert before["ok"] and before["source_font_pptr"] == (0, 0)

    typed = make_tmp_font_dynamic_typed(raw, source_font_path_id=933)
    # byte parity with the arithmetic transform on the same assignment
    assert typed == make_tmp_font_dynamic(raw, source_font_path_id=933)
    after = parse_with_typetree(typed)
    assert after["ok"], after.get("reason")
    assert after["atlas_population_mode"] == 1
    assert after["glyph_count"] == 0 and after["char_count"] == 0
    assert after["source_font_pptr"] == (0, 933)
    assert after["atlas_texture_count"] == 0  # factory-dynamic reset
    assert after["atlas_width"] == before["atlas_width"]
    # everything before the population mode and after the modeled prefix is
    # byte-exact; the tail spans exactly the modeled prefix's end
    assert typed[: before["mode_offset"]] == raw[: before["mode_offset"]]
    assert typed[after["prefix_end"] :] == raw[before["prefix_end"] :]
    # smaller by exactly the dropped tables + atlas list (three 4-byte counts
    # remain); idempotent: already-dynamic payloads pass through untouched
    dropped = (before["tables_end"] + 4 + before["atlas_texture_count"] * 12
               - before["glyph_table_offset"] - 12)
    assert len(typed) == len(raw) - dropped
    assert make_tmp_font_dynamic_typed(typed, source_font_path_id=999) == typed


@has_game
def test_typetree_transform_guards(game_dir):
    """Null-source refusal, existing-source protection, junk rejection."""
    from ut.tmpfont import make_tmp_font_dynamic_typed, parse_with_typetree

    payloads = _load_payloads(game_dir)
    with pytest.raises(tmpfont.TmpNoSourceFontError):
        make_tmp_font_dynamic_typed(payloads[971])
    with pytest.raises(tmpfont.TmpNoSourceFontError):
        make_tmp_font_dynamic_typed(payloads[81])  # no Font in its file either
    # 967 keeps its real source even when a candidate is offered
    typed = make_tmp_font_dynamic_typed(payloads[967], source_font_path_id=12345)
    info = parse_with_typetree(typed)
    assert info["ok"] and info["source_font_pptr"] == (0, 933)
    assert info["atlas_population_mode"] == 1
    # foreign junk is rejected, never raises
    bad = parse_with_typetree(os.urandom(2048))
    assert not bad["ok"] and bad.get("reason")


def test_typetree_transform_synthetic_mini_payload():
    """The typetree path consumes the synthetic mini payload (parameter-free
    layout check) with the same results as the arithmetic path."""
    raw, _, _, _ = _build_mini(src_path=0)  # null source
    from ut.tmpfont import parse_with_typetree

    before = parse_with_typetree(raw)
    assert before["ok"] and before["source_font_pptr"] == (0, 0)
    typed = make_tmp_font_dynamic_typed(raw, source_font_path_id=4242)
    assert typed == make_tmp_font_dynamic(raw, source_font_path_id=4242)
    after = parse_with_typetree(typed)
    assert after["ok"] and after["atlas_population_mode"] == 1
    assert after["glyph_count"] == 0 and after["char_count"] == 0
    assert after["source_font_pptr"] == (0, 4242)
    assert after["atlas_texture_count"] == 0
    with pytest.raises(tmpfont.TmpNoSourceFontError):
        make_tmp_font_dynamic_typed(raw)


@has_game
def test_typetree_transform_matches_arithmetic_on_verifiable_assets(game_dir):
    """967/970: the typed transform is byte-identical to the arithmetic one."""
    payloads = _load_payloads(game_dir)
    for pid in (967, 970):
        raw = payloads[pid]
        assert make_tmp_font_dynamic_typed(raw) == make_tmp_font_dynamic(raw)


def test_assign_source_synthetic_mini_payload():
    raw, _, _, _ = _build_mini(src_path=0)  # null source
    before = analyze_tmp_font_asset(raw)
    assert before["ok"] and before["source_font_pptr"] == (0, 0)
    new = make_tmp_font_dynamic(raw, source_font_path_id=4242)
    info = analyze_tmp_font_asset(new)
    assert info["ok"] and info["atlas_population_mode"] == 1
    assert info["glyph_count"] == 0 and info["char_count"] == 0
    assert info["source_font_pptr"] == (0, 4242)
    mode_off = before["mode_offset"]
    assert new[:mode_off] == raw[:mode_off]
    atlas_index_off = before["tables_end"] + 4 + before["atlas_texture_count"] * 12
    # three zero counts (glyph/char/atlas), then the payload resumes at the
    # atlas index field
    assert new[before["glyph_table_offset"] : before["glyph_table_offset"] + 12] == b"\x00" * 12
    assert new[before["glyph_table_offset"] + 12 :] == raw[atlas_index_off :]
    with pytest.raises(tmpfont.TmpNoSourceFontError):
        make_tmp_font_dynamic(raw)


# ---------------------------------------------------------------------------
# synthetic mini payload (parameter-free layout check)
# ---------------------------------------------------------------------------

def _build_mini(*, glyph_count=3, char_count=2, mode=0, src_path=77):
    def s(x):
        b = x.encode("utf-8")
        return struct.pack("<i", len(b)) + b + b"\x00" * ((-len(b)) % 4)

    def pptr(fid, pid):
        return struct.pack("<iq", fid, pid)

    out = bytearray()
    out += pptr(0, 0)
    out += struct.pack("<i", 1)
    out += pptr(1, 2610)
    out += s("Mini SDF")
    out += s("1.1.0")
    mode_off = len(out)
    out += struct.pack("<i", mode)
    out += s("Mini Family")
    out += s("Regular")
    nums = [struct.pack("<f", 64.0), struct.pack("<f", 1.0), struct.pack("<I", 1000)]
    nums += [struct.pack("<f", 90.0 + i) for i in range(16)]
    out += b"".join(nums)
    out += struct.pack("<II", 112, 0)
    out += s("a" * 32) + struct.pack("<I", 0) + s("b" * 32)
    out += struct.pack("<II", 0, 0)
    out += b"".join(struct.pack("<I", v) for v in (64, 8, 1, 0, 4096, 4096, 7))
    out += s("")
    out += s("c" * 32)
    out += struct.pack("<III", 0, 0, 0)
    out += struct.pack("<II", 4165, 0)
    out += pptr(0, src_path)
    out += struct.pack("<III", 0, 0, 1)
    glyph_table_off = len(out)
    out += struct.pack("<i", glyph_count)
    for g in range(glyph_count):
        rec = struct.pack("<I", g + 1)
        rec += struct.pack("<5f", 10.0 + g, 20.0, 1.0, 40.0, 12.0 + g)
        rec += struct.pack("<4i", g * 16, 0, 10, 20)
        rec += struct.pack("<fHH", 1.0, 0, 0)   # scale + atlasIndex + pad
        rec += struct.pack("<I", 0)
        assert len(rec) == 52
        out += rec
    char_table_off = len(out)
    out += struct.pack("<i", char_count)
    for c in range(char_count):
        rec = struct.pack("<IIIf", 1, 0x41 + c, g + c, 1.0)
        assert len(rec) == 16
        out += rec
    # atlas list + atlas fields + rect lists + a token tail
    out += struct.pack("<i", 1) + pptr(0, 361)
    out += struct.pack("<i", 0)              # atlas texture index
    out += struct.pack("<III", 0, 1, 0)      # flags
    out += struct.pack("<iiii", 4096, 4096, 8, 4165)
    out += struct.pack("<i", 1) + struct.pack("<4i", 0, 0, 26, 66)      # used
    out += struct.pack("<i", 1) + struct.pack("<4i", 26, 0, 100, 4096)  # free
    out += b"\x00" * 16                      # tail stand-in
    return bytes(out), mode_off, glyph_table_off, char_table_off


def test_analyze_synthetic_mini_payload():
    raw, mode_off, g_off, c_off = _build_mini()
    info = analyze_tmp_font_asset(raw)
    assert info["ok"], info.get("reason")
    assert info["name"] == "Mini SDF"
    assert info["version"] == "1.1.0"
    assert info["mode_offset"] == mode_off
    assert info["glyph_table_offset"] == g_off
    assert info["char_table_offset"] == c_off
    assert info["glyph_count"] == 3 and info["char_count"] == 2
    assert info["source_font_pptr"] == (0, 77)
    assert info["atlas_texture_pptrs"] == [(0, 361)]
    assert info["atlas_width"] == 4096 and info["atlas_height"] == 4096
    assert info["used_glyph_rects"] == 1 and info["free_glyph_rects"] == 1


def test_make_dynamic_synthetic_mini_payload():
    raw, _, _, _ = _build_mini()
    new = make_tmp_font_dynamic(raw)
    info = analyze_tmp_font_asset(new)
    assert info["ok"] and info["atlas_population_mode"] == 1
    assert info["glyph_count"] == 0 and info["char_count"] == 0
    assert info["source_font_pptr"] == (0, 77)
    assert info["atlas_texture_pptrs"] == []  # baked atlas list cleared
    assert info["family_name"] == "Mini Family"
    assert info["used_glyph_rects"] == 1 and info["free_glyph_rects"] == 1
    # already-dynamic payloads are a no-op
    assert make_tmp_font_dynamic(new) == new


def test_analyze_rejects_corrupted_layout():
    raw, _, _, _ = _build_mini()
    # break the face-info pointSize float -> implausible metrics
    # (mode i32 + family str + style str precede the numerics)
    bad = bytearray(raw)
    mode_off = analyze_tmp_font_asset(raw)["mode_offset"]
    struct.pack_into("<f", bad, mode_off + 32, 1e9)  # pointSize slot
    info = analyze_tmp_font_asset(bytes(bad))
    assert not info["ok"] and "face metrics" in info["reason"]
    # truncate mid-glyph-table -> overrun / implausible
    info2 = analyze_tmp_font_asset(raw[: len(raw) - 40])
    assert not info2["ok"]
    # random junk is never mistaken for TMP
    info3 = analyze_tmp_font_asset(os.urandom(2048))
    assert not info3["ok"]


def test_make_dynamic_refuses_unknown_layout():
    raw, _, _, _ = _build_mini()
    broken = bytearray(raw)
    broken[48] = 0x39  # m_Version "1.1.0" -> "9.1.0": unsupported layout
    with pytest.raises(tmpfont.TmpFontError):
        make_tmp_font_dynamic(bytes(broken))


# ---------------------------------------------------------------------------
# file-level stage on the real game
# ---------------------------------------------------------------------------

@has_game
def test_make_game_fonts_dynamic_patch_mode(game_dir, tmp_path):
    out = tmp_path / "out"
    data = _data_dir(game_dir)
    h_before0 = _sha256(os.path.join(data, "sharedassets0.assets"))
    h_before1 = _sha256(os.path.join(data, "sharedassets1.assets"))

    res = tmpfont.make_game_fonts_dynamic(str(game_dir), str(out), patch_mode=True)
    assert res["tmp_assets_found"] == 4
    # e2e-fix17: the typetree locates 971's null source field by schema, so
    # the file's Font 933 is assigned and 971 converts (made_dynamic=3).
    # Only DSEG14 (sharedassets1: no Font objects) is still skipped.
    assert res["made_dynamic"] == 3
    assert res["sources_assigned"] == 1
    assert res["skipped_no_source"] == 1
    assert res["files_modified"] == 1
    assert res["exported_files"] == ["SampleGame_Data/sharedassets0.assets"]

    # reload the patched file: TMP assets dynamic + empty, everything else
    # (object count) untouched
    import UnityPy

    env = UnityPy.load(str(out / "SampleGame_Data" / "sharedassets0.assets"))
    orig = UnityPy.load(os.path.join(data, "sharedassets0.assets"))
    assert len(env.objects) == len(orig.objects)
    objs = {o.path_id: o for o in env.objects}
    for pid in (967, 970):
        info = analyze_tmp_font_asset(objs[pid].get_raw_data())
        assert info["ok"] and info["atlas_population_mode"] == 1
        assert info["glyph_count"] == 0 and info["char_count"] == 0
        assert info["source_font_pptr"][1] == _EXPECT[pid]["src"]
        assert info["atlas_texture_pptrs"] == []  # baked atlas list cleared
    # e2e-fix17: 971 (null source) is TRANSFORMED via the typetree with the
    # file's Font 933 assigned as its source
    info = analyze_tmp_font_asset(objs[971].get_raw_data())
    assert info["ok"] and info["atlas_population_mode"] == 1
    assert info["glyph_count"] == 0 and info["char_count"] == 0
    assert info["source_font_pptr"] == (0, 933)
    assert info["atlas_texture_pptrs"] == []
    # spot objects unchanged (a Material and a Texture2D path_id from the file)
    def _raw(env_, pid):
        return next(o for o in env_.objects if o.path_id == pid).get_raw_data()
    for pid in (1, 500):
        assert _raw(env, pid) == _raw(orig, pid)

    # sharedassets1.assets was NOT written (its only asset has no candidate
    # Font in its file) and the ORIGINAL game is untouched
    assert not (out / "SampleGame_Data" / "sharedassets1.assets").exists()
    assert _sha256(os.path.join(data, "sharedassets0.assets")) == h_before0
    assert _sha256(os.path.join(data, "sharedassets1.assets")) == h_before1


@has_game
def test_stage_order_builds_on_font_stage_output(game_dir, tmp_path):
    """The TMP stage must build on out_dir/<rel> written by the font stage,
    never revert it to the pristine source (e2e-fix13 rule)."""
    from ut.fonts import replace_source_font

    ttf = tmp_path / "swap.ttf"
    ttf.write_bytes(b"FAKE-SWAPPED-TTF-" + b"x" * 64)
    out = tmp_path / "out"
    replace_source_font(str(game_dir), str(out), str(ttf), patch_mode=True)
    sa0 = out / "SampleGame_Data" / "sharedassets0.assets"
    assert sa0.exists()
    h_after_fonts = _sha256(str(sa0))

    res = tmpfont.make_game_fonts_dynamic(str(game_dir), str(out), patch_mode=True)
    assert res["made_dynamic"] == 3
    # the file was further modified (TMP tables dropped), still carrying the
    # swapped font bytes
    assert _sha256(str(sa0)) != h_after_fonts
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
    assert swapped == 2
    # e2e-fix17: BOTH null-source visible-text assets are converted — 971
    # (LiberationSans SDF) is dynamic with the file's Font 933 (whose bytes
    # this run swapped) as its source and a factory-fresh atlas state; 967
    # keeps its own verified source. DSEG14 (sharedassets1) stays static.
    objs = {o.path_id: o for o in env.objects}
    info = analyze_tmp_font_asset(objs[967].get_raw_data())
    assert info["ok"] and info["atlas_population_mode"] == 1
    assert info["glyph_count"] == 0 and info["char_count"] == 0
    assert info["source_font_pptr"] == (0, 933)
    assert info["atlas_texture_count"] == 0
    info971 = analyze_tmp_font_asset(objs[971].get_raw_data())
    assert info971["ok"] and info971["atlas_population_mode"] == 1
    assert info971["glyph_count"] == 0 and info971["char_count"] == 0
    assert info971["source_font_pptr"] == (0, 933)
    assert info971["atlas_texture_count"] == 0


@has_game
def test_make_game_fonts_dynamic_in_place(game_dir, tmp_path):
    import shutil

    work = tmp_path / "copy"
    shutil.copytree(str(game_dir), str(work))
    res = tmpfont.make_game_fonts_dynamic(str(work), str(work), in_place=True)
    assert res["made_dynamic"] == 3 and res["files_modified"] == 1
    import UnityPy

    env = UnityPy.load(str(work / "SampleGame_Data" / "sharedassets0.assets"))
    objs = {o.path_id: o for o in env.objects}
    assert analyze_tmp_font_asset(objs[967].get_raw_data())["atlas_population_mode"] == 1


@has_game
def test_cli_patch_reports_tmp_stat(game_dir, has_game, tmp_path):
    """Full CLI patch run with font_ttf set must report the TMP stat."""
    CLI = "slr_cli.py"

    def _run(manifest):
        m = tmp_path / "job.json"
        m.write_text(json.dumps(manifest), encoding="utf-8")
        r = tmp_path / "result.json"
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
        "tmp_dynamic": True,   # opt-in since e2e-fix18 (off by default)
        "patch_mode": True,
    })
    assert res["ok"] is True
    assert res["fonts_swapped"] >= 1
    # e2e-fix17: typetree-located source assignment converts 971 as well
    # (967 + 970 + 971); only DSEG14 (no Font in its file) is skipped
    assert res["tmp_assets_made_dynamic"] == 3
    assert res["tmp_assets_skipped_no_source"] == 1
    assert "SampleGame_Data/sharedassets0.assets" in res["exported_files"]
