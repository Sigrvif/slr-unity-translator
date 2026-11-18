import json
import os
import subprocess
import sys

CLI = "slr_cli.py"


def _run(manifest, tmp_path):
    m = tmp_path / "job.json"; m.write_text(json.dumps(manifest), encoding="utf-8")
    r = tmp_path / "result.json"
    p = subprocess.run([sys.executable, CLI, "--manifest", str(m), "--result", str(r)],
                       capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert p.returncode == 0, p.stderr
    return json.loads(r.read_text(encoding="utf-8"))

def test_bad_version(tmp_path):
    res = _run({"version": 99, "command": "scan", "game_dir": str(tmp_path)}, tmp_path)
    assert res["ok"] is False and "contract version" in res["error"]

def test_unknown_command(tmp_path):
    res = _run({"version": 2, "command": "nope", "game_dir": str(tmp_path)}, tmp_path)
    assert res["ok"] is False

def test_scan_empty_dir(tmp_path):
    res = _run({"version": 2, "command": "scan", "game_dir": str(tmp_path)}, tmp_path)
    assert res["ok"] is True and res["stats"]["files_scanned"] >= 0
    assert (tmp_path / "backend.log").exists()

def test_corrupt_manifest_still_writes_result(tmp_path):
    # envelope must never crash without writing result.json
    m = tmp_path / "job.json"; m.write_text("{not json", encoding="utf-8")
    r = tmp_path / "result.json"
    p = subprocess.run([sys.executable, CLI, "--manifest", str(m), "--result", str(r)],
                       capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert p.returncode == 0, p.stderr
    res = json.loads(r.read_text(encoding="utf-8"))
    assert res["ok"] is False and res["error"]

def test_progress_is_json_lines(tmp_path):
    # even when libraries print noise, every stdout line the addon sees must be JSON
    m = tmp_path / "job.json"; m.write_text(json.dumps(
        {"version": 2, "command": "scan", "game_dir": str(tmp_path)}), encoding="utf-8")
    r = tmp_path / "result.json"
    p = subprocess.run([sys.executable, CLI, "--manifest", str(m), "--result", str(r)],
                       capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert p.returncode == 0, p.stderr
    for line in p.stdout.splitlines():
        if line.strip():
            json.loads(line)  # raises on non-JSON noise

def test_scan_real_game(game_dir, has_game, tmp_path):
    res = _run({"version": 2, "command": "scan", "game_dir": game_dir}, tmp_path)
    assert res["ok"] is True
    sources = {s["source"] for s in res["strings"]}
    assert "typetree" in sources and "metadata" in sources

def test_patch_real_game(game_dir, has_game, tmp_path):
    scan = _run({"version": 2, "command": "scan", "game_dir": game_dir}, tmp_path)
    assert scan["ok"]
    jp = next(s["text"] for s in scan["strings"] if s["source"] == "metadata")
    (tmp_path / "trans.json").write_text(json.dumps({jp: "ENTEST"}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(scan["index"], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "gameout"
    res = _run({"version": 2, "command": "patch", "game_dir": game_dir, "out_dir": str(out),
                "translations_path": str(tmp_path / "trans.json"), "index_path": str(tmp_path / "index.json")}, tmp_path)
    assert res["ok"] is True and res["strings_written"] >= 1
