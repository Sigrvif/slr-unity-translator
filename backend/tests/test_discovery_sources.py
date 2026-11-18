import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                    # `from test_cli import _run`
sys.path.insert(0, os.path.dirname(_HERE))   # `import ut` for the deep checks

from ut import discovery, inject


def test_metadata_source_on_real_game(game_dir, has_game):
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)
    meta_hits = [h for h in rep.strings if h.source == "metadata"]
    assert meta_hits, "expected IL2CPP literal hits"
    texts = {h.text for h in meta_hits}
    assert any("研究施設" in t for t in texts)
    sample = meta_hits[0]
    assert sample.context.startswith("literal #") and sample.length > 0


def test_textfile_source_on_real_game(game_dir, has_game):
    # The example game ships save.jsonc; even if it yields no strings the
    # scan must not crash on it and must enumerate it.
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)
    files = {os.path.relpath(h.file, game_dir) for h in rep.strings}
    # asset-based files must still be there
    assert any(f.endswith("level0") for f in files)


def test_textfile_extraction_synthetic(tmp_path):
    game = tmp_path / "Fake_Data"
    (game / "StreamingAssets").mkdir(parents=True)
    (game / "StreamingAssets" / "dialog.json").write_text(
        json.dumps({"lines": ["こんにちは", "さようなら"]}, ensure_ascii=False), encoding="utf-8"
    )
    rep = discovery._scan_text_sources(str(tmp_path))
    hits = [h for h in rep if h.source == "textfile"]
    assert {h.text for h in hits} == {"こんにちは", "さようなら"}
    assert hits[0].key == "$.lines[0]" or hits[0].key == "$.lines[1]"


def test_suspect_report_synthetic(tmp_path):
    game = tmp_path / "Fake_Data"
    (game / "StreamingAssets").mkdir(parents=True)
    (game / "StreamingAssets" / "weird.txt").write_bytes(
        "Привет 日本 \x01\x02".encode("utf-8")  # has JP, fails looks_like_text
    )
    suspects = discovery._suspect_files(str(tmp_path))
    assert any("weird.txt" in s for s in suspects)


# --------------------------------------------------------------------------- #
# e2e-fix15: a TextAsset captured by BOTH discovery paths used to double-book
# its content — the typetree path emitted the whole m_Script as ONE hit
# (tokens ["m_Script"]) while the textasset path emitted the finer per-line /
# CSV keys. On patch the full-content replacement landed first and rewrote the
# whole m_Script, so every line key missed (real repro: 18 edits_missed on the
# credits TextAsset of sharedassets0.assets + the StaffRoll CSV).
# --------------------------------------------------------------------------- #
def test_no_mscript_typetree_hit_for_textassets(game_dir, has_game):
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    offenders = [
        h for h in rep.strings
        if h.source == "typetree" and h.obj_type == "TextAsset"
        and h.tokens and h.tokens[0] == "m_Script"
    ]
    assert offenders == [], (
        "TextAsset m_Script must be covered by the textasset source only: "
        + "; ".join(f"{os.path.basename(h.file)}#{h.path_id}" for h in offenders)
    )
    # the filter must stay NARROW: the textasset source still covers the
    # content (the real game's credits asset carries per-line hits) and other
    # TextAsset typetree leaves (m_Name) are still emitted.
    assert any(h.source == "textasset" for h in rep.strings), "textasset source lost"
    assert any(
        h.source == "typetree" and h.obj_type == "TextAsset"
        and h.tokens and h.tokens[0] != "m_Script"
        for h in rep.strings
    ), "non-m_Script TextAsset typetree hits (m_Name) must survive"


def test_full_translation_patch_misses_zero(game_dir, has_game, tmp_path):
    """The user-visible repro: translate EVERY unique string and patch —
    before the fix the full-content typetree hit rewrote whole m_Script blobs
    and orphaned their line keys (18 edits_missed). A short marker per string
    guarantees the blob's line structure changes, which is what unmasked the
    bug (an "[EN]" + original translation kept the lines and hid it)."""
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    trans: dict[str, str] = {}
    occ: dict[str, list] = {}
    for i, h in enumerate(rep.strings):
        trans.setdefault(h.text, f"[EN{i}]")
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, str(game_dir)), "abs": h.file,
            "path_id": h.path_id, "type": h.obj_type, "source": h.source,
            "offset": h.offset, "length": h.length, "key": h.key,
            "tokens": h.tokens})
    assert len(trans) >= 100, "example game should carry a real string body"

    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")
    stats = inject.patch_game(str(game_dir), str(tmp_path / "out"), trans,
                              str(idx), overwrite=True, patch_mode=True)
    assert stats["edits_missed"] == 0
    # every dispatched edit landed (nothing staged without being counted)
    staged = sum(len(occs) for occs in occ.values())
    assert stats["strings_written"] == staged and staged > 0
