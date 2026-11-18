"""Fix-wave guard tests (I1 + C1 structural guard).

I1: patch_game / replace_source_font / import_textures with out_dir ==
game_dir (or nested either way) must fail BEFORE any destructive step —
the old code rmtree'd the original game when out_dir pointed at it.
C1: any computed destination path must provably live inside its base.
"""
import json
import os

import pytest

from ut import inject, textures
from ut.files import assert_inside, assert_not_nested
from ut.fonts import replace_source_font


# --------------------------------------------------------------------------- #
# unit level: the guard primitives
# --------------------------------------------------------------------------- #
def test_assert_inside_accepts_real_subpath(tmp_path):
    f = tmp_path / "sub" / "file.png"
    f.parent.mkdir()
    f.write_bytes(b"x")
    assert_inside(str(tmp_path), str(f))  # no raise
    assert_inside(str(tmp_path), str(tmp_path))  # base itself is fine


@pytest.mark.parametrize("escape", [
    "..\\..\\orig\\assets",      # dot-dot traversal
    "../../orig/assets",
])
def test_assert_inside_blocks_traversal(tmp_path, escape):
    with pytest.raises(ValueError):
        assert_inside(str(tmp_path), os.path.join(str(tmp_path), escape))


def test_assert_inside_blocks_cross_drive():
    with pytest.raises(ValueError):
        assert_inside("C:\\base", "D:\\elsewhere\\x")


def test_assert_not_nested_blocks_equal_and_both_directions(tmp_path):
    g = str(tmp_path / "game")
    with pytest.raises(ValueError):
        assert_not_nested(g, g)                      # equal
    with pytest.raises(ValueError):
        assert_not_nested(g, os.path.join(g, "out"))  # out inside game
    with pytest.raises(ValueError):
        assert_not_nested(os.path.join(g, "out"), g)  # game inside out
    assert_not_nested(str(tmp_path / "a"), str(tmp_path / "b"))  # siblings ok


# --------------------------------------------------------------------------- #
# integration: the destructive entry points refuse before touching anything
# --------------------------------------------------------------------------- #
def _mini_game(tmp_path):
    """Smallest legal game dir for patch_game: one StreamingAssets json."""
    game = tmp_path / "game" / "Fake_Data" / "StreamingAssets"
    game.mkdir(parents=True)
    dialog = game / "dialog.json"
    dialog.write_text(json.dumps({"lines": ["こんにちは"]}, ensure_ascii=False),
                      encoding="utf-8")
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"こんにちは": [{
        "file": "Fake_Data/StreamingAssets/dialog.json",
        "abs": str(dialog), "path_id": 0, "source": "textfile",
        "offset": 0, "length": 0, "key": "$.lines[0]", "tokens": [],
    }]}), encoding="utf-8")
    return tmp_path / "game", idx, dialog


def test_patch_game_refuses_out_eq_game(tmp_path):
    game, idx, dialog = _mini_game(tmp_path)
    before = dialog.read_bytes()
    with pytest.raises(ValueError, match="must not equal or sit inside"):
        inject.patch_game(str(game), str(game), {"こんにちは": "EN"}, str(idx))
    assert dialog.read_bytes() == before  # nothing touched


def test_patch_game_refuses_nested_dirs(tmp_path):
    game, idx, _ = _mini_game(tmp_path)
    with pytest.raises(ValueError):
        inject.patch_game(str(game), str(game / "out"),
                          {"こんにちは": "EN"}, str(idx))
    with pytest.raises(ValueError):
        # game inside out_dir is equally fatal (rmtree(out) would eat game)
        inject.patch_game(str(game), str(game.parent),
                          {"こんにちは": "EN"}, str(idx))


def test_patch_game_stale_abs_cannot_escape_out_dir(tmp_path):
    """C1 structural guard: an index abs path OUTSIDE game_dir must raise
    instead of computing an out_file that escapes out_dir."""
    game, idx, dialog = _mini_game(tmp_path)
    outside = tmp_path / "other_game" / "dialog.json"
    outside.parent.mkdir()
    outside.write_text("{}", encoding="utf-8")
    bad_idx = tmp_path / "bad_index.json"
    bad_idx.write_text(json.dumps({"こんにちは": [{
        "file": "../other_game/dialog.json", "abs": str(outside),
        "path_id": 0, "source": "textfile", "offset": 0, "length": 0,
        "key": "$.lines[0]", "tokens": [],
    }]}), encoding="utf-8")
    out = tmp_path / "out_game"
    with pytest.raises(ValueError, match="escapes"):
        inject.patch_game(str(game), str(out), {"こんにちは": "EN"}, str(bad_idx))
    # nothing escaped: the outside file was never written, and out_dir holds
    # only the plain game copy (its dialog.json still untranslated)
    assert outside.read_text(encoding="utf-8") == "{}"
    out_dialog = out / "Fake_Data" / "StreamingAssets" / "dialog.json"
    assert "こんにちは" in out_dialog.read_text(encoding="utf-8")


def test_replace_source_font_refuses_nested(tmp_path):
    game = tmp_path / "game"
    (game / "Fake_Data").mkdir(parents=True)
    ttf = tmp_path / "f.ttf"
    ttf.write_bytes(b"fake-ttf")
    with pytest.raises(ValueError):
        replace_source_font(str(game), str(game), str(ttf))  # equal, not in_place
    with pytest.raises(ValueError):
        replace_source_font(str(game), str(game / "out"), str(ttf))
    with pytest.raises(ValueError):
        replace_source_font(str(game / "out"), str(game), str(ttf))


def test_import_textures_refuses_nested(tmp_path):
    game = tmp_path / "game"
    (game / "Fake_Data").mkdir(parents=True)
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        textures.import_textures(str(game), str(game), str(manifest_dir))
    with pytest.raises(ValueError):
        textures.import_textures(str(game), str(game / "out"), str(manifest_dir))
    with pytest.raises(ValueError):
        textures.import_textures(str(game / "out"), str(game), str(manifest_dir))
    # in_place still demands strict equality
    with pytest.raises(ValueError, match="in_place"):
        textures.import_textures(str(game), str(tmp_path / "x"),
                                 str(manifest_dir), in_place=True)
