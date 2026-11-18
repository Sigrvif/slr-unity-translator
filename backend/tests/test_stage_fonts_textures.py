"""I5 coverage holes the final review flagged: the font and texture STAGES
had no positive tests.

  * fonts in_place   — replace_source_font(game, game, ttf, in_place=True)
                       on a minimal fake game dir built from the REAL game's
                       fonts-bearing asset file (resources.assets: 1 legacy
                       Font). in_place is the mode cmd_patch uses on the
                       patched output copy; nothing may be rmtree'd/copied,
                       and the ORIGINAL game file must stay byte-identical.
  * texture roundtrip — extract_textures on the REAL game (bounded by
                       max_area), then import_textures into a minimal fake
                       copy of one referenced asset file. Proves the I4
                       <assetfile>__<name>__<path_id>.png naming collides
                       never, Sprites are skipped, edited pixels land, and
                       the C1 re-resolution never writes into the original
                       (the manifest's abs `file` entries point there).
"""
import hashlib
import json
import os
import shutil

import pytest

from ut import textures
from ut.fonts import list_fonts, replace_source_font

# 32-bit alpha formats: an exact-pixel assertion is safe (4444/5551 would
# quantize the marker colors).
_EXACT_ALPHA = {3, 5}


def _data_dir(game_dir):
    d = next(n for n in os.listdir(str(game_dir)) if n.endswith("_Data"))
    return os.path.join(str(game_dir), d)


def _copy_asset_file(src_file, dest_dir):
    """Copy an asset file (+ .resS companion when present) into dest_dir,
    preserving its basename; returns the copied path."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src_file))
    shutil.copy2(src_file, dest)
    ress = src_file + ".resS"
    if os.path.isfile(ress):
        shutil.copy2(ress, dest + ".resS")
    return dest


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_fonts_in_place_on_fake_game_from_real_asset(game_dir, has_game, tmp_path):
    """I5: fonts in_place stage — fake game dir built from the real game's
    resources.assets (which bears a legacy Font object)."""
    import UnityPy

    data = _data_dir(game_dir)
    src = os.path.join(data, "resources.assets")
    src_hash = _sha256(src)

    fake = tmp_path / "fakegame"
    fake_data = fake / os.path.basename(data)
    fake_file = _copy_asset_file(src, str(fake_data))

    entries = [e for e in list_fonts(str(fake)) if e.obj_type == "Font"]
    assert entries, "example game's resources.assets should bear a Font"

    payload = b"FAKE-TTF-PAYLOAD-0123456789abcdef"
    ttf = tmp_path / "replacement.ttf"
    ttf.write_bytes(payload)

    res = replace_source_font(str(fake), str(fake), str(ttf), in_place=True)
    assert res["fonts_replaced"] == len(entries)

    # the payload landed in every Font object's m_FontData, and NOTHING else
    # appeared in the fake game dir (in_place must not copy/rmtree anything)
    env = UnityPy.load(fake_file)
    landed = 0
    fonts_seen = 0
    for o in env.objects:
        if o.type.name != "Font":
            continue
        fonts_seen += 1
        fd = o.read_typetree().get("m_FontData")
        if isinstance(fd, list):
            fd = bytes(fd)
        if bytes(fd or b"") == payload:
            landed += 1
    assert fonts_seen == len(entries)
    assert landed == len(entries)
    assert {p.name for p in fake_data.iterdir()} == {
        "resources.assets", "resources.assets.resS"
    }

    # C1/I1 spirit: the ORIGINAL game's asset file is byte-identical
    assert _sha256(src) == src_hash


def test_texture_extract_import_roundtrip(game_dir, has_game, tmp_path):
    """I5 + I4 + C1: extract on the real game, import into a minimal copy."""
    import UnityPy
    from PIL import Image

    game = str(game_dir)
    ext = tmp_path / "ext"
    res = textures.extract_textures(game, str(ext), max_area=512 * 512)
    manifest = json.loads(
        (ext / "manifest.json").read_text(encoding="utf-8"))
    assert res["count"] == len(manifest) > 0

    # I4: PNG filenames are unique — the <assetfile> component keeps the
    # same path_id in different asset files from overwriting each other.
    pngs = [e["png"] for e in manifest]
    assert len(pngs) == len(set(pngs))
    assert len({e["rel"] for e in manifest}) > 1  # multi-file manifest really
    for e in manifest:                            # exercises the collision case
        assert (ext / e["png"]).is_file()
    # I4: Sprites are skipped on extract (their Texture2D carries the pixels)
    assert all(e["type"] == "Texture2D" for e in manifest)

    # pick a small exact-alpha entry (prefer resources.assets: cheap copy)
    cand = [
        e for e in manifest
        if e["format"] in _EXACT_ALPHA
        and (512 * 512 >= e["width"] * e["height"] > 4)
    ]
    pref = [e for e in cand if e["rel"].endswith("/resources.assets")]
    e0 = (pref or cand or manifest)[0]

    # C1: hash the ORIGINAL asset file the manifest points at — it must be
    # byte-identical after the import (the abs `file` entries resolve to it).
    src_file = os.path.join(game, *e0["rel"].split("/"))
    src_hash = _sha256(src_file)

    # minimal import target: a fake game holding just that asset file
    fake = tmp_path / "fakegame"
    rel_parts = e0["rel"].split("/")
    fake_file = _copy_asset_file(src_file, os.path.join(str(fake), *rel_parts[:-1]))

    # edit the PNG deterministically (same filename, separate png dir)
    pngd = tmp_path / "pngs"
    pngd.mkdir()
    img = Image.open(ext / e0["png"]).convert("RGBA")
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 1), (0, 255, 0, 255))
    img.save(str(pngd / e0["png"]))

    out = textures.import_textures(
        str(fake), str(tmp_path / "out"), str(ext),
        png_dir=str(pngd), import_all=True)
    assert out["imported"] >= 1, out["errors"]

    # C1: the original was never written (manifest abs pointed into it)
    assert _sha256(src_file) == src_hash

    # the edited pixels landed in the OUTPUT copy's texture
    out_file = os.path.join(str(tmp_path / "out"), *rel_parts)
    assert _sha256(out_file) != src_hash  # the copy really was re-packed
    env = UnityPy.load(out_file)
    obj = next(o for o in env.objects if o.path_id == e0["path_id"])
    got = obj.read().image.convert("RGBA")
    if e0["format"] in _EXACT_ALPHA:
        assert got.getpixel((0, 0)) == (255, 0, 0, 255)
        assert got.getpixel((1, 1)) == (0, 255, 0, 255)
