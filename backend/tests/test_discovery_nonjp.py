"""v2.2 collect-all-visible: discovery must yield strings WITHOUT Japanese.

The user-visible motivation: the test game's main-menu button says
"Achievement" (a TMP label with no typetree, so only the raw source can see
it, and no Japanese characters, so the old JP-only gate never collected it).
An English source row is the only way a user can fix awkward official
English (e.g. "Achievement" -> "Achievements").

Collection rules per source:
  * typetree    non-JP only from display fields (m_Text / m_Localized /
                titles / descriptions / messages / captions)
  * raw         non-JP only inside text components (GameObject leaf names a
                Text/TMP component)
  * textasset   non-JP lines only from content assets (license/package
                boilerplate assets skipped wholesale)
  * textfile    non-JP lines must pass the visible-text shape gate
  * metadata    stays Japanese-only (literal blobs are ~95% framework text;
    no reliable visibility signal)
The JAPANESE collection must be bit-for-bit unaffected (>= the old hit
count, same sources).
"""
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from ut import discovery, inject, jp, rawstrings


def test_nonjp_hits_on_real_game(game_dir, has_game):
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    en = [h for h in rep.strings if not jp.contains_japanese(h.text)]
    texts = {h.text for h in en}
    # the user's example: the main-menu button label
    assert "Achievement" in texts
    ach = next(h for h in en if h.text == "Achievement")
    assert ach.source == "raw" and ach.length > 0 and ach.offset > 0
    # the English localization table comes through typetree display fields
    assert any(h.source == "typetree" and h.text == "Chain Fall" for h in en)
    # menu UI
    assert {"NewGame", "NewGame+", "FreeRoom", "Back", "Credits"} <= texts


def test_japanese_collection_unaffected(game_dir, has_game):
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    assert len(rep.strings) >= 3570, "the JP base collection must not shrink"
    texts = {h.text for h in rep.strings}
    assert any("陽動" in t for t in texts)
    # metadata stays Japanese-only
    meta = [h for h in rep.strings if h.source == "metadata"]
    assert meta
    assert all(jp.contains_japanese(h.text) for h in meta)


def test_internal_names_stay_out(game_dir, has_game):
    """The no-gate probe of this game showed the non-JP material is ~97%
    internal names — none of those may leak into the grid."""
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    texts = {h.text for h in rep.strings}
    for noise in ("Player_IK", "Enemy_Anim", "Fellatio_Idle", "MouthDecal",
                  "AnimationPlayableAsset", "SetSeVolume",
                  "Achievement.NoDeathClear.DeathRecorded"):
        assert noise not in texts, noise


def test_typetree_nonjp_requires_display_field(game_dir, has_game):
    """m_Text/m_Localized leaves are collected without Japanese; m_Name
    leaves never are (even when they read like labels)."""
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    tt = [h for h in rep.strings if h.source == "typetree"
          and not jp.contains_japanese(h.text)]
    assert tt, "expected non-JP typetree hits (English tables)"
    for h in tt:
        leaf = str(h.tokens[-1]).lower() if h.tokens else ""
        assert leaf in discovery.DISPLAY_TT_FIELDS, (h.text, leaf)
        assert jp.looks_like_visible_text(h.text)


def test_textfile_nonjp_shape_gate(tmp_path):
    game = tmp_path / "Fake_Data"
    (game / "StreamingAssets").mkdir(parents=True)
    (game / "StreamingAssets" / "bindings.json").write_text(json.dumps({
        "a": "Grab Grip",        # display label -> collected
        "b": "NewGame+",         # PascalCase + '+'   -> collected
        "c": "interactui",       # lone lowercase key -> rejected
        "d": "messageId",        # lowerCamel         -> rejected
        "e": "a4fa450-47a3-401a-b659-076e7af3a3c7",  # GUID -> rejected
        "f": "こんにちは",        # JP row unchanged
    }, ensure_ascii=False), encoding="utf-8")
    hits = {h.text for h in discovery._scan_text_sources(str(tmp_path))}
    assert {"Grab Grip", "NewGame+", "こんにちは"} <= hits
    for noise in ("interactui", "messageId",
                  "a4fa450-47a3-401a-b659-076e7af3a3c7"):
        assert noise not in hits, noise


def test_textfile_license_boilerplate_skipped(tmp_path):
    game = tmp_path / "Fake_Data"
    (game / "StreamingAssets").mkdir(parents=True)
    body = "Copyright (c) 2024 Someone\nSplit\nIS\nNOT\nLAW\nFIRM\n"
    (game / "StreamingAssets" / "license.txt").write_text(body, encoding="utf-8")
    # same lines without the license marker ARE collected
    (game / "StreamingAssets" / "poem.txt").write_text(
        "Split\nIS\nNOT\n", encoding="utf-8")
    hits = {}
    for h in discovery._scan_text_sources(str(tmp_path)):
        hits.setdefault(os.path.basename(h.file), set()).add(h.text)
    assert hits.get("license.txt", set()) == set(), "license lines must be skipped"
    assert "Split" in hits.get("poem.txt", set()), "content lines survive"


def test_en_raw_string_patch_roundtrip(game_dir, has_game, tmp_path):
    """The whole point: an English source row must be exportable. Translate
    the raw 'Achievement' hit and check the patched level file carries it."""
    import UnityPy

    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    h = next(h for h in rep.strings if h.text == "Achievement" and h.source == "raw")
    occ = {h.text: [{
        "file": os.path.relpath(h.file, str(game_dir)).replace("\\", "/"),
        "abs": h.file, "path_id": str(h.path_id),
        "source": h.source, "offset": h.offset, "length": h.length,
        "key": "", "tokens": []}]}
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")
    tp = tmp_path / "trans.json"
    tp.write_text(json.dumps({"Achievement": "Achievements"},
                             ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "patch_out"
    stats = inject.patch_game(str(game_dir), str(out), {"Achievement": "Achievements"},
                              str(idx), patch_mode=True)
    assert stats["edits_missed"] == 0
    rel = os.path.relpath(h.file, str(game_dir))
    assert rel.replace("\\", "/") in stats["exported_files"]
    env = UnityPy.load(str(out / rel))
    obj = next(x for x in env.objects if x.path_id == h.path_id)
    assert b"Achievements" in obj.get_raw_data()
    # and the record still validates (length prefix + zero padding)
    found = [r.text for r in rawstrings.scan_strings(obj.get_raw_data(),
                                                     only_japanese=False)]
    assert "Achievements" in found
