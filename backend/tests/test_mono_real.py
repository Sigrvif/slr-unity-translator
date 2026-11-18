"""Mono #US positive controls on REAL assemblies.

The synthetic blobs in test_mono.py never exercise the #US parser path (they
are not PE files), and that gap is exactly how a minutes-per-game hang plus
thousands of mojibake "literals" shipped unnoticed: the first real game was
IL2CPP (no Managed/), so only the synthetic path was ever covered. These
tests COMPILE a real DLL with the .NET Framework csc (present on every
Windows install; skips cleanly when absent) and push it through the full
scan.
"""
import os
import shutil
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from ut import discovery, mono

CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
has_csc = pytest.mark.skipif(not os.path.isfile(CSC), reason="csc not present")

JP_SOURCE = (
    "public class GameText {\n"
    "    public string Empty() { return \"\"; }\n"
    "    public string Title() { return \"タイトル画面\"; }\n"
    "    public string Load() { return \"セーブデータをロード中\"; }\n"
    "    public string Start() { return \"ゲームを開始\"; }\n"
    "}\n"
)


@pytest.fixture(scope="module")
def real_jp_dll(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("monoreal")
    src = tmp / "GameText.cs"
    src.write_text(JP_SOURCE, encoding="utf-8")
    dll = tmp / "Assembly-CSharp.dll"
    r = subprocess.run([CSC, "/nologo", "/target:library",
                        "/out:" + str(dll), str(src)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return str(dll)


@has_csc
def test_us_heap_parses_real_csc_assembly_exactly(real_jp_dll):
    """The #US parser must consume the heap to its exact end and recover the
    genuine literals (record = prefix counting string bytes + 1, then the
    payload, then one flag byte — verified byte-level on csc AND Mono
    output)."""
    from ut.monopatch import DotNetAssembly

    asm = DotNetAssembly(open(real_jp_dll, "rb").read())
    heap = asm.us_heap
    assert heap
    hits = mono.scan_us_heap(heap)
    assert set(hits) == {"タイトル画面", "セーブデータをロード中", "ゲームを開始"}


@has_csc
def test_scan_managed_dir_finds_real_jp_dll(real_jp_dll, tmp_path):
    managed = tmp_path / "Game_Data" / "Managed"
    managed.mkdir(parents=True)
    shutil.copy2(real_jp_dll, managed / "Assembly-CSharp.dll")
    report = mono.scan_managed_dir(str(tmp_path / "Game_Data"))
    assert len(report) == 1
    assert report[0]["dll"] == "Assembly-CSharp.dll"
    assert report[0]["count"] == 3
    assert set(report[0]["samples"]) == {"タイトル画面", "セーブデータをロード中",
                                        "ゲームを開始"}


@has_csc
def test_discover_mono_report_uses_us_path(real_jp_dll, tmp_path):
    """End-to-end: discover() reports the real literals through the #US path
    (and nothing leaks into translation targets)."""
    managed = tmp_path / "Game_Data" / "Managed"
    managed.mkdir(parents=True)
    shutil.copy2(real_jp_dll, managed / "Assembly-CSharp.dll")
    (tmp_path / "Game_Data" / "globalgamemanagers").write_bytes(b"x")
    rep = discovery.discover(str(tmp_path), scan_textures=False,
                             scan_fonts=False, scan_demosaic=False)
    assert rep.mono_report and rep.mono_report[0]["count"] == 3
    # v2.6: the literals are ALSO collected as translation targets
    mono_rows = [h for h in rep.strings if h.source == "mono"]
    assert {h.text for h in mono_rows} == set(rep.mono_report[0]["samples"])
