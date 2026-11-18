// Real-backend integration test (Task 7, brief Step 1).
//
// Runs `scan` against the REAL example game through lib/backend.runBackend
// with the SYSTEM python (`python` on PATH) and the real backend/slr_cli.py —
// no stubs. The test SKIPS unless every marker is present:
//   1. `python --version` spawns successfully and reports >= 3.10;
//   2. `python -c "import UnityPy"` exits 0;
//   3. the example game directory exists.
// Detection is spawn-based (nothing is imported into this process), and the
// skip decision is factored into pure functions so both the missing-python /
// missing-UnityPy / missing-game directions are unit-tested with faked
// markers — no python installs or removals needed. A manual E2E knob exists
// for the same purpose: SLRUNITY_INTEG_PYTHON=<bogus command> forces the
// spawn probe to fail, exercising the real skip path end to end.
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { spawnSync } = require("child_process");
const { runBackend } = require("../lib/backend");

const ROOT = path.resolve(__dirname, "..", "..");
const BACKEND_CLI = path.join(ROOT, "backend", "slr_cli.py");
// Local example game (skips cleanly when absent — set SLRUNITY_EXAMPLE_GAME).
const EXAMPLE_GAME = process.env.SLRUNITY_EXAMPLE_GAME || "";  // local dev convenience; live parts skip when unset

// The dialog string the scan must surface as a raw hit in level0 when the
// local example game is present (the synthetic fixture row at path_id 21334,
// Canvas/NewGameCheck/Scroll View/Text (TMP) mirrors that slot).
const DIALOG_TEXT = "新しくゲームを開始しますか？\n※セーブデータが消去されます";

// ---------------------------------------------------------------------------
// Gate: spawn-based marker detection + a PURE verdict function.
// ---------------------------------------------------------------------------

// "Python 3.10.6" -> [3, 10]; anything else -> null.
function parsePythonVersion(out) {
  const m = /Python (\d+)\.(\d+)/.exec(String(out || ""));
  return m ? [Number(m[1]), Number(m[2])] : null;
}

// Pure decision over probed markers (unit-tested with fakes below):
//   {version: [maj, min] | null, importOk: boolean, gameDir: string}
function verdictFromProbe(probe) {
  if (!probe.version) return { ok: false, reason: "system python not found (spawn failed or --version unparseable)" };
  if (probe.version[0] * 100 + probe.version[1] < 310)
    return { ok: false, reason: "system python " + probe.version.join(".") + " is older than 3.10" };
  if (!probe.importOk) return { ok: false, reason: "UnityPy is not importable by the system python (pip install UnityPy to enable this test)" };
  const st = probe.gameDir ? fs.statSync(probe.gameDir, { throwIfNoEntry: false }) : null;
  if (!st || !st.isDirectory())
    return { ok: false, reason: "example game not present: " + probe.gameDir };
  return { ok: true };
}

// Spawn both python markers for `cmd` (the real detection path).
function probeSystemPython(cmd, gameDir) {
  const ver = spawnSync(cmd, ["--version"], { encoding: "utf-8", windowsHide: true });
  const imp = spawnSync(cmd, ["-c", "import UnityPy"], { encoding: "utf-8", windowsHide: true });
  return {
    version: ver.status === 0 ? parsePythonVersion((ver.stdout || "") + (ver.stderr || "")) : null,
    importOk: imp.status === 0,
    gameDir,
  };
}

function fullGate(opts) {
  opts = opts || {};
  const cmd = opts.pythonCmd || process.env.SLRUNITY_INTEG_PYTHON || "python";
  const gameDir = opts.gameDir !== undefined ? opts.gameDir : EXAMPLE_GAME;
  return Object.assign({ pythonCmd: cmd }, verdictFromProbe(probeSystemPython(cmd, gameDir)));
}

// ---------------------------------------------------------------------------
// Unit tests for the skip logic — faked markers, nothing installed/removed.
// ---------------------------------------------------------------------------

test("verdictFromProbe: missing python marker -> skip", () => {
  const v = verdictFromProbe({ version: null, importOk: false, gameDir: EXAMPLE_GAME });
  assert.equal(v.ok, false);
  assert.match(v.reason, /python not found/);
});

test("verdictFromProbe: python older than 3.10 -> skip", () => {
  const v = verdictFromProbe({ version: [3, 9], importOk: true, gameDir: EXAMPLE_GAME });
  assert.equal(v.ok, false);
  assert.match(v.reason, /older than 3\.10/);
});

test("verdictFromProbe: python ok but UnityPy missing -> skip", () => {
  const v = verdictFromProbe({ version: [3, 10], importOk: false, gameDir: EXAMPLE_GAME });
  assert.equal(v.ok, false);
  assert.match(v.reason, /UnityPy/);
});

test("verdictFromProbe: python + UnityPy ok but game dir absent -> skip", () => {
  const v = verdictFromProbe({ version: [3, 11], importOk: true, gameDir: "X:/no/such/game" });
  assert.equal(v.ok, false);
  assert.match(v.reason, /example game not present/);
});

test("verdictFromProbe: all markers present -> ok", () => {
  assert.equal(verdictFromProbe({ version: [3, 10], importOk: true, gameDir: __dirname }).ok, true);
});

test("fullGate: spawn of a nonexistent interpreter yields the missing-python skip", () => {
  const v = fullGate({ pythonCmd: "slrunity-no-such-python-interpreter" });
  assert.equal(v.ok, false);
  assert.match(v.reason, /python not found/);
});

test("fullGate: with a working python, a missing game dir yields the game skip", () => {
  const v = fullGate({ gameDir: path.join(os.tmpdir(), "slrunity-no-such-game-dir") });
  assert.equal(v.ok, false);
  if (/python/.test(v.reason)) return; // box has no python at all — already skipping
  assert.match(v.reason, /example game not present/);
});

// ---------------------------------------------------------------------------
// The real thing. ~30-60 s with the example game present on this dev box.
// ---------------------------------------------------------------------------

test("integration: real backend scan over the example game (system python + UnityPy)", async (t) => {
  const g = fullGate();
  if (!g.ok) return t.skip("skipped: " + g.reason);

  const jobsDir = fs.mkdtempSync(path.join(os.tmpdir(), "slrunity-integ-"));
  let res;
  try {
    res = await runBackend(
      { version: 2, command: "scan", game_dir: EXAMPLE_GAME },
      { jobsDir, python: { cmd: g.pythonCmd, baseArgs: [BACKEND_CLI] } });
  } finally {
    fs.rmSync(jobsDir, { recursive: true, force: true });
  }

  assert.equal(res.ok, true, "scan envelope ok: " + (res.error || ""));
  assert.equal(res.version, 2, "backend speaks contract v2 (v1 manifests are rejected)");

  // The new-game dialog must be in the index with a RAW occurrence in level0.
  const occ = res.index && res.index[DIALOG_TEXT];
  assert.ok(Array.isArray(occ) && occ.length > 0, "dialog text present in index");
  assert.ok(
    occ.some((o) => o.source === "raw" && String(o.file || "").replace(/\\/g, "/").endsWith("/level0")),
    "dialog text has a raw occurrence in level0");

  assert.ok(Array.isArray(res.mono), "mono is an array (report-only Managed-DLL literals)");

  const stats = res.stats || {};
  assert.ok((stats.files_scanned || 0) > 0, "files_scanned > 0");
  assert.deepEqual(stats.errors || [], [], "stats.errors empty");
});
