"""fix21 regression: path_id must survive the JS JSON.parse round-trip.

Unity path ids are int64 and routinely exceed 2^53 (real case: the Stealth
game's Localization StringTable objects, ids like 103263707529466115 and
-2933933604927357922). As JSON NUMBERS they are silently rounded by the JS
layer's JSON.parse (103263707529466115 -> ...110), the addon then persists
the rounded values into the staged index.json, and at patch time no edit can
match the object again. Net effect: the two localization bundles exported
with zero landed edits, were not listed in exported_files, and the JS revert
phase copied the staged ORIGINALS over them ("Including original 1/2, 2/2")
while the summary reported "180 staged edits did not land".

Contract v2.1: the scan payload carries path_id as a decimal STRING in every
row that crosses into JS; the patch dispatch accepts both forms (int()).
"""
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                    # `from test_cli import _run`
sys.path.insert(0, os.path.dirname(_HERE))   # `import ut`

from test_cli import _run
from test_patch_mode import _rel_files

from ut import discovery, inject

# The exact ids from the user's broken staged index (post-JSON.parse values)
# — proof this regression test guards the real-world shape.
BROKEN_ZH_ID = 103263707529466110      # true id ends ...115
BROKEN_JA_ID = -2933933604927358000    # true id ends ...922


# --------------------------------------------------------------------------- #
# scan payload: path_id is a decimal string everywhere it crosses into JS
# --------------------------------------------------------------------------- #
def test_scan_payload_path_ids_are_strings(game_dir, has_game, tmp_path):
    res = _run({"version": 2, "command": "scan", "game_dir": str(game_dir)},
               tmp_path)
    assert res["ok"] is True, res.get("error")
    assert res["strings"], "example game must yield strings"
    for row in res["strings"]:
        assert isinstance(row["path_id"], str), row
    for occs in res["index"].values():
        for o in occs:
            assert isinstance(o["path_id"], str), o
    for key in ("fonts", "textures", "text_assets"):
        for row in res.get(key) or []:
            assert isinstance(row["path_id"], str), (key, row)


def test_big_id_survives_json_roundtrip(game_dir, has_game, tmp_path):
    """The exact corruption the addon suffered: json.dumps -> JSON.parse on a
    >2^53 id. With the string form the value is bit-exact after the parse."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node not available for the JSON.parse proof")
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    big = next(h for h in rep.strings if abs(h.path_id) > 2**53)
    res = _run({"version": 2, "command": "scan", "game_dir": str(game_dir)},
               tmp_path)
    emitted = {o["path_id"] for occs in res["index"].values() for o in occs}
    assert str(big.path_id) in emitted
    # and the emitted string survives the JS-side parse EXACTLY
    node = json.dumps({"p": str(big.path_id)})
    out = subprocess.run(
        ["node", "-e", f"console.log(JSON.parse({node!r}).p)"],
        capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == str(big.path_id)


# --------------------------------------------------------------------------- #
# patch: string path_ids land their edits (legacy ints keep working)
# --------------------------------------------------------------------------- #
def test_patch_lands_string_path_ids(game_dir, has_game, tmp_path):
    """Every typetree hit of the real game dispatched with a STRING path_id —
    including the >2^53 Localization-table ids — must land, and its file must
    appear in exported_files (the bug shipped it as an unmodified original)."""
    import UnityPy

    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    tt = [h for h in rep.strings if h.source == "typetree"]
    assert tt, "example game must yield typetree hits"
    # make sure the >2^53 shape is actually covered: without a Localization
    # table hit this game cannot reproduce the real-world failure
    if not any(abs(h.path_id) > 2**53 for h in tt):
        pytest.skip("example game has no >2^53 path_id (Localization tables)")

    trans = {}
    occ = {}
    for h in tt:
        trans.setdefault(h.text, f"[STR]{h.text}:en")
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, str(game_dir)).replace("\\", "/"),
            "abs": h.file,
            "path_id": str(h.path_id),  # v2.1 wire form
            "source": h.source, "offset": h.offset,
            "length": h.length, "key": h.key, "tokens": h.tokens})

    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")
    tp = tmp_path / "trans.json"
    tp.write_text(json.dumps(trans, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "patch_out"
    stats = inject.patch_game(str(game_dir), str(out), trans, str(idx),
                              patch_mode=True)
    assert stats["edits_missed"] == 0
    big_files = {os.path.relpath(h.file, str(game_dir)).replace("\\", "/")
                 for h in tt if abs(h.path_id) > 2**53}
    assert big_files, "no >2^53 files to check"
    assert big_files <= set(stats["exported_files"]), (
        "a file whose edits all landed must be in exported_files")

    # the >2^53 edits really landed in the exported bundles
    for h in tt:
        if abs(h.path_id) <= 2**53:
            continue
        rel = os.path.relpath(h.file, str(game_dir))
        env = UnityPy.load(str(out / rel))
        obj = next(x for x in env.objects if x.path_id == h.path_id)
        tree = obj.read_typetree()
        cur = tree
        for t in h.tokens:
            cur = cur[t]
        assert cur == trans[h.text]


def test_patch_still_accepts_legacy_int_path_ids(tmp_path, game_dir, has_game):
    """Pre-v2.1 indexes carry int path_ids — int('..') and int(..) both work,
    so old projects keep patching after the update."""
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    h = next(h for h in rep.strings if h.source == "typetree"
             and abs(h.path_id) <= 2**53)
    occ = {h.text: [{
        "file": os.path.relpath(h.file, str(game_dir)).replace("\\", "/"),
        "abs": h.file, "path_id": h.path_id,  # legacy int
        "source": h.source, "offset": h.offset,
        "length": h.length, "key": h.key, "tokens": h.tokens}]}
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")
    tp = tmp_path / "trans.json"
    tp.write_text(json.dumps({h.text: "[INT]en"}, ensure_ascii=False),
                  encoding="utf-8")
    out = tmp_path / "patch_out"
    stats = inject.patch_game(str(game_dir), str(out), {h.text: "[INT]en"},
                              str(idx), patch_mode=True)
    assert stats["edits_missed"] == 0
    assert stats["strings_written"] >= 1


# --------------------------------------------------------------------------- #
# the user-visible failure mode, end to end: a >2^53 id that crossed JS as a
# NUMBER (the old broken wire form) must be DETECTABLE, not silently no-op
# --------------------------------------------------------------------------- #
def test_broken_js_roundtrip_id_reports_as_missed(game_dir, has_game, tmp_path):
    """A staged index written from JSON.parse-rounded numbers can never match.
    The patch must count those edits in edits_missed (the "180 staged edits
    did not land" warning) — never claim success. This pins the contract that
    makes the JS side ship strings."""
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    big = next(h for h in rep.strings
               if h.source == "typetree" and abs(h.path_id) > 2**53)
    # simulate the JS damage: round through a float, exactly like JSON.parse
    rounded = int(float(big.path_id))
    assert rounded != big.path_id
    occ = {big.text: [{
        "file": os.path.relpath(big.file, str(game_dir)).replace("\\", "/"),
        "abs": big.file, "path_id": rounded,
        "source": big.source, "offset": big.offset,
        "length": big.length, "key": big.key, "tokens": big.tokens}]}
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")
    tp = tmp_path / "trans.json"
    tp.write_text(json.dumps({big.text: "EN"}, ensure_ascii=False),
                  encoding="utf-8")
    out = tmp_path / "patch_out"
    stats = inject.patch_game(str(game_dir), str(out), {big.text: "EN"},
                              str(idx), patch_mode=True)
    assert stats["strings_written"] == 0
    assert stats["edits_missed"] >= 1
    assert big.file.replace("\\", "/") not in stats["exported_files"]
    assert _rel_files(out) == set()
