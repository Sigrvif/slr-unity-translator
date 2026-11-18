"""Contract v2: patch_mode export tests.

``patch_mode=True`` writes ONLY the files actually modified into out_dir —
a patch folder of whole-file replacements at their game-relative paths.
No whole-game copy, no unrelated files, and the source game stays
byte-identical. Guards (out_dir == game_dir / nested either way) must fire
before any write in patch mode too, and in_place + patch_mode is rejected.
"""
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                    # `from test_cli import _run`
sys.path.insert(0, os.path.dirname(_HERE))   # `import ut` for the deep checks

from test_cli import _run
from test_guards import _mini_game
from test_stage_fonts_textures import _copy_asset_file, _data_dir, _sha256

from ut import discovery, inject, textures
from ut.fonts import list_fonts, replace_source_font

ARIAL = r"C:\Windows\Fonts\arial.ttf"

# 32-bit alpha formats: an exact-pixel assertion is safe (4444/5551 would
# quantize the marker colors) — mirrors test_stage_fonts_textures.
_EXACT_ALPHA = {3, 5}


def _rel_files(root) -> set:
    """Every file under root as a '/'-separated root-relative path."""
    out = set()
    for r, _dirs, names in os.walk(str(root)):
        for n in names:
            out.add(os.path.relpath(os.path.join(r, n), str(root)).replace("\\", "/"))
    return out


def _get_by_tokens(tree, tokens):
    cur = tree
    for t in tokens:
        cur = cur[t]
    return cur


# --------------------------------------------------------------------------- #
# strings stage: only changed files land, patched correctly
# --------------------------------------------------------------------------- #
def test_patch_mode_writes_only_changed_files(game_dir, has_game, tmp_path):
    import UnityPy

    from ut import metadata as ut_metadata

    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    # one translation per source family this game actually has
    trans = {}
    for src in ("typetree", "raw", "metadata", "textasset"):
        h = next((h for h in rep.strings if h.source == src), None)
        if h:
            trans[h.text] = f"[PM]{src}:en"
    assert len(trans) >= 4, "example game should yield all four source families"

    # index subset restricted to the translated texts, exported the way
    # slr_cli does it
    occ = {}
    for h in rep.strings:
        if h.text in trans:
            occ.setdefault(h.text, []).append({
                "file": os.path.relpath(h.file, str(game_dir)), "abs": h.file,
                "path_id": h.path_id, "source": h.source, "offset": h.offset,
                "length": h.length, "key": h.key, "tokens": h.tokens})
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")

    expected = {o["file"].replace("\\", "/")
                for occs in occ.values() for o in occs}

    # canary: hash every ORIGINAL file we expect to read from
    orig_hashes = {rel: _sha256(os.path.join(str(game_dir), *rel.split("/")))
                   for rel in expected}

    out = tmp_path / "patch_out"
    stats = inject.patch_game(str(game_dir), str(out), trans, str(idx),
                              patch_mode=True)

    assert stats["copied"] is False
    assert stats["strings_written"] >= len(trans)
    assert stats["edits_missed"] == 0
    # the patch folder holds EXACTLY the modified files — nothing else
    assert _rel_files(out) == expected
    assert stats["exported_files"] == sorted(expected)

    # the originals are byte-identical
    for rel, h in orig_hashes.items():
        assert _sha256(os.path.join(str(game_dir), *rel.split("/"))) == h

    # the translations really landed in the exported files
    def out_path(o):
        return os.path.join(str(out), *o["file"].split("/"))

    def object_at(o):
        env = UnityPy.load(out_path(o))
        return next(x for x in env.objects if x.path_id == o["path_id"])

    for jp, en in trans.items():
        o = occ[jp][0]
        if o["source"] == "metadata":
            with open(out_path(o), "rb") as f:
                lits = ut_metadata.read_literals(f.read())
            lit = next(l for l in lits if l.index == o["path_id"])
            assert lit.text == en
        elif o["source"] == "typetree":
            tree = object_at(o).read_typetree()
            assert _get_by_tokens(tree, o["tokens"]) == en
        elif o["source"] == "textasset":
            d = object_at(o).read()
            script = getattr(d, "m_Script", b"")
            if isinstance(script, str):
                script = script.encode("utf-8", "surrogateescape")
            assert en in bytes(script).decode("utf-8", "ignore")
        elif o["source"] == "raw":
            payload = object_at(o).get_raw_data()
            assert en.encode("utf-8") in payload


# --------------------------------------------------------------------------- #
# guards: refuse nonsense BEFORE any write, also in patch mode
# --------------------------------------------------------------------------- #
def test_patch_mode_guard(tmp_path):
    game, idx, dialog = _mini_game(tmp_path)
    before = dialog.read_bytes()
    trans = {"こんにちは": "EN"}
    with pytest.raises(ValueError, match="must not equal or sit inside"):
        inject.patch_game(str(game), str(game), trans, str(idx), patch_mode=True)
    with pytest.raises(ValueError):
        inject.patch_game(str(game), str(game / "out"), trans, str(idx),
                          patch_mode=True)
    with pytest.raises(ValueError):
        # game inside out_dir is equally fatal
        inject.patch_game(str(game), str(game.parent), trans, str(idx),
                          patch_mode=True)
    # nothing touched, nothing created
    assert dialog.read_bytes() == before
    assert not (game / "out").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["game", "index.json"]


def test_patch_mode_rejects_in_place_combo(tmp_path):
    game = tmp_path / "game"
    (game / "Fake_Data").mkdir(parents=True)
    ttf = tmp_path / "f.ttf"
    ttf.write_bytes(b"fake-ttf")
    md = tmp_path / "manifest"
    md.mkdir()
    (md / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="mutually exclusive"):
        replace_source_font(str(game), str(game), str(ttf),
                            in_place=True, patch_mode=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        textures.import_textures(str(game), str(game), str(md),
                                 in_place=True, patch_mode=True)


def test_patch_mode_fonts_guard(tmp_path):
    game = tmp_path / "game"
    (game / "Fake_Data").mkdir(parents=True)
    ttf = tmp_path / "f.ttf"
    ttf.write_bytes(b"fake-ttf")
    md = tmp_path / "manifest"
    md.mkdir()
    (md / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        replace_source_font(str(game), str(game), str(ttf), patch_mode=True)
    with pytest.raises(ValueError):
        replace_source_font(str(game), str(game / "out"), str(ttf),
                            patch_mode=True)
    with pytest.raises(ValueError):
        textures.import_textures(str(game), str(game), str(md), patch_mode=True)
    with pytest.raises(ValueError):
        textures.import_textures(str(game), str(game / "out"), str(md),
                                 patch_mode=True)
    assert not (game / "out").exists()


# --------------------------------------------------------------------------- #
# fonts stage: patch_mode writes only the font-bearing files
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.isfile(ARIAL), reason="arial.ttf not present")
def test_patch_mode_fonts_stage(game_dir, has_game, tmp_path):
    """Fake game built from the real game's resources.assets (bears a legacy
    Font): patch_mode must export ONLY that file with the new TTF embedded,
    leaving both the real game and the fake source untouched."""
    import UnityPy

    data = _data_dir(game_dir)
    src = os.path.join(str(data), "resources.assets")
    src_hash = _sha256(src)

    fake = tmp_path / "fakegame"
    fake_data = fake / os.path.basename(str(data))
    fake_file = _copy_asset_file(src, str(fake_data))
    fake_hash = _sha256(fake_file)

    entries = [e for e in list_fonts(str(fake)) if e.obj_type == "Font"]
    assert entries, "example game's resources.assets should bear a Font"

    out = tmp_path / "patchout"
    res = replace_source_font(str(fake), str(out), ARIAL, patch_mode=True)
    assert res["fonts_replaced"] == len(entries) >= 1

    rel = f"{os.path.basename(str(data))}/resources.assets"
    # the patch folder holds EXACTLY the font-bearing file (no .resS: the
    # embedded TTF lives inside the .assets object, streams are untouched)
    assert _rel_files(out) == {rel}
    assert res["exported_files"] == [rel]

    # sources untouched — real game AND fake source dir
    assert _sha256(src) == src_hash
    assert _sha256(fake_file) == fake_hash

    # the exported file carries the arial payload in every Font object
    with open(ARIAL, "rb") as f:
        arial_bytes = f.read()
    env = UnityPy.load(str(out / os.path.basename(str(data)) / "resources.assets"))
    landed = fonts_seen = 0
    for o in env.objects:
        if o.type.name != "Font":
            continue
        fonts_seen += 1
        fd = o.read_typetree().get("m_FontData")
        if isinstance(fd, list):
            fd = bytes(fd)
        if bytes(fd or b"") == arial_bytes:
            landed += 1
    assert fonts_seen == len(entries)
    assert landed == len(entries)


# --------------------------------------------------------------------------- #
# e2e-fix13: font_source_dir — the patch font stage must reach fonts living
# in files the strings project never staged (string-less font files), and
# must build on an earlier stage's out_dir output instead of reverting it.
# --------------------------------------------------------------------------- #
def _font_game(game_dir, tmp_path, name):
    """Smallest game dir that exposes the staging gap: resources.assets from
    the real game (bears a legacy Font, bears NO strings) plus a plain
    StreamingAssets dialog.json (the string file). Returns (game, font_rel,
    string_rel); the game is a fresh copy, safe to mutate/hash."""
    data = _data_dir(game_dir)
    data_name = os.path.basename(str(data))
    game = tmp_path / name
    _copy_asset_file(os.path.join(str(data), "resources.assets"),
                     str(game / data_name))
    # one *_Data dir for the whole synthetic game: the font file AND the
    # string file share SampleGame_Data, so find_data_dir is
    # unambiguous (two _Data dirs would make it pick the wrong one).
    dialog = game / data_name / "StreamingAssets" / "dialog.json"
    dialog.parent.mkdir(parents=True)
    dialog.write_text('{"lines": ["こんにちは"]}', encoding="utf-8")
    font_rel = f"{data_name}/resources.assets"
    return game, font_rel, f"{data_name}/StreamingAssets/dialog.json"


def test_patch_mode_font_source_dir_stage(game_dir, has_game, tmp_path):
    """replace_source_font(patch_mode) against a game that ALSO holds a
    string file: only the FONT-BEARING file is exported (the string file is
    never pulled into the font patch), the payload lands, and the source is
    byte-identical."""
    import UnityPy

    game, font_rel, string_rel = _font_game(game_dir, tmp_path, "fntgame")
    before = {rel: _sha256(os.path.join(str(game), *rel.split("/")))
              for rel in (font_rel, string_rel)}

    payload = b"FONT-SOURCE-DIR-PAYLOAD-0123456789"
    ttf = tmp_path / "replacement.ttf"
    ttf.write_bytes(payload)

    out = tmp_path / "patchout"
    res = replace_source_font(str(game), str(out), str(ttf), patch_mode=True)
    assert res["fonts_replaced"] >= 1
    # ONLY the font file — the string file stays out of a font-only patch
    assert res["exported_files"] == [font_rel]
    assert _rel_files(out) == {font_rel}

    # the payload really landed
    env = UnityPy.load(str(out / font_rel.replace("/", os.sep)))
    fonts_seen = landed = 0
    for o in env.objects:
        if o.type.name != "Font":
            continue
        fonts_seen += 1
        fd = o.read_typetree().get("m_FontData")
        if isinstance(fd, list):
            fd = bytes(fd)
        if bytes(fd or b"") == payload:
            landed += 1
    assert fonts_seen >= 1
    assert landed == fonts_seen

    # the source game is untouched
    for rel, h in before.items():
        assert _sha256(os.path.join(str(game), *rel.split("/"))) == h


def test_patch_mode_font_stage_builds_on_out_dir_output(game_dir, has_game, tmp_path):
    """Stage-order rule: when out_dir/<rel> already exists (an earlier stage
    of the same patch run exported that file — sharedassets-style files bear
    BOTH strings and fonts), the swap must apply to THAT file, not revert it
    with the pristine source."""
    import UnityPy

    game, font_rel, _string_rel = _font_game(game_dir, tmp_path, "fntgame2")
    font_path = os.path.join(str(game), *font_rel.split("/"))
    rel = font_rel.replace("/", os.sep)

    # Simulate an earlier stage's export: a UnityPy round-trip of the source
    # with a marker edit the font stage must preserve.
    env = UnityPy.load(font_path)
    marker = "STAGE-ORDER-MARKER"
    marked = 0
    for o in env.objects:
        try:
            tree = o.read_typetree()
        except Exception:  # noqa: BLE001
            continue
        if isinstance(tree, dict) and tree.get("m_Name"):
            tree["m_Name"] = tree["m_Name"] + marker
            o.save_typetree(tree)
            marked += 1
            break
    assert marked == 1, "resources.assets should bear a namable object"
    out = tmp_path / "patchout"
    os.makedirs(os.path.join(str(out), os.path.dirname(rel)))
    # env.save appends the archive basename, so pack into a temp dir first
    # (the same pattern ut/fonts.py and ut/inject.py use) and move the file
    # into out_dir/<rel>.
    import shutil as _shutil
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as td:
        env.save(pack="lz4", out_path=td)
        _shutil.copy2(os.path.join(td, os.path.basename(font_path)),
                      os.path.join(str(out), rel))

    payload = b"BUILD-ON-OUT-PAYLOAD-9876543210"
    ttf = tmp_path / "replacement.ttf"
    ttf.write_bytes(payload)
    res = replace_source_font(str(game), str(out), str(ttf), patch_mode=True)
    assert res["fonts_replaced"] >= 1

    out_file = os.path.join(str(out), rel)
    env2 = UnityPy.load(out_file)
    saw_font_with_payload = saw_marker = False
    for o in env2.objects:
        try:
            tree = o.read_typetree()
        except Exception:  # noqa: BLE001
            continue
        if o.type.name == "Font":
            fd = tree.get("m_FontData")
            if isinstance(fd, list):
                fd = bytes(fd)
            if bytes(fd or b"") == payload:
                saw_font_with_payload = True
        if isinstance(tree, dict) and str(tree.get("m_Name", "")).endswith(marker):
            saw_marker = True
    assert saw_font_with_payload, "font swap landed"
    assert saw_marker, "the earlier stage's edit survived (not reverted to the source)"


def test_patch_mode_font_source_dir_cli(game_dir, has_game, tmp_path):
    """The full CLI job the JS export sends: staged dir holds ONLY the string
    file, font_source_dir points at the ORIGINAL game. out must gain the
    font file (swapped) AND the translated string file; nothing is written
    back to either source tree."""
    import UnityPy

    game, font_rel, string_rel = _font_game(game_dir, tmp_path, "origgame")
    font_rel_pl = font_rel.replace("/", os.sep)
    string_rel_pl = string_rel.replace("/", os.sep)

    staged = tmp_path / "staged"
    (staged / string_rel_pl).parent.mkdir(parents=True)
    staged_dialog = staged / string_rel_pl
    staged_dialog.write_bytes(
        (game / string_rel_pl).read_bytes())  # staging copies the original

    trans = {"こんにちは": "ENPATCH"}
    index = {"こんにちは": [{
        "file": string_rel, "abs": str(staged_dialog), "path_id": 0,
        "source": "textfile", "offset": 0, "length": 0,
        "key": "$.lines[0]", "tokens": [],
    }]}
    (tmp_path / "trans.json").write_text(
        json.dumps(trans, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    payload = b"CLI-FONT-SOURCE-DIR-PAYLOAD-0123456789"
    ttf = tmp_path / "replacement.ttf"
    ttf.write_bytes(payload)

    def snapshot(root):
        return {rel: _sha256(os.path.join(str(root), *rel.split("/")))
                for rel in sorted(_rel_files(root))}

    orig_before = snapshot(game)
    staged_before = snapshot(staged)

    out = tmp_path / "patchout"
    res = _run({"version": 2, "command": "patch", "game_dir": str(staged),
                "out_dir": str(out),
                "translations_path": str(tmp_path / "trans.json"),
                "index_path": str(tmp_path / "index.json"),
                "patch_mode": True, "font_ttf": str(ttf),
                "font_source_dir": str(game)}, tmp_path)
    assert res["ok"] is True, res.get("error")
    assert res["strings_written"] == 1
    assert res["fonts_swapped"] >= 1
    # out holds EXACTLY both files: translated string file + swapped font file
    assert _rel_files(out) == {font_rel, string_rel}
    assert json.loads((out / string_rel_pl).read_text(encoding="utf-8")) == \
        {"lines": ["ENPATCH"]}
    env = UnityPy.load(str(out / font_rel_pl))
    landed = fonts_seen = 0
    for o in env.objects:
        if o.type.name != "Font":
            continue
        fonts_seen += 1
        fd = o.read_typetree().get("m_FontData")
        if isinstance(fd, list):
            fd = bytes(fd)
        if bytes(fd or b"") == payload:
            landed += 1
    assert fonts_seen >= 1
    assert landed == fonts_seen

    # neither source tree was touched
    assert snapshot(game) == orig_before
    assert snapshot(staged) == staged_before

    # backward compat: the same job WITHOUT font_source_dir scans the staged
    # dir only — no font files staged there, so the font stage is a no-op and
    # out gains just the string file.
    out2 = tmp_path / "patchout-compat"
    res2 = _run({"version": 2, "command": "patch", "game_dir": str(staged),
                 "out_dir": str(out2),
                 "translations_path": str(tmp_path / "trans.json"),
                 "index_path": str(tmp_path / "index.json"),
                 "patch_mode": True, "font_ttf": str(ttf)}, tmp_path)
    assert res2["ok"] is True, res2.get("error")
    assert res2.get("fonts_swapped", 0) == 0
    assert _rel_files(out2) == {string_rel}


def test_patch_mode_font_source_dir_guard(game_dir, has_game, tmp_path):
    """The font_source_dir nesting guard fires BEFORE any write — even the
    strings stage must not have started when the guard rejects."""
    game, _font_rel, string_rel = _font_game(game_dir, tmp_path, "guardgame")
    string_rel_pl = string_rel.replace("/", os.sep)
    staged = tmp_path / "staged"
    (staged / string_rel_pl).parent.mkdir(parents=True)
    (staged / string_rel_pl).write_bytes((game / string_rel_pl).read_bytes())
    index = {"こんにちは": [{
        "file": string_rel, "abs": str(staged / string_rel_pl), "path_id": 0,
        "source": "textfile", "offset": 0, "length": 0,
        "key": "$.lines[0]", "tokens": [],
    }]}
    (tmp_path / "trans.json").write_text('{"こんにちは":"EN"}', encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    ttf = tmp_path / "replacement.ttf"
    ttf.write_bytes(b"guard-payload")

    def job(font_source, out):
        return {"version": 2, "command": "patch", "game_dir": str(staged),
                "out_dir": str(out),
                "translations_path": str(tmp_path / "trans.json"),
                "index_path": str(tmp_path / "index.json"),
                "patch_mode": True, "font_ttf": str(ttf),
                "font_source_dir": str(font_source)}

    # font_source_dir == out_dir, inside it, or containing it → rejected.
    # All three pair up with the SAME out_dir ("out-eq"): the third case is
    # font_source=tmp_path, which contains out-eq.
    out_base = tmp_path / "out-eq"
    for bad_source in (out_base, out_base / "nested", tmp_path):
        res = _run(job(bad_source, out_base), tmp_path)
        assert res["ok"] is False, f"expected rejection for {bad_source}"
        assert "must not equal or sit inside" in res["error"]
        # rejected BEFORE the strings stage: out_dir was never created
        assert not out_base.exists()
    assert not out_base.exists()


# --------------------------------------------------------------------------- #
# textures stage: patch_mode writes only the re-imported texture files
# --------------------------------------------------------------------------- #
def test_patch_mode_textures_stage(game_dir, has_game, tmp_path):
    """Extract on the real game (bounded), edit one PNG, import with
    patch_mode: out holds ONLY the touched texture file, and both the
    original game file and the source copy stay byte-identical."""
    import UnityPy
    from PIL import Image

    game = str(game_dir)
    ext = tmp_path / "ext"
    res = textures.extract_textures(game, str(ext), max_area=512 * 512)
    manifest = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
    assert res["count"] == len(manifest) > 0

    cand = [e for e in manifest
            if e["format"] in _EXACT_ALPHA
            and (512 * 512 >= e["width"] * e["height"] > 4)]
    pref = [e for e in cand if e["rel"].endswith("/resources.assets")]
    e0 = (pref or cand or manifest)[0]

    src_file = os.path.join(game, *e0["rel"].split("/"))
    src_hash = _sha256(src_file)

    fake = tmp_path / "fakegame"
    rel_parts = e0["rel"].split("/")
    fake_file = _copy_asset_file(src_file, os.path.join(str(fake), *rel_parts[:-1]))
    fake_hash = _sha256(fake_file)

    pngd = tmp_path / "pngs"
    pngd.mkdir()
    img = Image.open(ext / e0["png"]).convert("RGBA")
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 1), (0, 255, 0, 255))
    img.save(str(pngd / e0["png"]))

    out = tmp_path / "patchout"
    res = textures.import_textures(str(fake), str(out), str(ext),
                                   png_dir=str(pngd), import_all=True,
                                   patch_mode=True)
    assert res["imported"] >= 1, res["errors"]

    # the patch folder holds EXACTLY the touched texture file
    assert _rel_files(out) == {e0["rel"]}
    assert res["exported_files"] == [e0["rel"]]

    # C1: the original was never written, and neither was the fake source
    assert _sha256(src_file) == src_hash
    assert _sha256(fake_file) == fake_hash

    # the edited pixels landed in the exported file
    out_file = os.path.join(str(out), *rel_parts)
    assert _sha256(out_file) != src_hash  # really re-packed
    env = UnityPy.load(out_file)
    obj = next(o for o in env.objects if o.path_id == e0["path_id"])
    got = obj.read().image.convert("RGBA")
    if e0["format"] in _EXACT_ALPHA:
        assert got.getpixel((0, 0)) == (255, 0, 0, 255)
        assert got.getpixel((1, 1)) == (0, 255, 0, 255)


# --------------------------------------------------------------------------- #
# CLI envelope: v2 manifest with patch_mode round-trips ok:true
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.isfile(ARIAL), reason="arial.ttf not present")
def test_patch_mode_v2_cli_roundtrip(game_dir, has_game, tmp_path):
    scan = _run({"version": 2, "command": "scan", "game_dir": str(game_dir)},
                tmp_path)
    assert scan["ok"] is True

    # canary: hash every file of the original game before patching
    orig = {rel: _sha256(os.path.join(str(game_dir), *rel.split("/")))
            for rel in _rel_files(game_dir)}

    jp = next(s["text"] for s in scan["strings"] if s["source"] == "metadata")
    (tmp_path / "trans.json").write_text(
        json.dumps({jp: "ENTEST-PM"}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(scan["index"], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "gameout"
    res = _run({"version": 2, "command": "patch", "game_dir": str(game_dir),
                "out_dir": str(out),
                "translations_path": str(tmp_path / "trans.json"),
                "index_path": str(tmp_path / "index.json"),
                "patch_mode": True, "font_ttf": ARIAL}, tmp_path)
    assert res["ok"] is True
    assert res["version"] == 2
    assert res["strings_written"] >= 1
    assert res["fonts_swapped"] >= 1
    assert res["copied"] is False

    exported = set(res["exported_files"])
    assert exported, "patch-mode export should list its files"
    # out_dir holds EXACTLY the exported files — no full game copy
    assert _rel_files(out) == exported
    assert len(exported) < len(orig), "a patch must be smaller than the game"

    # the whole original game tree is byte-identical
    for rel, h in orig.items():
        assert _sha256(os.path.join(str(game_dir), *rel.split("/"))) == h


def test_patch_mode_stale_abs_still_guards(tmp_path):
    """C1 in patch mode: an index abs outside game_dir must raise instead of
    computing an out_file that escapes out_dir."""
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
        inject.patch_game(str(game), str(out), {"こんにちは": "EN"},
                          str(bad_idx), patch_mode=True)
    assert outside.read_text(encoding="utf-8") == "{}"
    assert not out.exists() or _rel_files(out) == set()
