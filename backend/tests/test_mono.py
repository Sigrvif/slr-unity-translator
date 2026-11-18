"""I2: Mono Managed-DLL literal detection (spec §3.3 — report only).

Synthetic .dll-shaped blobs carrying (u8 length prefix + UTF-16LE payload)
records; the scanner must find the Japanese ones, ignore non-JP records,
and surface per-DLL counts + samples through discover() and the CLI scan
payload. It never produces translation targets.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from ut import discovery, mono

CLI = "slr_cli.py"


def _rec(s: str) -> bytes:
    """One #US-heap-like record: u8 char count + UTF-16LE payload."""
    b = s.encode("utf-16-le")
    return bytes([len(s)]) + b


def test_scan_finds_jp_records_and_ignores_rest():
    blob = (
        b"MZ\x90\x00\x03\x00\x00\x00"          # PE-ish header noise
        + _rec("こんにちは世界")                  # JP (kana+kanji run)
        + b"\x00\x10\x40\x00\xff\xff"
        + _rec("セーブデータをロード中")          # JP
        + _rec("no japanese here")              # plain ASCII record: ignored
        + b"\x01\x02\x03\x04"
        + _rec("指示をしてください")              # JP (kanji-only run)
    )
    hits = mono.scan_utf16_records(blob)
    assert "こんにちは世界" in hits
    assert "セーブデータをロード中" in hits
    assert "指示をしてください" in hits
    assert "no japanese here" not in hits


def test_scan_no_jp_buffer_is_cheap_none():
    # deterministic non-JP binaries: PE-ish header zeros, ASCII tables
    assert mono.scan_utf16_records(b"\x00" * 4096) == []
    assert mono.scan_utf16_records(b"Library foo bar \x00" * 100) == []
    assert mono.scan_utf16_records(bytes(range(256)) * 16) == []


def test_noise_short_kanji_records_rejected():
    # 2-3 char kanji-only decodes are exactly what random binary noise
    # produces; the kana-or->=4-JP rule must reject them while still
    # catching real kanji-only strings (研究施設).
    blob = b"MZ" + _rec("畚姄") + b"\x00" + _rec("畚姄嗗") + _rec("研究施設")
    hits = mono.scan_utf16_records(blob)
    assert hits == ["研究施設"]


def test_scan_managed_dir_reports_per_dll(tmp_path):
    managed = tmp_path / "Fake_Data" / "Managed"
    managed.mkdir(parents=True)
    (managed / "Assembly-CSharp.dll").write_bytes(
        b"MZ" + _rec("タイトル画面") + b"\x00\x00" + _rec("コンティニュー")
    )
    (managed / "UnityEngine.CoreModule.dll").write_bytes(
        b"MZ" + bytes(range(256)) * 8  # deterministic binary noise: no JP
    )
    sub = managed / "sub"
    sub.mkdir()
    (sub / "Plugin.dll").write_bytes(b"MZ" + _rec("プラグイン設定"))

    report = mono.scan_managed_dir(str(tmp_path / "Fake_Data"))
    by_dll = {r["dll"]: r for r in report}
    assert set(by_dll) == {"Assembly-CSharp.dll", "sub/Plugin.dll"}
    assert by_dll["Assembly-CSharp.dll"]["count"] == 2
    assert set(by_dll["Assembly-CSharp.dll"]["samples"]) == {"タイトル画面", "コンティニュー"}
    assert by_dll["sub/Plugin.dll"]["count"] == 1  # Managed subdirs scanned too


def test_samples_capped_at_50(tmp_path):
    managed = tmp_path / "Fake_Data" / "Managed"
    managed.mkdir(parents=True)
    blob = b"MZ"
    for i in range(55):
        blob += _rec(f"設定項目番号{i:03d}")
    (managed / "Big.dll").write_bytes(blob)
    report = mono.scan_managed_dir(str(tmp_path / "Fake_Data"))
    assert report[0]["count"] == 55
    assert len(report[0]["samples"]) == 50


def test_discover_wires_mono_report(tmp_path):
    managed = tmp_path / "Fake_Data" / "Managed"
    managed.mkdir(parents=True)
    (managed / "Assembly-CSharp.dll").write_bytes(b"MZ" + _rec("ゲームを開始"))
    rep = discovery.discover(str(tmp_path), scan_textures=False, scan_fonts=False)
    assert rep.mono_report == [
        {"dll": "Assembly-CSharp.dll", "count": 1, "samples": ["ゲームを開始"]}
    ]
    # report-only: none of it leaks into translation targets
    assert all(h.source != "mono" for h in rep.strings)


def test_cli_scan_payload_carries_mono(tmp_path):
    managed = tmp_path / "Fake_Data" / "Managed"
    managed.mkdir(parents=True)
    (managed / "Assembly-CSharp.dll").write_bytes(b"MZ" + _rec("ゲームを開始"))
    m = tmp_path / "job.json"
    m.write_text(json.dumps(
        {"version": 2, "command": "scan", "game_dir": str(tmp_path)}), encoding="utf-8")
    r = tmp_path / "result.json"
    p = subprocess.run(
        [sys.executable, CLI, "--manifest", str(m), "--result", str(r)],
        capture_output=True, text=True,
        cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
    )
    assert p.returncode == 0, p.stderr
    res = json.loads(r.read_text(encoding="utf-8"))
    assert res["ok"] is True
    assert res["mono"][0]["dll"] == "Assembly-CSharp.dll"
    assert res["mono"][0]["samples"] == ["ゲームを開始"]
    # I3: errors travel as the full list, not a count
    assert isinstance(res["stats"]["errors"], list)


# --------------------------------------------------------------------------- #
