import json
import os
import shutil
import sys
from collections import defaultdict

import pytest
import UnityPy

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from ut import discovery, inject, jp


def _translations_for(rep, source, n=2):
    # v2.2: hits now include NON-Japanese strings. This test verifies JP
    # write-back round-trips; its "[EN]..." verification markers are bracketed
    # ASCII and deliberately fail the new visible-text gate on rescan (real
    # translations are prose, not markers). EN write-back is covered by
    # test_discovery_nonjp.test_en_raw_string_patch_roundtrip.
    texts = [h.text for h in rep.strings
             if h.source == source and jp.contains_japanese(h.text)][:n]
    return {t: f"[EN]{t[:8]}" for t in texts}


def test_roundtrip_all_sources(game_dir, has_game, tmp_path):
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)
    out = tmp_path / "out_game"
    # export index the way slr_cli will
    idx = tmp_path / "index.json"
    occ = {}
    for h in rep.strings:
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, game_dir), "abs": h.file,
            "path_id": h.path_id, "source": h.source, "offset": h.offset,
            "length": h.length, "key": h.key, "tokens": h.tokens})
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")

    trans = {}
    # "textasset" added to the brief's three so this game's 19 TextAsset
    # hits get round-trip coverage too (it has 0 textfile hits, so that
    # source stays uncovered until a game ships StreamingAssets text).
    for src in ("typetree", "raw", "metadata", "textasset"):
        trans.update(_translations_for(rep, src))
    stats = inject.patch_game(str(game_dir), str(out), trans, str(idx))
    assert stats["strings_written"] >= 1
    # Task 6 interface: per-source file counters must be present.
    assert {"metadata_patched", "textassets_patched", "textfiles_patched"} <= set(stats)
    if any(h.source == "metadata" for h in rep.strings):
        assert stats["metadata_patched"] >= 1
    if any(h.source == "textasset" for h in rep.strings):
        assert stats["textassets_patched"] >= 1

    # verify: patched game scans back with the translations present
    rep2 = discovery.discover(str(out), scan_textures=False, scan_fonts=False)
    texts2 = {h.text for h in rep2.strings}
    for t in trans.values():
        assert t in texts2, f"missing {t!r}"
    # originals gone
    for t in trans:
        assert t not in texts2 or t in trans.values()


def _get_by_tokens(tree, tokens):
    """Read the leaf `tokens` addresses in a typetree (mirror of
    inject._set_by_tokens' navigation); only called on paths a successful
    write just landed on, so plain indexing is safe."""
    cur = tree
    for t in tokens:
        cur = cur[t]
    return cur


def test_same_object_typetree_and_textasset_both_land(game_dir, has_game, tmp_path):
    """Regression (fix round 1): when ONE object has both typetree edits and
    textasset edits, both must land. The old per-object loop did a separate
    read_typetree/save_typetree pair per source; UnityPy 1.25.2's
    save_typetree only stashes bytes while read_typetree re-reads the
    ORIGINAL buffer, so the second save silently discarded the first edit
    (real case: sharedassets0.assets pid 885 — m_Name tt hit + m_Script
    line ta hits)."""
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)

    # find a (file, path_id) with BOTH a typetree hit and a textasset hit
    by_obj = defaultdict(list)
    for h in rep.strings:
        if h.source in ("typetree", "textasset"):
            by_obj[(h.file, h.path_id)].append(h)
    dual = {
        k: v for k, v in by_obj.items()
        if any(h.source == "typetree" for h in v)
        and any(h.source == "textasset" for h in v)
    }
    assert dual, "expected an object carrying both typetree and textasset hits"

    # prefer a tt hit on a leaf other than the full m_Script (m_Name style —
    # its marker lands outside m_Script, independent of the ta rewrite);
    # this game's sharedassets0.assets pid 885 is exactly that.
    pick = None
    for k in sorted(dual):
        tt_hit = next(h for h in dual[k] if h.source == "typetree")
        if tt_hit.tokens != ["m_Script"]:
            pick = k
            break
    if pick is None:
        pick = sorted(dual)[0]
    hits = dual[pick]
    tt_hit = next(h for h in hits if h.source == "typetree")
    ta_hit = next(h for h in hits if h.source == "textasset")

    trans = {tt_hit.text: "EN_TT_X", ta_hit.text: "EN_TA_X"}
    assert len(trans) == 2  # distinct source texts -> distinct markers
    # every occurrence of these two texts is one edit that must land
    expected_written = sum(
        1 for h in rep.strings if h.text in trans
    )

    # index restricted to those texts, built the way slr_cli exports it
    occ = {}
    for h in rep.strings:
        if h.text in trans:
            occ.setdefault(h.text, []).append({
                "file": os.path.relpath(h.file, game_dir), "abs": h.file,
                "path_id": h.path_id, "source": h.source, "offset": h.offset,
                "length": h.length, "key": h.key, "tokens": h.tokens})
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "out_game"
    stats = inject.patch_game(str(game_dir), str(out), trans, str(idx))

    # BOTH markers present in the output copy of that one object
    obj_file, pid = pick
    rel = os.path.relpath(obj_file, str(game_dir))
    env = UnityPy.load(str(out / rel))
    obj = next(o for o in env.objects if o.path_id == pid)
    tree = obj.read_typetree()
    # typetree marker, at the field the occurrence's tokens address
    assert "EN_TT_X" in _get_by_tokens(tree, tt_hit.tokens)
    # textasset marker, inside the (decoded) m_Script
    script = tree.get("m_Script", "")
    if isinstance(script, (bytes, bytearray)):
        script = bytes(script).decode("utf-8", "surrogateescape")
    assert "EN_TA_X" in script
    # counting: only edits that landed, one per occurrence
    assert stats["strings_written"] == expected_written
    assert stats["skipped_raw"] == 0
    assert stats["textassets_patched"] >= 1
