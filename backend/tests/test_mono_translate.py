"""End-to-end: patch_game translates Mono code literals (source "mono").

Builds a mini game tree (csc-compiled Assembly-CSharp.dll inside <Game>_Data/
Managed), scans it, fills translations for the literal, exports via
inject.patch_game, then — when the Mono test game (SLRUNITY_MONO_GAME) with its embedded
Mono runtime is present — loads the PATCHED DLL on the game's own Mono and
checks the translated strings come out.
"""
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import has_game  # noqa: F401

from ut import inject

CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
has_csc = pytest.mark.skipif(not os.path.isfile(CSC), reason="csc not present")

# a real Unity Mono runtime to execute the patched DLL (the best possible
# oracle); present next to the second example game
# a local Unity Mono game (never named in the repository) supplying the
# real runtime + managed dir; set SLRUNITY_MONO_GAME to enable these tests
MONO_GAME = os.environ.get("SLRUNITY_MONO_GAME", "")
import glob as _glob
MONO_MANAGED = next(iter(_glob.glob(os.path.join(MONO_GAME, "*_Data",
                                                 "Managed"))), "")
has_mono_rt = pytest.mark.skipif(
    not os.path.isfile(os.path.join(MONO_GAME, "MonoBleedingEdge",
                                    "EmbedRuntime", "mono-2.0-bdwgc.dll")),
    reason="embedded Mono runtime not present (SLRUNITY_MONO_GAME)")

JP = {
    "Title": "タイトル画面",
    "Load": "セーブデータをロード中",
}
SOURCE = (
    "public class GameText {\n"
    "    public static string Title() { return \"%s\"; }\n"
    "    public static string Load() { return \"%s\"; }\n"
    "}\n" % (JP["Title"], JP["Load"])
)
EN = {"タイトル画面": "Title Screen", "セーブデータをロード中": "Loading save data..."}


@pytest.fixture(scope="module")
def mono_game(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("monoe2e")
    game = tmp / "MiniMonoGame"
    managed = game / "MiniMonoGame_Data" / "Managed"
    managed.mkdir(parents=True)
    src = tmp / "GameText.cs"
    src.write_text(SOURCE, encoding="utf-8")
    dll = managed / "Assembly-CSharp.dll"
    r = subprocess.run([CSC, "/nologo", "/target:library", "/out:" + str(dll),
                        str(src)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    (game / "MiniMonoGame_Data" / "globalgamemanagers").write_bytes(b"x")
    return str(game)


@has_csc
def test_scan_collects_mono_literals(mono_game):
    from ut import discovery

    rep = discovery.discover(mono_game, scan_textures=False, scan_fonts=False,
                             scan_demosaic=False)
    mono_rows = [h for h in rep.strings if h.source == "mono"]
    assert {h.text for h in mono_rows} == set(JP.values())
    assert all(h.obj_type == "MonoLiteral" for h in mono_rows)
    assert all(h.file.endswith("Assembly-CSharp.dll") for h in mono_rows)


@has_csc
def test_patch_game_translates_mono_literals(mono_game, tmp_path):
    from ut import discovery

    rep = discovery.discover(mono_game, scan_textures=False, scan_fonts=False,
                             scan_demosaic=False)
    index = {}
    for h in rep.strings:
        index.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, mono_game).replace("\\", "/"),
            "abs": h.file, "path_id": h.path_id, "source": h.source,
            "offset": h.offset, "length": h.length, "key": h.key,
            "tokens": h.tokens})
    out = tmp_path / "patch"
    stats = inject.patch_game(
        mono_game, str(out), dict(EN), str(_write_index(index, tmp_path)),
        overwrite=True, patch_mode=True)
    assert stats["mono_patched"] == 1
    assert stats["strings_written"] >= len(JP)
    assert stats["edits_missed"] == 0
    patched_dll = out / "MiniMonoGame_Data" / "Managed" / "Assembly-CSharp.dll"
    assert patched_dll.is_file()
    # the original stays untouched
    with open(os.path.join(mono_game, "MiniMonoGame_Data", "Managed",
                           "Assembly-CSharp.dll"), "rb") as f:
        assert f.read() != patched_dll.read_bytes()
    _assert_translated(str(patched_dll))


@has_csc
@has_mono_rt
def test_game_mono_runs_the_patched_dll(mono_game, tmp_path):
    """The strongest oracle: the patched DLL executes on the game's own
    embedded Mono and returns the translated strings."""
    from ut import discovery

    rep = discovery.discover(mono_game, scan_textures=False, scan_fonts=False,
                             scan_demosaic=False)
    index = {}
    for h in rep.strings:
        index.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, mono_game).replace("\\", "/"),
            "abs": h.file, "path_id": h.path_id, "source": h.source,
            "offset": h.offset, "length": h.length, "key": h.key,
            "tokens": h.tokens})
    out = tmp_path / "patch"
    inject.patch_game(mono_game, str(out), dict(EN),
                      str(_write_index(index, tmp_path)),
                      overwrite=True, patch_mode=True)
    patched = str(out / "MiniMonoGame_Data" / "Managed" / "Assembly-CSharp.dll")
    oracle = os.path.join(_HERE, "_mono_oracle.ps1")
    rr = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", oracle, "-DllPath", patched,
         "-MonoDir", os.path.join(MONO_GAME, "MonoBleedingEdge"),
         "-ManagedDir", MONO_MANAGED,
         "-TypeName", "GameText"],
        capture_output=True, text=True, timeout=300)
    out_text = rr.stdout.strip()
    assert "Title=Title Screen" in out_text, out_text
    assert "Load=Loading save data..." in out_text, out_text


def _write_index(index, tmp_path):
    p = tmp_path / "index.json"
    p.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return p


def _assert_translated(patched_dll):
    """Parser-level check: the grown #US heap contains the EN records."""
    from ut.monopatch import DotNetAssembly, us_records

    asm = DotNetAssembly(open(patched_dll, "rb").read())
    strings = {s for _o, _x, s in us_records(asm.us_heap)}
    assert {"Title Screen", "Loading save data..."} <= strings


# --------------------------------------------------------------------------- #
# v2.6b full-heap compaction: the practical budget is the whole old heap
# plus the section slack (translated Japanese bytes are reclaimed), so real
# translation sets fit completely.
# --------------------------------------------------------------------------- #
@has_csc
def test_compaction_preserves_untranslated_and_repoints_all(mono_game):
    """Translate ONE of three literals; the other two must survive verbatim
    and every ldstr token in the patched image must land on a valid record
    of the new heap."""
    import struct

    from ut.monopatch import (DotNetAssembly, _iter_il, _LDSTR,
                              method_body_info, translate_literals, us_records)

    dll = os.path.join(mono_game, "MiniMonoGame_Data", "Managed",
                       "Assembly-CSharp.dll")
    data = open(dll, "rb").read()
    patched, stats = translate_literals(data, {JP["Title"]: "Title Screen"})
    assert patched is not None
    assert stats["texts_applied"] == 1 and stats["dropped"] == 0

    asm = DotNetAssembly(patched)
    strings = {s for _o, _x, s in us_records(asm.us_heap)}
    assert "Title Screen" in strings
    assert JP["Load"] in strings          # untranslated survives verbatim

    # every ldstr token targets a valid record of the NEW heap
    valid = {o for o, _x, _s in us_records(asm.us_heap)}
    checked = 0
    for _m, rva, _n, _s in asm.method_rows():
        body = method_body_info(asm, rva)
        if not body:
            continue
        _o, co, cs, _e = body
        for _i, op, opnd in _iter_il(patched, co, cs):
            if op == _LDSTR and opnd:
                tok = struct.unpack_from("<I", patched, opnd)[0]
                if (tok >> 24) == 0x70:
                    checked += 1
                    assert (tok & 0xFFFFFF) in valid
    assert checked >= 2


@has_csc
def test_compaction_full_coverage_real_dll():
    """The feature's real case: EVERY JP literal of the Mono test game's
    Assembly-CSharp.dll translated with realistic-length English must fit
    (compaction reclaims the Japanese bytes) and leave a self-consistent
    image."""
    real_dll = os.path.join(MONO_MANAGED, "Assembly-CSharp.dll")
    if not os.path.isfile(real_dll):
        pytest.skip("Mono test game not present (SLRUNITY_MONO_GAME)")
    from ut.monopatch import (DotNetAssembly, _iter_il, _LDSTR,
                              method_body_info, translate_literals,
                              us_records)

    orig = open(real_dll, "rb").read()
    asm0 = DotNetAssembly(orig)

    def is_jp(s):
        return any("぀" <= c <= "ヿ" or "一" <= c <= "龯"
                   for c in s)

    jp = [(o, x, s) for o, x, s in us_records(asm0.us_heap) if is_jp(s)]
    assert len(jp) >= 100
    trans = {}
    for _o, _x, s in jp:
        ratio = 2.2 if len(s) > 12 else 1.3   # dialogue expands, labels less
        trans[s] = "W" * max(3, int(len(s) * ratio))
    patched, stats = translate_literals(orig, trans)
    assert stats["dropped"] == 0, "realistic set must fit fully"
    assert stats["texts_applied"] == len(jp)
    assert stats["methods_skipped"] == 0

    # self-consistency of the patched image
    asm = DotNetAssembly(patched)
    valid = {o for o, _x, _s in us_records(asm.us_heap)}
    for _m, rva, _n, _s in asm.method_rows():
        body = method_body_info(asm, rva)
        if not body:
            continue
        _o, co, cs, _e = body
        for _i, op, opnd in _iter_il(patched, co, cs):
            if op == _LDSTR and opnd:
                tok = struct.unpack_from("<I", patched, opnd)[0]
                if (tok >> 24) == 0x70:
                    assert (tok & 0xFFFFFF) in valid
