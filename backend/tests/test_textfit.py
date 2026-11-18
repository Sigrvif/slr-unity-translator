"""Text-fit calibration (contract v2.6c): the game prices text-box width at
N units per CHARACTER (full-width Japanese assumption) — verified in the wild
as `ldc 32; mul` feeding set_sizeDelta in a MessageWindow/Narration typewriter
coroutine. The rewrite scales that constant for Latin text. Same-size IL
edits; both the .NET loader and the game's own Mono execute the result.
"""
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

import ut.monopatch as mp
from ut.monopatch import (DotNetAssembly, _iter_il,
                          calibrate_text_sizing_dll, method_body_info)

CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
has_csc = pytest.mark.skipif(not os.path.isfile(CSC), reason="csc not present")

# the game's exact shape: get_Length -> ldc 32 -> mul -> set_sizeDelta
SOURCE = (
    "public struct V2 { public float x, y; public V2(float a, float b) "
    "{ x = a; y = b; } }\n"
    "public class BoxSizer {\n"
    "    public static int Width() {\n"
    "        string s = \"abcd1234\";\n"
    "        int n = s.Length * 50;\n"
    "        set_sizeDelta(new V2(n, 96f));\n"
    "        return n;\n"
    "    }\n"
    "    public static void set_sizeDelta(V2 v) { }\n"
    "    public static string Label() { return \"w\" + Width(); }\n"
    "}\n"
)


@pytest.fixture(scope="module")
def sizer_dll(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("textfit")
    src = tmp / "BoxSizer.cs"
    src.write_text(SOURCE, encoding="utf-8")
    dll = tmp / "BoxSizer.dll"
    r = subprocess.run([CSC, "/nologo", "/target:library",
                        "/out:" + str(dll), str(src)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return str(dll)


def _mul_const(asm, data):
    """The BoxSizer.Width per-char multiplier constant (ldc before mul)."""
    for ns, tn, mlist, end in mp._type_method_spans(asm):
        if tn != "BoxSizer":
            continue
        for _m, rva, mn, _s in list(asm.method_rows())[mlist - 1:end]:
            if mn != "Width":
                continue
            _o, co, cs, _e = method_body_info(asm, rva)
            last = None
            for _i, op, opnd in _iter_il(data, co, cs):
                if op == 0x1F and opnd is not None:
                    last = data[opnd]
                elif op == 0x5A:
                    return last
    raise AssertionError("no ldc/mul pair found")


@has_csc
def test_pattern_found_and_ratio_scaled(sizer_dll):
    data = open(sizer_dll, "rb").read()
    assert _mul_const(DotNetAssembly(data), data) == 50
    patched, stats = calibrate_text_sizing_dll(data, char_ratio=0.5)
    assert stats["sites"] == 1 and stats["rewritten"] == 1
    assert _mul_const(DotNetAssembly(patched), patched) == 25  # 50 * 0.5


@has_csc
def test_patch_is_same_size_and_loadable(sizer_dll, tmp_path):
    data = open(sizer_dll, "rb").read()
    patched, _st = calibrate_text_sizing_dll(data, char_ratio=0.5)
    assert len(patched) == len(data)
    out = tmp_path / "Patched.dll"
    out.write_bytes(patched)
    DotNetAssembly(open(out, "rb").read())  # still a parseable assembly


@has_csc
def test_ratio_one_leaves_site_byte_identical(sizer_dll):
    """Ratio 1 (no shrink after rounding) must not touch the DLL."""
    data = open(sizer_dll, "rb").read()
    patched, stats = calibrate_text_sizing_dll(data, char_ratio=1)
    assert stats["rewritten"] == 0
    assert patched == data


@has_csc
def test_ratio_floor_keeps_price_positive(sizer_dll):
    """An aggressive ratio must never produce a zero/invalid price."""
    data = open(sizer_dll, "rb").read()
    patched, stats = calibrate_text_sizing_dll(data, char_ratio=0.05)
    assert _mul_const(DotNetAssembly(patched), patched) == 2  # max(2, round(2.5))
    assert stats["rewritten"] == 1


@has_csc
def test_no_pattern_left_unchanged(sizer_dll):
    """Without the set_sizeDelta name the window never matches — the DLL
    comes back byte-identical with zero rewrites."""
    data = open(sizer_dll, "rb").read()
    renamed = data.replace(b"set_sizeDelta", b"sett_sizeDeltb")
    patched, stats = calibrate_text_sizing_dll(renamed, char_ratio=0.5)
    assert stats["rewritten"] == 0
    assert patched == renamed


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


@has_csc
@has_mono_rt
def test_game_mono_executes_calibrated_dll(sizer_dll, tmp_path):
    """The strongest oracle: the game's own Mono computes the CALIBRATED
    width from the patched DLL (8 chars * (50*0.5) = 200)."""
    data = open(sizer_dll, "rb").read()
    patched, _st = calibrate_text_sizing_dll(data, char_ratio=0.5)
    dst = tmp_path / "Patched.dll"
    dst.write_bytes(patched)
    oracle = os.path.join(_HERE, "_mono_oracle.ps1")
    rr = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", oracle, "-DllPath", str(dst),
         "-MonoDir", os.path.join(MONO_GAME, "MonoBleedingEdge"),
         "-ManagedDir", MONO_MANAGED,
         "-TypeName", "BoxSizer", "-Methods", "Label"],
        capture_output=True, timeout=300)
    out = rr.stdout.decode("utf-8", "replace").strip()
    assert "Label=w200" in out, out  # 8 chars * 25 = 200


@has_mono_rt
def test_wrapper_accepts_exact_cli_kwargs(tmp_path):
    """Regression (v2.6i/v2.6j shipped this TypeError): slr_cli calls the
    WRAPPER with swing_scale, but only the DLL-level function had that
    parameter — every armed export died in the stage's fail-soft except and
    (before the sizing_rewritten=0 fix) printed nothing at all. Call the
    wrapper with the CLI's exact keyword set."""
    from ut.monopatch import calibrate_text_sizing
    res = calibrate_text_sizing(
        MONO_GAME, str(tmp_path),
        char_ratio=0.5, swing_scale=0.25, patch_mode=True)
    assert res["sizing_rewritten"] >= 1, res["sizing_log"]
    log = "\n".join(res["sizing_log"])
    assert "32 -> 16" in log, log
    assert "swing amplitude 400 -> 100" in log, log


@has_mono_rt
def test_wrapper_writes_game_relative_path(tmp_path):
    """Regression (v2.6k shipped this): the stage reported success but wrote
    to bare out_dir/Managed/... — no stage, zip or game ever reads that
    path, so the patch shipped UNCALIBRATED while the log claimed two sites
    rewritten. The output must land at the game-relative location the zip
    and the game both use, with the calibrated constants in it."""
    import struct

    from ut.monopatch import (DotNetAssembly, _iter_il, method_body_info)
    from ut.monopatch import calibrate_text_sizing as _cts
    data_dir = next(d for d in os.listdir(MONO_GAME)
                    if d.endswith("_Data"))
    res = _cts(MONO_GAME, str(tmp_path),
               char_ratio=0.5, swing_scale=0.25, patch_mode=True)
    assert res["sizing_rewritten"] >= 1, res["sizing_log"]
    rel = f"{data_dir}/Managed/Assembly-CSharp.dll"
    assert res["exported_files"] == [rel], res["exported_files"]
    out = tmp_path / data_dir / "Managed" / "Assembly-CSharp.dll"
    assert out.is_file(), f"patched DLL missing at game-relative path: {out}"
    assert not (tmp_path / "Managed").exists(), \
        "stray bare Managed/ output (the v2.6k bug shape)"
    patched = out.read_bytes()
    # d__15's ldc.i4.s constants must now read 16, 64, 16, 64 (was 32, 64, 32, 64)
    asm = DotNetAssembly(patched)
    all_consts = []
    for _ns, tn, mlist, end in mp._type_method_spans(asm):
        if tn != "<DrawText>d__15":
            continue
        rows = list(asm.method_rows())
        for _m, rva, mn, _s in rows[mlist - 1:end]:
            _o, co, cs, _e = method_body_info(asm, rva)
            try:
                insns = list(_iter_il(patched, co, cs))
            except Exception:
                continue
            all_consts.append([patched[opnd] for _i, op, opnd in insns
                               if op == 0x1F and opnd is not None])
    nonempty = [c for c in all_consts if c]
    assert nonempty == [[16, 64, 16, 64]], nonempty
