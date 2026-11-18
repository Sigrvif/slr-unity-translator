"""Monopatch: the code-level demosaic tier (UUD DumbTypeDemosaic equivalent).

The fixture DLL is COMPILED at test time with the .NET Framework csc (present
on every Windows install) so the parser/stubber is exercised against real
ECMA-335 output, not hand-rolled bytes. Skips cleanly when csc is missing.
"""
import os
import shutil
import struct
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from ut import monopatch as mp

CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
has_csc = pytest.mark.skipif(not os.path.isfile(CSC), reason="csc not present")

SOURCE = r"""
using System;
public struct Strength { public float v; }
public class MosaicController : UnityEngine.MonoBehaviourProxy {
    private float strength = 16f;
    public void Awake() { Console.WriteLine("awake"); }
    public void Update() { Console.WriteLine("upda" + strength); }
    public float GetStrength() { return strength * 2f; }
    public string GetName() { return "mos"; }
    public Strength GetStruct() { return new Strength(); }
    public void OnEnable() { try { Console.WriteLine("x"); } catch (Exception) { } }
}
public class CensorPlate : UnityEngine.MonoBehaviourProxy {
    public void Update() { Console.WriteLine("plate"); }
    public void LateUpdate() { Console.WriteLine("late"); }
}
public class NormalController : UnityEngine.MonoBehaviourProxy {
    public void Update() { Console.WriteLine("normal"); }
}
namespace UnityEngine { public class MonoBehaviourProxy { } }
"""


@pytest.fixture(scope="module")
def fixture_dll(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("monopatch")
    src = tmp / "src.cs"
    src.write_text(SOURCE, encoding="utf-8")
    dll = tmp / "Assembly-CSharp.dll"
    r = subprocess.run([CSC, "/nologo", "/target:library",
                        "/out:" + str(dll), str(src)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return str(dll)


@has_csc
def test_parse_finds_types_and_methods(fixture_dll):
    asm = mp.DotNetAssembly(open(fixture_dll, "rb").read())
    types = {(ns, n): ml for _r, ns, n, ml in asm.type_rows()}
    assert ("", "MosaicController") in types
    assert ("", "CensorPlate") in types
    assert ("", "NormalController") in types
    names = [m[2] for m in asm.method_rows()]
    for want in ("Awake", "Update", "GetStrength", "GetName", "GetStruct",
                 "OnEnable", "LateUpdate"):
        assert want in names


@has_csc
def test_scan_managed_code_reports_types_and_game_rel(fixture_dll, tmp_path):
    # a fake game: <root>/Game_Data/Managed/Assembly-CSharp.dll
    managed = tmp_path / "Game_Data" / "Managed"
    managed.mkdir(parents=True)
    shutil.copy2(fixture_dll, managed / "Assembly-CSharp.dll")
    (managed / "mscorlib.dll").write_bytes(b"MZ not a table stream")
    rows = mp.scan_managed_code(str(tmp_path / "Game_Data"), game_dir=str(tmp_path))
    by_type = {r["type"]: r for r in rows}
    assert set(by_type) == {"MosaicController", "CensorPlate"}
    assert by_type["MosaicController"]["rel"] == "Game_Data/Managed/Assembly-CSharp.dll"
    assert "Update" in by_type["CensorPlate"]["methods"]
    # system-prefixed and unparseable DLLs are skipped, never fatal
    assert all(r["rel"].endswith("Assembly-CSharp.dll") for r in rows)


@has_csc
def test_stubber_hits_magic_methods_only_same_size(fixture_dll):
    data = open(fixture_dll, "rb").read()
    patched, actions, skipped = mp.patch_managed_dll(data)
    # same-size, in-place: the PE never changes length or section layout
    assert len(patched) == len(data)
    stubbed = {(a["type"], a["method"]) for a in actions}
    assert ("MosaicController", "Awake") in stubbed
    assert ("MosaicController", "Update") in stubbed
    assert ("CensorPlate", "Update") in stubbed
    assert ("CensorPlate", "LateUpdate") in stubbed
    # non-magic methods of the SAME type are untouched (callers keep working)
    assert ("MosaicController", "GetStrength") not in stubbed
    assert ("MosaicController", "GetStruct") not in stubbed
    # the unrelated class is untouched entirely
    assert all(a["type"] != "NormalController" for a in actions)
    # OnEnable carries a try/catch (EH clauses) -> skipped with a note
    assert any("OnEnable" in s and "exception" in s for s in skipped), skipped


@has_csc
def test_stubbed_bodies_are_valid_return_default(fixture_dll):
    data = open(fixture_dll, "rb").read()
    asm = mp.DotNetAssembly(data)
    patched, actions, _ = mp.patch_managed_dll(data)
    assert actions
    # method row -> owning type name (rows in one type's span)
    spans = list(mp._type_method_spans(asm))
    owner = {}
    for ns, tname, mlist, end in spans:
        for r in range(mlist, end + 1):
            owner[r] = tname
    stubbed = {(owner.get(m[0]), m[2]) for m in asm.method_rows()
               if (owner.get(m[0]), m[2]) in {(a["type"], a["method"]) for a in actions}}
    assert ("MosaicController", "Update") in stubbed
    for mrow, rva, mname, sig_i in asm.method_rows():
        key = (owner.get(mrow), mname)
        body = mp.method_body_info(asm, rva)
        if body is None:
            continue
        _off, code_off, size, _eh = body
        code = patched[code_off:code_off + size]
        if key in stubbed:
            assert code[-1] == 0x2A, f"{key}: must end in ret"
            assert all(b == 0x00 for b in code[:-1]), \
                f"{key}: leading bytes must be nops, got {code.hex()}"
        elif key == ("MosaicController", "GetStrength"):
            assert code == data[code_off:code_off + size], \
                "a non-magic method of a censored type keeps its body"


@has_csc
def test_return_default_il_covers_the_common_shapes():
    # void
    assert mp.return_default_il(bytes([0x00, 0x00, 0x01])) == b"\x2a"
    # i4 (param count 1) -> ldc.i4.0 ; ret
    assert mp.return_default_il(bytes([0x00, 0x01, 0x08])) == b"\x16\x00\x2a"
    # r4
    assert mp.return_default_il(bytes([0x00, 0x00, 0x0C])) == b"\x22\x00\x00\x00\x00\x2a"
    # string (0x0E)
    assert mp.return_default_il(bytes([0x00, 0x00, 0x0E])) == b"\x14\x2a"
    # valuetype (0x11 + token) and typedref (0x16): unsafe, refused
    assert mp.return_default_il(bytes([0x00, 0x00, 0x11, 0x01, 0x00, 0x00, 0x02])) is None
    assert mp.return_default_il(bytes([0x00, 0x00, 0x16])) is None


@has_csc
def test_patch_managed_code_writes_into_out_dir_only(fixture_dll, tmp_path):
    game = tmp_path / "game"
    managed = game / "Game_Data" / "Managed"
    managed.mkdir(parents=True)
    shutil.copy2(fixture_dll, managed / "Assembly-CSharp.dll")
    out = tmp_path / "patch"
    stats = mp.patch_managed_code(str(game), str(out), patch_mode=True)
    written = out / "Game_Data" / "Managed" / "Assembly-CSharp.dll"
    assert stats["code_methods"] >= 4
    assert written.is_file()
    original = open(fixture_dll, "rb").read()
    assert open(managed / "Assembly-CSharp.dll", "rb").read() == original, \
        "the staged original must stay untouched"
    assert len(open(written, "rb").read()) == len(original)
    # the patched DLL still parses as an assembly (metadata intact)
    mp.DotNetAssembly(open(written, "rb").read())


def test_patch_managed_code_il2cpp_reports_not_patchable(tmp_path):
    # an IL2CPP layout: GameAssembly.dll present, no Managed dir
    game = tmp_path / "game"
    data = game / "Game_Data"
    data.mkdir(parents=True)
    (game / "GameAssembly.dll").write_bytes(b"MZ" + b"\x00" * 200)
    stats = mp.patch_managed_code(str(game), str(tmp_path / "out"), patch_mode=True)
    assert stats["code_il2cpp"] is True
    assert stats["code_methods"] == 0
    assert any("IL2CPP" in line or "native" in line
               for line in stats["code_log"]), stats["code_log"]
