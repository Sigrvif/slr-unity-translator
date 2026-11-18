"""I5a: textfile source end-to-end — discover -> index -> patch_game.

Synthetic tmp game whose StreamingAssets/dialog.json holds Japanese; the
patched OUTPUT copy must carry the translation and the original must stay
byte-identical. Also covers the I3 unknown-source occurrence accounting.
"""
import json
import os

from ut import discovery, inject


def _make_game(tmp_path):
    game = tmp_path / "game"
    sa = game / "Fake_Data" / "StreamingAssets"
    sa.mkdir(parents=True)
    (sa / "dialog.json").write_text(
        json.dumps({"lines": ["こんにちは", "さようなら"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return game


def _build_index(rep, game):
    """Index in the slr_cli cmd_scan shape."""
    occ = {}
    for h in rep.strings:
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, str(game)).replace("\\", "/"),
            "abs": h.file, "path_id": h.path_id, "source": h.source,
            "offset": h.offset, "length": h.length, "key": h.key,
            "tokens": h.tokens})
    return occ


def test_textfile_patch_roundtrip(tmp_path):
    game = _make_game(tmp_path)
    dialog = game / "Fake_Data" / "StreamingAssets" / "dialog.json"
    original = dialog.read_bytes()

    rep = discovery.discover(str(game), scan_textures=False, scan_fonts=False)
    hits = [h for h in rep.strings if h.source == "textfile"]
    assert {h.text for h in hits} == {"こんにちは", "さようなら"}

    idx_path = tmp_path / "index.json"
    idx_path.write_text(json.dumps(_build_index(rep, game), ensure_ascii=False),
                        encoding="utf-8")
    trans = {"こんにちは": "Hello there", "さようなら": "Goodbye"}
    out = tmp_path / "out_game"
    stats = inject.patch_game(str(game), str(out), trans, str(idx_path))

    assert stats["textfiles_patched"] == 1
    assert stats["strings_written"] == 2
    assert stats["edits_missed"] == 0

    # output copy carries the translations
    out_dialog = out / "Fake_Data" / "StreamingAssets" / "dialog.json"
    data = json.loads(out_dialog.read_text(encoding="utf-8"))
    assert data["lines"] == ["Hello there", "Goodbye"]
    # original untouched, byte-identically
    assert dialog.read_bytes() == original


def test_patch_reports_unknown_source_occurrences(tmp_path):
    """I3: an index occurrence with a source no write-back handles must be
    counted in edits_missed instead of disappearing silently."""
    game = _make_game(tmp_path)
    rep = discovery.discover(str(game), scan_textures=False, scan_fonts=False)
    occ = _build_index(rep, game)
    occ["こんにちは"].append({
        "file": "Fake_Data/StreamingAssets/dialog.json",
        "abs": str(game / "Fake_Data" / "StreamingAssets" / "dialog.json"),
        "path_id": 0, "source": "nope", "offset": 0, "length": 0,
        "key": "", "tokens": []})
    idx_path = tmp_path / "index.json"
    idx_path.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "out_game"
    stats = inject.patch_game(str(game), str(out), {"こんにちは": "Hi"},
                              str(idx_path))
    assert stats["strings_written"] == 1   # the textfile occurrence landed
    assert stats["edits_missed"] == 1      # the bogus one did not vanish
    data = json.loads((out / "Fake_Data" / "StreamingAssets" / "dialog.json")
                      .read_text(encoding="utf-8"))
    assert data["lines"][0] == "Hi"
