// Backend runner: executes slr_cli.py jobs via manifest/result JSON files
// (Contract v2, docs/CONTRACT.md — version-agnostic transport: this module
// passes the job body through verbatim EXCEPT path-valued keys, which are
// made absolute first — see PATH_KEYS —; only the callers pin the version).
//
// Contract recap driving this module:
//   <runtime-python> slr_cli.py --manifest job.json --result result.json
//   - All paths travel inside job.json (non-ASCII-safe; never argv).
//   - Exit code is always 0; outcomes live in result.json.
//   - stdout is EXACTLY zero or more progress lines, each one JSON object:
//     {"type":"progress","done":<int>,"total":<int>,"file":"<rel>"}.
//   - Result envelope: {"ok":true,"version":2,...payload} or
//     {"ok":false,"version":2,"error":"...","traceback":"..."}.
//
// Pure-lib rule: no NW.js / SLR globals here. The spawn function is injected
// (opts.spawnFn) or defaults to require("child_process").spawn, which works
// both in plain Node tests and inside the NW page (full Node access).
"use strict";

const fs = require("fs");
const path = require("path");

// {cmd, baseArgs:[slrCliPath]} for this OS's bundled runtime, or null when
// the payload is absent (runtime not fetched / wrong-OS payload).
function resolveRuntime(basePath) {
  basePath = path.resolve(basePath);   // tolerate relative basePaths (caller cwd)
  const cli = path.join(basePath, "backend", "slr_cli.py");
  if (process.platform === "win32") {
    const exe = path.join(basePath, "runtime", "python", "python.exe");
    return fs.existsSync(exe) ? { cmd: exe, baseArgs: [cli] } : null;
  }
  const exe = path.join(basePath, "runtime", "bin", "python3");
  return fs.existsSync(exe) ? { cmd: exe, baseArgs: [cli] } : null;
}

// Line-buffered progress parsing: split `chunk` on newlines across calls,
// JSON.parse each complete line, keep only progress objects, ignore noise.
// `state` is a caller-owned {buf: ""} that carries the partial tail.
function parseProgressLines(chunk, state) {
  state.buf += chunk.toString("utf-8");
  const out = [];
  let idx;
  while ((idx = state.buf.indexOf("\n")) >= 0) {
    const line = state.buf.slice(0, idx).trim();
    state.buf = state.buf.slice(idx + 1);
    if (!line) continue;
    try {
      const o = JSON.parse(line);
      if (o && o.type === "progress") out.push(o);
    } catch (e) {
      // non-JSON noise on stdout — backend logs its own stderr into backend.log
    }
  }
  return out;
}

let _jobCounter = 0;

// Manifest keys whose values are filesystem paths (union over the contract's
// commands: patch has all seven; scan carries game_dir; extract_textures
// carries game_dir + out_dir — both subsets of the list).
const PATH_KEYS = new Set([
  "game_dir", "out_dir", "translations_path", "index_path", "font_ttf",
  "textures_manifest_dir", "textures_dir",
  // e2e-fix19: font_bake role->TTF map keys travel nested inside the
  // font_bake object; resolveManifestPaths walks nested objects, so adding
  // the keys here is enough to keep them absolute wherever they appear.
  "latin_ttf", "cjk_ttf",
]);

// e2e-fix8 BUG 1 (user-reported backend FileNotFoundError on jobs\translations.json):
// SLR's proj.cache.cachePath is RELATIVE ("www/php/cache/<id>", against the
// app cwd — common.initStaging), so every manifest path the addon derives from
// it starts out relative too. runBackend already absolute-ifies jobsDir and the
// manifest/result argv (the child gets cwd = jobsDir), but the manifest CONTENT
// used to pass through verbatim — the backend then resolved a relative
// translations_path/out_dir/... against the jobs dir and died. Before the
// manifest is written, every PATH_KEYS value that is a non-empty string is
// made absolute with path.resolve(): a no-op on already-absolute paths, never
// touches disk, and everything else in the job (versions, flags, unknown keys)
// stays verbatim. Nested objects/arrays are walked so the resolution holds
// wherever a path key may appear.
function resolveManifestPaths(value, key) {
  if (Array.isArray(value)) return value.map((v) => resolveManifestPaths(v));
  if (value && typeof value === "object")
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveManifestPaths(v, k)]));
  if (key !== undefined && PATH_KEYS.has(key) && typeof value === "string" && value)
    return path.resolve(value);
  return value;
}

// runBackend(job, opts) -> Promise<result envelope>
//   job  = {version:2, command, ...} (contract manifest body)
//   opts = {jobsDir, python?: {cmd, baseArgs}, spawnFn?, onProgress?, basePath?}
// Backend failures (ok:false) RESOLVE with the full envelope; only harness
// errors (missing runtime payload, spawn failure, no result file) reject.
async function runBackend(job, opts) {
  const spawnFn = opts.spawnFn || require("child_process").spawn;
  const python = opts.python || resolveRuntime(opts.basePath || ".");
  if (!python) throw new Error("Unity backend runtime not found (payload missing for this OS)");
  // Absolute paths everywhere: callers may hand us a RELATIVE jobsDir (SLR's
  // common.initStaging returns "www/php/cache/<id>" relative to the app cwd),
  // and the child gets cwd=jobsDir — relative manifest/result args would then
  // resolve against the wrong base and the backend would die instantly.
  const jobsDir = path.resolve(opts.jobsDir);
  const logPath = path.join(jobsDir, "backend.log");
  fs.mkdirSync(jobsDir, { recursive: true });
  const id = `job-${Date.now()}-${++_jobCounter}`;
  const manifest = path.join(jobsDir, id + ".json");
  const result = path.join(jobsDir, id + ".result.json");
  fs.writeFileSync(manifest, JSON.stringify(resolveManifestPaths(job)), "utf-8");
  return await new Promise((resolve, reject) => {
    const child = spawnFn(python.cmd, [...python.baseArgs, "--manifest", manifest, "--result", result],
      { windowsHide: true, cwd: jobsDir });
    const state = { buf: "" };
    let errTail = "";   // last 2 KB of stderr, for diagnosing a child that
                        // died before slr_cli could write its own backend.log
    child.stdout.on("data", (d) => {
      for (const p of parseProgressLines(d, state)) opts.onProgress && opts.onProgress(p);
    });
    child.stderr.on("data", (d) => { errTail = (errTail + d.toString("utf-8")).slice(-2048); });
    child.on("error", reject);
    child.on("close", (code) => {
      try {
        const payload = JSON.parse(fs.readFileSync(result, "utf-8"));
        resolve(payload);
      } catch (e) {
        try {   // never leave the log pointer a dead end
          fs.appendFileSync(logPath,
            `\n[runner] backend exited (code ${code}) without writing ${result}\n[runner] stderr tail:\n${errTail}\n`);
        } catch (e2) { /* best effort */ }
        reject(new Error(
          `backend exited (code ${code}) without writing a result file; see ${logPath}` +
          (errTail ? `\nstderr: ${errTail.split("\n").slice(-3).join(" | ").slice(0, 400)}` : "")));
      }
    });
  });
}

module.exports = { runBackend, parseProgressLines, resolveRuntime };
