// Loader-eval simulation tests for UnityAddon.js (Task 5 + the Task 6 zip
// fix + final-review I1 + e2e-fix1): exportHandler orchestration +
// exportOptionFields shape + the Task 3 deferred try/finally fix (hideProgress
// must run even when runBackend rejects) + the carried Task 5 finding: zip
// mode must archive the staging folder's CONTENTS at the archive root via
// common.aSpawn (cwd + bare "*", rmmv.js:3046-3075) with the exit code read
// from onDone(result, code), and must not claim success on a non-zero exit;
// common.pack stays as the aSpawn/7z-unavailable fallback only; and I1: a
// VERIFIED zip success must rmSync the zip-out-* staging dir (SLR's .slrproj
// save packs the whole staging cache, jobs/ included — trans-full.js:1649-
// 1652), while every failure path keeps it (the alerts point there).
// e2e-fix1 adds: the New Project dialog menu entry (addMenu at 3 + the
// select-folder -> ui.startProject flow), the createProject game-root
// resolver (parent picks resolve to the Unity child; bad picks alert+throw),
// and the host-app font (common.getRootDir()/www/core/SEP/SLRCondensedCJK,
// staged as a fake SLR root below; missing => alert + font stage skipped).
// e2e-fix3 adds: progress routing into the loading overlay's console
// (ui.loadingProgress with {consoleOnly:false, mode:"consoleOutput"} when
// $("#loadingOverlay") matches; bottom-right bubble otherwise), the
// scan-complete note before buildProject, and the SEP-texture + first-run
// help asks as in-DOM modals (z-index 100001, two buttons, removed on click)
// instead of native confirm()s that sat under the overlay.
// e2e-fix4 adds: the night-mode modal palette (body.nightMode -> SLR night
// colors #262626/#3d4a5c/#eee incl. button styling; absent -> the palette the
// modal always shipped), the overlay Close-button contract for texture
// extraction (holdCloseButton BEFORE the spawn when the overlay is up,
// .loadingStatus driven with "Unity: extracting textures d/t name" /
// "Unity: textures extracted", releaseCloseButton after done, "Unity
// textures" phase title in the router; overlay absent -> neither hold nor
// release and the bubble fallback), and the persistent Tools-menu help entry
// (ui.mainMenu.addChild("tools") + select -> the shared openHelp).
// e2e-fix5 adds: the FLASHBANG fix (SLR night mode inverts the whole page via
// html{filter:invert(92%)} — the dark-authored modal container now carries
// SLR's own #loadingOverlay precedent counter-inversion filter:invert(100%
// in night mode; light world has NO filter), the removal of the DEAD
// Tools-menu registration (no #mainMenu element exists in any SLR html —
// addChild appends into an empty jQuery set and silently no-ops) replaced by
// a visible "Open documentation" link on the addon's own New Project slide,
// and openHelp preferring the v3.078 offline documentation system
// (docsHelp.open("addon-SLRUnity"), the addonDocs "addon-<folder>"
// id scheme) with assets/help.html kept as the fallback.
// e2e-fix6 adds: the image (SEP) workflow DECOUPLED from the strings
// project. The slide now wires THREE actions (strings project / standalone
// extract textures / standalone insert translated images); the standalone
// runners drive the loading-overlay lifecycle themselves (ui.showLoading()
// once, ui.loadingProgress lines, ui.loadingEnd() EXACTLY ONCE, from the
// finally block — backend rejection included) and use a temp jobs dir under
// os.tmpdir(); insert guards on unity_img_manifest.json BEFORE any backend
// call and builds a patch manifest with temp EMPTY {} translations/index
// plus the texture dirs (font_ttf only when the slide checkbox is checked);
// the post-create askTextures modal + its setTimeout are GONE; export lost
// unitySepRenderDir + the options.unity.imgDir read (strings + font only).
// e2e-fix7 adds: contract v2 everywhere (every backend job sends version:2 —
// the backend now REJECTS v1 manifests), STAGING AT CREATION (createProject
// copies every index-referenced file into <cache>/data/game/<rel>, rewrites
// the index abs paths to the staged copies, keeps `file` verbatim, stores
// options.unity.stagedGameDir, and writes index.json from the REWRITTEN
// index — the .slrproj packs data/ so the project is self-contained), and
// the export rework: a unityExportKind select (enum + titleMap, the SLR
// conventions from CustomParserLoader.js:444-496 + options.js:905-917, form
// keys laid out explicitly because jsonform's "*" does not dedup), patch
// mode as the default (patch_mode:true + game_dir=stagedGameDir +
// exported_files reporting + a "copy over the game folder" summary line),
// revert support (a file selection makes selected-but-unexported files get
// their staged ORIGINALS copied verbatim into the patch and lifts the
// nothing-to-export guard), and full-copy mode (v1 behavior against the
// original gameDir; missing original -> clear alert, no backend call).
//
// UnityAddon.js is not a CommonJS module: www/js/addon.js evals its body as
// an AsyncFunction with `this` = the Addon instance and the NW page globals
// (engines, Engines, common, trans, ui, document, alert, confirm, require) in
// scope. These tests reproduce that contract directly:
//   - the REAL file bytes are compiled into an AsyncFunction;
//   - page globals are recorded stubs (engines handler registry, common.aSpawn
//     / common.pack / common.getRootDir, alert queue, fake document for the
//     progress overlay, ui.newProjectDialog + ui.startProject recorders);
//   - lib/backend.js is swapped for a stub in a sandbox COPY of the addon
//     (plus runtime-payload marker files) so runBackend is deterministic.
// The real backend runner has its own suite in backend.test.js.
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs");

const ADDON_DIR = path.join(__dirname, "..");
const BODY = fs.readFileSync(path.join(ADDON_DIR, "UnityAddon.js"), "utf-8");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// ---------------------------------------------------------------------------
// Sandbox: copy of the addon with lib/backend.js replaced by a stub whose
// behavior is driven through globalThis hooks (reset per scenario).
// ---------------------------------------------------------------------------
const SANDBOX = fs.mkdtempSync(path.join(os.tmpdir(), "slrunity-addon-"));

fs.mkdirSync(path.join(SANDBOX, "lib"), { recursive: true });
for (const f of fs.readdirSync(path.join(ADDON_DIR, "lib")))
  if (f !== "backend.js") fs.copyFileSync(path.join(ADDON_DIR, "lib", f), path.join(SANDBOX, "lib", f));

// Stub backend.js: same exported surface (runBackend, resolveRuntime) as the
// real module; resolveRuntime is copied verbatim so the payload gating in
// UnityAddon.js is exercised for real.
fs.writeFileSync(path.join(SANDBOX, "lib", "backend.js"), `"use strict";
const path = require("path");
const fs = require("fs");
function resolveRuntime(basePath) {
  const cli = path.join(basePath, "backend", "slr_cli.py");
  if (process.platform === "win32") {
    const exe = path.join(basePath, "runtime", "python", "python.exe");
    return fs.existsSync(exe) ? { cmd: exe, baseArgs: [cli] } : null;
  }
  const exe = path.join(basePath, "runtime", "bin", "python3");
  return fs.existsSync(exe) ? { cmd: exe, baseArgs: [cli] } : null;
}
async function runBackend(job, opts) {
  globalThis.__unityBackendCalls.push({ job, jobsDir: opts.jobsDir, python: opts.python, hasProgress: typeof opts.onProgress === "function" });
  // e2e-fix4: ordering evidence — hold/release vs backend spawn/done.
  (globalThis.__unitySeq = globalThis.__unitySeq || []).push("backend:" + job.command);
  if (opts.onProgress) opts.onProgress({ done: 1, total: 2, file: "level0" }); // show the overlay once
  const next = globalThis.__unityBackendNext;
  if (next && next.reject) throw new Error(next.reject);
  if (typeof next === "function") return next(job, opts);
  if (next) return next;
  return { ok: true, version: 2 };
}
module.exports = { runBackend, resolveRuntime };
`);

// Runtime payload markers (Task 6 fetches the real payload; resolveRuntime
// only checks existence, so empty files gate the addon on).
function ensurePayload() {
  for (const p of ["backend/slr_cli.py", "runtime/python/python.exe", "runtime/bin/python3"]) {
    const f = path.join(SANDBOX, p);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    if (!fs.existsSync(f)) fs.writeFileSync(f, "");
  }
}

// ---------------------------------------------------------------------------
// Page-global stubs mirroring the NW surface the addon body expects.
// ---------------------------------------------------------------------------
function makePage(overrides) {
  const page = {
    reg: { engines: {}, handlers: {}, props: {}, detectors: [] },
    alerts: [],
    engines: null, Engines: null, common: null, trans: { project: null },
    ui: null, document: null, alert: null, confirm: () => false,
    ...overrides,
  };
  page.engines = page.engines || {
    add: (name, meta) => { page.reg.engines[name] = meta; },
    addHandler: (names, event, fn) => {
      for (const n of [].concat(names)) (page.reg.handlers[n] = page.reg.handlers[n] || {})[event] = fn;
    },
    getEngine: (name) => ({ addProperty: (prop, fn) => { (page.reg.props[name] = page.reg.props[name] || {})[prop] = fn; } }),
  };
  page.Engines = page.Engines || { registerDetector: (fn) => page.reg.detectors.push(fn) };
  // ui: the New Project dialog recorders (e2e-fix1). addMenu mirrors
  // ui.js:5782 (queues when uninitialized — here it just records); close is
  // async like the jQuery-UI-backed original.
  page.ui = page.ui || {
    menus: [], dialogClosed: 0, startProjectCalls: [],
    // e2e-fix3: ui.loadingProgress recorder (www/js/ui.js:1517+). On default
    // pages nothing routes here — the router also requires a jQuery whose
    // $("#loadingOverlay") matches — so existing bubble tests are unaffected.
    loadingCalls: [],
    // e2e-fix6: the standalone overlay-lifecycle recorders — the runners
    // call showLoading() once up front and loadingEnd() exactly once from a
    // finally block (the createProjectFromEngine pattern, trans-full.js:641-
    // 683), NOT hold/release (those left with the old extractTextures).
    // e2e-fix9: every ui.* overlay call also writes into the __unitySeq
    // ordering log, so tests can assert alerts never fire while the overlay
    // is up (the export overlay contract).
    showLoadingCalls: 0, loadingEndCalls: 0,
    showLoading: function () {
      this.showLoadingCalls++;
      (globalThis.__unitySeq = globalThis.__unitySeq || []).push("ui:showLoading");
    },
    loadingEnd: function () {
      this.loadingEndCalls++;
      (globalThis.__unitySeq = globalThis.__unitySeq || []).push("ui:loadingEnd");
    },
    newProjectDialog: {
      addMenu: (opts) => { page.ui.menus.push(opts); },
      close: async () => { page.ui.dialogClosed++; },
    },
    startProject: (file) => { page.ui.startProjectCalls.push(file); },
    loadingProgress: function (title, text, opts) {
      this.loadingCalls.push({ title, text, opts });
      (globalThis.__unitySeq = globalThis.__unitySeq || []).push("ui:progress:" + title);
    },
    // e2e-fix10: ui.log recorder (www/js/ui.js:1601 — appends into the
    // overlay console). The export's post-backend lines (revert phase,
    // warnings, summary) prefer it while the overlay is visible; like the
    // real one it is async with a synchronous append, so ordering holds
    // without awaiting. Also lands in the __unitySeq ordering log.
    logCalls: [],
    log: async function (msg) {
      const line = String(msg);
      this.logCalls.push(line);
      (globalThis.__unitySeq = globalThis.__unitySeq || []).push("ui:log:" + line.split("\n")[0]);
    },
  };
  page.common = page.common || {
    packCalls: [],
    pack: async function (from, to) { this.packCalls.push({ from, to }); return to; },
    // e2e-fix1: the host-app root the font is resolved from
    // (www/js/common.js:3024). Overridable per scenario via __unityRootDir.
    getRootDir: function () { return globalThis.__unityRootDir || "C:/fake-slr"; },
    // aSpawn stub mirroring www/js/common.js:2133-2191: resolves with the
    // output buffer on close (even on non-zero exit — the exit code reaches
    // the caller ONLY through onDone(result, code)), rejects on spawn errors.
    // Per-call exit codes come from globalThis.__unityZipExitPlan (consumed
    // left to right; default 0), stderr text from __unityZipStderr, and a
    // forced rejection from __unityZipSpawnErr.
    aSpawnCalls: [],
    aSpawn: async function (bin, args, options) {
      this.aSpawnCalls.push({ bin, args: args.slice(), cwd: options && options.cwd });
      if (globalThis.__unityZipSpawnErr) throw new Error(globalThis.__unityZipSpawnErr);
      const plan = globalThis.__unityZipExitPlan || [];
      const code = plan.length ? plan.shift() : 0;
      if (code !== 0 && globalThis.__unityZipStderr && options && typeof options.onError === "function")
        options.onError(globalThis.__unityZipStderr);
      if (options && typeof options.onDone === "function") options.onDone("", code);
      // The real 7z creates the archive as a side effect; so does the stub
      // (size controllable via __unityZipBytes, default 1 KiB > the 22-byte
      // empty-zip threshold the addon checks).
      if (code === 0 && args[0] === "a") {
        try { fs.writeFileSync(args[2], Buffer.alloc(globalThis.__unityZipBytes || 1024, 0x61)); } catch (e) {}
      }
      return "";
    },
    get7zipBin: function () { return "C:/fake-slr/node_modules/7zip-bin/win/x64/7za.exe"; },
    halt: function () { return { halt: true }; },
  };
  // e2e-fix9: alerts land in the ordering log too — "no alert while the
  // overlay is up" is asserted as loadingEnd preceding every alert entry.
  page.alert = page.alert || ((m) => {
    page.alerts.push(m);
    (globalThis.__unitySeq = globalThis.__unitySeq || [])
      .push("alert:" + String(m).split("\n")[0].slice(0, 50));
  });
  const els = {};
  let elSerial = 0;
  // Fake DOM (e2e-fix3): enough surface for the progress bubble AND the
  // unityAsk modal — appendChild/parentNode wiring, click handlers, removal,
  // and a textContent HISTORY (`texts`) so tests can assert lines that were
  // shown and later overwritten (the "Extracting textures to ..." note).
  function fakeRemoveEl(el) {
    if (el && el.parentNode && typeof el.parentNode.removeChild === "function") el.parentNode.removeChild(el);
    else if (el && typeof el.remove === "function") el.remove();
  }
  page._fakeRemoveEl = fakeRemoveEl;
  page.document = page.document || {
    getElementById: (id) => els[id] || null,
    createElement: (tag) => {
      const el = {
        tag: String(tag), id: "", style: {}, children: [], handlers: {},
        _serial: ++elSerial, texts: [],
        appendChild(child) { el.children.push(child); child.parentNode = el; return child; },
        addEventListener(ev, fn) { (el.handlers[ev] = el.handlers[ev] || []).push(fn); },
        click() { (el.handlers.click || []).slice().forEach((fn) => fn.call(el, {})); },
        remove() { fakeRemoveEl(el); },
      };
      Object.defineProperty(el, "textContent", {
        get() { return el.texts.length ? el.texts[el.texts.length - 1] : ""; },
        set(v) { el.texts.push(String(v)); },
        configurable: true,
      });
      return el;
    },
    body: {
      children: [],
      appendChild(el) { this.children.push(el); el.parentNode = this; if (el.id) els[el.id] = el; return el; },
      removeChild(el) {
        const i = this.children.indexOf(el);
        if (i !== -1) this.children.splice(i, 1);
        if (el.id && els[el.id] === el) delete els[el.id];
        el.parentNode = null;
        return el;
      },
    },
  };
  page._els = els;
  return page;
}

// Traversal helpers for the fake DOM (e2e-fix3 modal assertions).
function walkEl(el, fn) {
  if (!el) return;
  fn(el);
  (el.children || []).forEach((c) => walkEl(c, fn));
}
function modalText(root) {
  let t = "";
  walkEl(root, (el) => { if (el.texts && el.texts.length) t += el.texts[el.texts.length - 1] + "\n"; });
  return t;
}
function modalButtons(root) {
  const b = [];
  walkEl(root, (el) => { if (el.tag === "button") b.push(el); });
  return b; // DOM order: [negative, affirmative] — unityAsk appends no, then yes
}

// Eval the real UnityAddon.js body exactly the way www/js/addon.js does.
// `inst` overrides the Addon instance (`this`) — used to give the body a
// config/setConfig pair for the Task 7 help-opener tests, mirroring
// www/js/addon.js:380-383 (this.config = sys.config.addons.package[...]) and
// addon.js:51-54 (setConfig persists via sys.saveConfig()); the dialog tests
// add package.version + getWebLocation (addon.js:166).
async function loadAddon(page, inst) {
  const fn = new AsyncFunction(
    "engines", "Engines", "common", "trans", "ui", "document", "alert", "confirm", "require", "console", "nw", "window", "docsHelp",
    BODY);
  await fn.call(inst || { fullPath: SANDBOX, basePath: "C:/addons" },
    page.engines, page.Engines, page.common, page.trans, page.ui, page.document, page.alert, page.confirm, require, console,
    page.nw, page.window, page.docsHelp);
  return page;
}

function freshScenario(behavior) {
  globalThis.__unityBackendCalls = [];
  globalThis.__unityBackendNext = behavior || null;
  globalThis.__unityZipExitPlan = null;
  globalThis.__unityZipStderr = null;
  globalThis.__unityZipSpawnErr = null;
  globalThis.__unitySeq = []; // e2e-fix4 ordering log (ui:hold/release vs backend:<cmd>)
  globalThis.__unityRootDir = SLR_ROOT; // fake host-app root (font present)
  const page = makePage();
  return page;
}

// Fake SLR installation root: the export font stage resolves
// <getRootDir()>/www/core/SEP/SLRCondensedCJK-Regular.ttf (e2e-fix1) against
// the REAL fs (fsh.isFile in UnityAddon.js), so the file must actually exist.
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "slrunity-export-"));
const SLR_ROOT = path.join(TMP, "slr-root");
const SLR_FONT = path.join(SLR_ROOT, "www", "core", "SEP", "SLRCondensedCJK-Regular.ttf");
fs.mkdirSync(path.dirname(SLR_FONT), { recursive: true });
fs.writeFileSync(SLR_FONT, "fake-slr-condensed-cjk-ttf");
// e2e-fix19: the Latin bake face (<SLR>/fonts/sucr.ttf, Stint Ultra
// Condensed) lives in the same fake root — the export's font_bake map
// resolves it against the REAL fs too.
const SLR_SUCR = path.join(SLR_ROOT, "fonts", "sucr.ttf");
fs.mkdirSync(path.dirname(SLR_SUCR), { recursive: true });
fs.writeFileSync(SLR_SUCR, "fake-slr-stint-ultra-condensed-ttf");

// Fake Unity game on the real fs for the createProject resolver: a folder
// with a <Name>_Data/globalgamemanagers marker (what isDirectUnity checks).
function makeFakeGame(name) {
  const root = path.join(TMP, name);
  fs.mkdirSync(path.join(root, "Fake_Data"), { recursive: true });
  fs.writeFileSync(path.join(root, "Fake_Data", "globalgamemanagers"), "x");
  return root;
}
const FAKE_GAME = makeFakeGame("fake-game");

// Minimal project in the shape projectBuilder.buildProject produces: data
// rows are [jp, translation]; options.unity carries gameDir (reference, only
// the full-copy export reads it) + stagedGameDir (e2e-fix7: the
// self-contained copy the patch export reads from).
function makeProject(cachePath, extraUnity) {
  return {
    gameEngine: "unity", parser: "unity",
    cache: { cacheID: "cid", cachePath },
    files: {
      "FakeGame_Data/level0": { data: [["こんにちは", "Hello"], ["さようなら", ""], ["はい", "  Yes  "]] },
      "FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat": { data: [["メニュー", "Menu"]] },
    },
    options: { unity: Object.assign(
      { contract: 2, gameDir: "E:/Games/FakeGame",
        stagedGameDir: path.join(cachePath, "data", "game") }, extraUnity || {}) },
  };
}

function stageProject(tmp, extraUnity) {
  const cachePath = path.join(tmp, "cache");
  fs.mkdirSync(path.join(cachePath, "data"), { recursive: true });
  fs.writeFileSync(path.join(cachePath, "data", "index.json"), "{}", "utf-8");
  const proj = makeProject(cachePath, extraUnity);
  // e2e-fix7: the staged game tree the patch export reads from — one file
  // per project key (the keys ARE the game-rel paths), distinct bytes so
  // verbatim revert copies are provable against backend output.
  const staged = path.join(cachePath, "data", "game");
  for (const key of Object.keys(proj.files)) {
    const p = path.join(staged, key);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, "STAGED-ORIGINAL:" + key);
  }
  const page = freshScenario();
  page.trans.project = proj;
  return { page, proj, cachePath, staged };
}

// Spy on fs.rmSync for the staging-cleanup assertions (final-review I1). The
// addon body requires the SAME fs singleton as this test file, so swapping
// the module property is visible to the eval'd code. Always delegates to the
// real implementation (rmSync with force:true is a no-op on the nonexistent
// staging dirs these tests use); re-calling replaces any earlier wrapper —
// no nesting, stale recorders go out of scope.
const REAL_RM = fs.rmSync;
function spyRmSync() {
  const calls = [];
  fs.rmSync = function (p, opts) { calls.push({ p, opts }); return REAL_RM.call(fs, p, opts); };
  return calls;
}

// e2e-fix10: the export's post-backend console lines (revert phase, warnings,
// the summary) land in ui.logCalls when the overlay is up (the ui.log channel
// the addon prefers), in the bottom-right bubble's text history otherwise
// (the uiLogLine router's fallback). Either way each line is recorded
// verbatim — this is what the summary assertions read.
function exportConsoleLines(page) {
  const lines = (page.ui.logCalls || []).slice();
  const bub = page._els["slr-unity-progress"];
  if (bub && bub.texts) for (const t of bub.texts) lines.push(String(t));
  return lines;
}

// ---------------------------------------------------------------------------

test("addon stays silent when the runtime payload is absent", async () => {
  const page = freshScenario();
  await loadAddon(page); // SANDBOX has lib/ but no payload markers yet (unless a
  // later ensurePayload() ran first — recreate the pristine state to be sure)
  if (page.reg.handlers.unity || page.reg.detectors.length || page.reg.props.unity) {
    assert.ok(false, "no handler/detector/property may register without the payload");
  }
});

test("registers unity engine, detector, createProject + exportHandler, and exportOptionFields", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  assert.equal(page.reg.engines.unity.title, "Unity (IL2CPP / Mono)");
  assert.equal(page.reg.detectors.length, 1);
  assert.equal(typeof page.reg.handlers.unity.createProject, "function");
  assert.equal(typeof page.reg.handlers.unity.exportHandler, "function");
  assert.equal(typeof page.reg.props.unity.exportOptionFields, "function");
});

test("exportOptionFields: enum select + checkbox, CustomParserLoader/options.js conventions, explicit form keys", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  const fields = page.reg.props.unity.exportOptionFields();
  // Real shape (CustomParserLoader.js:444-496): { jsonForm: { schema, form } } —
  // NOT the brief schematic's flat { name: {type:"checkbox", label} } map.
  assert.ok(fields.jsonForm && fields.jsonForm.schema && Array.isArray(fields.jsonForm.form));
  const schema = fields.jsonForm.schema;
  assert.equal(schema.unitySwapFonts.type, "boolean"); // jsonform renders boolean as a checkbox
  assert.equal(typeof schema.unitySwapFonts.title, "string");
  // e2e-fix7: the export-kind select mirrors the customParser enum convention
  // (writeEncoding: "type":"string" + "enum" -> jsonform renders a <select>),
  // with the human labels carried by a form-element titleMap exactly the way
  // SLR's own options layer builds them (options.js:905-917).
  const kind = schema.unityExportKind;
  assert.equal(kind.type, "string");
  assert.deepEqual(kind.enum, ["patch", "full"]);
  assert.equal(kind.default, "patch", "patch is the default export kind");
  assert.equal(schema.unitySepRenderDir, undefined);
  // v2.3 custom fonts: two optional string fields (absolute .ttf/.otf paths,
  // empty = SLR defaults) joined the export kind + swap checkbox.
  assert.equal(schema.unityFontCjk.type, "string");
  assert.equal(schema.unityFontLatin.type, "string");
  assert.equal(schema.unityFontCjk.default, "");
  // v2.5 demosaic: the opt-in checkbox (default OFF — the user must ask for
  // it, exactly like the font swap) + its detection-level select.
  assert.equal(schema.unityDemosaic.type, "boolean");
  assert.equal(schema.unityDemosaic.default, false);
  const dlevel = schema.unityDemosaicLevel;
  assert.equal(dlevel.type, "string");
  assert.deepEqual(dlevel.enum, ["high", "medium"]);
  assert.equal(dlevel.default, "high");
  // v2.5b tiers: reveal (ShaderReplace/MaterialReplace behavior) and the
  // deep code opt-in (DumbTypeDemosaic equivalent) — both default OFF.
  assert.equal(schema.unityDemosaicReveal.type, "boolean");
  assert.equal(schema.unityDemosaicReveal.default, false);
  assert.equal(schema.unityDemosaicCode.type, "boolean");
  assert.equal(schema.unityDemosaicCode.default, false);
  // v2.6 text-fit: the sliding/overflowing bubble fix joins as a checkbox +
  // v2.6j: its width calibration as a RATIO of each site's own constant.
  assert.equal(schema.unityTextFit.type, "boolean");
  assert.equal(schema.unityTextFit.default, false);
  assert.equal(schema.unityTextFitRatio.type, "string");
  assert.equal(schema.unityTextFitRatio.default, "0.5");
  // v2.6g: Latin advance scale for the font swap (auto-sized bubble fix)
  assert.equal(schema.unityFontLatinScale.type, "string");
  assert.equal(schema.unityFontLatinScale.default, "1");
  assert.equal(Object.keys(schema).length, 11,
    "export kind + font swap + 2 font slots + demosaic + level + reveal + "
    + "code + textfit + ratio + latin scale");
  const form = fields.jsonForm.form;
  const kindEntry = form.find((f) => f && typeof f === "object" && f.key === "unityExportKind");
  assert.ok(kindEntry, "explicit form entry carries the select's titleMap");
  assert.deepEqual(kindEntry.titleMap,
    { patch: "patch (changed/selected files only)", full: "full game copy" });
  assert.ok(form.some((f) => f && typeof f === "object" && f.key === "unitySwapFonts"),
    "font checkbox laid out explicitly too");
  const dlevelEntry = form.find((f) => f && typeof f === "object" && f.key === "unityDemosaicLevel");
  assert.ok(dlevelEntry && dlevelEntry.titleMap &&
    dlevelEntry.titleMap.high && dlevelEntry.titleMap.medium,
    "level select carries human labels via titleMap");
  // jsonform's "*" wildcard expands over EVERY schema property without
  // skipping keys already laid out (jsonform.js buildTree) — using it here
  // would render the select TWICE (duplicate name attr in the FormData), so
  // the form must not carry a wildcard.
  assert.equal(form.some((f) => f === "*"), false, "no '*' wildcard alongside explicit keys");
  assert.ok(form.some((f) => f && typeof f === "object" && f.type === "msg"), "header message kept");
});

test("v2.6o: export options remember the last export (localStorage -> schema defaults)", async () => {
  ensurePayload();
  const page = freshScenario();
  // The addon reads window.localStorage lazily at call time; the fake store
  // is a Map with the localStorage surface. jQuery is included because the
  // body's $jq fallback captures window.jQuery at load.
  const store = new Map();
  const fakeStore = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
  };
  page.window = { jQuery: fakeJQuery(), localStorage: fakeStore };
  await loadAddon(page);
  const props = page.reg.props.unity;

  // nothing saved yet: every built-in default stands
  let fields = props.exportOptionFields();
  assert.equal(fields.jsonForm.schema.unityExportKind.default, "patch");
  assert.equal(fields.jsonForm.schema.unitySwapFonts.default, false);
  assert.equal(fields.jsonForm.schema.unityTextFitRatio.default, "0.5");
  assert.equal(fields.jsonForm.schema.unityDemosaic.default, false);

  // a real export run (patch, all-originals baseline — the cheapest full
  // handler run) saves the FormData-shaped selection at export start ...
  const { proj } = stageProject(TMP + "-p-mem");
  page.trans.project = proj; // the addon closure reads THIS page's trans
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 0, exported_files: [] };
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-mem"), {
    mode: "dir",
    custom: { unityExportKind: "patch", unitySwapFonts: "on", unityTextFit: "on",
              unityTextFitRatio: "0.3", unityDemosaicLevel: "medium" },
  });
  assert.ok(out && out.halt === true, "the export ran");
  const saved = JSON.parse(store.get("slrUnityExportOptions.v1"));
  // checkboxes normalize "on"/absent into real booleans (schema type), the
  // rest stay strings; every schema key is carried
  assert.equal(saved.unitySwapFonts, true);
  assert.equal(saved.unityTextFit, true);
  assert.equal(saved.unityDemosaic, false, "absent checkbox -> false");
  assert.equal(saved.unityDemosaicReveal, false);
  assert.equal(saved.unityDemosaicCode, false);
  assert.equal(saved.unityTextFitRatio, "0.3");
  assert.equal(saved.unityDemosaicLevel, "medium");
  assert.equal(saved.unityFontCjk, "", "empty string fields are carried");
  // ... and the NEXT dialog open seeds the schema defaults from it
  fields = props.exportOptionFields();
  assert.equal(fields.jsonForm.schema.unitySwapFonts.default, true);
  assert.equal(fields.jsonForm.schema.unityTextFit.default, true);
  assert.equal(fields.jsonForm.schema.unityDemosaic.default, false);
  assert.equal(fields.jsonForm.schema.unityTextFitRatio.default, "0.3");
  assert.equal(fields.jsonForm.schema.unityDemosaicLevel.default, "medium");
  assert.equal(fields.jsonForm.schema.unityExportKind.default, "patch");
  assert.equal(fields.jsonForm.schema.unityFontCjk.default, "");
  // schema types/shape untouched by the seeding (fresh clones per call)
  assert.equal(fields.jsonForm.schema.unitySwapFonts.type, "boolean");
  assert.equal(Object.keys(fields.jsonForm.schema).length, 11);
  assert.notEqual(fields.jsonForm.schema, props.exportOptionFields().jsonForm.schema,
    "each dialog open gets its own schema object (jsonform may mutate)");

  // unchecking is remembered too: an export with everything off reverts the
  // stored booleans and the next dialog opens with the built-in look
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-mem2"), {
    mode: "dir", custom: { unityExportKind: "patch" },
  });
  fields = props.exportOptionFields();
  assert.equal(fields.jsonForm.schema.unitySwapFonts.default, false);
  assert.equal(fields.jsonForm.schema.unityTextFit.default, false);
  assert.equal(fields.jsonForm.schema.unityExportKind.default, "patch");
});

test("v2.6o: without localStorage the memory degrades to built-in defaults (no crash)", async () => {
  ensurePayload();
  const page = freshScenario();
  page.window = undefined; // no window at all: store lookups must not throw
  await loadAddon(page);
  const fields = page.reg.props.unity.exportOptionFields();
  assert.equal(fields.jsonForm.schema.unitySwapFonts.default, false);
  assert.equal(fields.jsonForm.schema.unityTextFitRatio.default, "0.5");
  // the save path without a store (every exportHandler test runs windowless,
  // and unitySaveLastOptions sits at export start) is a silent no-op —
  // covered by the whole suite above.
});

test("onOpenExportDialogReady (e2e-fix15): moves the customFields wrapper to the top of the export dialog", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  const hook = page.reg.handlers.unity.onOpenExportDialogReady;
  assert.equal(typeof hook, "function", "the position hook is registered on the unity engine");
  // Minimal jQuery-ish dialog double: SLR's dialog has the wrapper as its
  // LAST child (trans.html:899); the hook prepends it so the Unity options
  // render above the built-in fields.
  const $fields = { length: 1 };
  const prepended = [];
  const $dialog = {
    find: (sel) => (sel === "form.customFields" ? $fields : { length: 0 }),
    prepend: (el) => prepended.push(el),
  };
  hook($dialog);
  assert.equal(prepended.length, 1, "wrapper moved");
  assert.ok(prepended[0] === $fields, "the wrapper element itself was moved");
  // guarded no-ops: no wrapper rendered, or a non-jQuery argument
  hook({ find: () => ({ length: 0 }), prepend: () => prepended.push("bogus") });
  hook(null);
  hook(undefined);
  assert.equal(prepended.length, 1, "missing wrapper / bad argument never prepends");
});

test("exportHandler returns false for a bogus mode (customParser precedent)", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "yaml" });
  assert.equal(out, false);
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("exportHandler: missing unity metadata -> alert + halt, no backend call", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  page.trans.project = { files: {}, options: {}, cache: { cachePath: path.join(TMP, "c1") } };
  const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "dir" });
  assert.ok(out, "must halt the PHP export");
  assert.match(page.alerts[0], /metadata missing/);
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("exportHandler (e2e-fix8): FULL copy with no translations + no font -> guard alert (patch is the revert escape), halt, no backend call", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  const cachePath = path.join(TMP, "c2");
  fs.mkdirSync(path.join(cachePath, "data"), { recursive: true });
  page.trans.project = {
    cache: { cachePath },
    files: { "a": { data: [["こんにちは", ""]] } }, // untranslated
    options: { unity: { gameDir: "E:/Games/FakeGame" } },
  };
  // e2e-fix8: the nothing-to-export guard is FULL-COPY ONLY — patch mode can
  // always export an all-originals baseline/revert, so only a full copy of an
  // unchanged game (a no-op) is blocked.
  const out = await page.reg.handlers.unity.exportHandler("X:/out", {
    mode: "dir", custom: { unityExportKind: "full" } });
  assert.ok(out);
  assert.match(page.alerts[0], /Nothing to export in full game copy mode/);
  assert.match(page.alerts[0], /no translations are filled in and the font swap is off/);
  assert.match(page.alerts[0], /full copy of an unchanged game would be a no-op/);
  assert.match(page.alerts[0], /patch export kind instead/);
  assert.match(page.alerts[0], /all-originals patch/);
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("exportHandler (e2e-fix8): PATCH with no translations + no selection -> NO guard, all-originals patch: every staged file copied verbatim", async () => {
  ensurePayload();
  const { page, proj, staged } = stageProject(TMP + "-p-patchall");
  for (const f of Object.values(page.trans.project.files))
    for (const row of f.data) row[1] = ""; // wipe every translation
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 0, exported_files: [] };
  await loadAddon(page);
  const outDir = path.join(TMP, "out-patchall");
  const out = await page.reg.handlers.unity.exportHandler(outDir, { mode: "dir", custom: {} });
  assert.ok(out && out.halt === true, "patch export runs — the guard never applies to patch mode");
  assert.equal(page.alerts.some((m) => /Nothing to export/.test(m)), false, "no guard alert");
  assert.equal(globalThis.__unityBackendCalls.length, 1, "job manifest built");
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.version, 2);
  assert.equal(job.command, "patch");
  assert.equal(job.patch_mode, true);
  assert.equal(job.game_dir, staged, "reads the staged game");
  // Empty selection = ALL project files in scope (SLR semantics: rmmv.js:1539,
  // SEP.js:502): the backend exported nothing, so EVERY staged file is copied
  // verbatim into out — same count, same bytes.
  const wrote = [];
  (function walk(d) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p); else wrote.push(p);
    }
  })(outDir);
  const expectedKeys = Object.keys(proj.files).sort();
  assert.equal(wrote.length, expectedKeys.length, "one output file per project file");
  for (const p of wrote) {
    const rel = path.relative(outDir, p);
    assert.ok(expectedKeys.includes(rel.split("\\").join("/")), "game-rel path preserved: " + rel);
    assert.deepEqual(fs.readFileSync(p), fs.readFileSync(path.join(staged, rel)),
      "byte-identical to the staged original: " + rel);
  }
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /reverted \(originals\): 2/.test(t)), "reverted count line");
  assert.ok(lines.some((t) => /files exported: 0/.test(t)), "files exported line");
  assert.equal(page.alerts.length, 0, "e2e-fix10: success summary is console lines, no alert");
});

test("exportHandler (e2e-fix7): file selection lifts the nothing-to-export guard (revert export)", async () => {
  ensurePayload();
  const { page, staged } = stageProject(TMP + "-p-selectguard");
  // No translations, no font — but SLR passed a file selection
  // (trans-full.js:2091-2098 fills options.files from the checked files).
  page.trans.project.files = {
    "FakeGame_Data/level0": { data: [["こんにちは", ""]] },
    "FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat": { data: [["メニュー", ""]] },
  };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-revert-only"), {
    mode: "dir", custom: {}, files: ["FakeGame_Data/level0"],
  });
  assert.ok(out && out.halt === true, "export ran instead of blocking");
  assert.equal(globalThis.__unityBackendCalls.length, 1);
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /reverted \(originals\): 1/.test(t)), "reverted count line");
  assert.ok(lines.some((t) => /Including original 1\/1 {2}FakeGame_Data\/level0/.test(t)),
    "per-file 'Including original i/N  <rel>' line for the selected original");
  assert.equal(page.alerts.length, 0, "no alert on the success path");
  // Scope = the selection ONLY: the unselected file must not leak into out.
  assert.equal(fs.existsSync(path.join(TMP, "out-revert-only",
    "FakeGame_Data", "il2cpp_data")), false);
  // The selected untranslated file's staged original IS the patch output.
  assert.equal(fs.readFileSync(path.join(TMP, "out-revert-only", "FakeGame_Data", "level0"), "utf-8"),
    "STAGED-ORIGINAL:FakeGame_Data/level0");
  assert.equal(fs.readFileSync(path.join(staged, "FakeGame_Data", "level0"), "utf-8"),
    "STAGED-ORIGINAL:FakeGame_Data/level0");
});

test("exportHandler: missing scan index -> alert, no backend call", async () => {
  ensurePayload();
  const page = freshScenario();
  await loadAddon(page);
  const cachePath = path.join(TMP, "c3"); // no data/index.json
  fs.mkdirSync(cachePath, { recursive: true });
  page.trans.project = makeProject(cachePath);
  const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "dir", custom: {} });
  assert.ok(out);
  assert.match(page.alerts[0], /Scan index missing/);
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("exportHandler dir mode (patch, default): v2 manifest against stagedGameDir, option plumbing, summary, halt", async () => {
  ensurePayload();
  const { page, cachePath, staged } = stageProject(TMP + "-p8");
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 5, edits_missed: 2, fonts_swapped: 3,
    tmp_assets_made_dynamic: 2, tmp_assets_skipped_no_source: 1,
    exported_files: ["FakeGame_Data/level0"] };
  await loadAddon(page);

  // Real CustomParser plumbing: values arrive under options.custom as FLAT
  // FormData strings — checked checkbox is the string "on", not a boolean.
  // e2e-fix7: unityExportKind absent (its default "patch") -> PATCH mode;
  // stale unitySepRenderDir values must be ignored entirely.
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dir"), {
    mode: "dir",
    custom: { unitySwapFonts: "on", unitySepRenderDir: path.join(TMP, "stale-sep") },
  });

  assert.ok(out && out.halt === true, "returns common.halt()'s truthy token");
  assert.equal(globalThis.__unityBackendCalls.length, 1);
  const call = globalThis.__unityBackendCalls[0];
  const job = call.job;
  assert.equal(job.version, 2, "contract v2 manifest (backend rejects v1)");
  assert.equal(job.command, "patch");
  assert.equal(job.patch_mode, true, "default export kind is the file-replacement patch");
  assert.equal(job.game_dir, staged, "patch reads from the STAGED game (self-contained project)");
  assert.equal(job.out_dir, path.join(TMP, "out-dir")); // dir mode: patch straight to target
  assert.equal(job.index_path, path.join(cachePath, "data", "index.json"));
  assert.ok(call.hasProgress, "showProgress is forwarded as onProgress");
  const expectExe = process.platform === "win32"
    ? path.join(SANDBOX, "runtime", "python", "python.exe")
    : path.join(SANDBOX, "runtime", "bin", "python3");
  assert.equal(call.python && call.python.cmd, expectExe);
  assert.equal(job.font_ttf, SLR_FONT); // host app font via common.getRootDir (e2e-fix1), not a bundled file
  // e2e-fix6: NO texture keys in the export manifest, whatever custom carried.
  assert.equal(job.textures_manifest_dir, undefined);
  assert.equal(job.textures_dir, undefined);
  assert.ok(/zip-out-/.test(job.out_dir) === false);

  // translations.json: written into the staging jobs dir; blank rows skipped,
  // values kept verbatim (backend filters whitespace itself).
  const transPath = path.join(cachePath, "jobs", "translations.json");
  assert.deepEqual(JSON.parse(fs.readFileSync(transPath, "utf-8")),
    { "こんにちは": "Hello", "はい": "  Yes  ", "メニュー": "Menu" });

  // Summary surfaces contract payload fields — v2 adds exported_files count
  // and the patch-mode application hint. e2e-fix8: NO selection was passed,
  // so ALL project files are in scope; the backend exported only level0, so
  // the other file is copied verbatim from the staged game (revert) and the
  // summary reports it. e2e-fix10: the summary is CONSOLE LINES (exportLine)
  // now — the success path alerts nothing at all.
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /Export complete/.test(t)), "summary heading");
  assert.ok(lines.some((t) => /mode: patch/.test(t)), "mode line");
  assert.ok(lines.some((t) => /strings written: 5/.test(t)));
  assert.ok(lines.some((t) => /files exported: 1/.test(t)));
  assert.ok(lines.some((t) => /reverted \(originals\): 1/.test(t)));
  assert.ok(lines.some((t) => /apply: copy the files over the game folder/.test(t)),
    "patch-mode apply hint line");
  assert.ok(lines.some((t) => /WARNING: 2 staged edits/.test(t)));
  assert.ok(lines.some((t) => /fonts swapped: 3/.test(t)));
  // e2e-fix15: the TMP stage stats surface in the summary (gated on > 0).
  assert.ok(lines.some((t) => /TMP fonts made dynamic: 2/.test(t)));
  assert.ok(lines.some((t) => /TMP fonts skipped \(no source font\): 1/.test(t)));
  assert.ok(lines.some((t) => /output: /.test(t)), "output line");
  assert.ok(lines.some((t) => /Including original 1\/1 {2}FakeGame_Data\/il2cpp_data\/Metadata\/global-metadata\.dat/.test(t)),
    "the revert copy announced its file BEFORE copying");
  assert.equal(page.alerts.length, 0, "no success alert anymore");
  assert.equal(fs.readFileSync(path.join(TMP, "out-dir", "FakeGame_Data", "il2cpp_data", "Metadata", "global-metadata.dat"), "utf-8"),
    "STAGED-ORIGINAL:FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat",
    "unexported in-scope file reverted to its staged original");
  // progress overlay hidden again
  assert.equal(page._els["slr-unity-progress"].style.display, "none");
  assert.equal(page.common.packCalls.length, 0); // dir mode never packs
  assert.equal(page.common.aSpawnCalls.length, 0); // ...nor spawns 7z
  // Staging cleanup (I1) is zip-mode-only: dir mode patches straight into
  // the user's target — it must NEVER be rmSync'd.
  const rmsDir = spyRmSync();
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dir"), { mode: "dir", custom: {} });
  assert.equal(rmsDir.length, 0, "dir mode deletes nothing");
});

test("exportHandler dir mode (e2e-fix7): selection -> selected-but-unexported files copied verbatim from stagedGameDir", async () => {
  ensurePayload();
  const { page, staged } = stageProject(TMP + "-p-select");
  // Backend exported only level0 (the file with translations); the OTHER
  // selected file must come back as its staged original — and level0 itself
  // must NOT be overwritten by a revert copy.
  globalThis.__unityBackendNext = (job) => {
    fs.mkdirSync(path.join(job.out_dir, "FakeGame_Data"), { recursive: true });
    fs.writeFileSync(path.join(job.out_dir, "FakeGame_Data", "level0"), "PATCHED-BY-BACKEND");
    return { ok: true, version: 2, strings_written: 1,
      exported_files: ["FakeGame_Data/level0"] };
  };
  await loadAddon(page);
  const outDir = path.join(TMP, "out-sel");
  const out = await page.reg.handlers.unity.exportHandler(outDir, {
    mode: "dir", custom: {},
    files: ["FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat", "FakeGame_Data/level0"],
  });
  assert.ok(out && out.halt === true);
  // Selected + exported -> backend bytes kept (no revert copy over it).
  assert.equal(fs.readFileSync(path.join(outDir, "FakeGame_Data", "level0"), "utf-8"),
    "PATCHED-BY-BACKEND");
  // Selected + NOT exported -> staged ORIGINAL, verbatim, at its game-rel path.
  const rel = path.join(outDir, "FakeGame_Data", "il2cpp_data", "Metadata", "global-metadata.dat");
  assert.equal(fs.readFileSync(rel, "utf-8"), "STAGED-ORIGINAL:FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat");
  assert.equal(fs.readFileSync(path.join(staged, "FakeGame_Data", "il2cpp_data", "Metadata", "global-metadata.dat"), "utf-8"),
    "STAGED-ORIGINAL:FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat");
  // Nothing else leaked into the patch folder.
  const wrote = [];
  (function walk(d) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p); else wrote.push(path.relative(outDir, p).split("\\").join("/"));
    }
  })(outDir);
  assert.deepEqual(wrote.sort(),
    ["FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat", "FakeGame_Data/level0"],
    "the patch contains exactly the backend export + the revert copy");
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /files exported: 1/.test(t)));
  assert.ok(lines.some((t) => /reverted \(originals\): 1/.test(t)));
  assert.ok(lines.some((t) => /Including original 1\/1 {2}FakeGame_Data\/il2cpp_data\/Metadata\/global-metadata\.dat/.test(t)),
    "revert phase line for the selected unexported file");
  // A selected key with no staged original (old v1 project / deleted file) is
  // skipped silently — never a crash, never a phantom entry.
  const out2 = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-sel2"), {
    mode: "dir", custom: {}, files: ["Does/Not/Exist.bundle"] });
  assert.ok(out2 && out2.halt === true);
  assert.equal(fs.existsSync(path.join(TMP, "out-sel2", "Does")), false);
});

test("exportHandler (e2e-fix7): full game copy mode — v1 behavior against the ORIGINAL gameDir, no patch_mode", async () => {
  ensurePayload();
  const realGame = makeFakeGame("fullcopy-game");
  const { page, staged } = stageProject(TMP + "-p-full", { gameDir: realGame });
  globalThis.__unityBackendNext = { ok: true, version: 2, copied: 74, strings_written: 3, fonts_swapped: 1 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-full"), {
    mode: "dir", custom: { unityExportKind: "full" },
  });
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.version, 2);
  assert.equal(job.game_dir, realGame, "full copy reads the ORIGINAL game folder");
  assert.notEqual(job.game_dir, staged, "NOT the staged copy");
  assert.equal(job.patch_mode, undefined, "no patch_mode value = v1 whole-game copy behavior (JSON.stringify drops the undefined key on the wire)");
  // e2e-fix10: summary lines, read through the same helper.
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /mode: full/.test(t)), "mode line says full");
  assert.ok(lines.some((t) => /fonts swapped: 1/.test(t)));
  assert.equal(lines.some((t) => /apply: copy the files over the game folder/.test(t)), false,
    "no patch-apply hint in full-copy mode");
  assert.equal(lines.some((t) => /revert/.test(t)), false,
    "revert copies are patch-mode only (whole game is copied anyway)");
  assert.equal(page.alerts.length, 0, "no success alert anymore");
  // A file selection in full-copy mode changes nothing: the whole game is
  // copied; nothing is copied on top of it.
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-full2"), {
    mode: "dir", custom: { unityExportKind: "full" },
    files: ["FakeGame_Data/level0"] });
  assert.equal(globalThis.__unityBackendCalls.length, 2);
});

test("exportHandler (e2e-fix7): full copy with a MISSING original -> clear alert, no backend call", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p-fullmissing", { gameDir: "E:/Games/DeletedGame" });
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fullmissing"), {
    mode: "dir", custom: { unityExportKind: "full" },
  });
  assert.ok(out, "halts the PHP export");
  assert.equal(globalThis.__unityBackendCalls.length, 0, "no backend call at all");
  assert.equal(page.alerts.length, 1, "exactly the explanatory alert");
  assert.match(page.alerts[0], /Full copy export needs the original game folder/);
  assert.match(page.alerts[0], /E:\/Games\/DeletedGame/);
  assert.match(page.alerts[0], /Use patch export — the project is self-contained/);
  // Zip mode halts the same way BEFORE any staging dir is created.
  const out2 = await page.reg.handlers.unity.exportHandler(path.join(TMP, "never.zip"), {
    mode: "zip", custom: { unityExportKind: "full" },
  });
  assert.ok(out2);
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("exportHandler: font option absent -> optional manifest keys undefined", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p9");
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out2"), {
    mode: "dir",
    custom: { unitySepRenderDir: path.join(TMP, "does-not-exist") }, // stale value: must be IGNORED (e2e-fix6)
  });
  assert.ok(out);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_ttf, undefined);
  assert.equal(job.textures_manifest_dir, undefined);
  assert.equal(job.textures_dir, undefined);
});

test("exportHandler ok:false -> failure alert with log path, halt, no pack", async () => {
  ensurePayload();
  const { page, cachePath } = stageProject(TMP + "-p10");
  globalThis.__unityBackendNext = { ok: false, version: 2, error: "ValueError: out_dir nested in game_dir" };
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out3"), { mode: "zip", custom: {} });
  assert.ok(out);
  assert.match(page.alerts[0], /Unity patch failed/);
  assert.match(page.alerts[0], /ValueError: out_dir nested/);
  assert.match(page.alerts[0], /backend\.log/);
  assert.equal(page.common.packCalls.length, 0);
  assert.equal(rms.length, 0, "backend failure keeps the staging dir");
});

test("exportHandler zip mode: 7z via aSpawn with cwd=outDir + bare \"*\" (contents at archive root), then integrity check", async () => {
  ensurePayload();
  const { page, cachePath } = stageProject(TMP + "-p11");
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 3, fonts_swapped: 2, edits_missed: 1, textures_imported: 4 };
  await loadAddon(page);
  const target = path.join(TMP, "game.zip");
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(target, {
    mode: "zip", custom: { unitySwapFonts: "on" },
  });
  assert.ok(out && out.halt === true, "returns common.halt()'s truthy token");
  const job = globalThis.__unityBackendCalls[0].job;
  const jobsZip = path.join(cachePath, "jobs");
  assert.ok(job.out_dir.startsWith(jobsZip) && /zip-out-\d+/.test(job.out_dir), "zip mode patches into a staging dir");
  assert.notEqual(job.out_dir, target);

  // Carried Task 5 finding, fixed per rmmv.js:3046-3055: 7z is spawned with
  // ["a","-tzip",target,"*"] and cwd = the staging dir, so the patched game
  // CONTENTS land at the archive root (common.pack would archive the folder
  // itself as a wrapper). The rmmv.js:3058-3075 "t" integrity check follows.
  assert.equal(page.common.aSpawnCalls.length, 2, "create + verify calls");
  assert.deepEqual(page.common.aSpawnCalls[0], {
    bin: "C:/fake-slr/node_modules/7zip-bin/win/x64/7za.exe",
    args: ["a", "-tzip", target, "*"],
    cwd: job.out_dir,
  });
  assert.deepEqual(page.common.aSpawnCalls[1].args, ["t", target]);
  assert.equal(page.common.packCalls.length, 0); // pack is the fallback-only path now

  // Final-review I1: verified zip success deletes the multi-GB staging dir
  // (a leftover zip-out-<ts> would be packed into every subsequent .slrproj
  // save — trans-full.js:1649-1652 excludes nothing under jobs/).
  assert.equal(rms.length, 1, "staging dir removed exactly once");
  assert.equal(rms[0].p, job.out_dir);
  assert.ok(rms[0].opts && rms[0].opts.recursive === true, "recursive removal");

  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /Packing archive…/.test(t)),
    "e2e-fix10: the zip phase is announced in the overlay console");
  assert.ok(lines.some((t) => /archive: /.test(t)), "archive line carries the target");
  assert.ok(lines.some((t) => t.indexOf(target) !== -1), "archive line names the target path");
  assert.ok(lines.some((t) => /mode: patch/.test(t)));
  assert.ok(lines.some((t) => /WARNING: 1 staged edits/.test(t)));
  assert.ok(lines.some((t) => /fonts swapped: 2/.test(t)));
  assert.ok(lines.some((t) => /reverted \(originals\): 2/.test(t)),
    "no exported_files payload -> both project files reverted");
  assert.equal(page.alerts.length, 0, "no success alert anymore");
  // e2e-fix6: even when the payload carries textures_imported, the EXPORT
  // summary must not claim texture work (images are the standalone tool).
  assert.equal(lines.some((t) => /textures imported/.test(t)), false);
});

test("exportHandler zip mode: 7z non-zero exit -> failure alert with code/stderr/staging path, success NOT claimed", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p12");
  // common.aSpawn RESOLVES on non-zero exit (common.js:2133-2191) — only the
  // onDone(result, code) callback carries the code. Plan: creation fails.
  globalThis.__unityZipExitPlan = [2];
  globalThis.__unityZipStderr = "7-Zip cannot write the archive";
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "bad.zip"), { mode: "zip", custom: {} });
  assert.ok(out, "halts the PHP export (plain true on the failure path)");
  assert.equal(page.common.aSpawnCalls.length, 1, "no integrity call after a failed create");
  assert.equal(page.alerts.length, 1, "no success alert after a zip failure");
  assert.match(page.alerts[0], /Failed to create the zip archive/);
  assert.match(page.alerts[0], /code 2/);
  assert.match(page.alerts[0], /7-Zip cannot write the archive/);
  assert.match(page.alerts[0], /zip-out-/);
  assert.equal(rms.length, 0, "staging dir KEPT — the failure alert points at it");
});

test("exportHandler zip mode: integrity \"t\" check non-zero exit -> alert, success NOT claimed", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p12b");
  globalThis.__unityZipExitPlan = [0, 1]; // create ok, verify fails
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "bad2.zip"), { mode: "zip", custom: {} });
  assert.ok(out);
  assert.equal(page.common.aSpawnCalls.length, 2);
  assert.equal(page.alerts.length, 1);
  assert.match(page.alerts[0], /empty or failed its integrity check/);
  assert.match(page.alerts[0], /zip-out-/);
  assert.equal(rms.length, 0, "verify failure keeps the staging dir");
});

test("exportHandler zip mode: zip smaller than the 22-byte empty-archive floor -> alert", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p12c");
  globalThis.__unityZipBytes = 10; // create + verify both "succeed", file is 10 bytes
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "tiny.zip"), { mode: "zip", custom: {} });
  assert.ok(out);
  assert.equal(page.alerts.length, 1);
  assert.match(page.alerts[0], /empty or failed its integrity check/);
  assert.equal(rms.length, 0, "empty-archive floor failure keeps the staging dir");
});

test("exportHandler zip mode: aSpawn rejection (7z binary missing) -> failure alert, handler resolves", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p12d");
  globalThis.__unityZipSpawnErr = "spawn ENOENT: 7za.exe";
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "no7z.zip"), { mode: "zip", custom: {} });
  assert.ok(out);
  assert.equal(page.alerts.length, 1);
  assert.match(page.alerts[0], /Failed to create the zip archive/);
  assert.match(page.alerts[0], /spawn ENOENT/);
  assert.match(page.alerts[0], /zip-out-/);
  assert.equal(rms.length, 0, "spawn error keeps the staging dir");
});

test("exportHandler zip mode: common.pack fallback when aSpawn/7z are unavailable", async () => {
  ensurePayload();
  const { page, cachePath } = stageProject(TMP + "-p12e");
  // A page whose common predates aSpawn/get7zipBin — pack is the only tool.
  const packCalls = [];
  page.common = {
    pack: async function (from, to) { packCalls.push({ from, to }); return to; },
    halt: function () { return { halt: true }; },
  };
  await loadAddon(page);
  const target = path.join(TMP, "fallback.zip");
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(target, { mode: "zip", custom: {} });
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.deepEqual(packCalls, [{ from: job.out_dir, to: target }]); // pack(from, to) order per common.js:2232
  assert.ok(exportConsoleLines(page).some((t) => /archive: /.test(t)),
    "e2e-fix10: archive summary line (console, not an alert)");
  assert.equal(page.alerts.length, 0, "no success alert on the pack fallback either");
  // I1: the pack fallback keeps the staging dir on purpose — no "t" verify
  // ran there and the wrapper-folder zip may make staging the only
  // correctly-rooted copy of the patched game.
  assert.equal(rms.length, 0, "pack fallback never deletes the staging dir");
});

test("exportHandler zip mode: pack fallback failure -> alert keeps staging dir path", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p12f");
  const pageCommon = { packCalls: [], pack: async () => "" }; // no halt on this page -> plain-true fallback too
  page.common = pageCommon;
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "bad3.zip"), { mode: "zip", custom: {} });
  assert.equal(out, true, "halts via the plain-true fallback when common.halt is absent");
  assert.match(page.alerts[0], /Zip creation failed/);
  assert.match(page.alerts[0], /zip-out-/);
  assert.equal(rms.length, 0, "pack failure keeps the staging dir");
});

test("exportHandler zip mode: no zip tool at all (no aSpawn, no pack) -> alert, no success claim", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p12g");
  page.common = { halt: function () { return { halt: true }; } };
  await loadAddon(page);
  const rms = spyRmSync();
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "toolless.zip"), { mode: "zip", custom: {} });
  assert.ok(out);
  assert.equal(page.alerts.length, 1);
  assert.match(page.alerts[0], /No zip tool is available/);
  assert.match(page.alerts[0], /zip-out-/);
  assert.equal(rms.length, 0, "no-tool failure keeps the staging dir");
});

test("exportHandler: runBackend rejection still hides the progress overlay and rejects", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-p13");
  globalThis.__unityBackendNext = { reject: "spawn ENOENT" };
  await loadAddon(page);
  await assert.rejects(
    page.reg.handlers.unity.exportHandler(path.join(TMP, "out4"), { mode: "dir", custom: {} }),
    /spawn ENOENT/);
  assert.equal(page._els["slr-unity-progress"].style.display, "none", "try/finally hid the overlay");
});

test("createProject (Task 3 fix): runBackend rejection still hides the overlay", async () => {
  ensurePayload();
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c9") });
  globalThis.__unityBackendNext = { reject: "backend produced no result file" };
  await loadAddon(page);
  await assert.rejects(
    page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" }),
    /no result file/);
  assert.equal(page._els["slr-unity-progress"].style.display, "none", "try/finally hid the overlay");
});

test("createProject (e2e-fix7): v2 scan, staging copies the game files, index rewritten to staged abs, project self-contained", async () => {
  ensurePayload();
  // Synthesize a scan payload from the synthetic scan fixture rows (same trick as
  // sceneGraphCore.test.js — the fixture carries strings, not an index), then
  // back it with REAL files in a fake game so staging has something to copy.
  const scan = require("./fixtures/stealth-scan-sample.json");
  const rels = [...new Set(scan.strings.map((s) => s.file))];
  const gameRoot = path.join(TMP, "stage-game");
  fs.rmSync(gameRoot, { recursive: true, force: true });
  fs.mkdirSync(path.join(gameRoot, "Fake_Data"), { recursive: true }); // resolveGameRoot marker
  fs.writeFileSync(path.join(gameRoot, "Fake_Data", "globalgamemanagers"), "x");
  for (const rel of rels) {
    const p = path.join(gameRoot, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, "ORIGINAL-BYTES:" + rel);
  }
  const index = {};
  for (const s of scan.strings) (index[s.text] = index[s.text] || []).push(
    { file: s.file, abs: path.join(gameRoot, s.file), path_id: s.path_id, source: s.source, offset: s.offset, length: s.length, key: s.key, tokens: s.tokens });
  const cachePath = path.join(TMP, "c10");
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath });
  globalThis.__unityBackendNext = { ok: true, version: 2, index, strings: scan.strings, stats: { files_scanned: 1 } };
  await loadAddon(page);
  const proj = await page.reg.handlers.unity.createProject({ selectedFile: gameRoot, gameFolder: path.dirname(gameRoot), gameTitle: "Fake", projectId: "pid" });
  // The scan job itself speaks contract v2.
  assert.equal(globalThis.__unityBackendCalls[0].job.version, 2);
  assert.equal(globalThis.__unityBackendCalls[0].job.command, "scan");
  assert.equal(proj.gameEngine, "unity");
  assert.equal(Object.keys(proj.files).length > 0, true);
  // Staging: every index-referenced file copied VERBATIM into
  // <cachePath>/data/game/<game-rel>, rel structure preserved.
  const stagedGameDir = path.join(cachePath, "data", "game");
  for (const rel of rels)
    assert.equal(fs.readFileSync(path.join(stagedGameDir, rel), "utf-8"), "ORIGINAL-BYTES:" + rel,
      "staged copy of " + rel);
  // index.json is written from the REWRITTEN index: every abs points into the
  // staging cache, every `file` (game-rel key) kept verbatim.
  const written = JSON.parse(fs.readFileSync(path.join(cachePath, "data", "index.json"), "utf-8"));
  const wRels = new Set(), wAbs = new Set();
  for (const occs of Object.values(written))
    for (const o of occs) { wRels.add(o.file); wAbs.add(o.abs); }
  assert.deepEqual([...wRels].sort(), [...rels].sort(), "file keys unchanged");
  for (const a of wAbs)
    assert.ok(a.startsWith(stagedGameDir + path.sep) || a.startsWith(stagedGameDir + "/"),
      "abs rewritten into the staging cache: " + a);
  assert.equal(wAbs.size, rels.length, "one staged abs per file");
  // The in-memory index handed to buildProject was rewritten the same way
  // (the ORIGINAL abs paths are nowhere in the written index — walk the
  // parsed object; JSON.stringify would double the backslashes).
  let origLeak = false;
  for (const occs of Object.values(written))
    for (const o of occs) if (String(o.abs).startsWith(gameRoot)) origLeak = true;
  assert.equal(origLeak, false, "original abs paths do not leak into index.json");
  // options.unity carries both dirs: stagedGameDir for patch export, gameDir
  // kept as the reference only full-copy export reads.
  assert.equal(proj.options.unity.stagedGameDir, stagedGameDir);
  assert.equal(proj.options.unity.gameDir, gameRoot);
  assert.equal(proj.options.unity.contract, 2);
  // Staging progress went through the existing router, sorted rel order:
  // il2cpp_data/... < level0, so level0 is the 2/2 line.
  const bub = page._els["slr-unity-progress"];
  assert.ok(bub.texts.some((t) => /Staging files 1\/2 {2}global-metadata\.dat/.test(t)),
    "first staging progress line (sorted rel order)");
  assert.ok(bub.texts.some((t) => /Staging files 2\/2 {2}level0/.test(t)),
    "last staging progress line");
  assert.equal(page._els["slr-unity-progress"].style.display, "none");
  // e2e-fix3: the bubble fallback showed the scan-complete note during the
  // build, then hideProgress() cleared it once the project existed.
  assert.ok(bub.texts.some((t) => /Scan complete — building project/.test(t)),
    "fallback bubble carried the scan-complete note");
  // e2e-fix6: no post-create SEP-texture offer exists anymore. The FIRST-RUN
  // help ask may still be up (this page never marked it seen) — it must never
  // mention textures.
  await new Promise((r) => setTimeout(r, 50));
  const ask = page._els["slr-unity-ask"];
  if (ask) assert.doesNotMatch(modalText(ask), /texture/i, "no texture offer — only the help ask may exist");
});

test("createProject (e2e-fix7): a file that fails to stage keeps its ORIGINAL abs (backend re-resolves only stale abs)", async () => {
  ensurePayload();
  const gameRoot = path.join(TMP, "stage-game-locked");
  fs.rmSync(gameRoot, { recursive: true, force: true });
  fs.mkdirSync(path.join(gameRoot, "Fake_Data"), { recursive: true });
  fs.writeFileSync(path.join(gameRoot, "Fake_Data", "globalgamemanagers"), "x");
  fs.writeFileSync(path.join(gameRoot, "Fake_Data", "level0"), "ORIGINAL-BYTES");
  // level1 is referenced by the index but MISSING from disk (simulates a
  // locked/vanished source): its occurrences must keep the original abs.
  const index = {
    "あるテキスト": [
      { file: "Fake_Data/level0", abs: path.join(gameRoot, "Fake_Data/level0"), path_id: 1, source: "raw", offset: 0, length: 1, key: "", tokens: [] },
      { file: "Fake_Data/level1", abs: path.join(gameRoot, "Fake_Data/level1"), path_id: 1, source: "raw", offset: 0, length: 1, key: "", tokens: [] },
    ],
  };
  const cachePath = path.join(TMP, "c10b");
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath });
  globalThis.__unityBackendNext = { ok: true, version: 2, index, strings: [], stats: { files_scanned: 1 } };
  await loadAddon(page);
  const proj = await page.reg.handlers.unity.createProject({ selectedFile: gameRoot, gameFolder: path.dirname(gameRoot), gameTitle: "Fake", projectId: "pid" });
  const written = JSON.parse(fs.readFileSync(path.join(cachePath, "data", "index.json"), "utf-8"));
  const occs = written["あるテキスト"];
  assert.ok(occs[0].abs.startsWith(path.join(cachePath, "data", "game")),
    "copied file's abs points into the staging cache");
  assert.equal(occs[1].abs, path.join(gameRoot, "Fake_Data", "level1"),
    "failed copy keeps the ORIGINAL abs — the backend keeps an abs whose file exists");
  assert.equal(fs.existsSync(path.join(cachePath, "data", "game", "Fake_Data", "level1")), false);
});

// ---------------------------------------------------------------------------
// Help-page opener (Task 7): one-time offer on the first createProject.
// ---------------------------------------------------------------------------

// Minimal ok-scan payload (buildProject tolerates an empty index).
function emptyScan() { return { ok: true, version: 2, index: {}, strings: [], stats: { files_scanned: 0 } }; }

// e2e-fix3: the offer is an in-DOM modal now (it fired while the loading
// overlay was already up, and a native confirm() can sit under it) — accept
// by clicking the modal's affirmative button (modalButtons()[1]).
test("help opener (e2e-fix3 modal): first createProject offers the help page exactly once, flag via addon setConfig", async () => {
  ensurePayload();
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c20") });
  const saved = [];
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    config: {},
    // mirrors Addon.prototype.setConfig (www/js/addon.js:51-54): writes the
    // config object and persists (recorded here instead of sys.saveConfig())
    setConfig: function (k, v) { this.config[k] = v; saved.push([k, v]); return this.config; },
  };
  await loadAddon(page, inst);
  globalThis.__unityBackendNext = emptyScan();
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  const modal = page._els["slr-unity-ask"];
  assert.ok(modal, "help ask modal shown on the first createProject");
  assert.match(modal.style.cssText, /z-index:100001/, "modal sits above the loading overlay");
  assert.match(modalText(modal), /First Unity project/);
  assert.match(modalText(modal), /help page/);
  modalButtons(modal)[1].click(); // "Open help page"
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(page._els["slr-unity-ask"], undefined, "modal removed after the answer");
  // Accepting with no nw/window available falls back to the alert with the path.
  assert.ok(page.alerts.some((m) => /SLRUnity help page/.test(m) && /help\.html/.test(m)),
    "open attempt reached the alert fallback");
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid2" });
  // e2e-fix6: no ask may (re)appear on later projects — the help flag is seen
  // AND the post-create SEP offer no longer exists at all.
  assert.equal(page._els["slr-unity-ask"], undefined, "help offer must not repeat (no ask at all)");
  assert.deepEqual(saved, [["seenHelp", true]], "flag persisted through the addon config object, exactly once");
});

test("help opener (e2e-fix3 modal): accepting opens assets/help.html via nw.Shell.openExternal", async () => {
  ensurePayload();
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c21") });
  const opened = [];
  page.nw = { Shell: { openExternal: (url) => opened.push(url) } }; // SLR's way (common.js:4449)
  await loadAddon(page); // default instance: no config, no localStorage -> first run
  globalThis.__unityBackendNext = emptyScan();
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  const modal = page._els["slr-unity-ask"];
  assert.ok(modal, "help ask modal shown");
  modalButtons(modal)[1].click(); // accept
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(opened.length, 1);
  const expect = "file:///" + path.join(SANDBOX, "assets", "help.html").split("\\").join("/");
  assert.equal(opened[0], expect, "forward-slash file URL into the addon's own folder");
  assert.equal(page.alerts.length, 0, "no alert fallback when nw opened it");
});

test("help opener (e2e-fix3 modal): localStorage fallback when the addon config object is absent", async () => {
  ensurePayload();
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c22") });
  const store = {}, items = [];
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; items.push([k, v]); },
  };
  try {
    await loadAddon(page); // default instance: no config, no setConfig
    globalThis.__unityBackendNext = emptyScan();
    await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
    const modal = page._els["slr-unity-ask"];
    assert.ok(modal, "help ask modal shown");
    modalButtons(modal)[0].click(); // "Not now"
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(page._els["slr-unity-ask"], undefined, "modal removed on decline");
    await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid2" });
    // e2e-fix6: nothing may reappear — help flag stored, SEP offer removed.
    assert.equal(page._els["slr-unity-ask"], undefined, "help offer must not repeat");
    assert.deepEqual(items, [["slrUnitySeenHelp", "1"]], "flag stored under the localStorage key");
  } finally {
    delete globalThis.localStorage;
  }
});

// ---------------------------------------------------------------------------
// e2e-fix1: New Project dialog entry + createProject game-root resolver +
// host-app font.
// ---------------------------------------------------------------------------

// Minimal jQuery double: records on/one handlers per element and lets tests
// fire them (trigger) and read/write .value — enough for the slide wiring
// ($slide.find(sel).on("click", h), $elm.one("input", h).trigger("click")).
// e2e-fix3: collections expose .length; with {loadingOverlay:true} the
// "#loadingOverlay" selector matches (length 1) so the progress router sends
// its lines to ui.loadingProgress.
// e2e-fix4: .is(":visible") mirrors jQuery's display filter (an element the
// double "found" is visible), and .text(v) records status-line writes into
// elm.texts — what $("#loadingOverlay .loadingStatus").text(...) targets.
// e2e-fix6: .is(":checked") reads the element's `checked` property so tests
// can tick the insert-font checkbox ($fontBox is captured at WIRING time in
// UnityAddon.js, so the element recorded here in $.elms is the same object).
function fakeJQuery(opts) {
  const withOverlay = !!(opts && opts.loadingOverlay);
  const registry = [];
  const elms = [];
  function make(sel) {
    const elm = {
      sel, value: "",
      length: (sel === "#loadingOverlay" && !withOverlay) ? 0 : 1,
      on(ev, fn) { registry.push({ sel, ev, fn }); return elm; },
      one(ev, fn) { registry.push({ sel, ev, fn }); return elm; },
      trigger(ev) { registry.filter((r) => r.sel === sel && r.ev === ev)
        .forEach((r) => r.fn.call(elm, {})); return elm; },
      find(s) { return make(sel + " " + s); },
      prop() { return elm; },
      val(v) { if (v === undefined) return elm.value; elm.value = v; return elm; },
      is(s) {
        if (s === ":visible") return elm.length > 0;
        if (s === ":checked") return elm.checked === true;
        return false;
      },
      text(v) {
        if (v === undefined) return "";
        (elm.texts = elm.texts || []).push(String(v));
        return elm;
      },
    };
    elms.push(elm);
    return elm;
  }
  const $ = (html) => make(String(html));
  $.registry = registry;
  $.elms = elms;
  return $;
}

test("new project dialog: menu entry registered (at 3, icon via getWebLocation, slide, description)", async () => {
  ensurePayload();
  const page = freshScenario();
  page.window = { jQuery: fakeJQuery() }; // the body's window.jQuery fallback for `$`
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    package: { version: "0.1.0" },
    getWebLocation: function () { return "/addons/SLRUnity"; }, // addon.js:166
  };
  await loadAddon(page, inst);
  assert.equal(page.ui.menus.length, 1, "exactly one menu entry");
  const menu = page.ui.menus[0];
  assert.equal(menu.at, 3); // after rmrgss (1), before customParser (9)
  assert.equal(menu.goToSlide, "unityTranslator");
  assert.equal(menu.icon, "/addons/SLRUnity/assets/icon.png"); // e2e-fix11: icon ships in assets/
  assert.ok(menu.slides && menu.slides.unityTranslator, "slide registered under its id");
  assert.match(menu.descriptionBar, /Unity game/);
});

// e2e-fix9: the slide markup itself now contains "nwdirectory" (its four
// dvSelectPath inputs), so the RUNTIME picker element unitySelectDir() mints
// (`<input type="file" nwdirectory />`) must be matched by its exact source.
const isRuntimePicker = (e) => e.sel.indexOf("<input type=\"file\" nwdirectory") !== -1;

test("new project dialog: Select Folder click -> picker -> close -> ui.startProject(picked)", async () => {
  ensurePayload();
  const page = freshScenario();
  const $ = fakeJQuery();
  page.window = { jQuery: $ };
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    getWebLocation: function () { return "/addons/SLRUnity"; },
  };
  await loadAddon(page, inst);
  const click = $.registry.find((r) => r.sel.endsWith(".unitySelectFolder") && r.ev === "click");
  assert.ok(click, "action button has a click handler");
  const flow = click.fn.call({}); // async: waits on the picker promise
  const picker = $.elms.filter(isRuntimePicker).pop();
  assert.ok(picker, "nwdirectory picker input created (customParser pattern)");
  picker.value = "C:/Games/MyGame";
  picker.trigger("input"); // user picked a folder
  await flow;
  assert.equal(page.ui.dialogClosed, 1, "dialog closed before starting the project");
  assert.deepEqual(page.ui.startProjectCalls, ["C:/Games/MyGame"]);
});

test("new project dialog: cancelled picker (empty value) closes nothing, starts nothing", async () => {
  ensurePayload();
  const page = freshScenario();
  const $ = fakeJQuery();
  page.window = { jQuery: $ };
  const inst = { fullPath: SANDBOX, basePath: "C:/addons",
    getWebLocation: function () { return "/addons/SLRUnity"; } };
  await loadAddon(page, inst);
  const click = $.registry.find((r) => r.sel.endsWith(".unitySelectFolder") && r.ev === "click");
  const flow = click.fn.call({});
  const picker = $.elms.filter(isRuntimePicker).pop();
  picker.value = ""; // cancelled
  picker.trigger("input");
  await flow;
  assert.equal(page.ui.dialogClosed, 0);
  assert.equal(page.ui.startProjectCalls.length, 0);
});

test("createProject (e2e-fix1): parent-folder pick resolves to the single Unity child; loc/title/gameDir follow", async () => {
  ensurePayload();
  const parent = path.join(TMP, "launch-parent");
  fs.rmSync(parent, { recursive: true, force: true });
  const child = path.join(parent, "RealGame");
  fs.mkdirSync(path.join(child, "RealGame_Data"), { recursive: true });
  fs.writeFileSync(path.join(child, "RealGame_Data", "globalgamemanagers"), "x");
  const cachePath = path.join(TMP, "c-res1");
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath });
  globalThis.__unityBackendNext = { ok: true, version: 2, index: {}, strings: [], stats: {} };
  await loadAddon(page);
  // args mirror trans.createProjectFromEngine: gameFolder = DIRNAME(picked).
  const proj = await page.reg.handlers.unity.createProject({
    selectedFile: parent, gameFolder: path.dirname(parent), projectId: "pid" });
  assert.equal(globalThis.__unityBackendCalls[0].job.game_dir, child, "scan targets the resolved Unity child");
  assert.equal(proj.loc, child);
  assert.equal(proj.options.unity.gameDir, child);
  assert.equal(proj.gameTitle, "RealGame", "title = basename of the resolved root");
});

test("createProject (e2e-fix1): non-Unity pick alerts and throws before any backend call", async () => {
  ensurePayload();
  const nowhere = path.join(TMP, "not-a-game");
  fs.mkdirSync(path.join(nowhere, "sub"), { recursive: true });
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-res2") });
  await loadAddon(page);
  await assert.rejects(
    page.reg.handlers.unity.createProject({ selectedFile: nowhere, gameFolder: path.dirname(nowhere), projectId: "pid" }),
    /No Unity game found/);
  assert.match(page.alerts[0], /No Unity game found/);
  assert.equal(globalThis.__unityBackendCalls.length, 0, "no scan started");
});

test("exportHandler (e2e-fix1): SLRCondensedCJK missing -> alert, font stage skipped, strings still patched", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fontmiss");
  // AFTER stageProject (freshScenario resets the root dir): a root with no font.
  globalThis.__unityRootDir = path.join(TMP, "slr-root-empty");
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 2 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fontless"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(out, "still halts the PHP export");
  // e2e-fix10: the notice is a console WARNING line in the export log now,
  // and the whole success path alerts nothing.
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /WARNING: SLRCondensedCJK font not found in SLR installation/.test(t)),
    "missing-font notice as a console WARNING line");
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.command, "patch", "string patch still runs");
  assert.equal(job.font_ttf, undefined, "font stage skipped");
  assert.ok(lines.some((t) => /strings written: 2/.test(t)), "summary still lands");
  assert.equal(page.alerts.length, 0, "no alert at all — warning and summary are console lines");
});

// ---------------------------------------------------------------------------
// e2e-fix3: progress routing into the loading overlay's console
// (ui.loadingProgress, www/js/ui.js:1517+; rmrgss precedent rmrgss.js:1847-
// 1913) + the SEP-texture ask as an in-DOM modal above the overlay.
// ---------------------------------------------------------------------------

test("progress routing (e2e-fix3): with the loading overlay up, scan lines go to ui.loadingProgress, NOT the bubble", async () => {
  ensurePayload();
  const page = freshScenario();
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) }; // $("#loadingOverlay").length === 1
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-fix3a") });
  globalThis.__unityBackendNext = { ok: true, version: 2, index: {}, strings: [], stats: {} };
  await loadAddon(page);
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  const calls = page.ui.loadingCalls;
  // v2.8: three lines — the per-file progress line, the scan-complete note,
  // and the risk-tag summary note.
  assert.equal(calls.length, 3, "per-file progress + scan-complete note + tags note");
  assert.equal(calls[0].title, "Unity scan");
  assert.match(calls[0].text, /^1\/2 {2}level0$/, "rmrgss-style 'done/total  basename' line");
  assert.deepEqual(calls[0].opts, { consoleOnly: false, mode: "consoleOutput" });
  assert.equal(calls[1].title, "Unity scan");
  assert.match(calls[1].text, /Scan complete — building project/);
  assert.deepEqual(calls[1].opts, { consoleOnly: false, mode: "consoleOutput" });
  assert.equal(calls[2].title, "Unity scan");
  assert.match(calls[2].text, /Tags: .* translate by default/);
  assert.deepEqual(calls[2].opts, { consoleOnly: false, mode: "consoleOutput" });
  assert.equal(page._els["slr-unity-progress"], undefined,
    "no bottom-right bubble was ever created while the overlay console took every line");
  // e2e-fix6: no post-create SEP offer exists anymore — nothing more is
  // logged; the first-run HELP ask may be up but must not mention textures.
  await new Promise((r) => setTimeout(r, 50));
  const ask = page._els["slr-unity-ask"];
  if (ask) assert.doesNotMatch(modalText(ask), /texture/i, "no texture offer — only the help ask may exist");
  // v2.8: the three createProject lines are all there is (progress,
  // scan-complete note, tags note).
  assert.equal(page.ui.loadingCalls.length, 3, "nothing else was logged");
});

test("progress routing (e2e-fix3): ui.loadingProgress present but NO loading overlay -> bubble fallback", async () => {
  ensurePayload();
  const page = freshScenario();
  page.window = { jQuery: fakeJQuery() }; // $jq resolves, but $("#loadingOverlay").length === 0
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-fix3b") });
  globalThis.__unityBackendNext = { ok: true, version: 2, index: {}, strings: [], stats: {} };
  await loadAddon(page);
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  assert.equal(page.ui.loadingCalls.length, 0, "ui.loadingProgress untouched without the overlay");
  const bub = page._els["slr-unity-progress"];
  assert.ok(bub, "bottom-right bubble used");
  assert.ok(bub.texts.some((t) => /Unity backend: 1\/2 {2}level0/.test(t)), "per-file line bubbled");
});

test("progress routing (e2e-fix3): no jQuery at all -> bubble fallback, loadingProgress untouched", async () => {
  ensurePayload();
  const page = freshScenario(); // default page: no window.jQuery -> $jq null
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-fix3c") });
  globalThis.__unityBackendNext = { ok: true, version: 2, index: {}, strings: [], stats: {} };
  await loadAddon(page);
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  assert.equal(page.ui.loadingCalls.length, 0);
  assert.ok(page._els["slr-unity-progress"], "bubble created");
});

// e2e-fix6: the SEP-texture offer tests are GONE with the feature — no
// post-create modal exists anymore. The standalone replacements live in the
// e2e-fix6 section further down.

// lib/paths.js siblingDir, expected <Game>_unity_img value (forward slashes)
function siblingDirOf(gameDir) {
  const abs = path.resolve(gameDir);
  return path.join(path.dirname(abs), path.basename(abs) + "_unity_img").split("\\").join("/");
}

// ---------------------------------------------------------------------------
// e2e-fix4: night-mode modal palette (issue 1). The overlay Close-button
// handling tests (issue 2) left with extractTextures in e2e-fix6 — standalone
// tools now own the overlay lifecycle (showLoading/loadingEnd) instead of
// holding a finished overlay's Close button.
// ---------------------------------------------------------------------------

test("unityAsk palette (e2e-fix4 + e2e-fix5 flashbang): body.nightMode -> SLR night palette AND container counter-inversion", async () => {
  ensurePayload();
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-fix4-pal1") });
  // SLR night mode is just a body class (www/core/nightMode/main.js:34).
  page.document.body.classList = { contains: (c) => c === "nightMode" };
  globalThis.__unityBackendNext = emptyScan();
  await loadAddon(page);
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  const modal = page._els["slr-unity-ask"];
  assert.ok(modal, "help offer modal shown");
  assert.match(modal.style.cssText, /background:rgba\(0,0,0,0\.6\)/, "night backdrop");
  // e2e-fix5 (flashbang): SLR night mode is html{filter:invert(92%)}
  // (!nightMode.css:1) — the dark-authored modal must counter-invert itself
  // exactly like SLR's own dark-authored #loadingOverlay (!nightMode.css:88).
  // The container carries the filter; the wave-4 palette stays final-authored.
  assert.match(modal.style.cssText, /filter:invert\(100%\)/,
    "modal container carries the #loadingOverlay-precedent counter-inversion");
  const box = modal.children[0];
  assert.match(box.style.cssText, /background:#262626/, "SLR night panel color (kept)");
  assert.match(box.style.cssText, /border:1px solid #3d4a5c/, "SLR night header border (kept)");
  assert.match(box.style.cssText, /color:#eee/, "night text color (kept)");
  assert.doesNotMatch(box.style.cssText, /filter:/, "only the CONTAINER is counter-inverted, not the box");
  const btns = modalButtons(modal);
  assert.match(btns[0].style.cssText, /background:#3d4a5c/, "buttons carry the night palette");
  assert.match(btns[1].style.cssText, /background:#3d4a5c/);
});

test("unityAsk palette (e2e-fix4 + e2e-fix5): no nightMode class -> unchanged light-world palette, NO filter", async () => {
  ensurePayload();
  const page = freshScenario(); // fake body has no classList -> detection falls back to light
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-fix4-pal2") });
  globalThis.__unityBackendNext = emptyScan();
  await loadAddon(page);
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  const modal = page._els["slr-unity-ask"];
  assert.ok(modal, "help offer modal shown");
  assert.match(modal.style.cssText, /background:rgba\(0,0,0,0\.55\)/, "unchanged backdrop");
  assert.doesNotMatch(modal.style.cssText, /filter:/, "light world must not carry any inversion filter");
  const box = modal.children[0];
  assert.match(box.style.cssText, /background:#252525/, "unchanged box");
  assert.doesNotMatch(box.style.cssText, /#3d4a5c/, "no night border in the light world");
  const btns = modalButtons(modal);
  assert.doesNotMatch(btns[0].style.cssText, /background:/, "default (browser) button look kept");
  assert.doesNotMatch(btns[1].style.cssText, /background:/);
});

// ---------------------------------------------------------------------------
// e2e-fix5: the Tools-menu registration was DEAD (no #mainMenu element exists
// in any SLR html — ui.mainMenu.addChild appends into an empty jQuery set and
// silently no-ops) and is REMOVED. Its replacement is a visible "Open
// documentation" link on the addon's own New Project slide, wired to the
// shared openHelp — which prefers the v3.078 offline docs system
// (docsHelp.open("addon-SLRUnity")) and falls back to
// assets/help.html.
// ---------------------------------------------------------------------------

test("slide help control (e2e-fix5): the dead mainMenu registration is gone; the slide carries an 'Open documentation' link", async () => {
  ensurePayload();
  const page = freshScenario();
  const $ = fakeJQuery();
  page.window = { jQuery: $ };
  const items = [];
  page.ui.mainMenu = { addChild: (menu, opts) => { items.push({ menu, opts }); return { on() {} }; } };
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    package: { version: "0.1.0" },
    getWebLocation: function () { return "/addons/SLRUnity"; },
  };
  await loadAddon(page, inst);
  assert.equal(items.length, 0, "no ui.mainMenu.addChild call at all (dead API, silently no-ops in SLR)");
  const link = $.registry.find((r) => r.sel.endsWith(".unityOpenDocs") && r.ev === "click");
  assert.ok(link, "an .unityOpenDocs click handler is wired on the slide");
  const menu = page.ui.menus[0];
  assert.ok(menu && menu.descriptionBar && menu.slides, "the New Project entry itself is still registered");
});

test("slide help control (e2e-fix5): clicking it opens assets/help.html when docsHelp is absent (older host)", async () => {
  ensurePayload();
  const page = freshScenario();
  const opened = [];
  page.nw = { Shell: { openExternal: (url) => opened.push(url) } }; // SLR's opener (common.js:4449)
  const $ = fakeJQuery();
  page.window = { jQuery: $ };
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    package: { version: "0.1.0" },
    getWebLocation: function () { return "/addons/SLRUnity"; },
  };
  await loadAddon(page, inst); // no page.docsHelp -> fallback path
  const link = $.registry.find((r) => r.sel.endsWith(".unityOpenDocs") && r.ev === "click");
  link.fn.call({}, { preventDefault() {} }); // click, event prevented
  assert.equal(opened.length, 1, "the shared openHelp fell through to nw.Shell.openExternal");
  const expect = "file:///" + path.join(SANDBOX, "assets", "help.html").split("\\").join("/");
  assert.equal(opened[0], expect);
});

test("openHelp (e2e-fix5): prefers the v3.078 docs system — docsHelp.open('addon-SLRUnity')", async () => {
  ensurePayload();
  const page = freshScenario();
  const opened = [];
  page.nw = { Shell: { openExternal: (url) => opened.push(url) } }; // must stay untouched
  page.docsHelp = { open: (pageId, anchor) => opened.push(["docs", pageId, anchor]) }; // www/js/docsHelp.js surface
  const $ = fakeJQuery();
  page.window = { jQuery: $ };
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    package: { version: "0.1.0" },
    getWebLocation: function () { return "/addons/SLRUnity"; },
  };
  await loadAddon(page, inst);
  const link = $.registry.find((r) => r.sel.endsWith(".unityOpenDocs") && r.ev === "click");
  link.fn.call({}, { preventDefault() {} });
  assert.deepEqual(opened, [["docs", "addon-SLRUnity", undefined]],
    "docsHelp.open got the addon page id (addon-<folder>, www/js/addonDocs.js) — no help.html fallback");
});

test("openHelp (e2e-fix5): the first-run offer's accept routes through docsHelp when present", async () => {
  ensurePayload();
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath: path.join(TMP, "c-fix5-help") });
  const calls = [];
  page.nw = { Shell: { openExternal: () => { throw new Error("help.html fallback must not run"); } } };
  page.docsHelp = { open: (pageId) => calls.push(pageId) };
  await loadAddon(page);
  globalThis.__unityBackendNext = emptyScan();
  await page.reg.handlers.unity.createProject({ selectedFile: FAKE_GAME, gameFolder: path.dirname(FAKE_GAME), gameTitle: "Fake", projectId: "pid" });
  const modal = page._els["slr-unity-ask"];
  assert.ok(modal, "help offer modal shown");
  assert.match(modalText(modal), /First Unity project/);
  modalButtons(modal)[1].click(); // "Open help page"
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(calls, ["addon-SLRUnity"], "accept opened the docs page via docsHelp");
});

// ---------------------------------------------------------------------------
// e2e-fix6: the decoupled image (SEP) workflow. The New Project slide wires
// THREE actions; the standalone runners own the loading-overlay lifecycle
// (showLoading -> loadingProgress lines -> loadingEnd EXACTLY once, from the
// finally block — backend rejection included) and use temp jobs dirs; insert
// guards on unity_img_manifest.json before any backend call.
// ---------------------------------------------------------------------------

// Shared driver: load the addon with a fake-jQuery page and the menu
// instance, then hand back the helpers tests need to poke the slide.
async function loadSlide() {
  const page = freshScenario();
  const $ = fakeJQuery();
  page.window = { jQuery: $ };
  // e2e-fix9: the page-global DVField instance (ui.js:6385) the slide's
  // dvSelectPath inputs are rendered through — a recorder here so tests can
  // assert renderSelectPath ran over the right collection.
  page.dvRenderCalls = [];
  page.ui.dvField = {
    renderSelectPath: function ($elm) { page.dvRenderCalls.push(String(($elm && $elm.sel) || "")); },
  };
  const inst = {
    fullPath: SANDBOX, basePath: "C:/addons",
    package: { version: "0.1.0" },
    getWebLocation: function () { return "/addons/SLRUnity"; },
  };
  await loadAddon(page, inst);
  const click = (sel) => {
    const rec = $.registry.find((r) => r.sel.endsWith(sel) && r.ev === "click");
    assert.ok(rec, "click handler wired: " + sel);
    return rec.fn;
  };
  // Each unitySelectDir() call mints ONE runtime picker element; drive them
  // strictly sequentially and always take the NEWEST one.
  const pickFolder = async (sel, value) => {
    const before = $.elms.filter(isRuntimePicker).length;
    const flow = click(sel).call({});
    await new Promise((r) => setTimeout(r, 0)); // let the picker be created
    const pickers = $.elms.filter(isRuntimePicker);
    assert.equal(pickers.length, before + 1, "exactly one new picker for " + sel);
    const picker = pickers[pickers.length - 1];
    picker.value = value;
    picker.trigger("input"); // user picked / cancelled
    await flow;
  };
  // e2e-fix9 (issue 2): the four insert-tool pickers are dvSelectPath inputs
  // now — DVField's "..." button writes the picked path into the input and
  // fires "change", so tests drive the same contract: set value + change.
  const setPick = (sel, value) => {
    const rec = $.registry.find((r) => r.sel.endsWith(sel) && r.ev === "change");
    assert.ok(rec, "change handler wired: " + sel);
    const input = $.elms.filter((e) => e.sel.endsWith(sel) && typeof e.trigger === "function").pop();
    assert.ok(input, "dvSelectPath input present: " + sel);
    input.value = value;
    input.trigger("change");
  };
  return { page, $, click, pickFolder, setPick };
}

test("slide (e2e-fix9): three action sections — strings project, standalone extract, insert with four dvSelectPath pickers", async () => {
  ensurePayload();
  const { page, $, click } = await loadSlide();
  assert.equal(page.ui.menus.length, 1, "exactly one menu entry");
  assert.ok(page.ui.menus[0].slides.unityTranslator, "slide registered");
  // (a) the strings-project action kept its wiring
  assert.ok(click(".unitySelectFolder"), "(a) unitySelectFolder");
  // (b) the standalone extraction action
  assert.ok(click(".unityExtractTextures"), "(b) unityExtractTextures");
  // (c) run button + inline font checkbox + run wiring
  assert.ok(click(".unityInsertRun"), "(c) run button");
  assert.ok($.elms.some((e) => e.sel.endsWith(".unityInsertFont")), "(c) inline font checkbox on the slide");
  // (c) e2e-fix9: the four pickers are SLR-native dvSelectPath inputs in the
  // markup, wired on "change" (DVField's "..." button fires it), and rendered
  // through the page-global ui.dvField.renderSelectPath.
  const markup = page.ui.menus[0].slides.unityTranslator.sel;
  assert.equal((markup.match(/type="dvSelectPath"/g) || []).length, 4,
    "four dvSelectPath inputs in the slide markup");
  assert.equal((markup.match(/nwdirectory/g) || []).length, 4, "directory-picking semantics on all four");
  for (const which of ["game", "extract", "rendered", "out"])
    assert.ok(markup.includes('data-unity-pick="' + which + '"'), "data-unity-pick=" + which);
  for (const sel of [".unityPickGame", ".unityPickExtract", ".unityPickRendered", ".unityPickOut"])
    assert.ok($.registry.some((r) => r.sel.endsWith(sel) && r.ev === "change"),
      "change handler wired: " + sel);
  assert.equal($.registry.some((r) => r.sel.endsWith(".unityPickGame") && r.ev === "click"), false,
    "the old hand-rolled picker buttons are gone (no click handlers)");
  assert.equal(page.dvRenderCalls.length, 1, "renderSelectPath invoked once");
  assert.ok(page.dvRenderCalls[0].endsWith("[type=dvSelectPath]"),
    "rendered over the slide's dvSelectPath collection");
});

test("standalone extract (e2e-fix6): drives its own overlay — showLoading once, loadingEnd EXACTLY once, temp jobs dir", async () => {
  ensurePayload();
  const { page, pickFolder } = await loadSlide();
  await pickFolder(".unityExtractTextures", FAKE_GAME);
  // The overlay lifecycle the createProjectFromEngine flow owns
  // (trans-full.js:641-683): showLoading -> console lines -> loadingEnd.
  assert.equal(page.ui.showLoadingCalls, 1, "ui.showLoading driven by the addon itself");
  assert.equal(page.ui.loadingEndCalls, 1, "ui.loadingEnd exactly once (the true end)");
  const ex = globalThis.__unityBackendCalls.find((c) => c.job.command === "extract_textures");
  assert.ok(ex, "extract_textures backend job started");
  assert.equal(ex.job.version, 2, "contract v2 manifest (backend rejects v1)");
  assert.equal(ex.job.game_dir, FAKE_GAME, "extraction targets the resolved game root");
  assert.equal(ex.job.out_dir, siblingDirOf(FAKE_GAME), "<Game>_unity_img next to the game");
  assert.ok(ex.jobsDir.startsWith(path.join(os.tmpdir(), "slr-unity-img-")),
    "jobs dir is a fresh temp dir under os.tmpdir(), not a project staging cache: " + ex.jobsDir);
  // Progress lines: "Unity textures" title, rmrgss-style done/total line,
  // consoleOutput opts — and nothing routed through the project-flow router.
  const tex = page.ui.loadingCalls.filter((c) => c.title === "Unity textures");
  assert.ok(tex.length >= 2, "start note + per-file line");
  assert.match(tex[1].text, /^1\/2 {2}level0$/, "per-file console line");
  for (const c of page.ui.loadingCalls)
    assert.deepEqual(c.opts, { consoleOnly: false, mode: "consoleOutput" }, "consoleOutput opts on every line");
  assert.equal(page._els["slr-unity-progress"], undefined, "no bottom-right bubble — the overlay IS the channel");
  assert.equal(page.ui.dialogClosed, 1, "dialog closed before the run");
  // e2e-fix10: the completion summary is CONSOLE LINES + loadingEnd LAST (no
  // success alert) — output folder + the numbered 4-step SEP round-trip.
  const done = page.ui.loadingCalls.filter((c) => c.title === "Unity textures").map((c) => c.text);
  assert.ok(done.some((t) => /Extraction complete/.test(t)), "summary heading line");
  assert.ok(done.some((t) => /output: /.test(t)), "output line");
  assert.ok(done.some((t) => /1\) Delete the images you don't want translated/.test(t)));
  assert.ok(done.some((t) => /2\) Create an SEP project on that folder/.test(t)));
  assert.ok(done.some((t) => /3\) Translate the images in SEP/.test(t)));
  assert.ok(done.some((t) => /4\) Come back here/.test(t)));
  assert.equal(page.alerts.length, 0, "no success alert — the overlay log IS the report");
  assert.equal(globalThis.__unitySeq[globalThis.__unitySeq.length - 1], "ui:loadingEnd",
    "loadingEnd is the FINAL event — Close appears on the finished log");
});

test("standalone extract (e2e-fix6): backend rejection -> failure alert, loadingEnd STILL exactly once (finally)", async () => {
  ensurePayload();
  const { page, pickFolder } = await loadSlide();
  globalThis.__unityBackendNext = { reject: "backend produced no result file" };
  await pickFolder(".unityExtractTextures", FAKE_GAME);
  assert.equal(page.ui.showLoadingCalls, 1);
  assert.equal(page.ui.loadingEndCalls, 1, "loadingEnd ran despite the rejection — finally block");
  assert.equal(globalThis.__unityBackendCalls.filter((c) => c.job.command === "extract_textures").length, 1);
  const msg = page.alerts[page.alerts.length - 1];
  assert.match(msg, /Texture extraction failed/);
  assert.match(msg, /no result file/);
  assert.match(msg, /backend\.log/);
});

test("standalone extract (e2e-fix6): non-Unity pick alerts, dialog stays open, no backend call", async () => {
  ensurePayload();
  const { page, pickFolder } = await loadSlide();
  const nowhere = path.join(TMP, "not-a-game-either");
  fs.mkdirSync(path.join(nowhere, "sub"), { recursive: true });
  await pickFolder(".unityExtractTextures", nowhere);
  assert.match(page.alerts[0], /No Unity game found/);
  assert.equal(page.ui.dialogClosed, 0, "dialog stays open so the user can re-pick");
  assert.equal(page.ui.showLoadingCalls, 0, "no overlay for a failed pick");
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("standalone insert (e2e-fix6): builds the patch manifest (empty temp translations/index, texture dirs, font) + own overlay", async () => {
  ensurePayload();
  const extractDir = path.join(TMP, "img-extract");
  fs.mkdirSync(extractDir, { recursive: true });
  fs.writeFileSync(path.join(extractDir, "unity_img_manifest.json"), "{}", "utf-8");
  const renderedDir = path.join(TMP, "img-rendered");
  fs.mkdirSync(renderedDir, { recursive: true });
  const outDir = path.join(TMP, "img-out");
  const { page, $, click, setPick } = await loadSlide();
  setPick(".unityPickGame", FAKE_GAME);
  setPick(".unityPickExtract", extractDir);
  setPick(".unityPickRendered", renderedDir);
  setPick(".unityPickOut", outDir);
  // picked paths are shown on the slide (e2e-fix9: via the change -> hint update)
  for (const [cls, val] of [[".unityPickGamePath", FAKE_GAME], [".unityPickExtractPath", extractDir],
    [".unityPickRenderedPath", renderedDir], [".unityPickOutPath", outDir]]) {
    const span = $.elms.filter((e) => e.sel.endsWith(cls)).pop();
    assert.ok(span.texts && span.texts.length > 0, cls + " got the picked path");
    assert.equal(span.texts[span.texts.length - 1], val, cls + " shows the picked path verbatim");
  }
  // tick the inline font checkbox (element captured at wiring time)
  $.elms.find((e) => e.sel.endsWith(".unityInsertFont")).checked = true;
  globalThis.__unityBackendNext = { ok: true, version: 2, copied: 74, textures_imported: 5, fonts_swapped: 2 };
  await click(".unityInsertRun").call({});
  assert.equal(page.ui.dialogClosed, 1, "dialog closed on run");
  assert.equal(page.ui.showLoadingCalls, 1);
  assert.equal(page.ui.loadingEndCalls, 1);
  const call = globalThis.__unityBackendCalls.find((c) => c.job.command === "patch");
  assert.ok(call, "patch job started");
  const job = call.job;
  assert.equal(job.version, 2, "contract v2 manifest (backend rejects v1)");
  assert.equal(job.patch_mode, undefined, "insert tool keeps the full-copy behavior (no patch_mode)");
  assert.equal(job.game_dir, FAKE_GAME, "game root resolved from the game pick");
  assert.equal(job.out_dir, outDir, "user-picked output folder");
  assert.equal(job.textures_manifest_dir, extractDir);
  assert.equal(job.textures_dir, renderedDir);
  assert.equal(job.font_ttf, SLR_FONT, "font stage armed by the slide checkbox");
  assert.ok(call.jobsDir.startsWith(path.join(os.tmpdir(), "slr-unity-insert-")), "temp jobs dir");
  // The two EMPTY temp json files the contract requires ({} + {})
  assert.deepEqual(JSON.parse(fs.readFileSync(job.translations_path, "utf-8")), {}, "empty translations");
  assert.deepEqual(JSON.parse(fs.readFileSync(job.index_path, "utf-8")), {}, "empty index");
  assert.ok(path.dirname(job.translations_path) === call.jobsDir, "temp files live in the jobs dir");
  // "Unity images" console lines through the addon-owned overlay
  const lines = page.ui.loadingCalls.filter((c) => c.title === "Unity images");
  assert.ok(lines.length >= 2, "start note + per-file line");
  assert.match(lines[1].text, /^1\/2 {2}level0$/);
  // e2e-fix10: completion summary as console lines + loadingEnd LAST.
  const done = page.ui.loadingCalls.filter((c) => c.title === "Unity images").map((c) => c.text);
  assert.ok(done.some((t) => /Insert complete/.test(t)), "summary heading line");
  assert.ok(done.some((t) => t.indexOf(outDir) !== -1 && /output: /.test(t)), "output line names the folder");
  assert.ok(done.some((t) => /textures imported: 5/.test(t)));
  assert.ok(done.some((t) => /fonts swapped: 2/.test(t)));
  assert.equal(page.alerts.length, 0, "no success alert — the overlay log IS the report");
  assert.equal(globalThis.__unitySeq[globalThis.__unitySeq.length - 1], "ui:loadingEnd",
    "loadingEnd is the FINAL event (after the summary lines)");
});

test("standalone insert (e2e-fix6): font unchecked -> no font_ttf, no fonts_swapped line", async () => {
  ensurePayload();
  const extractDir = path.join(TMP, "img-extract2");
  fs.mkdirSync(extractDir, { recursive: true });
  fs.writeFileSync(path.join(extractDir, "unity_img_manifest.json"), "{}", "utf-8");
  const renderedDir = path.join(TMP, "img-rendered2");
  fs.mkdirSync(renderedDir, { recursive: true });
  const { page, click, setPick } = await loadSlide();
  setPick(".unityPickGame", FAKE_GAME);
  setPick(".unityPickExtract", extractDir);
  setPick(".unityPickRendered", renderedDir);
  setPick(".unityPickOut", path.join(TMP, "img-out2"));
  globalThis.__unityBackendNext = { ok: true, version: 2, copied: 74, textures_imported: 3, fonts_swapped: 9 };
  await click(".unityInsertRun").call({});
  const job = globalThis.__unityBackendCalls.find((c) => c.job.command === "patch").job;
  assert.equal(job.font_ttf, undefined, "checkbox unchecked -> font stage skipped");
  const done = page.ui.loadingCalls.filter((c) => c.title === "Unity images").map((c) => c.text);
  assert.ok(done.some((t) => /textures imported: 3/.test(t)));
  assert.equal(done.some((t) => /fonts swapped/.test(t)), false,
    "payload fonts_swapped not surfaced when the stage was not armed");
});

test("standalone insert (e2e-fix6): missing unity_img_manifest.json -> alert + abort BEFORE any backend call", async () => {
  ensurePayload();
  const extractDir = path.join(TMP, "img-nomanifest"); // exists, no manifest
  fs.mkdirSync(extractDir, { recursive: true });
  const renderedDir = path.join(TMP, "img-rendered3");
  fs.mkdirSync(renderedDir, { recursive: true });
  const { page, click, setPick } = await loadSlide();
  setPick(".unityPickGame", FAKE_GAME);
  setPick(".unityPickExtract", extractDir);
  setPick(".unityPickRendered", renderedDir);
  setPick(".unityPickOut", path.join(TMP, "img-out3"));
  await click(".unityInsertRun").call({});
  assert.equal(globalThis.__unityBackendCalls.length, 0, "no backend call");
  assert.equal(page.ui.showLoadingCalls, 0, "no overlay either");
  assert.equal(page.ui.dialogClosed, 0, "dialog stays open so the user can re-pick");
  assert.match(page.alerts[0], /No unity_img_manifest\.json found in:/);
  assert.match(page.alerts[0], new RegExp(extractDir.replace(/\\/g, "\\\\")));
});

test("standalone insert (e2e-fix6): incomplete picks -> pick-all-four alert, nothing runs", async () => {
  ensurePayload();
  const { page, click, setPick } = await loadSlide();
  setPick(".unityPickGame", FAKE_GAME); // only one of four
  await click(".unityInsertRun").call({});
  assert.match(page.alerts[0], /Pick all four folders first/);
  assert.equal(globalThis.__unityBackendCalls.length, 0);
  assert.equal(page.ui.dialogClosed, 0);
});

// e2e-fix9 (issue 2): a cleared input restores the row hint and re-blocks the run.
test("standalone insert (e2e-fix9): clearing a picker input restores its hint, run guard re-blocks", async () => {
  ensurePayload();
  const extractDir = path.join(TMP, "img-clear");
  fs.mkdirSync(extractDir, { recursive: true });
  fs.writeFileSync(path.join(extractDir, "unity_img_manifest.json"), "{}", "utf-8");
  const { page, $, click, setPick } = await loadSlide();
  setPick(".unityPickGame", FAKE_GAME);
  setPick(".unityPickExtract", extractDir);
  setPick(".unityPickRendered", path.join(TMP, "img-clear-rendered"));
  setPick(".unityPickOut", path.join(TMP, "img-clear-out"));
  setPick(".unityPickGame", ""); // user wiped the field (it renders as a text input)
  const hint = $.elms.filter((e) => e.sel.endsWith(".unityPickGamePath")).pop();
  assert.equal(hint.texts[hint.texts.length - 1], "(not selected)", "hint restored on empty");
  await click(".unityInsertRun").call({});
  assert.match(page.alerts[0], /Pick all four folders first/, "empty pick re-blocks the run");
  assert.equal(globalThis.__unityBackendCalls.length, 0);
});

test("standalone insert (e2e-fix6): backend ok:false -> failure alert with log path, loadingEnd still exactly once", async () => {
  ensurePayload();
  const extractDir = path.join(TMP, "img-extract4");
  fs.mkdirSync(extractDir, { recursive: true });
  fs.writeFileSync(path.join(extractDir, "unity_img_manifest.json"), "{}", "utf-8");
  const renderedDir = path.join(TMP, "img-rendered4");
  fs.mkdirSync(renderedDir, { recursive: true });
  const { page, click, setPick } = await loadSlide();
  setPick(".unityPickGame", FAKE_GAME);
  setPick(".unityPickExtract", extractDir);
  setPick(".unityPickRendered", renderedDir);
  setPick(".unityPickOut", path.join(TMP, "img-out4"));
  globalThis.__unityBackendNext = { ok: false, version: 2, error: "ValueError: out_dir nested in game_dir" };
  await click(".unityInsertRun").call({});
  assert.equal(page.ui.loadingEndCalls, 1, "loadingEnd exactly once on the failure path too");
  const msg = page.alerts[page.alerts.length - 1];
  assert.match(msg, /Inserting translated images failed/);
  assert.match(msg, /out_dir nested in game_dir/);
  assert.match(msg, /backend\.log/);
});

test("slide strings action (e2e-fix6): (a) is unchanged — picker -> close -> ui.startProject(picked)", async () => {
  ensurePayload();
  const { page, pickFolder } = await loadSlide();
  await pickFolder(".unitySelectFolder", "C:/Games/MyGame");
  assert.equal(page.ui.dialogClosed, 1);
  assert.deepEqual(page.ui.startProjectCalls, ["C:/Games/MyGame"]);
  assert.equal(globalThis.__unityBackendCalls.length, 0, "the slide action only STARTS the project");
});

test("slide strings action (e2e-fix6): cancelled picker closes nothing, starts nothing", async () => {
  ensurePayload();
  const { page, pickFolder } = await loadSlide();
  await pickFolder(".unitySelectFolder", ""); // cancelled
  assert.equal(page.ui.dialogClosed, 0);
  assert.equal(page.ui.startProjectCalls.length, 0);
});

// ---------------------------------------------------------------------------
// e2e-fix9 (issue 1) / e2e-fix10: EXPORT OVERLAY LIFECYCLE. The export handler
// owns the standard loading-overlay lifecycle exactly the way
// createProjectFromEngine does (trans-full.js:641-683): showLoading + a
// "Processing" preamble at the very start, backend progress lines into the
// overlay console ("Unity export" phase title, done/total + basename), and
// ui.loadingEnd() EXACTLY ONCE on EVERY path — success, backend failure,
// backend rejection, and all synchronous guard paths alike. e2e-fix10 closes
// the gap the old `finally` around runBackend left: the overlay stays up
// through the revert copies and the zip step, post-backend lines (revert
// phase, warnings, the SUMMARY — which replaces the old success alert) go
// through ui.log into the same console, and loadingEnd is the FINAL event so
// Close only appears on the finished log. The ordering is asserted through
// the __unitySeq log (ui:showLoading / ui:progress:* / ui:log:* / backend:* /
// ui:loadingEnd / alert:* entries): no alert entry may precede the single
// ui:loadingEnd, no console line may follow it, and the success path alerts
// nothing at all.
// ---------------------------------------------------------------------------

// Shared assertion: the overlay contract on a finished export scenario.
function assertOverlayContract(page, seq, expectAlerts) {
  assert.equal(page.ui.showLoadingCalls, 1, "showLoading exactly once at handler start");
  assert.equal(page.ui.loadingEndCalls, 1, "loadingEnd exactly once on this path");
  assert.equal(seq[0], "ui:showLoading", "the overlay is up FIRST");
  const preamble = page.ui.loadingCalls.find((c) => c.title === "Processing");
  assert.ok(preamble && /Unity export/.test(preamble.text), "'Processing' preamble (createProjectFromEngine style)");
  assert.deepEqual(preamble.opts, { consoleOnly: false, mode: "consoleOutput" });
  const end = seq.indexOf("ui:loadingEnd");
  assert.ok(end !== -1, "loadingEnd logged");
  // e2e-fix10: every ui.log console line precedes loadingEnd — the log is
  // complete when Close appears (nothing may be appended to a closed run).
  const logIdxs = seq.map((e, i) => (e.startsWith("ui:log:") ? i : -1)).filter((i) => i !== -1);
  const lastLog = logIdxs.length ? logIdxs[logIdxs.length - 1] : -1;
  assert.ok(lastLog === -1 || lastLog < end,
    "console lines (revert/summary) only while the overlay is up, BEFORE loadingEnd (seq: " + seq.join(" | ") + ")");
  if (expectAlerts) {
    const firstAlert = seq.findIndex((e) => e.startsWith("alert:"));
    assert.ok(firstAlert > end,
      "every alert fires only AFTER loadingEnd (seq: " + seq.join(" | ") + ")");
    // No progress line may arrive after the overlay closed either.
    const progIdxs = seq.map((e, i) => (e.startsWith("ui:progress:") ? i : -1)).filter((i) => i !== -1);
    const lastProgress = progIdxs.length ? progIdxs[progIdxs.length - 1] : -1;
    assert.ok(lastProgress === -1 || lastProgress < end, "progress lines only while the overlay is up");
  } else {
    assert.equal(seq.some((e) => e.startsWith("alert:")), false, "no alert at all on this path");
  }
}

test("export overlay (e2e-fix10): success — NO alert, revert + summary as console lines, loadingEnd is the FINAL event", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fx9-ok");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) }; // overlay up -> exportLine takes the ui.log path
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 3, exported_files: ["FakeGame_Data/level0"] };
  await loadAddon(page);
  const outDir = path.join(TMP, "out-fx9");
  const out = await page.reg.handlers.unity.exportHandler(outDir, { mode: "dir", custom: {} });
  assert.ok(out && out.halt === true);
  const seq = globalThis.__unitySeq.slice();
  assertOverlayContract(page, seq, false);
  // (b): the per-file backend progress reached the overlay console under the
  // "Unity export" phase title, rmrgss-style done/total + basename.
  const prog = page.ui.loadingCalls.filter((c) => c.title === "Unity export");
  assert.ok(prog.length >= 1, "backend progress routed into the overlay console");
  assert.match(prog[0].text, /^1\/2 {2}level0$/, "done/total + basename line");
  assert.deepEqual(prog[0].opts, { consoleOnly: false, mode: "consoleOutput" });
  // Ordering across the whole run: showLoading -> preamble -> backend spawn
  // -> progress -> revert copies (console lines) -> summary -> loadingEnd LAST.
  const idx = (e) => seq.indexOf(e);
  assert.ok(idx("backend:patch") > idx("ui:progress:Processing"), "backend started after the preamble");
  assert.ok(idx("ui:progress:Unity export") > idx("backend:patch"), "progress line during the backend run");
  assert.equal(seq[seq.length - 1], "ui:loadingEnd",
    "loadingEnd is the FINAL event — Close appears only on the finished log");
  // e2e-fix10: the revert phase logs per file BEFORE copying; the copy really
  // happened (revert ordering is asserted file-wise below via logCalls).
  const logs = page.ui.logCalls;
  const revertAt = logs.findIndex((t) => /Including original/.test(t));
  const summaryAt = logs.findIndex((t) => /Export complete/.test(t));
  assert.ok(revertAt !== -1, "the revert copy announced itself in the console");
  assert.match(logs[revertAt], /Including original 1\/1 {2}FakeGame_Data\/il2cpp_data\/Metadata\/global-metadata\.dat/);
  assert.ok(summaryAt !== -1, "summary present");
  assert.ok(revertAt < summaryAt, "revert lines precede the summary");
  for (const want of ["Export complete", "mode: patch", "strings written: 3", "files exported: 1",
    "reverted (originals): 1", "output: " + outDir, "apply: copy the files over the game folder"])
    assert.ok(logs.some((t) => t.indexOf(want) !== -1), "summary line: " + want);
  // The overlay console took every line — no bottom-right bubble was created,
  // and the success path alerted NOTHING (the log replaces the alert).
  assert.equal(page._els["slr-unity-progress"], undefined, "no bubble fallback while the overlay is up");
  assert.equal(page.alerts.length, 0, "success alerts NOTHING — the console log is the report");
});

test("export overlay (e2e-fix9): backend ok:false — loadingEnd exactly once BEFORE the failure alert", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fx9-fail");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityBackendNext = { ok: false, version: 2, error: "ValueError: out_dir nested in game_dir" };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fx9f"), { mode: "zip", custom: {} });
  assert.ok(out, "still halts the PHP export");
  assertOverlayContract(page, globalThis.__unitySeq.slice(), true);
  assert.match(page.alerts[page.alerts.length - 1], /Unity patch failed/);
});

test("export overlay (e2e-fix9): backend rejection — loadingEnd still exactly once, rejection propagates, no alert ever", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fx9-rej");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityBackendNext = { reject: "spawn ENOENT" };
  await loadAddon(page);
  await assert.rejects(
    page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fx9r"), { mode: "dir", custom: {} }),
    /spawn ENOENT/);
  assertOverlayContract(page, globalThis.__unitySeq.slice(), false);
  assert.equal(page.ui.loadingCalls.some((c) => c.title === "Unity export"), true,
    "the per-file line still reached the overlay console before the rejection");
});

test("export overlay (e2e-fix9): guard paths — overlay up, closed exactly once BEFORE the guard alert", async () => {
  ensurePayload();
  // (a) missing unity metadata
  {
    const page = freshScenario();
    page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
    page.trans.project = { files: {}, options: {}, cache: { cachePath: path.join(TMP, "c-fx9-g1") } };
    await loadAddon(page);
    const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "dir" });
    assert.ok(out);
    assertOverlayContract(page, globalThis.__unitySeq.slice(), true);
    assert.match(page.alerts[0], /metadata missing/);
  }
  // (b) full-copy nothing-to-export guard
  {
    const page = freshScenario();
    page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
    const cachePath = path.join(TMP, "c-fx9-g2");
    fs.mkdirSync(path.join(cachePath, "data"), { recursive: true });
    page.trans.project = {
      cache: { cachePath },
      files: { "a": { data: [["こんにちは", ""]] } }, // untranslated
      options: { unity: { gameDir: "E:/Games/FakeGame" } },
    };
    await loadAddon(page);
    const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "dir", custom: { unityExportKind: "full" } });
    assert.ok(out);
    assertOverlayContract(page, globalThis.__unitySeq.slice(), true);
    assert.match(page.alerts[0], /Nothing to export in full game copy mode/);
  }
  // (c) missing scan index
  {
    const page = freshScenario();
    page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
    const cachePath = path.join(TMP, "c-fx9-g3"); // no data/index.json
    fs.mkdirSync(cachePath, { recursive: true });
    page.trans.project = makeProject(cachePath);
    await loadAddon(page);
    const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "dir", custom: {} });
    assert.ok(out);
    assertOverlayContract(page, globalThis.__unitySeq.slice(), true);
    assert.match(page.alerts[0], /Scan index missing/);
  }
  // (d) full copy with a missing original game folder
  {
    const page = freshScenario();
    page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
    const cachePath = path.join(TMP, "c-fx9-g4");
    fs.mkdirSync(path.join(cachePath, "data"), { recursive: true });
    fs.writeFileSync(path.join(cachePath, "data", "index.json"), "{}", "utf-8");
    page.trans.project = makeProject(cachePath, { gameDir: "E:/Games/DeletedGame" });
    await loadAddon(page);
    const out = await page.reg.handlers.unity.exportHandler("X:/out", { mode: "dir", custom: { unityExportKind: "full" } });
    assert.ok(out);
    assertOverlayContract(page, globalThis.__unitySeq.slice(), true);
    assert.match(page.alerts[0], /Full copy export needs the original game folder/);
  }
});

// e2e-fix10: a revert copy that FAILS ends the overlay first, then alerts
// with the failing file (the output written so far stays, partial).
test("export overlay (e2e-fix10): revert copy failure — loadingEnd exactly once BEFORE the failing-file alert, halt", async () => {
  ensurePayload();
  const { page, staged } = stageProject(TMP + "-fx10-revfail");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  // Backend exports level0 -> the revert list is global-metadata.dat only; a
  // FILE blocking its destination directory makes mkdirSync (recursive) throw.
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1,
    exported_files: ["FakeGame_Data/level0"] };
  await loadAddon(page);
  const outDir = path.join(TMP, "out-fx10-revfail");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, "FakeGame_Data"), "blocks the mkdir");
  const out = await page.reg.handlers.unity.exportHandler(outDir, { mode: "dir", custom: {} });
  assert.ok(out, "still halts the PHP export");
  assert.equal(page.ui.loadingEndCalls, 1, "loadingEnd exactly once");
  const seq = globalThis.__unitySeq.slice();
  const end = seq.indexOf("ui:loadingEnd");
  const firstAlert = seq.findIndex((e) => e.startsWith("alert:"));
  assert.ok(end !== -1 && firstAlert > end,
    "the alert fires only AFTER loadingEnd (seq: " + seq.join(" | ") + ")");
  assert.match(page.alerts[0], /could not copy the original FakeGame_Data\/il2cpp_data\/Metadata\/global-metadata\.dat/);
  assert.match(page.alerts[0], /partial export output/);
  // The source staged original is untouched; the per-file line for the failed
  // copy DID reach the console (before its copy attempt).
  assert.equal(fs.readFileSync(path.join(staged, "FakeGame_Data", "il2cpp_data", "Metadata", "global-metadata.dat"), "utf-8"),
    "STAGED-ORIGINAL:FakeGame_Data/il2cpp_data/Metadata/global-metadata.dat");
  assert.ok(page.ui.logCalls.some((t) => /Including original 1\/1 {2}FakeGame_Data\/il2cpp_data/.test(t)),
    "the failing copy was announced before it failed");
});

// e2e-fix9 (issue 1) + e2e-fix10: the deferred font warning — the export
// resolves the font non-alerting (the overlay is up) and the notice is now a
// console WARNING line in the same overlay log as the summary, BEFORE it.
test("export overlay (e2e-fix10): missing SLRCondensedCJK -> WARNING console line before the summary, no alert at all", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fx9-font");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityRootDir = path.join(TMP, "slr-root-empty");
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 2 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fx9font"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(out);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_ttf, undefined, "font stage skipped");
  assertOverlayContract(page, globalThis.__unitySeq.slice(), false);
  const logs = page.ui.logCalls;
  const warnAt = logs.findIndex((t) => /WARNING: SLRCondensedCJK font not found/.test(t));
  const summaryAt = logs.findIndex((t) => /Export complete/.test(t));
  assert.ok(warnAt !== -1, "the font notice is a console WARNING line");
  assert.ok(summaryAt !== -1 && warnAt < summaryAt, "the warning precedes the summary");
  assert.ok(logs.some((t) => /strings written: 2/.test(t)), "summary content intact");
  assert.equal(page.alerts.length, 0, "no alert — warning and summary are console lines");
});

// ---------------------------------------------------------------------------
// e2e-fix19: BAKED TMP ATLASES. The swap checkbox now also sends font_bake —
// a role->TTF map the backend re-renders static TMP_FontAsset atlases with:
// CJK-charset assets (the JP dialogue font) bake from SLRCondensedCJK (so
// untranslated Japanese keeps rendering), everything else (the default UI
// font) from <SLR>/fonts/sucr.ttf (Stint Ultra Condensed). Missing sucr.ttf
// -> font_bake omitted + a deferred WARNING line; the legacy swap itself
// still runs off SLRCondensedCJK.
// ---------------------------------------------------------------------------

test("exportHandler (e2e-fix19): swap armed sends font_bake {latin_ttf: sucr, cjk_ttf: SLRCondensedCJK}", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fx19-bake");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 2,
    fonts_swapped: 3, fonts_baked: 2, fonts_bake_failed: 1 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fx19bake"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(out);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.deepEqual(job.font_bake, { latin_ttf: SLR_SUCR, cjk_ttf: SLR_FONT },
    "the role map points at the two host-app faces");
  assert.ok(path.isAbsolute(job.font_bake.latin_ttf) && path.isAbsolute(job.font_bake.cjk_ttf));
  const logs = page.ui.logCalls;
  assert.ok(logs.some((t) => /TMP font atlases re-rendered: 2/.test(t)),
    "bake summary line reports the baked count");
  assert.ok(logs.some((t) => /skipped: 1/.test(t)),
    "a non-zero skip count surfaces in the summary");
});

test("exportHandler (e2e-fix19): missing sucr.ttf -> font_bake omitted + WARNING, legacy swap still armed", async () => {
  ensurePayload();
  const cjkOnly = path.join(TMP, "slr-root-nosucr");
  fs.mkdirSync(path.join(cjkOnly, "www", "core", "SEP"), { recursive: true });
  fs.writeFileSync(path.join(cjkOnly, "www", "core", "SEP", "SLRCondensedCJK-Regular.ttf"), "cjk-only");
  const { page } = stageProject(TMP + "-fx19-nosucr");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityRootDir = cjkOnly;
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fx19nosucr"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(out);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_bake, undefined, "bake stage omitted without the Latin face");
  assert.equal(job.font_ttf,
    path.join(cjkOnly, "www", "core", "SEP", "SLRCondensedCJK-Regular.ttf"),
    "the legacy swap still runs off SLRCondensedCJK");
  const logs = page.ui.logCalls;
  assert.ok(logs.some((t) => /WARNING: Stint Ultra Condensed font not found/.test(t)),
    "the missing bake face is a deferred console WARNING");
  assert.ok(logs.some((t) => /WARNING: SLRCondensedCJK font not found/.test(t)) === false,
    "the CJK face IS present: only the bake warning appears");
});

test("exportHandler (e2e-fix19): swap off -> no font_bake, no bake warning", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-fx19-off");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1 };
  await loadAddon(page);
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-fx19off"), {
    mode: "dir", custom: {},
  });
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_bake, undefined);
  assert.equal(job.font_ttf, undefined);
  assert.equal(page.ui.logCalls.some((t) => /WARNING/.test(t)), false,
    "no font warnings with the swap off");
});

// ---------------------------------------------------------------------------
// v2.3 CUSTOM FONTS: the two export-option font slots (unityFontCjk /
// unityFontLatin, absolute .ttf/.otf paths, empty = SLR default). The export
// resolves each role independently — custom file when it exists, else the
// SLR default, else the stage skips with a deferred WARNING — and persists
// non-empty values into the addon config so the dialog prefills them.
// ---------------------------------------------------------------------------

// An Addon instance with a working config (mirrors www/js/addon.js:380-383:
// this.config = sys.config.addons.package[<name>]; setConfig persists).
function configInst() {
  const store = {};
  return {
    fullPath: SANDBOX, basePath: "C:/addons", config: store,
    getConfig(k) { return store[k]; },
    setConfig(k, v) { store[k] = v; },
  };
}

test("load (v2.4 rename): old SLRUnityTranslator package config migrates to SLRUnity", async () => {
  ensurePayload();
  // The per-package addon config is keyed by package.name (addon.js:380-383);
  // the rename would orphan it. The addon body carries the old key over once.
  const saved = [];
  globalThis.sys = {
    saveConfig() { saved.push(true); },
    config: { addons: { package: {
      "SLRUnityTranslator": { fontCjk: "C:/fonts/my.ttf", seenHelp: true },
      "SLRUnity": { fontLatin: "C:/fonts/ui.ttf" },  // already-written new key wins
    } } },
  };
  try {
    const page = freshScenario();
    const inst = configInst();
    inst.config = globalThis.sys.config.addons.package["SLRUnity"]; // addon.js:383 binding
    await loadAddon(page, inst);
    const pkgs = globalThis.sys.config.addons.package;
    assert.equal(pkgs["SLRUnityTranslator"], undefined, "stale key removed");
    assert.equal(pkgs["SLRUnity"].fontCjk, "C:/fonts/my.ttf", "old values carried over");
    assert.equal(pkgs["SLRUnity"].seenHelp, true, "old values carried over (2)");
    assert.equal(pkgs["SLRUnity"].fontLatin, "C:/fonts/ui.ttf", "existing new values not clobbered");
    assert.deepEqual(saved, [true], "config saved after migration");
    assert.equal(page.reg.handlers.unity && typeof page.reg.handlers.unity.exportHandler, "function");
  } finally {
    delete globalThis.sys;
  }
});

test("exportHandler (v2.3): custom fonts flow into font_ttf + font_bake and persist", async () => {
  ensurePayload();
  const cjkFont = path.join(TMP, "my-dialog-font.ttf");
  const latinFont = path.join(TMP, "my-ui-font.ttf");
  fs.writeFileSync(cjkFont, "fake-custom-cjk");
  fs.writeFileSync(latinFont, "fake-custom-latin");
  const { page } = stageProject(TMP + "-v23-both");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  const inst = configInst();
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1 };
  await loadAddon(page, inst);
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-v23both"), {
    mode: "dir",
    custom: { unitySwapFonts: "on", unityFontCjk: cjkFont, unityFontLatin: latinFont },
  });
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_ttf, cjkFont, "the custom dialogue font replaces SLRCondensedCJK");
  assert.deepEqual(job.font_bake, { latin_ttf: latinFont, cjk_ttf: cjkFont },
    "both atlas roles point at the custom fonts");
  assert.equal(inst.getConfig("fontCjk"), cjkFont, "persisted for the next dialog");
  assert.equal(inst.getConfig("fontLatin"), latinFont, "persisted for the next dialog");
  assert.equal(page.ui.logCalls.some((t) => /WARNING/.test(t)), false,
    "both custom files exist: no warnings");
});

test("exportHandler (v2.3): missing custom font falls back to the SLR default with a WARNING", async () => {
  ensurePayload();
  const ghost = path.join(TMP, "does-not-exist.ttf");
  const { page } = stageProject(TMP + "-v23-ghost");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  const inst = configInst();
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1 };
  await loadAddon(page, inst);
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-v23ghost"), {
    mode: "dir",
    custom: { unitySwapFonts: "on", unityFontCjk: ghost },
  });
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_ttf, SLR_FONT, "fell back to the host-app CJK face");
  assert.deepEqual(job.font_bake, { latin_ttf: SLR_SUCR, cjk_ttf: SLR_FONT },
    "the bake runs with the resolved faces");
  const logs = page.ui.logCalls;
  assert.ok(logs.some((t) => t.includes("WARNING: Custom SLRCondensedCJK font not found (" + ghost + ")")),
    "the fallback warning names the missing custom path");
  assert.ok(logs.some((t) => /Export complete/.test(t)), "export still completes");
});

test("exportHandler (v2.3): missing custom + missing SLR default skips only that stage", async () => {
  ensurePayload();
  const ghost = path.join(TMP, "gone.ttf");
  const { page } = stageProject(TMP + "-v23-none");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  globalThis.__unityRootDir = path.join(TMP, "slr-root-empty"); // no SLR fonts
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1 };
  await loadAddon(page, configInst());
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-v23none"), {
    mode: "dir",
    custom: { unitySwapFonts: "on", unityFontLatin: ghost },
  });
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_ttf, undefined, "no CJK face anywhere -> swap skipped");
  assert.equal(job.font_bake, undefined, "no Latin face anywhere -> bake skipped");
  const logs = page.ui.logCalls;
  assert.ok(logs.some((t) => t.includes("WARNING: SLRCondensedCJK font not found in SLR installation")),
    "the no-custom missing-default wording is unchanged");
  assert.ok(logs.some((t) => t.includes("WARNING: Custom Stint Ultra Condensed font not found (" + ghost + ")")),
    "the custom+default-missing warning names both");
  assert.ok(logs.some((t) => /strings written: 1/.test(t)), "strings still patched");
});

test("exportHandler (v2.3): blanks clear persisted font config; swap off never touches it", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-v23-clear");
  page.window = { jQuery: fakeJQuery({ loadingOverlay: true }) };
  const inst = configInst();
  inst.setConfig("fontCjk", path.join(TMP, "old.ttf"));
  inst.setConfig("fontLatin", path.join(TMP, "old2.ttf"));
  globalThis.__unityBackendNext = { ok: true, version: 2, strings_written: 1 };
  await loadAddon(page, inst);
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-v23clear"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.equal(inst.getConfig("fontCjk"), "", "empty field clears the stored path");
  assert.equal(inst.getConfig("fontLatin"), "");
  const callsBefore = globalThis.__unityBackendCalls.length;
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-v23off"), {
    mode: "dir", custom: {},
  });
  assert.equal(inst.getConfig("fontCjk"), "", "swap off leaves the config untouched");
  assert.equal(globalThis.__unityBackendCalls.length, callsBefore + 1);
});

// ---------------------------------------------------------------------------
// e2e-fix13: font files ride along (staging) + font_source_dir (export).
// The staged game only ever carried string-bearing files, so a patch-mode
// font swap silently missed fonts embedded in string-less files (the real
// game's resources.assets). Two halves:
//   (a) createProject stages scan.fonts[].file too (deduped, progress counts
//       them) — NEW projects are self-contained for font swaps;
//   (b) exportHandler sends font_source_dir (the ORIGINAL gameDir) when the
//       swap is armed and that folder still exists — EXISTING projects get a
//       complete swap too; missing/vanished gameDir falls back silently.
// ---------------------------------------------------------------------------

test("createProject (e2e-fix13): font-bearing files join the staging set (deduped, progress covers them)", async () => {
  ensurePayload();
  const gameRoot = path.join(TMP, "stage-font-game");
  fs.rmSync(gameRoot, { recursive: true, force: true });
  fs.mkdirSync(path.join(gameRoot, "Fake_Data"), { recursive: true }); // resolveGameRoot marker
  fs.writeFileSync(path.join(gameRoot, "Fake_Data", "globalgamemanagers"), "x");
  const stringRel = "Fake_Data/level0";             // string file — also a font file (dedupe case)
  const fontOnlyRel = "Fake_Data/resources.assets"; // font in a STRING-LESS file (the fix13 gap)
  fs.writeFileSync(path.join(gameRoot, stringRel), "ORIGINAL:level0");
  fs.writeFileSync(path.join(gameRoot, fontOnlyRel), "ORIGINAL:resources.assets");
  const index = {
    "あるテキスト": [
      { file: stringRel, abs: path.join(gameRoot, stringRel), path_id: 1, source: "raw", offset: 0, length: 1, key: "", tokens: [] },
    ],
  };
  const strings = [
    { file: "", path_id: 1, obj_type: "MonoBehaviour", name: "Dlg", source: "raw", offset: 0, length: 1, key: "", text: "あるテキスト", tokens: [] },
  ];
  const fonts = [
    { file: path.join(gameRoot, fontOnlyRel), path_id: 7, name: "PerfectDOSVGA437", font_type: "Font" },
    { file: path.join(gameRoot, stringRel), path_id: 9, name: "NotoSansCJKjp-Regular", font_type: "Font" }, // already staged as a string file
    { file: path.join(TMP, "outside.assets"), path_id: 3, name: "OutsideGameDir", font_type: "Font" },      // must never stage
  ];
  const cachePath = path.join(TMP, "c13stage");
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath });
  globalThis.__unityBackendNext = { ok: true, version: 2, index, strings, fonts, stats: { files_scanned: 1 } };
  await loadAddon(page);
  const proj = await page.reg.handlers.unity.createProject({ selectedFile: gameRoot, gameFolder: path.dirname(gameRoot), gameTitle: "Fake", projectId: "pid" });
  const stagedGameDir = path.join(cachePath, "data", "game");

  // the string-less font file is staged verbatim alongside the string file
  assert.equal(fs.readFileSync(path.join(stagedGameDir, fontOnlyRel), "utf-8"), "ORIGINAL:resources.assets",
    "string-less font-bearing file staged");
  assert.equal(fs.readFileSync(path.join(stagedGameDir, stringRel), "utf-8"), "ORIGINAL:level0",
    "string file staged as before");

  // project files stay STRING-scoped: the font file is not a row source
  assert.deepEqual(Object.keys(proj.files), [stringRel]);

  // progress covers the font file (sorted: level0 < resources.assets), and
  // the count is 2 — the shared file is NOT double-staged, the outside one
  // is not staged at all.
  const bub = page._els["slr-unity-progress"];
  assert.ok(bub.texts.some((t) => /Staging files 1\/2 {2}level0/.test(t)), "first staging line");
  assert.ok(bub.texts.some((t) => /Staging files 2\/2 {2}resources\.assets/.test(t)),
    "font file got its own staging progress line");
});

test("exportHandler (e2e-fix13): patch + font swap + existing original gameDir -> font_source_dir in the manifest", async () => {
  ensurePayload();
  // The original game folder must EXIST on disk (fsh.isDir gate).
  const origGame = path.join(TMP, "orig-game-present");
  fs.mkdirSync(origGame, { recursive: true });
  const { page } = stageProject(TMP + "-f13a", { gameDir: origGame });
  globalThis.__unityBackendNext = { ok: true, version: 2, fonts_swapped: 3 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-f13a"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.patch_mode, true);
  assert.equal(job.font_ttf, SLR_FONT, "swap armed -> font stage on");
  assert.equal(job.font_source_dir, origGame,
    "the ORIGINAL game dir rides along so the font stage sees the complete catalog");
});

test("exportHandler (e2e-fix13): missing or vanished gameDir -> silent staged-only fallback, export still runs", async () => {
  ensurePayload();
  // (a) gameDir blank (old project / metadata lost) -> staged-only behavior.
  const { page: p1 } = stageProject(TMP + "-f13b1", { gameDir: "" });
  globalThis.__unityBackendNext = { ok: true, version: 2 };
  await loadAddon(p1);
  const r1 = await p1.reg.handlers.unity.exportHandler(path.join(TMP, "out-f13b1"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(r1 && r1.halt === true);
  assert.equal(globalThis.__unityBackendCalls[0].job.font_source_dir, undefined,
    "no gameDir -> no font_source_dir");
  assert.equal(globalThis.__unityBackendCalls[0].job.font_ttf, SLR_FONT,
    "the font stage still runs against the staged game");
  assert.equal(p1.alerts.length, 0, "fallback is silent");

  // (b) gameDir points at a folder that no longer exists -> same fallback.
  const { page: p2 } = stageProject(TMP + "-f13b2", { gameDir: path.join(TMP, "vanished-game") });
  globalThis.__unityBackendNext = { ok: true, version: 2 };
  await loadAddon(p2);
  const r2 = await p2.reg.handlers.unity.exportHandler(path.join(TMP, "out-f13b2"), {
    mode: "dir", custom: { unitySwapFonts: "on" },
  });
  assert.ok(r2 && r2.halt === true);
  assert.equal(globalThis.__unityBackendCalls[0].job.font_source_dir, undefined,
    "vanished gameDir -> no font_source_dir");
  assert.equal(p2.alerts.length, 0, "fallback is silent");
});

test("exportHandler (e2e-fix13): full-copy mode never sends font_source_dir (in-place swap on the copy is complete)", async () => {
  ensurePayload();
  const origGame = path.join(TMP, "orig-game-full");
  fs.mkdirSync(origGame, { recursive: true });
  const { page } = stageProject(TMP + "-f13c", { gameDir: origGame });
  globalThis.__unityBackendNext = { ok: true, version: 2, fonts_swapped: 3 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-f13c"), {
    mode: "dir", custom: { unitySwapFonts: "on", unityExportKind: "full" },
  });
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.patch_mode, undefined, "full copy keeps v1 behavior");
  assert.equal(job.game_dir, origGame);
  assert.equal(job.font_source_dir, undefined,
    "full copy's in-place swap already reads the original — no font_source_dir key");
  assert.equal(job.font_ttf, SLR_FONT);
});

test("exportHandler (e2e-fix13): swap unchecked -> neither font key (font_source_dir absent even with gameDir)", async () => {
  ensurePayload();
  const origGame = path.join(TMP, "orig-game-nofont");
  fs.mkdirSync(origGame, { recursive: true });
  const { page } = stageProject(TMP + "-f13d", { gameDir: origGame });
  globalThis.__unityBackendNext = { ok: true, version: 2 };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-f13d"), {
    mode: "dir", custom: {},
  });
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.font_ttf, undefined);
  assert.equal(job.font_source_dir, undefined,
    "font_source_dir only appears when the swap is armed");
});

// ---------------------------------------------------------------------------
// v2.5 demosaic: the opt-in mosaic-censorship removal stage.
// ---------------------------------------------------------------------------

test("exportHandler (v2.5): demosaic option plumbing — job key, level, summary lines, log cap", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-dm1", {
    demosaicFound: [{ file: "FakeGame_Data/level0", path_id: "11", kind: "object",
                      name: "mosaic", confidence: "high", evidence: ["object name 'mosaic'"] }],
  });
  globalThis.__unityBackendNext = { ok: true, version: 2,
    demosaic_objects: 2, demosaic_renderers: 1, demosaic_materials: 3,
    demosaic_revealed: 2, demosaic_files: 2,
    demosaic_code_methods: 4, demosaic_code_types: 1, demosaic_code_files: 1,
    demosaic_code_log: ["stubbed MosaicController.Update (Assembly-CSharp.dll)"],
    demosaic_log: Array.from({ length: 25 }, (_v, i) => "hidden object 'mosaic' #" + i),
    exported_files: ["FakeGame_Data/level0"] };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dm1"), {
    mode: "dir",
    custom: { unityDemosaic: "on", unityDemosaicLevel: "medium",
              unityDemosaicReveal: "on", unityDemosaicCode: "on" },
  });
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.deepEqual(job.demosaic,
    { enabled: true, level: "medium", reveal: true, code: true },
    "the demosaic stage rides the patch manifest only when armed");
  // reveal + code summary lines
  const lines2 = exportConsoleLines(page);
  assert.ok(lines2.some((t) => /2 revealed \(display shader, textures kept\)/.test(t)),
    "revealed-materials summary line");
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /Demosaic: 2 object\(s\) hidden, 1 extra renderer\(s\) hidden, 3 material\(s\) neutered, 2 revealed \(display shader, textures kept\) in 2 file\(s\)/.test(t)),
    "summary counts line");
  assert.equal(lines.filter((t) => /^  hidden object/.test(t)).length, 20,
    "at most 20 per-action lines are shown");
  assert.ok(lines.some((t) => /… and 5 more \(backend\.log\)/.test(t)), "overflow hint");
  assert.ok(lines.some((t) => /Demosaic code: 4 censor method\(s\) stubbed across 1 type\(s\) in 1 managed DLL\(s\)/.test(t)),
    "code-stage summary line");
  assert.ok(lines.some((t) => /stubbed MosaicController\.Update/.test(t)),
    "code-stage action line");
});

test("exportHandler (v2.5b): demosaic on, tiers off -> level/reveal/code keys minimal; IL2CPP code note", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-dm1b");
  globalThis.__unityBackendNext = { ok: true, version: 2,
    demosaic_objects: 1, demosaic_renderers: 0, demosaic_materials: 1,
    demosaic_revealed: 0, demosaic_files: 1, demosaic_log: [],
    demosaic_code_methods: 0, demosaic_code_types: 0,
    demosaic_code_il2cpp: true, demosaic_code_log: [] };
  await loadAddon(page);
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dm1b"), {
    mode: "dir", custom: { unityDemosaic: "on" },  // tiers unchecked
  });
  const job = globalThis.__unityBackendCalls[0].job;
  assert.deepEqual(job.demosaic, { enabled: true, level: "high" },
    "unchecked tiers send no keys at all");
  const lines = exportConsoleLines(page);
  assert.ok(lines.some((t) => /compiles to native \(IL2CPP\)/.test(t)),
    "IL2CPP code tier reports not-patchable");
});

test("exportHandler (v2.5): demosaic unchecked -> no job key, no summary", async () => {
  ensurePayload();
  const { page } = stageProject(TMP + "-dm2");
  globalThis.__unityBackendNext = { ok: true, version: 2 };
  await loadAddon(page);
  await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dm2"), {
    mode: "dir", custom: {},
  });
  assert.equal(globalThis.__unityBackendCalls[0].job.demosaic, undefined);
  const lines = exportConsoleLines(page);
  assert.ok(!lines.some((t) => /Demosaic/.test(t)), "silent when off");
});

test("exportHandler (v2.5): demosaic armed but nothing found — found-nothing line, old-project warning on legacy projects", async () => {
  ensurePayload();
  // legacy project: no demosaicFound key at all (pre-v2.5 staging)
  const legacy = stageProject(TMP + "-dm3");
  globalThis.__unityBackendNext = { ok: true, version: 2,
    demosaic_objects: 0, demosaic_renderers: 0, demosaic_materials: 0,
    demosaic_files: 0, demosaic_log: [] };
  await loadAddon(legacy.page);
  await legacy.page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dm3"), {
    mode: "dir", custom: { unityDemosaic: "on" },
  });
  let lines = exportConsoleLines(legacy.page);
  assert.ok(lines.some((t) => /Demosaic: enabled, but no mosaic candidates/.test(t)));
  assert.ok(lines.some((t) => /predates mosaic scanning/.test(t)),
    "legacy project gets the re-create hint");

  // current project: demosaicFound present (possibly empty) -> no warning
  const cur = stageProject(TMP + "-dm3b", { demosaicFound: [] });
  globalThis.__unityBackendNext = { ok: true, version: 2,
    demosaic_objects: 0, demosaic_renderers: 0, demosaic_materials: 0,
    demosaic_files: 0, demosaic_log: [] };
  await loadAddon(cur.page);
  await cur.page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dm3b"), {
    mode: "dir", custom: { unityDemosaic: "on" },
  });
  lines = exportConsoleLines(cur.page);
  assert.ok(lines.some((t) => /no mosaic candidates/.test(t)));
  assert.ok(!lines.some((t) => /predates mosaic scanning/.test(t)),
    "current projects are not told to re-create");
});

test("exportHandler (v2.5): demosaic-only full copy lifts the nothing-to-export guard", async () => {
  ensurePayload();
  const origGame = path.join(TMP, "orig-game-dm");
  fs.mkdirSync(origGame, { recursive: true });
  const { page } = stageProject(TMP + "-dm4", { gameDir: origGame });
  globalThis.__unityBackendNext = { ok: true, version: 2,
    demosaic_objects: 5, demosaic_renderers: 0, demosaic_materials: 1,
    demosaic_files: 2, demosaic_log: [] };
  await loadAddon(page);
  const out = await page.reg.handlers.unity.exportHandler(path.join(TMP, "out-dm4"), {
    mode: "dir",
    custom: { unityExportKind: "full", unityDemosaic: "on" },
  });
  assert.equal(page.alerts.length, 0, "no guard alert — demosaic alone is work");
  assert.ok(out && out.halt === true);
  const job = globalThis.__unityBackendCalls[0].job;
  assert.equal(job.patch_mode, undefined, "still the full-copy behavior");
  assert.deepEqual(job.demosaic, { enabled: true, level: "high" });
});

test("createProject (v2.5): demosaic candidate files join the staging set", async () => {
  ensurePayload();
  const scan = require("./fixtures/stealth-scan-sample.json");
  const rels = [...new Set(scan.strings.map((s) => s.file))];
  const gameRoot = path.join(TMP, "stage-game-dm");
  fs.rmSync(gameRoot, { recursive: true, force: true });
  fs.mkdirSync(path.join(gameRoot, "Fake_Data"), { recursive: true });
  fs.writeFileSync(path.join(gameRoot, "Fake_Data", "globalgamemanagers"), "x");
  for (const rel of rels) {
    const p = path.join(gameRoot, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, "ORIGINAL-BYTES:" + rel);
  }
  // a mosaic candidate in a file NO string references — must stage anyway
  const mosaicRel = "FakeGame_Data/sharedassets9.assets";
  fs.mkdirSync(path.join(gameRoot, "FakeGame_Data"), { recursive: true });
  fs.writeFileSync(path.join(gameRoot, mosaicRel), "ORIGINAL-BYTES:" + mosaicRel);
  const index = {};
  for (const s of scan.strings) (index[s.text] = index[s.text] || []).push(
    { file: s.file, abs: path.join(gameRoot, s.file), path_id: s.path_id,
      source: s.source, offset: s.offset, length: s.length, key: s.key,
      tokens: s.tokens });
  const cachePath = path.join(TMP, "c-dm");
  const page = freshScenario();
  page.common.initStaging = async () => ({ cacheID: "cid", cachePath });
  globalThis.__unityBackendNext = { ok: true, version: 2, index,
    strings: scan.strings, stats: { files_scanned: 1 },
    demosaic: [{ file: mosaicRel.split(path.sep).join("/"), path_id: "40",
                 kind: "material", name: "Mosaic_default", confidence: "high",
                 evidence: ["material name 'Mosaic_default'"] }] };
  await loadAddon(page);
  const proj = await page.reg.handlers.unity.createProject({
    selectedFile: gameRoot, gameFolder: path.dirname(gameRoot),
    gameTitle: "Fake", projectId: "pid" });
  const stagedGameDir = path.join(cachePath, "data", "game");
  assert.equal(fs.readFileSync(path.join(stagedGameDir, mosaicRel), "utf-8"),
    "ORIGINAL-BYTES:" + mosaicRel,
    "the string-less mosaic file was staged for the demosaic stage");
  assert.ok(Array.isArray(proj.options.unity.demosaicFound) &&
    proj.options.unity.demosaicFound.length === 1,
    "the candidate list rides into the project metadata");
});
