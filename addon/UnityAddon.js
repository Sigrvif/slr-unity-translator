// SLRUnity — engine wiring (Task 3).
//
// LOADER CONTRACT (www/js/addon.js, Addon.loadJS): this file's body is eval'd
// as an AsyncFunction with `this` = the Addon instance — top-level statements
// only, NO IIFE (an IIFE would shadow the `this` binding the loader sets).
// Top-level `return` bails out of the body; top-level `await` would be legal
// in the eval'd body but is NOT used here so `node --check` (CommonJS parse)
// stays valid. The NW page has full Node access, so plain require("path") and
// the page globals (engines, Engines, common, trans, ui, document, alert,
// confirm) are all in scope — the same surface SEP.js uses.
//
// Lib imports follow the canonical absolute-path pattern from
// www/core/SEP/SEP.js:34-66: resolve thisAddon.fullPath (the addon's own
// folder, unlike thisAddon.basePath which is only the addons source dir) and
// require() through it.
const thisAddon = this;
// v2.4 RENAME SLRUnityTranslator -> SLRUnity: the per-package addon config
// (sys.config.addons.package[<package name>] — fontCjk/fontLatin/seenHelp)
// is keyed by package.name, so the renamed addon would start from a blank
// config. One-time carry-over: merge the old key's values into the new
// entry, drop the stale key, save. Runs before anything reads this.config
// (addon.js:380-383 has already pointed this.config at the NEW — possibly
// empty — entry by the time this body evals, so rebind it after the merge).
try {
  const pkgs = (typeof sys !== "undefined" && sys && sys.config &&
                sys.config.addons) ? sys.config.addons.package : null;
  const oldCfg = pkgs && pkgs["SLRUnityTranslator"];
  if (oldCfg && typeof oldCfg === "object") {
    const cur = pkgs["SLRUnity"] || {};
    for (const k of Object.keys(oldCfg)) if (!(k in cur)) cur[k] = oldCfg[k];
    pkgs["SLRUnity"] = cur;
    delete pkgs["SLRUnityTranslator"];
    this.config = cur;
    if (typeof sys.saveConfig === "function") sys.saveConfig();
  }
} catch (eMig) { console.warn("unity: addon config migration failed:", eMig); }
const path = require("path");
const os = require("os");
const addonDir = path.resolve(thisAddon.fullPath);
const L = (f) => require(path.join(addonDir, f)); // SEP.js getOCR()/getRender() pattern
const { runBackend, resolveRuntime } = L("lib/backend.js");
const { buildProject } = L("lib/projectBuilder.js");
const { detectUnity, resolveGameRoot } = L("lib/detector.js");
const { siblingDir, toForwardSlashes } = L("lib/paths.js");
// v2.8: the Unity pattern sets for the DSLR engine + their runtime installer
// (the SLRUnreal uePatterns/ueEngine scheme — see lib/unityEngine.js).
const { UNITY_PATTERN_DEFAULTS } = L("lib/unityPatterns.js");
const { installUnityPatternOverride } = L("lib/unityEngine.js");

// Bundled Python runtime for this OS (runtime payloads are fetched at BUILD
// time only — Task 6). Absent payload => the addon stays silent: no engine,
// no detector, no handlers.
const RUNTIME = resolveRuntime(addonDir);
if (!RUNTIME) return;

const fs = require("fs");
const fsh = {
  isFile: (p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } },
  isDir: (p) => { try { return fs.statSync(p).isDirectory(); } catch (e) { return false; } },
  listDir: (p) => { try { return fs.readdirSync(p); } catch (e) { return []; } },
  join: (...a) => path.join(...a), dirname: (p) => path.dirname(p),
};

// Engine registration + detection. Engines.detect() (www/js/Engines.js:140)
// probes registered detectors after the built-in ones; the returned id must
// match the name passed to engines.add().
engines.add("unity", { title: "Unity (IL2CPP / Mono)" });
Engines.registerDetector(async (gamePath) => detectUnity(fsh, gamePath));

// Persistent help access (e2e-fix5, was e2e-fix4 issue 3): the e2e-fix4
// "Tools menu" registration (ui.mainMenu.addChild("tools", ...), the
// customParser precedent) is DEAD CODE in SLR — there is no #mainMenu element
// in ANY SLR html (only a trans.css rule and ui.js code reference it), so
// addChild appends into an empty jQuery set and silently no-ops (the
// customParser module still carries the same dead call — reported to the SLR
// maintainer). Removed here; the persistent entry point is now a visible
// "Open documentation" link on our own New Project slide (below), which calls
// the SAME shared openHelp the first-run offer uses.

// ---------------------------------------------------------------------------
// New Project dialog entry (e2e-fix1). Registering only the engine +
// detector made the addon load cleanly yet stay INVISIBLE: SLR's New Project
// dialog has no generic "any folder" slide — every JS engine that wants an
// entry must add one itself (customParser precedent:
// www/core/customParser/CustomParserLoader.js drawProjectCreator, ~288-382).
// ui.newProjectDialog.addMenu queues itself until the dialog initializes
// (www/js/ui.js:5782-5786), so registering at addon load time is safe.
//
// `$` is a page global in NW; the window.jQuery fallback keeps the loader-eval
// test harness (which injects page globals as function parameters) working.
const $jq = (typeof $ === "function") ? $ :
  ((typeof window !== "undefined" && window && typeof window.jQuery === "function") ? window.jQuery : null);
function unitySelectDir() {
  // customParser selectDir pattern (CustomParserLoader.js): an invisible
  // nwdirectory file input resolves with the picked folder on "input".
  // `this.value` (not $(this).val()) so a minimal fake jQuery suffices in
  // tests; on a real <input> element both read the same property.
  return new Promise((resolve) => {
    const $elm = $jq("<input type=\"file\" nwdirectory />");
    $elm.one("input", function () { resolve(this.value || ""); });
    $elm.trigger("click");
  });
}
// v2.3 custom fonts: same pattern for a single FONT FILE. In NW.js a plain
// file input's value is the absolute path; the accept filter narrows the
// picker dialog to font files (a hint, not a hard gate).
function unitySelectFontFile() {
  return new Promise((resolve) => {
    const $elm = $jq("<input type=\"file\" accept=\".ttf,.otf\" />");
    $elm.one("input", function () { resolve(this.value || ""); });
    $elm.trigger("click");
  });
}
// e2e-fix6: the slide now carries THREE clearly-separated action sections —
// (a) create a strings translation project (unchanged flow), (b) standalone
// texture extraction for SEP image translation, (c) standalone insertion of
// SEP-rendered images into a game copy. The image (SEP) workflow is fully
// DECOUPLED from the strings project: it used to hang off project creation
// (a post-scan offer that ran after the loading overlay had already shown
// "All done!" — its Close button only appears at ui.loadingEnd, so holding it
// after the fact cannot work) and glued its state into the project's
// options.unity. The standalone tools instead drive the overlay lifecycle
// THEMSELVES, the same lifecycle createProjectFromEngine uses
// (trans-full.js:641-683): ui.showLoading() first, per-file lines via
// ui.loadingProgress(title, line, {consoleOnly:false, mode:"consoleOutput"}),
// then ui.loadingEnd() at the TRUE end (a finally block guarantees it on
// success and failure alike). No project is created or touched by (b)/(c).
function unityDrawProjectCreator() {
  const version = (thisAddon.package && thisAddon.package.version) ? (" Ver. " + thisAddon.package.version) : "";
  const $slide = $jq(`<div>
    <h1><i class="icon-plus-circled"></i><span>SLRUnity${version}</span></h1>
    <div class="dialogSectionBlock fieldgroup">
        <h2>Create a translation project</h2>
        <div class="info">Pick the game folder (or the game's .exe). The scan collects strings from six sources: serialized scene/object fields, raw length-prefixed text records, IL2CPP code literals (global-metadata.dat), Mono DLL code literals, TextAssets (JSON/CSV/...) and StreamingAssets text files. Every row is risk-tagged: safe dialogue translates by default, likely-safe rows (data tables, non-display fields) wait tagged yellow, structural strings (names, lookup keys) tagged red. Fonts and textures are catalogued too. A full scan takes a few minutes. The game itself is never modified — translations are written into a copy on export.</div>
        <div class="actionButtons">
            <button class="unitySelectFolder"><i class="icon-folder-open"></i>Select Folder</button>
        </div>
    </div>
    <div class="dialogSectionBlock fieldgroup">
        <h2>Extract textures for image translation (SEP)</h2>
        <div class="info">Standalone tool — no translation project needed. Dumps every Texture2D as PNG into &lt;Game&gt;_unity_img next to the game (plus a manifest). Start here when text inside images needs translating; nothing in the game is modified.</div>
        <div class="actionButtons">
            <button class="unityExtractTextures"><i class="icon-picture"></i>Select Game Folder &amp; Extract Textures</button>
        </div>
    </div>
    <div class="dialogSectionBlock fieldgroup">
        <h2>Insert translated images into a game copy</h2>
        <div class="info">Standalone tool — the last step of the image round-trip. Copies the game and applies the SEP-rendered images (optionally the font swap too). Strings are translated separately, from the strings project's own export.</div>
        <div class="unityPickRow" style="max-width:34em;margin:8px auto;text-align:center">
            <div style="margin-bottom:2px">1. Game folder</div>
            <label>
                <input type="dvSelectPath" nwdirectory class="fullWidth unityPick unityPickGame" data-unity-pick="game" value="" />
            </label>
            <div class="unityPickGamePath" style="opacity:.75;font-size:.9em;margin-top:2px">(not selected)</div>
        </div>
        <div class="unityPickRow" style="max-width:34em;margin:8px auto;text-align:center">
            <div style="margin-bottom:2px">2. Extracted-images folder</div>
            <label>
                <input type="dvSelectPath" nwdirectory class="fullWidth unityPick unityPickExtract" data-unity-pick="extract" value="" />
            </label>
            <div class="unityPickExtractPath" style="opacity:.75;font-size:.9em;margin-top:2px">(with unity_img_manifest.json)</div>
        </div>
        <div class="unityPickRow" style="max-width:34em;margin:8px auto;text-align:center">
            <div style="margin-bottom:2px">3. SEP rendered-images folder</div>
            <label>
                <input type="dvSelectPath" nwdirectory class="fullWidth unityPick unityPickRendered" data-unity-pick="rendered" value="" />
            </label>
            <div class="unityPickRenderedPath" style="opacity:.75;font-size:.9em;margin-top:2px">(SEP's export output)</div>
        </div>
        <div class="unityPickRow" style="max-width:34em;margin:8px auto;text-align:center">
            <div style="margin-bottom:2px">4. Output folder (game copy)</div>
            <label>
                <input type="dvSelectPath" nwdirectory class="fullWidth unityPick unityPickOut" data-unity-pick="out" value="" />
            </label>
            <div class="unityPickOutPath" style="opacity:.75;font-size:.9em;margin-top:2px">(the patched copy lands here)</div>
        </div>
        <div style="max-width:34em;margin:8px auto;text-align:center">
            <label><input type="checkbox" class="unityInsertFont"> Replace game fonts (SLRCondensedCJK)</label>
        </div>
        <div class="actionButtons">
            <button class="unityInsertRun"><i class="icon-ok"></i>Insert translated images</button>
        </div>
    </div>
    <div class="dialogSectionBlock fieldgroup">
        <h2>Remove mosaic censorship (standalone)</h2>
        <div class="info">Standalone tool — no translation project needed, works on already-translated games too. Finds the game's mosaic censorship layers (the same treatment as the export option) and packs only the changed files into a &lt;Game&gt;_demosaic.zip next to the game: extract its contents over the game folder; delete the changed files (or re-extract originals) to undo.</div>
        <div class="unityDmRow" style="max-width:40em;margin:10px auto;text-align:center">
            <div>Detection level</div>
            <select class="unityDemosaicLevel" style="margin-top:2px">
                <option value="high">high confidence only (recommended)</option>
                <option value="medium">include medium confidence</option>
            </select>
            <div style="opacity:.75;font-size:.9em;margin-top:2px">High patches only unambiguous candidates: mosaic-named objects/materials/shaders, or shaders with explicit mosaic properties. Medium also patches weaker matches (e.g. a shader carrying only the typical mosaic size parameters) &mdash; more coverage on stubborn games, higher false-positive risk.</div>
        </div>
        <div class="unityDmRow" style="max-width:40em;margin:10px auto;text-align:center">
            <div><label><input type="checkbox" class="unityDemosaicReveal"> Reveal instead of hide <span style="opacity:.75">(advanced)</span></label></div>
            <div style="opacity:.75;font-size:.9em;margin-top:2px">For mosaic materials on modeled body meshes: swap their shader for the game's own display shader and KEEP the textures, instead of making them invisible &mdash; the treatment to try when the standard removal makes body parts disappear entirely. Dedicated overlay objects are still hidden.</div>
        </div>
        <div class="unityDmRow" style="max-width:40em;margin:10px auto;text-align:center">
            <div><label><input type="checkbox" class="unityDemosaicCode"> Patch code-level censorship <span style="opacity:.75">(Mono games, advanced)</span></label></div>
            <div style="opacity:.75;font-size:.9em;margin-top:2px">Additionally stubs mosaic-named censor components in the game's managed DLLs so their censoring code never runs. Mono games only &mdash; IL2CPP games are detected but cannot be patched. Opt-in and test on a copy: a malformed patch could crash the game.</div>
        </div>
        <div class="actionButtons">
            <button class="unityDemosaicRun"><i class="icon-folder-open"></i>Select Game Folder &amp; Remove Mosaic</button>
        </div>
    </div>
    <p class="unitySlideHelp" style="margin:12px 0 0;text-align:center">
        <a href="#" class="unityOpenDocs">Open documentation</a>
        &mdash; workflow, export options and troubleshooting.
    </p>
    </div>`);
  // (a) strings project — the pre-e2e-fix6 flow, unchanged.
  $slide.find(".unitySelectFolder").on("click", async function () {
    const target = await unitySelectDir();
    if (!target) return; // folder picker cancelled
    await ui.newProjectDialog.close();
    // ui.startProject (www/js/ui.js:6171): with no engine preselected it
    // runs Engines.detect (our registered detector) and routes JS engines to
    // trans.procedureCreateProject — exactly the customParser flow.
    ui.startProject(target);
  });
  // (b) standalone texture extraction. One button = one folder pick; the
  // picker is cancelled -> nothing happens. A non-Unity pick alerts and the
  // dialog stays open.
  $slide.find(".unityExtractTextures").on("click", async function () {
    const target = await unitySelectDir();
    if (!target) return;
    let gameDir;
    try {
      gameDir = resolveGameRoot(fsh, target, null);
    } catch (e) {
      alert(e && e.message ? e.message : String(e));
      return;
    }
    await ui.newProjectDialog.close();
    await unityRunExtractTextures(gameDir);
  });
  // (c) insert translated images. Four folder pickers (paths shown on the
  // slide) + an inline font checkbox — plain HTML on our own slide, no
  // jsonForm needed. The picks live in this closure; the run handler guards,
  // closes the dialog and hands off to the standalone runner.
  //
  // e2e-fix9 (issue 2, slide alignment): the four hand-rolled small
  // left-aligned picker BUTTONS are replaced by SLR's native dvSelectPath
  // widget — `<input type="dvSelectPath" nwdirectory class="fullWidth ...">`
  // rows (precedents: the export target path www/js/ui.js:1110, the rmmv dev
  // path ui.js:3465, SEP.js:885), rendered right below through the page-global
  // ui.dvField instance (ui.js:6385) exactly the way ui.js:1110-1115 renders
  // fresh content: dvField.renderSelectPath($content.find("[type=dvSelectPath]
  // )"). renderSelectPath wraps each input in a dvSelectPath_wrapper span with
  // the "..." folder button whose onSelect writes the picked path into the
  // input and fires "change" — so the values are read here on change into the
  // same `picks` state the run button reads, and the per-row hint labels are
  // kept updated. Rows are centered (max-width + margin:auto, the section's
  // .actionButtons alignment); the font checkbox aligns with them. Best-effort
  // render: a host without DVField just leaves plain text inputs (paths can
  // still be pasted); nothing else on the slide depends on it.
  const picks = { game: "", extract: "", rendered: "", out: "" };
  const pickHints = {
    game: "(not selected)",
    extract: "(with unity_img_manifest.json)",
    rendered: "(SEP's export output)",
    out: "(the patched copy lands here)",
  };
  const wirePick = (inputCls, pathCls, key) => {
    $slide.find(inputCls).on("change", function () {
      const dir = String((this && this.value) || "");
      picks[key] = dir;
      try { $slide.find(pathCls).text(dir || pickHints[key]); } catch (e) {}
    });
  };
  wirePick(".unityPickGame", ".unityPickGamePath", "game");
  wirePick(".unityPickExtract", ".unityPickExtractPath", "extract");
  wirePick(".unityPickRendered", ".unityPickRenderedPath", "rendered");
  wirePick(".unityPickOut", ".unityPickOutPath", "out");
  try {
    const dv = (ui && ui.dvField && typeof ui.dvField.renderSelectPath === "function") ? ui.dvField
      : ((typeof DVField === "function") ? new DVField() : null);
    if (dv) dv.renderSelectPath($slide.find("[type=dvSelectPath]"));
    else console.warn("unity: DVField unavailable — insert-tool path inputs stay plain text fields");
  } catch (e) { console.warn("unity dvSelectPath render failed:", e); }
  // Held from wiring time so the run reads the SAME collection (a jQuery
  // re-find is equivalent in a real DOM but breaks element-identity in the
  // loader-eval test double).
  const $fontBox = $slide.find(".unityInsertFont");
  $slide.find(".unityInsertRun").on("click", async function () {
    if (!picks.game || !picks.extract || !picks.rendered || !picks.out) {
      alert("Pick all four folders first:\n  1. the game folder,\n  2. the extracted-images folder (from \"Extract textures...\"),\n  3. SEP's rendered-images folder,\n  4. the output folder for the patched game copy.");
      return;
    }
    // Guard BEFORE any backend call (and before the dialog closes, so the
    // user can re-pick): the extract folder must be the one our extraction
    // step produced — that is where unity_img_manifest.json lives.
    if (!fsh.isFile(path.join(picks.extract, "unity_img_manifest.json"))) {
      alert("No unity_img_manifest.json found in:\n" + picks.extract +
            "\n\nPick the folder created by \"Extract textures for image translation (SEP)\".");
      return;
    }
    let gameDir;
    try {
      gameDir = resolveGameRoot(fsh, picks.game, null);
    } catch (e) {
      alert(e && e.message ? e.message : String(e));
      return;
    }
    const wantFont = $fontBox.is(":checked") === true;
    await ui.newProjectDialog.close();
    await unityRunInsertImages({ gameDir, extractDir: picks.extract, renderedDir: picks.rendered, outDir: picks.out, wantFont });
  });
  // (d) standalone demosaic (v2.8): one folder pick + the inline option row.
  // The selection is remembered across sessions in localStorage (the v2.6o
  // pattern) so repeat treatments keep the user's level/reveal/code picks.
  const $dmLevel = $slide.find(".unityDemosaicLevel");
  const $dmReveal = $slide.find(".unityDemosaicReveal");
  const $dmCode = $slide.find(".unityDemosaicCode");
  try {
    const savedDm = JSON.parse(localStorage.getItem("slrUnityDemosaicTool.v1") || "{}");
    if (savedDm && typeof savedDm === "object") {
      if (savedDm.level === "medium") $dmLevel.val("medium");
      if (savedDm.reveal) $dmReveal.prop("checked", true);
      if (savedDm.code) $dmCode.prop("checked", true);
    }
  } catch (e) { /* no storage in the test harness: built-in defaults stand */ }
  $slide.find(".unityDemosaicRun").on("click", async function () {
    const target = await unitySelectDir();
    if (!target) return; // folder picker cancelled
    let gameDir;
    try {
      gameDir = resolveGameRoot(fsh, target, null);
    } catch (e) {
      alert(e && e.message ? e.message : String(e));
      return;
    }
    const opts = {
      level: $dmLevel.val() === "medium" ? "medium" : "high",
      reveal: $dmReveal.is(":checked") === true,
      code: $dmCode.is(":checked") === true,
    };
    try { localStorage.setItem("slrUnityDemosaicTool.v1", JSON.stringify(opts)); } catch (e) {}
    await ui.newProjectDialog.close();
    await unityRunDemosaic(gameDir, opts);
  });
  // Persistent help entry point (e2e-fix5 — replaces the dead Tools-menu
  // registration above): a visible link on OUR slide. openHelp (hoisted,
  // shared with the first-run offer) prefers the offline documentation's
  // addon page and falls back to assets/help.html.
  $slide.find(".unityOpenDocs").on("click", function (ev) {
    try { if (ev && typeof ev.preventDefault === "function") ev.preventDefault(); } catch (e0) {}
    openHelp();
  });
  ui.newProjectDialog.addMenu({
    icon: thisAddon.getWebLocation() + "/assets/icon.png", // addon.js:166 — e2e-fix11: icon lives in assets/ (root path 404'd)
    descriptionBar: `<h2>Translate a Unity game (IL2CPP / Mono)</h2>
                     <p>Scan a Unity game's strings and translate them with the DSLR engine — plus standalone tools for SEP image translation (texture extract / insert) and mosaic-censorship removal.</p>`,
    actionBar: "",
    goToSlide: "unityTranslator",
    at: 3, // after rmrgss (1), before customParser (9)
    slides: { "unityTranslator": $slide },
  });
}
try {
  if ($jq && ui && ui.newProjectDialog && typeof ui.newProjectDialog.addMenu === "function") {
    unityDrawProjectCreator();
  } else {
    console.warn("unity: New Project dialog entry NOT registered (no $ / ui.newProjectDialog.addMenu)");
  }
} catch (e) { console.warn("unity New Project dialog entry failed:", e); }

// ---------------------------------------------------------------------------
// Help page (Task 7). First-run offer: on the first createProject, confirm()
// -> open assets/help.html. SLR's own way to open a page is
// nw.Shell.openExternal (www/js/common.js:4449, options.js:408, ui.js:2495);
// window.open and a plain alert with the path are fallbacks.
//
// The seenHelp flag persists through the ADDON CONFIG: by the time this body
// is eval'd, www/js/addon.js:380-383 has already re-pointed thisAddon.config
// at sys.config.addons.package[<package name>] and thisAddon.setConfig(key,
// value) writes there AND calls sys.saveConfig() (addon.js:51-54) — so the
// flag lands in SLR's data/config.json and survives restarts. localStorage
// ("slrUnitySeenHelp") is the fallback for contexts without the config
// object (e.g. the plain-Node test harness).
function unityHelpSeen() {
  try { if (thisAddon.config && thisAddon.config.seenHelp) return true; } catch (e) {}
  try {
    if (typeof localStorage !== "undefined" && localStorage.getItem("slrUnitySeenHelp") === "1") return true;
  } catch (e) {}
  return false;
}
function unityMarkHelpSeen() {
  // Mark BEFORE asking: the offer must fire exactly once per install, whether
  // or not the user accepts.
  try { if (typeof thisAddon.setConfig === "function") { thisAddon.setConfig("seenHelp", true); return; } } catch (e) {}
  try { if (thisAddon.config) thisAddon.config.seenHelp = true; } catch (e) {}
  try { if (typeof localStorage !== "undefined") localStorage.setItem("slrUnitySeenHelp", "1"); } catch (e) {}
}
// The help-page OPENER, shared by the first-run offer and the New Project
// slide's "Open documentation" link (e2e-fix5; was the Tools-menu entry).
// Preferred path (v3.078): the host's offline documentation. docsHelp
// (www/js/docsHelp.js, loaded by trans.html) opens/focuses the docs viewer
// with a page-id hash; addon pages get the id "addon-<folder>"
// (www/js/addonDocs.js — the "docs" package.json field declares the page).
// The self-contained assets/help.html stays as the fallback for hosts/contexts
// without the docs system (older SLR, bare test harness): SLR's own way to
// open a page is nw.Shell.openExternal (www/js/common.js:4449, options.js:408,
// ui.js:2495); window.open and a plain alert with the path are fallbacks.
function openHelp() {
  try {
    if (typeof docsHelp !== "undefined" && docsHelp && typeof docsHelp.open === "function") {
      docsHelp.open("addon-SLRUnity");
      return;
    }
  } catch (e) {}
  const url = "file:///" + toForwardSlashes(path.join(addonDir, "assets", "help.html"));
  try {
    if (typeof nw !== "undefined" && nw && nw.Shell && typeof nw.Shell.openExternal === "function") {
      nw.Shell.openExternal(url); // SLR's established way (common.js:4449)
      return;
    }
  } catch (e) {}
  try { if (typeof window !== "undefined" && window.open) { window.open(url); return; } } catch (e) {}
  alert("SLRUnity help page (open in a browser):\n" + path.join(addonDir, "assets", "help.html"));
}
function maybeOfferHelp() {
  if (unityHelpSeen()) return;
  unityMarkHelpSeen();
  // Modal (e2e-fix3, was confirm()): the loading overlay is already up by the
  // time createProject runs, and a native confirm() can end up under it,
  // unclickable — the same bug the SEP-texture offer had. Fire-and-forget:
  // the scan does not wait for the answer.
  unityAsk(
    "First Unity project — open the help page?\n\n" +
    "(Workflow, options and troubleshooting. Find it again on the SLRUnity\n" +
    "slide of the New Project dialog, or in Help's offline documentation.)",
    "Open help page", "Not now"
  ).then((yes) => { if (yes) openHelp(); });
}

// Progress plumbing (e2e-fix3). While a project is being created SLR shows a
// full-screen loading overlay with its own console (the "Creating project
// (unity)" lines). Scan progress used to go to a small bottom-right bubble
// that users never associated with that flow — route every line INTO the
// overlay's console instead, the same way rmrgss streams its spawn progress
// (www/core/rmrgss/rmrgss.js:1847-1913):
//   ui.loadingProgress(title, text, {consoleOnly:false, mode:"consoleOutput"})
// (www/js/ui.js:1517+). Outside the loading flow (export) the overlay is
// gone, so the bottom-right bubble stays the visible channel.
// Loading-overlay helper (e2e-fix4): overlayUp(): the overlay element is
// present AND visible (jQuery's own :visible filter — the user may already
// have closed it).
function overlayUp() {
  try {
    if (!$jq) return false;
    const $o = $jq("#loadingOverlay");
    if (!$o || !$o.length) return false;
    if (typeof $o.is === "function") return $o.is(":visible") === true;
    return true; // a jQuery predating .is(): presence is the best signal left
  } catch (e) { return false; }
}
function uiLogLine(title, text) {
  try {
    if (typeof ui !== "undefined" && ui && typeof ui.loadingProgress === "function" &&
        overlayUp()) {
      ui.loadingProgress(title || "Unity scan", text, { consoleOnly: false, mode: "consoleOutput" });
      return true;
    }
  } catch (e) {}
  return false;
}
function progressOverlay() {
  let el = document.getElementById("slr-unity-progress");
  if (!el) {
    el = document.createElement("div");
    el.id = "slr-unity-progress";
    el.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:99999;background:#222;color:#fff;padding:8px 12px;border-radius:6px;font:12px sans-serif;display:none;max-width:420px";
    document.body.appendChild(el);
  }
  return el;
}
// The PROJECT-FLOW router (scan/createProject, export patch): into the
// loading overlay's console when it is up, the bottom-right bubble otherwise.
// e2e-fix6: the standalone image tools no longer route through here — they
// drive the overlay lifecycle themselves (uiShowLoading/uiLine/uiLoadingEnd
// below), which is the only correct pattern for work SLR did not start.
function showProgress(p) {
  const line = p.done + "/" + p.total + "  " + path.basename(p.file || "");
  if (uiLogLine("Unity scan", line)) return; // loading overlay's console took it
  const el = progressOverlay();
  el.style.display = "block";
  el.textContent = "Unity backend: " + line;
}
// One-off status line through the same router (scan-complete note). Falls
// back to the bubble when the overlay is gone.
function noteLine(title, text) {
  if (uiLogLine(title, text)) return;
  const el = progressOverlay();
  el.style.display = "block";
  el.textContent = text;
}
// No-op when the lines went to the UI log (the bubble never existed/stayed
// hidden — getElementById returns null and nothing happens).
function hideProgress() { const el = document.getElementById("slr-unity-progress"); if (el) el.style.display = "none"; }
// e2e-fix7 (contract v2): staging-at-creation progress. Same router as
// showProgress (loading overlay's console while it is up, bottom-right bubble
// otherwise) but its own line shape, so the user can tell the copy phase apart
// from the scan: "Staging files 12/35  level22".
function stageProgress(done, total, rel) {
  const line = "Staging files " + done + "/" + total + "  " + path.basename(rel || "");
  if (uiLogLine("Unity scan", line)) return;
  const el = progressOverlay();
  el.style.display = "block";
  el.textContent = "Unity staging: " + line;
}

// e2e-fix9 (issue 1): export progress — same router, its own phase title, so
// the overlay console tells an export apart from a scan. Used by the export
// handler ONLY while its own overlay is up (uiShowLoading before the backend
// call), so uiLogLine takes the loadingProgress path in the real app; the
// bottom-right bubble stays the fallback for hosts without the overlay.
function exportProgress(p) {
  const line = p.done + "/" + p.total + "  " + path.basename(p.file || "");
  if (uiLogLine("Unity export", line)) return;
  const el = progressOverlay();
  el.style.display = "block";
  el.textContent = "Unity backend: " + line;
}

// e2e-fix10: post-backend export lines (revert phase, warnings, the final
// summary) go into the SAME overlay console the progress lines use, so the
// whole export reads as one log that stays open (Close only appears at the
// final loadingEnd). ui.log (ui.js:1601-1625) is the preferred channel: it
// appends into #loadingOverlay .console — the exact channel loadingEnd's own
// "Log ended!" lines use — and the append is SYNCHRONOUS at call time, so
// line order is preserved fire-and-forget (awaiting ui.log's promise would
// serialize every line behind the logIsAdded observer, or a 250ms timeout
// each — minutes over a big revert phase). Without ui.log (or the overlay)
// the existing uiLogLine router applies — loadingProgress consoleOutput while
// the overlay is up, the bottom-right bubble otherwise (same shape as
// showProgress/noteLine above).
function exportLine(title, text) {
  try {
    if (overlayUp() && typeof ui !== "undefined" && ui && typeof ui.log === "function") {
      const p = ui.log(text);
      if (p && typeof p.catch === "function") p.catch(() => {});
      return true;
    }
  } catch (e) {}
  if (uiLogLine(title, text)) return true;
  const el = progressOverlay();
  el.style.display = "block";
  el.textContent = text;
}

// e2e-fix7 STAGING AT CREATION (contract v2, self-contained projects):
// copy every string-bearing game file into <cachePath>/data/game/<game-rel>
// and rewrite the index's abs paths to those staged copies. The .slrproj then
// carries the whole project (Save Project packs the ENTIRE staging cache;
// _slrprojZip's -x! excludes cover only cache-internal marker files, data/
// travels), so patch export never needs the original game folder again.
// - The file set is derived from scan.index occurrences' `file` values (abs
//   originals read straight from the just-scanned game_dir), plus — since
//   e2e-fix13 — every scan.fonts[].file, so patch-mode font swaps see the
//   complete font catalog even in string-less files.
// - `file` (game-rel) keys are preserved VERBATIM; only `abs` changes.
// - A copy failure (locked/read-only source — rare, the scan just read it)
//   leaves that file's occurrences pointing at the ORIGINAL abs: the backend
//   keeps an abs whose file exists and patch export still works.
// Returns the staged game dir (= cachePath/data/game).
// e2e-fix13: `fonts` (scan.fonts — [{file: abs, path_id, name, font_type}])
// extends the staging set with every font-bearing file, deduped against the
// string-file set. Patch-mode font swaps scan the staged dir, so a font
// living in a string-less file (resources.assets carrying only an embedded
// Font — the common case) must be staged or the swap silently misses it.
// Same copy/progress machinery: the font rels join the sorted list, so the
// "Staging files i/N" count covers them; a failed copy is only a warn (the
// export's font_source_dir still covers projects whose original survives).
// v2.5 demosaic: `demosaicRows` (scan.demosaic — [{file: game-rel, kind, ...}],
// the mosaic censorship candidates) extends the staging set the same way: the
// export-time demosaic stage re-detects over the staged tree, so every
// candidate-bearing file must ride along or the stage would silently find
// nothing on a self-contained project.
function stageScanFiles(index, gameDir, stagedGameDir, fonts, demosaicRows) {
  const failed = new Set();
  const rels = new Set();
  for (const occs of Object.values(index || {}))
    for (const o of (occs || [])) if (o && o.file) rels.add(String(o.file));
  try {
    for (const f of (fonts || [])) {
      if (!f || !f.file) continue;
      const rel = path.relative(gameDir, String(f.file)).split(path.sep).join("/");
      if (!rel || rel.startsWith("..")) continue; // outside gameDir: never stage
      rels.add(rel); // Set dedupes against the string files
    }
  } catch (e) { console.warn("unity staging: font list unusable:", e && e.message ? e.message : e); }
  try {
    for (const r of (demosaicRows || [])) {
      if (!r || !r.file) continue;
      if (r.kind === "script") continue; // report-only: never bloats the project
      const rel = String(r.file).split("#")[0].split(path.sep).join("/");
      if (!rel || rel.startsWith("..")) continue; // outside gameDir: never stage
      rels.add(rel);
    }
  } catch (e) { console.warn("unity staging: demosaic list unusable:", e && e.message ? e.message : e); }
  const list = [...rels].sort();
  let done = 0;
  for (const rel of list) {
    const dst = path.join(stagedGameDir, rel);
    try {
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      fs.copyFileSync(path.join(gameDir, rel), dst);
    } catch (e) {
      failed.add(rel);
      console.warn("unity staging: could not copy " + rel + ":",
        e && e.message ? e.message : e);
    }
    done++;
    stageProgress(done, list.length, rel);
  }
  for (const occs of Object.values(index || {}))
    for (const o of (occs || []))
      if (o && o.file && !failed.has(String(o.file)))
        o.abs = path.join(stagedGameDir, o.file);
  return stagedGameDir;
}

// ---------------------------------------------------------------------------
// In-DOM question modal (e2e-fix3). The post-scan SEP-texture offer used to
// be a native confirm() inside a setTimeout — it rendered UNDER ui's loading
// overlay and never reacted. This modal is fixed-position at z-index 100001
// (ABOVE the overlay), so it stays clickable while project creation is still
// finishing behind it. Plain DOM like the progress bubble — no jQuery-UI
// dependency. Resolves true (affirmative) or false (negative); either click
// removes the modal. Degrades to confirm() when the DOM is unusable.
function removeEl(el) {
  try {
    if (el && el.parentNode && typeof el.parentNode.removeChild === "function") el.parentNode.removeChild(el);
    else if (el && typeof el.remove === "function") el.remove();
  } catch (e) {}
}
function unityAsk(question, yesLabel, noLabel) {
  return new Promise((resolve) => {
    let modal = null, settled = false;
    const finish = (val) => { if (settled) return; settled = true; removeEl(modal); resolve(val); };
    try {
      // One ask at a time: an earlier unanswered ask (e.g. the first-run
      // help offer) must not stack under this one.
      try { removeEl(document.getElementById("slr-unity-ask")); } catch (e0) {}
      // e2e-fix4 (issue 1): pick the palette at CREATION time from SLR's
      // night-mode body class (www/core/nightMode/main.js:34 adds
      // "nightMode"). SLR's night CSS is hand-authored and knows nothing of
      // this modal, so in night mode it must carry its own dark palette
      // (SLR night colors: panel #262626, header #3d4a5c, text #eee) instead
      // of default-styled (light/white) buttons blasting out of the dark.
      // Light world: the colors this modal always shipped with.
      let night = false;
      try {
        night = !!(document.body && document.body.classList &&
                   typeof document.body.classList.contains === "function" &&
                   document.body.classList.contains("nightMode"));
      } catch (eN) {}
      // e2e-fix5 (flashbang root cause): SLR night mode is not just the body
      // class — it INVERTS THE WHOLE PAGE at the CSS level:
      // `html { filter: invert(92%); }` (www/core/nightMode/css/!nightMode.css:1).
      // Our dark palette is FINAL-authored, so the page inversion flips it
      // back to a blinding light box. SLR's own precedent for dark-authored
      // chrome is to counter-invert it:
      // `.nightMode #loadingOverlay { filter: invert(100%); }` (same file,
      // :88-91 — the loading overlay is dark-authored too). Give the modal
      // CONTAINER the same inline counter-inversion in night mode; the wave-4
      // palette stays untouched. Light path: no filter at all.
      const backdrop = night ? "rgba(0,0,0,0.6)" : "rgba(0,0,0,0.55)";
      const boxStyle = night
        ? "background:#262626;color:#eee;max-width:500px;padding:18px 22px;border-radius:8px;" +
          "font:13px/1.5 sans-serif;box-shadow:0 6px 30px rgba(0,0,0,0.5);border:1px solid #3d4a5c"
        : "background:#252525;color:#eee;max-width:500px;padding:18px 22px;border-radius:8px;" +
          "font:13px/1.5 sans-serif;box-shadow:0 6px 30px rgba(0,0,0,0.5)";
      const btnStyle = night
        ? "padding:6px 14px;margin-left:8px;font:13px sans-serif;background:#3d4a5c;color:#eee;" +
          "border:1px solid #55647a;border-radius:4px;cursor:pointer"
        : "padding:6px 14px;margin-left:8px;font:13px sans-serif";
      modal = document.createElement("div");
      modal.id = "slr-unity-ask";
      modal.style.cssText = "position:fixed;left:0;top:0;right:0;bottom:0;z-index:100001;" +
        "display:flex;align-items:center;justify-content:center;background:" + backdrop +
        (night ? ";filter:invert(100%)" : "");
      const box = document.createElement("div");
      box.style.cssText = boxStyle;
      const text = document.createElement("div");
      text.style.cssText = "white-space:pre-wrap";
      text.textContent = question;
      const row = document.createElement("div");
      row.style.cssText = "margin-top:16px;text-align:right";
      const noBtn = document.createElement("button");
      noBtn.textContent = noLabel;
      noBtn.style.cssText = btnStyle;
      const yesBtn = document.createElement("button");
      yesBtn.textContent = yesLabel;
      yesBtn.style.cssText = btnStyle;
      row.appendChild(noBtn);
      row.appendChild(yesBtn);
      box.appendChild(text);
      box.appendChild(row);
      modal.appendChild(box);
      document.body.appendChild(modal);
      yesBtn.addEventListener("click", () => finish(true));
      noBtn.addEventListener("click", () => finish(false));
    } catch (e) {
      removeEl(modal); // a half-built modal must not linger
      try { if (typeof confirm === "function") { resolve(!!confirm(question)); return; } } catch (e2) {}
      resolve(false);
    }
  });
}

// v2.8 risk-tag counts for the scan-complete summary: per unique text at its
// WORST occurrence (the same ranking lib/projectBuilder.js writes into SLR's
// per-row tags — a text patched everywhere must wear its most conservative
// tag). ""/green translate by default, yellow is confirm-first, red flagged.
function unityTagCounts(scan) {
  const RANK = { red: 3, yellow: 2, "": 1, green: 0 };
  const worst = new Map();
  for (const row of scan.strings || []) {
    const risk = RANK[row.risk] !== undefined ? row.risk : "";
    const prev = worst.get(row.text);
    if (prev === undefined || RANK[risk] > RANK[prev]) worst.set(row.text, risk);
  }
  let safe = 0, confirm = 0, flagged = 0;
  for (const risk of worst.values()) {
    if (risk === "red") flagged++;
    else if (risk === "yellow") confirm++;
    else safe++; // "" and green are both translated by default
  }
  return { total: worst.size, safe, confirm, flagged };
}

// createProject handler. Called by Trans.createProjectFromEngine
// (www/js/trans-full.js:640-682) with
//   {gameFolder, selectedFile, gameEngine, gameTitle, projectId, options}
// and must resolve to a project object with `files` (see the shape documented
// on that function — buildProject produces exactly it). A throw is caught by
// SLR and reported as an engine failure.
engines.addHandler("unity", "createProject", async (args) => {
  maybeOfferHelp(); // one-time help-page offer (Task 7)
  // e2e-fix1: args.gameFolder is DIRNAME(picked) — ui.startProject
  // (www/js/ui.js:6171) computes it as the picked path's parent, so the old
  // `gameFolder || selectedFile` scanned the PARENT whenever the user picked
  // the game folder itself. resolveGameRoot finds the actual Unity root from
  // the picked path (see lib/detector.js); ambiguous/none => alert + throw.
  let gameDir;
  try {
    gameDir = resolveGameRoot(fsh, args.selectedFile || args.gameFolder, args.gameFolder);
  } catch (e) {
    alert(e && e.message ? e.message : String(e));
    throw e;
  }
  const cache = await common.initStaging(args.projectId, args.gameTitle || "Unity game");
  const jobsDir = path.join(cache.cachePath, "jobs");
  // try/finally (Task 3 deferred fix): if runBackend rejects (spawn failure,
  // missing result file), the progress overlay must still be hidden.
  let scan;
  try {
    scan = await runBackend({ version: 2, command: "scan", game_dir: gameDir },
      { jobsDir, python: RUNTIME, basePath: addonDir, onProgress: showProgress });
  } finally {
    hideProgress();
  }
  if (!scan.ok) {
    alert("Unity scan failed: " + (scan.error || "unknown") + "\nLog: " + path.join(jobsDir, "backend.log"));
    throw new Error(scan.error || "scan failed");
  }
  if (scan.stats && scan.stats.errors && scan.stats.errors.length)
    console.warn("Unity scan errors:", scan.stats.errors);
  // e2e-fix3: after the LAST per-file line the overlay stays silent for the
  // seconds buildProject (+ SLR opening the project) takes — the user saw the
  // progress "end" long before "Done!". One extra line explains the gap.
  noteLine("Unity scan", "Scan complete — building project (rows, contexts, scene index; this can take a moment)...");
  // v2.8: surface the risk split while the project builds, so the user knows
  // what batch translation will pick up before the project even opens.
  const tc = unityTagCounts(scan);
  noteLine("Unity scan", "Tags: " + tc.safe + " of " + tc.total +
    " strings translate by default; " + tc.confirm + " confirm-first (yellow), " +
    tc.flagged + " flagged (red) — every row keeps its tag in the project.");
  fs.mkdirSync(path.join(cache.cachePath, "data"), { recursive: true });
  // e2e-fix7 (contract v2): stage every string-bearing file into the cache
  // BEFORE the index is written — index.json stores the STAGED abs paths, so
  // the saved project is self-contained (the .slrproj packs data/, game
  // files included). gameDir stays in options.unity as a reference for the
  // full-copy export mode only.
  const stagedGameDir = stageScanFiles(scan.index, gameDir,
    path.join(cache.cachePath, "data", "game"), scan.fonts, scan.demosaic);
  fs.writeFileSync(path.join(cache.cachePath, "data", "index.json"), JSON.stringify(scan.index), "utf-8");

  // v2.2 collect-all-visible: read SLR's "Japanese source" general option at
  // creation so rows without Japanese get SLR's gray no-Japanese tag (the
  // same mark batch paints and blacklists). Off -> every row untagged.
  const japaneseSource = (typeof sys !== "undefined" && sys && sys.config)
    ? sys.config.japanesesource == true : false;
  const proj = buildProject(scan, { gameDir, stagedGameDir, gameTitle: args.gameTitle || path.basename(gameDir), cache, japaneseSource });
  hideProgress(); // clears the scan-complete note (bubble fallback only)
  // e2e-fix6: no post-create texture offer anymore — the SEP image workflow
  // moved to standalone slide actions (see unityRunExtractTextures). The old
  // askTextures modal ran AFTER the loading overlay had already ended
  // ("All done!" + an active Close button), so extraction could never be
  // part of that overlay's lifecycle; a standalone overlay is the only
  // correct pattern, and that requires leaving the project flow entirely.
  return proj;
});

// ---------------------------------------------------------------------------
// Standalone image tools (e2e-fix6) — the SEP round-trip, decoupled from the
// strings project. Both runners OWN the loading-overlay lifecycle exactly the
// way createProjectFromEngine does (www/js/trans-full.js:641-683):
//   ui.showLoading()  ->  ui.loadingProgress(title, line, {consoleOnly:false,
//   mode:"consoleOutput"}) per event  ->  ui.loadingEnd() when the backend job
// settles. The finally block guarantees loadingEnd runs EXACTLY ONCE on the
// success AND the failure path — the overlay's Close button only appears at
// loadingEnd, so this is also the only point where the run is truly over.
// Every ui.* call is best-effort-guarded: a misbehaving host must never fail
// the backend job itself.
function uiShowLoading() {
  try {
    if (typeof ui !== "undefined" && ui && typeof ui.showLoading === "function") ui.showLoading();
  } catch (e) {}
}
function uiLoadingEnd() {
  try {
    if (typeof ui !== "undefined" && ui && typeof ui.loadingEnd === "function") ui.loadingEnd();
  } catch (e) {}
}
function uiLine(title, text) {
  try {
    if (typeof ui !== "undefined" && ui && typeof ui.loadingProgress === "function")
      ui.loadingProgress(title, text, { consoleOnly: false, mode: "consoleOutput" });
  } catch (e) {}
}
// Scratch jobs dir for standalone runs. Unlike the project flows there is no
// staging cache to hand back (nothing is restored into a project), so a temp
// dir under os.tmpdir() carries the manifest/result/log triple; failure
// alerts point at its backend.log for post-mortem.
function tempJobsDir(prefix) {
  try { return fs.mkdtempSync(path.join(os.tmpdir(), prefix)); } catch (e) {}
  return path.join(os.tmpdir(), prefix + "-" + Date.now()); // runBackend mkdirs it
}

// The host app's own condensed CJK face (e2e-fix1): no font is bundled with
// the addon. common.getRootDir() (www/js/common.js:3024) resolves the app
// root from addon context too. Missing file => alert + "" so callers skip
// ONLY the font stage (strings/textures are still patched).
// e2e-fix9: the path-only half (fontTtfPath) exists so the EXPORT handler can
// run the same resolution WITHOUT the alert — by the time it resolves, the
// loading overlay is already up, and a native alert under it goes unseen
// (the buried-dialog lesson from e2e-fix3). The export records the problem
// and alerts after its overlay has closed instead.
function fontTtfPath() {
  return path.join(common.getRootDir(), "www", "core", "SEP", "SLRCondensedCJK-Regular.ttf");
}
// v2.3 custom fonts: the SLR default face for the Latin/UI TMP bake role.
function defaultLatinTtfPath() {
  return path.join(common.getRootDir(), "fonts", "sucr.ttf");
}
// Resolution for one font role: a user-supplied path wins when the file
// exists; otherwise the SLR default is used (with a deferred warning naming
// the missing custom path); when NEITHER exists the role has no file (its
// stage skips). Warnings land in the export overlay console after the run —
// the same deferred pattern as every other export warning. `label`/
// `missingPhrase` keep the historic fixed-font wording for the no-custom
// case ("SLRCondensedCJK font not found in SLR installation …").
function resolveFontRole(customPath, defaultPath, label, missingPhrase) {
  if (customPath && fsh.isFile(customPath)) return { path: customPath, warning: "" };
  if (defaultPath && fsh.isFile(defaultPath)) {
    return {
      path: defaultPath,
      warning: customPath
        ? ("WARNING: Custom " + label + " font not found (" + customPath + ") — using the SLR default font (" + defaultPath + ").")
        : ""
    };
  }
  return {
    path: "",
    warning: customPath
      ? ("WARNING: Custom " + label + " font not found (" + customPath + ") and the SLR default is missing too (" +
         (defaultPath || "unknown location") + ") — skipping this font stage (strings are still patched).")
      : ("WARNING: " + label + " font not found in SLR installation (expected: " +
         (defaultPath || "?") + ") — " + missingPhrase)
  };
}
function resolveFontTtf() {
  const ttf = fontTtfPath();
  if (fsh.isFile(ttf)) return ttf;
  alert("SLRCondensedCJK font not found in SLR installation\n(expected: " + ttf + ")\n\n" +
        "Continuing without the font swap.");
  return "";
}

// (b) Extract textures: <gameRoot>_unity_img next to the RESOLVED Unity root.
// e2e-fix10: the success summary is console lines now (the overlay log IS the
// report — the user reads it at leisure once loadingEnd reveals Close, which
// is the FINAL statement on the success path); only failures alert, after
// their endLoading. The endLoading closure keeps loadingEnd EXACTLY ONCE on
// every path: the try/finally around the post-backend tail is the catch-all
// for an unexpected throw (the overlay still closes, the rejection
// propagates).
async function unityRunExtractTextures(gameDir) {
  const outDir = siblingDir(gameDir, "_unity_img");
  const jobsDir = tempJobsDir("slr-unity-img-");
  uiShowLoading();
  uiLine("Unity textures", "Extracting textures to " + outDir + " — progress in this console.");
  let res = null, err = null;
  try {
    res = await runBackend(
      { version: 2, command: "extract_textures", game_dir: gameDir, out_dir: outDir },
      { jobsDir, python: RUNTIME, basePath: addonDir,
        onProgress: (p) => uiLine("Unity textures", p.done + "/" + p.total + "  " + path.basename(p.file || "")) });
  } catch (e) { err = e; }
  let ended = false;
  const endLoading = () => { if (ended) return; ended = true; uiLoadingEnd(); };
  try {
    if (err || !res.ok) {
      endLoading(); // failures end the overlay FIRST — no alert under it
      alert("Texture extraction failed: " + (err ? (err.message || String(err)) : (res.error || "unknown")) +
            "\nLog: " + path.join(jobsDir, "backend.log"));
      return;
    }
    uiLine("Unity textures", "Extraction complete");
    uiLine("Unity textures", "output: " + outDir);
    uiLine("Unity textures", "next: 1) Delete the images you don't want translated (in that folder).");
    uiLine("Unity textures", "next: 2) Create an SEP project on that folder (New Project \u2192 SEP).");
    uiLine("Unity textures", "next: 3) Translate the images in SEP, then export SEP's rendered images to a folder.");
    uiLine("Unity textures", "next: 4) Come back here (New Project \u2192 SLRUnity) and use \"Insert translated images\".");
  } finally {
    endLoading(); // the TRUE end — last on success, exactly once on every path
  }
}

// (c) Insert translated images: a patch run with EMPTY strings (temp {}
// translations + temp {} index satisfy the manifest shape; the backend filters
// blanks and copies the game) plus the texture stage pointing extract-dir
// (manifest) and rendered-dir (PNGs) at each other. Optionally the font swap.
// e2e-fix7: the job speaks contract v2 (version:2); behavior is unchanged —
// no patch_mode, so the backend still makes a full game copy.
// e2e-fix10: summary as console lines + endLoading LAST (see the extract tool
// above); only the failure path alerts, after its endLoading.
async function unityRunInsertImages(o) {
  const jobsDir = tempJobsDir("slr-unity-insert-");
  const transPath = path.join(jobsDir, "translations.json");
  const indexPath = path.join(jobsDir, "index.json");
  fs.writeFileSync(transPath, "{}", "utf-8");
  fs.writeFileSync(indexPath, "{}", "utf-8");
  let ttf = "";
  if (o.wantFont) ttf = resolveFontTtf();
  uiShowLoading();
  uiLine("Unity images", "Inserting translated images — copying the game to " + o.outDir + " (progress in this console).");
  let res = null, err = null;
  try {
    res = await runBackend(
      { version: 2, command: "patch", game_dir: o.gameDir, out_dir: o.outDir,
        translations_path: transPath, index_path: indexPath,
        font_ttf: ttf || undefined,
        textures_manifest_dir: o.extractDir, textures_dir: o.renderedDir },
      { jobsDir, python: RUNTIME, basePath: addonDir,
        onProgress: (p) => uiLine("Unity images", p.done + "/" + p.total + "  " + path.basename(p.file || "")) });
  } catch (e) { err = e; }
  let ended = false;
  const endLoading = () => { if (ended) return; ended = true; uiLoadingEnd(); };
  try {
    if (err || !res.ok) {
      endLoading(); // failures end the overlay FIRST — no alert under it
      alert("Inserting translated images failed: " + (err ? (err.message || String(err)) : (res.error || "unknown")) +
            "\nLog: " + path.join(jobsDir, "backend.log"));
      return;
    }
    uiLine("Unity images", "Insert complete");
    uiLine("Unity images", "output: " + o.outDir);
    uiLine("Unity images", "textures imported: " + (res.textures_imported != null ? res.textures_imported : 0));
    if (ttf && res.fonts_swapped != null) uiLine("Unity images", "fonts swapped: " + res.fonts_swapped); // only when the stage was armed
    uiLine("Unity images", "apply: run the game from that folder (this is a full game copy).");
  } finally {
    endLoading(); // the TRUE end — last on success, exactly once on every path
  }
}

// (d) Standalone demosaic (v2.8): the export's demosaic stage without a
// project. A patch_mode backend run (out_dir receives ONLY the changed files)
// followed by a zip of that staging tree — archive root = game-relative
// paths, the same drop-in shape as the export patch — at
// <Game>_demosaic.zip next to the game. Owns the overlay lifecycle exactly
// like the other standalone tools; failures alert AFTER their endLoading.
async function unityRunDemosaic(gameDir, opts) {
  const zipPath = siblingDir(gameDir, "_demosaic.zip");
  const jobsDir = tempJobsDir("slr-unity-demosaic-");
  const outDir = path.join(jobsDir, "out");
  const dj = { enabled: true, level: opts.level };
  if (opts.reveal) dj.reveal = true;   // no undefined-valued keys: deepEqual
  if (opts.code) dj.code = true;       // callers see a clean shape
  uiShowLoading();
  uiLine("Unity demosaic", "Scanning " + path.basename(gameDir) +
    " for mosaic censorship layers (level " + opts.level +
    (opts.reveal ? ", reveal" : "") + (opts.code ? ", code" : "") +
    ") — progress in this console.");
  let res = null, err = null;
  try {
    res = await runBackend(
      { version: 2, command: "demosaic_patch", game_dir: gameDir, out_dir: outDir,
        demosaic: dj },
      { jobsDir, python: RUNTIME, basePath: addonDir,
        onProgress: (p) => uiLine("Unity demosaic", p.done + "/" + p.total + "  " + path.basename(p.file || "")) });
  } catch (e) { err = e; }
  let ended = false;
  const endLoading = () => { if (ended) return; ended = true; uiLoadingEnd(); };
  try {
    if (err || !res.ok) {
      endLoading(); // failures end the overlay FIRST — no alert under it
      alert("Demosaic failed: " + (err ? (err.message || String(err)) : (res.error || "unknown")) +
            "\nLog: " + path.join(jobsDir, "backend.log"));
      return;
    }
    // Code-tier lines first (they can be the only output on a game whose
    // mosaic lives in code), then the object/material summary.
    if (res.demosaic_code_methods != null || res.demosaic_code_types != null) {
      if (res.demosaic_code_methods) {
        uiLine("Unity demosaic", "Demosaic code: " + res.demosaic_code_methods +
          " censor method(s) stubbed across " + (res.demosaic_code_types || 0) +
          " type(s) in " + (res.demosaic_code_files || 0) + " managed DLL(s)");
        for (const line of (res.demosaic_code_log || []).slice(0, 20))
          uiLine("Unity demosaic", "  " + line);
      } else if (res.demosaic_code_il2cpp) {
        uiLine("Unity demosaic", "Demosaic code: this game compiles to native (IL2CPP) — " +
          "code-level censorship cannot be patched statically");
      } else if ((res.demosaic_code_types || 0) === 0) {
        uiLine("Unity demosaic", "Demosaic code: no mosaic-named types found in the managed DLLs");
      } else {
        uiLine("Unity demosaic", "Demosaic code: " + (res.demosaic_code_types || 0) +
          " type(s) found but no stubbable methods (see backend.log)");
      }
    }
    const changed = (res.exported_files || []).length;
    if (!changed) {
      uiLine("Unity demosaic", "No mosaic candidates were patched in this game" +
        (opts.level === "high" ? " — try the medium detection level if you expected some" : "") + ".");
      return; // finally ends the overlay; nothing was written
    }
    uiLine("Unity demosaic", "Demosaic: " + (res.demosaic_objects || 0) + " object(s) hidden, " +
      (res.demosaic_renderers || 0) + " extra renderer(s) hidden, " +
      (res.demosaic_materials || 0) + " material(s) neutered" +
      (res.demosaic_revealed ? ", " + res.demosaic_revealed + " revealed (display shader, textures kept)" : "") +
      (res.demosaic_files ? " in " + res.demosaic_files + " file(s)" : ""));
    for (const line of (res.demosaic_log || []).slice(0, 20))
      uiLine("Unity demosaic", "  " + line);
    const shownLog = (res.demosaic_log || []).length;
    if (shownLog > 20) uiLine("Unity demosaic", "  … and " + (shownLog - 20) + " more (backend.log)");
    // Zip the staging contents so the patch lands at the archive root — the
    // export's zip phase pattern (aSpawn + the host's 7z; common.pack would
    // wrap a folder around it and there is no exit-code visibility).
    uiLine("Unity demosaic", "Packing " + changed + " changed file(s) into " + path.basename(zipPath) + "…");
    let zipOk = false, zipErr = "";
    if (typeof common.aSpawn === "function" && typeof common.get7zipBin === "function") {
      try {
        const code = await new Promise((resolve) => {
          common.aSpawn(common.get7zipBin(), ["a", "-tzip", zipPath, "*"], {
            cwd: outDir,
            onDone: function (r, c) { resolve(c); },
            onError: function (d) { zipErr += (d ? d.toString() : ""); },
          });
        });
        let size = -1;
        if (code === 0) { try { size = fs.statSync(zipPath).size; } catch (e) {} }
        zipOk = code === 0 && size > 22; // 22 bytes = empty zip (export's guard)
      } catch (e) {
        zipErr += "\n" + (e && e.message ? e.message : String(e));
      }
    }
    if (!zipOk) {
      endLoading(); // failures end the overlay FIRST — no alert under it
      alert("Failed to pack the demosaic zip." + (zipErr ? "\n" + zipErr : "") +
            "\n\nThe patched files are still available at:\n" + outDir);
      return;
    }
    try { fs.rmSync(outDir, { recursive: true, force: true }); } catch (e) {}
    uiLine("Unity demosaic", "Demosaic complete");
    uiLine("Unity demosaic", "archive: " + zipPath);
    uiLine("Unity demosaic", "apply: extract the zip's contents over the game folder. To undo, remove the files it contains.");
  } finally {
    endLoading(); // the TRUE end — last on success, exactly once on every path
  }
}

// ---------------------------------------------------------------------------
// Export (Task 5).
//
// SLR export flow, verified against the real sources:
//   - Trans.prototype.export (www/js/trans-full.js:2077-2109) defaults
//     options.mode to "dir" (or "zip" from ui.js:6850) and calls
//     engines[engine].exportHandler.apply(this, [file, options]); a truthy
//     return halts the built-in PHP export.
//   - The export dialog (www/js/ui.js:2148-2205) renders
//     getProperty("exportOptionFields") through CustomFields (jsonform) and,
//     on Export Now, puts CustomFields.getValues() — a FLAT map from FormData,
//     so checked checkboxes arrive as "on", unchecked are absent, text fields
//     are strings — under options.custom. That is the nesting the custom
//     parser reads back as this.options?.custom?.readEncoding
//     (CustomParserLoader.js:81). Our field is therefore read from
//     options.custom.unitySwapFonts.
// e2e-fix6: export = the STRINGS project (+ optional font swap) only. The
// SEP image round-trip no longer has any hook here — it lives on the New
// Project slide's standalone "Extract textures" / "Insert translated images"
// tools, which patch a game copy without a project. options.unity.imgDir is
// gone with it (extraction no longer glues itself to a project).
//
// e2e-fix9 (issue 1): EXPORT PROGRESS UI. The handler used to run silently —
// the export dialog's "Export Now" closes only its own preparation dialog
// (ui.js:6879), the engine-halted branch in Trans.prototype.export
// (trans-full.js:2085-2093) returns BEFORE the built-in export's
// ui.showLoading() (trans-full.js:2110) ever runs, and Trans.prototype.export
// does NOT wrap the engine call in its own try/catch — so nothing showed any
// progress until the summary alert, and a throw here would strand whatever
// UI was up. The handler therefore owns the standard overlay lifecycle
// exactly the way createProjectFromEngine does (trans-full.js:641-646):
//   (a) ui.showLoading() + a "Processing" preamble line at the very start;
//   (b) backend progress lines routed into the overlay console — exportProgress
//       -> uiLogLine, and the overlay is UP by then, so the router takes the
//       ui.loadingProgress("Unity export", done+"/"+total+"  "+basename,
//       {consoleOnly:false, mode:"consoleOutput"}) path;
//   (c) ui.loadingEnd() BEFORE every alert — a native dialog under the
//       overlay goes unseen (the e2e-fix3 buried-dialog lesson), so even the
//       synchronous guard alerts fire only after the overlay closed;
//   (d) an exactly-once guarantee on ALL paths: `endLoading` below is
//       idempotent, every exit path calls it (guards explicitly, a backend
//       throw via its own catch, everything else via the trailing
//       try/finally that also covers a sync throw between the guards).
// e2e-fix10: the overlay now lives until the work is TRULY done. The old
// `finally { endLoading(); hideProgress(); }` wrapped ONLY the runBackend
// await, but substantial work follows it — the verbatim revert copies (can be
// hundreds of MB) and the zip step — so the overlay already showed loadingEnd's
// "Log ended!" while the export was still running. Now:
//   - the backend failure path is `if (!res.ok) { endLoading(); alert(...); }`
//     (failures still end the overlay first, alert after);
//   - the revert phase logs per file ("Including original i/N  <rel>") BEFORE
//     each copy, through exportLine (ui.log -> the overlay console);
//   - the zip phase logs "Packing archive…" and every remaining failure path
//     ends the overlay before its alert;
//   - the SUCCESS SUMMARY IS THE CONSOLE LOG (no success alert anymore):
//     "Export complete", mode/counts/output/apply lines — and loadingEnd is
//     the FINAL statement, so Close appears only when everything is done and
//     the user can read the whole log at leisure.
// Audited return statements: the bogus-mode `return false` sits BEFORE the
// overlay opens (SLR's PHP export owns the UI there); every other exit is
// behind endLoading(). The export dialog window itself needs no handling:
// only the preparation dialog closes on Export Now, the file-picker dialog
// stays open behind the overlay (same as for the built-in export) — fine,
// the full-screen overlay covers the main window either way.
engines.addHandler("unity", "exportHandler", async function (targetPath, options) {
  if (!options || (options.mode !== "dir" && options.mode !== "zip")) return false; // customParser precedent (CustomParserLoader.js:418)

  const customEarly = options.custom || {};
  const fullCopyEarly = customEarly.unityExportKind === "full";
  uiShowLoading();
  uiLine("Processing", "Unity export (" + (fullCopyEarly ? "full game copy" : "patch") + ")");
  // The exactly-once closer for this handler's overlay (see (d) above).
  let overlayClosed = false;
  const endLoading = () => { if (overlayClosed) return; overlayClosed = true; uiLoadingEnd(); };
  try {

  const proj = trans.project;
  const unity = proj && proj.options && proj.options.unity;
  if (!unity) {
    endLoading();
    alert("Unity project metadata missing — re-create the project.");
    return true;
  }

  // Data rows are [jp, translation] (projectBuilder.buildProject). The backend
  // filters blanks itself (contract v2); skipping them here keeps the
  // strings_written count and the full-copy guard below honest.
  const translations = {};
  for (const file of Object.values(proj.files || {}))
    for (const row of file.data || [])
      if (row[0] && typeof row[1] === "string" && row[1].trim()) translations[row[0]] = row[1];

  const custom = options.custom || {};
  // v2.6o: remember this selection for the next export dialog — the
  // exportOptionFields thunk below seeds the schema defaults from it (SLR
  // rebuilds the dialog from that object on every open). Best-effort: no
  // localStorage (node tests) just means no memory.
  unitySaveLastOptions(custom);
  const swapFonts = !!custom.unitySwapFonts; // FormData: "on" when checked, absent when not
  // v2.5 demosaic (opt-in, default off like the font swap): hide/neutralize
  // the game's mosaic censorship layers in the export. The backend stage
  // re-detects candidates over the export scope (staged tree in patch mode,
  // whole-game copy in full mode) and reports per action.
  const demosaic = !!custom.unityDemosaic;
  const demosaicLevel = custom.unityDemosaicLevel === "medium" ? "medium" : "high";
  // reveal tier (UUD ShaderReplace/MaterialReplace behavior): keep all-mosaic
  // skinned renderers and display-swap their materials instead of hiding —
  // the option to try when the standard treatment makes parts disappear.
  const demosaicReveal = !!custom.unityDemosaicReveal;
  // deep opt-in (UUD DumbTypeDemosaic equivalent): stub mosaic-named censor
  // Behaviours in the managed DLLs. Mono games only — IL2CPP code is native
  // and is reported not-patchable instead.
  const demosaicCode = !!custom.unityDemosaicCode;
  // text-fit calibration (advanced, opt-in): scale char-count-based text
  // box sizing for Latin text — the fix for sliding/overflowing bubbles.
  const textFit = !!custom.unityTextFit;
  // v2.6j: the width price is now a RATIO of each site's own original
  // constant (0.5 = Latin advance ≈ half the full-width Japanese price),
  // so "tick the box" is the whole calibration — no per-game number. The
  // backend clamps too; 1 leaves every site byte-identical.
  const textFitRatio = Math.max(0.05, Math.min(1,
    parseFloat(custom.unityTextFitRatio) || 0.5));
  // Latin advance scale for the font swap: eroge pixel fonts price Latin
  // much tighter than the CJK face we swap in — auto-sized bubble boxes
  // inflate for English. 1 = unchanged, lower = tighter Latin.
  const fontLatinScale = Math.max(0.1, Math.min(2,
    parseFloat(custom.unityFontLatinScale) || 1));
  // e2e-fix7 (contract v2): the export-kind select. FormData delivers plain
  // strings; "full" = v1 whole-game copy, anything else (absent, stale,
  // "patch") = the new file-replacement patch mode.
  const fullCopy = custom.unityExportKind === "full";
  // SLR's file selection (trans-full.js:2091-2098): the checked files in the
  // project's file list arrive as options.files — the project file keys,
  // which for this engine ARE the game-relative paths (forward slashes).
  // e2e-fix8 BUG 2 (user report "export everything still blocked"): SLR's
  // semantics (rmmv.js:1539, SEP.js:502: `if (empty(files)) process ALL`)
  // make an EMPTY list mean EXPORT EVERYTHING — a non-empty array means ONLY
  // those files. The old code read empty as "no selection" and blocked.
  const selectedFiles = (options && Array.isArray(options.files))
    ? options.files.filter((f) => typeof f === "string" && f) : [];
  // e2e-fix8: the nothing-to-export guard is FULL-COPY ONLY. Patch mode can
  // ALWAYS do something legitimate — with no translations and no font it
  // exports the staged originals of every file in scope (an all-originals
  // baseline / revert patch) — so the guard never applies to it. A demosaic-
  // only full copy is also legitimate work (v2.5).
  if (fullCopy && !Object.keys(translations).length && !swapFonts && !demosaic) {
    endLoading();
    alert("Nothing to export in full game copy mode: no translations are filled in and the font swap is off.\n\n" +
          "A full copy of an unchanged game would be a no-op. Use the patch export kind instead — " +
          "it writes translated files plus verbatim originals (revert) for everything else in scope, " +
          "so even with no changes it produces a valid all-originals patch.");
    return true; // halt PHP export
  }
  // e2e-fix1: the addon uses the HOST APP's own condensed CJK face,
  // SLRCondensedCJK-Regular.ttf (www/core/SEP), for the optional font swap —
  // resolveFontTtf() is the insert tool's alerting variant. The export can't
  // call it: by now the overlay is up and its alert would be buried (see
  // (c) above), so the same check runs non-alerting here (ONLY when the swap
  // is armed — untouched hosts may not even have common.getRootDir) and the
  // warning is surfaced AFTER the overlay closed, right before the summary.
  // v2.3 CUSTOM FONTS: each role is user-overridable from the export options
  // (options.custom.unityFontCjk / unityFontLatin, absolute .ttf/.otf paths,
  // empty = SLR default). Roles resolve independently: custom when the file
  // exists, else the SLR default, else the stage skips with a deferred
  // warning. Non-empty values persist into the addon config so the next
  // export's dialog prefills them (setConfig writes
  // sys.config.addons.package[<addon>] and saves).
  const customCjk = swapFonts ? String(custom.unityFontCjk || "").trim() : "";
  const customLatin = swapFonts ? String(custom.unityFontLatin || "").trim() : "";
  try {
    if (thisAddon.getConfig("fontCjk") !== customCjk) thisAddon.setConfig("fontCjk", customCjk);
    if (thisAddon.getConfig("fontLatin") !== customLatin) thisAddon.setConfig("fontLatin", customLatin);
  } catch (eCfg) { console.warn("unity: persisting font options failed:", eCfg); }
  const cjkRes = resolveFontRole(customCjk, swapFonts ? fontTtfPath() : "",
    "SLRCondensedCJK", "continuing without the font swap.");
  const latinRes = resolveFontRole(customLatin, swapFonts ? defaultLatinTtfPath() : "",
    "Stint Ultra Condensed", "TMP font atlases are NOT re-rendered; the game keeps its original baked fonts.");
  const fontWarnings = swapFonts ? [cjkRes.warning, latinRes.warning].filter(Boolean) : [];
  const ttf = cjkRes.path; // legacy embedded-font swap + the CJK atlas bake role
  // e2e-fix19 BAKED TMP ATLASES: static TMP_FontAssets render ONLY their
  // baked glyphs in this player (dynamic conversion is dead — e2e-fix18), so
  // re-fonting means REGENERATING those atlases at export time. Role map:
  // assets whose baked charset holds CJK (the JP dialogue font) bake from
  // the dialogue face (SLRCondensedCJK or the user's custom CJK font) —
  // untranslated Japanese keeps rendering — everything else (the default UI
  // font) bakes from the UI face (Stint Ultra Condensed or the user's custom
  // Latin font). A missing face skips the atlas bake (the legacy swap above
  // still runs when its own face resolved) and the deferred warnings say so.
  const fontBake = (swapFonts && cjkRes.path && latinRes.path)
    ? { latin_ttf: latinRes.path, cjk_ttf: cjkRes.path } : undefined;

  // e2e-fix7 FULL COPY mode: the v1 behavior, run against the ORIGINAL game
  // folder (options.unity.gameDir — the only thing that still needs it). A
  // self-contained project whose original is gone cannot full-copy: halt with
  // a clear pointer to patch export, BEFORE any backend call.
  const gameDir = fullCopy ? unity.gameDir : (unity.stagedGameDir || unity.gameDir);
  if (fullCopy && !fsh.isDir(gameDir)) {
    endLoading();
    alert("Full copy export needs the original game folder (" + gameDir + "), which is no longer available.\n\n" +
          "Use patch export — the project is self-contained (the scan copied every string-bearing file into the project).");
    return true;
  }

  const jobsDir = path.join(proj.cache.cachePath, "jobs");
  fs.mkdirSync(jobsDir, { recursive: true });
  const transPath = path.join(jobsDir, "translations.json");
  fs.writeFileSync(transPath, JSON.stringify(translations), "utf-8");
  const indexPath = path.join(proj.cache.cachePath, "data", "index.json");
  if (!fsh.isFile(indexPath)) {
    endLoading();
    alert("Scan index missing from staging — re-create the project.");
    return true;
  }

  let outDir = targetPath;
  if (options.mode === "zip") outDir = path.join(jobsDir, "zip-out-" + Date.now());

  // e2e-fix13 FONT SOURCE (patch mode + swap armed only): the staged game
  // only carries the files staging copied — projects created BEFORE font
  // files rode along (or projects whose font copy failed) would swap just a
  // subset of the embedded fonts. When the ORIGINAL game folder captured at
  // creation is still present, hand it to the backend as font_source_dir so
  // the font stage lists the complete catalog from there (only font-bearing
  // files travel into the patch; the original is never written). Missing /
  // vanished gameDir falls back to the staged-only behavior SILENTLY — the
  // swap degrades, the export still succeeds. Full-copy mode stays as-is:
  // its in-place swap runs on a whole copy of the original, already complete.

  // Contract v2 patch manifest (docs/CONTRACT.md "patch"): patch_mode:true
  // exports ONLY the files modified this run, as whole-file replacements at
  // their game-relative paths, read from the STAGED game (self-contained
  // project; old v1 projects fall back to gameDir — the backend re-resolves
  // index abs paths against game_dir when stale). Full-copy mode omits
  // patch_mode entirely = the v1 whole-game behavior. The font key is an
  // optional stage, present only when it should run. Texture keys never
  // appear here (e2e-fix6) — image insertion is a standalone tool.
  // The backend job. e2e-fix10: NO finally-endLoading here anymore — the
  // overlay must stay UP through the revert copies and the zip step that
  // follow (substantial work; the old finally let the overlay show loadingEnd's
  // "Log ended!" while the export was still running). The overlay closes when
  // the work is TRULY over: a throw closes it and rethrows (no alert — SLR
  // reports the engine failure), an ok:false closes it before the failure
  // alert, and the success path ends with it as the FINAL statement below.
  let res;
  try {
    const fontSourceDir = (!fullCopy && swapFonts && unity.gameDir && fsh.isDir(unity.gameDir))
      ? unity.gameDir : undefined;
    // v2.6j: armed-state line at export START. The per-stage summary lines
    // only print at the END, so an export whose calibration checkbox was
    // silently un-armed (SLR resets every option each export dialog) was
    // indistinguishable from a broken stage — a whole "still no change"
    // round-trip cost nothing but confusion. Now the state is visible
    // before any work runs; "off" next to a feature you meant to enable is
    // the signal to cancel, re-open the dialog and re-tick.
    exportLine("Unity export",
      "Options armed: font swap " + (swapFonts ? "on" : "off") +
      ", demosaic " + (demosaic ? demosaicLevel +
        (demosaicReveal ? "+reveal" : "") + (demosaicCode ? "+code" : "") : "off") +
      ", text-fit " + (textFit ? "ON (width ratio x" + textFitRatio + ")" : "off") +
      ", Latin font scale " + fontLatinScale +
      ", export kind " + (fullCopy ? "full game copy" : "patch"));
    res = await runBackend(
      { version: 2, command: "patch", game_dir: gameDir, out_dir: outDir,
        translations_path: transPath, index_path: indexPath,
        patch_mode: fullCopy ? undefined : true,
        font_ttf: ttf || undefined,
        font_source_dir: fontSourceDir || undefined,
        font_bake: fontBake,
        font_latin_scale: (swapFonts && fontLatinScale !== 1)
          ? fontLatinScale : undefined,
        demosaic: (() => {
          if (!demosaic) return undefined;
          const dj = { enabled: true, level: demosaicLevel };
          if (demosaicReveal) dj.reveal = true;   // no undefined-valued keys:
          if (demosaicCode) dj.code = true;       // deepEqual callers see a clean shape
          return dj;
        })(),
        textfit: textFit ? { enabled: true, char_ratio: textFitRatio } : undefined },
      { jobsDir, python: RUNTIME, basePath: addonDir, onProgress: exportProgress });
  } catch (e) {
    endLoading(); // the overlay must not outlive the handler
    hideProgress();
    throw e;
  }

  if (!res.ok) {
    endLoading(); // (c): failures end the overlay FIRST — the alert after it is seen
    hideProgress();
    alert("Unity patch failed: " + (res.error || "") + "\nLog: " + path.join(jobsDir, "backend.log"));
    return true;
  }

  // e2e-fix1 font swap, deferred warning (e2e-fix9, reworked e2e-fix10): the
  // font file was resolved non-alerting above because the overlay was up; the
  // notice is a console WARNING line in the same overlay log as the summary
  // now (success paths alert nothing at all anymore). Skipping ONLY the font
  // stage (strings/textures still patched) stays the behavior.
  // e2e-fix1/e2e-fix19 deferred warnings, v2.3: font resolution problems are
  // a console WARNING line in the same overlay log as the summary (skipping
  // ONLY the font stage — strings/textures still patched — stays the
  // behavior). resolveFontRole produced them: a missing custom path that
  // fell back to the SLR default, or a role with no file at all (stage
  // skipped). Success paths warn nothing.
  for (const fw of fontWarnings) exportLine("Unity export", fw);

  // e2e-fix7/8 revert support (patch mode only): every file IN SCOPE that the
  // backend did NOT export (not in res.exported_files) gets its staged
  // ORIGINAL copied verbatim into the patch, so the output always covers its
  // whole scope: translated files carry the new strings, everything else
  // reverts to the original bytes (apply the patch over a previous export to
  // revert). Scope follows SLR's selection semantics: the selected files when
  // one was passed, otherwise ALL project files (empty selection = export
  // everything). This runs BEFORE the zip step, so zip mode archives the
  // reverted originals too.
  // e2e-fix10: the overlay stays UP through this phase and each copy emits a
  // console line BEFORE it starts ("Including original i/N  <rel>") —
  // hundreds of MB can flow here and the log must show the export is still
  // alive. A copy failure ends the overlay, then alerts with the failing file
  // (the output written so far stays in place, partial).
  let reverted = 0;
  if (!fullCopy) {
    const exported = new Set(res.exported_files || []);
    const scope = selectedFiles.length ? selectedFiles : Object.keys(proj.files || {});
    const revertList = [];
    for (const rel of scope) {
      if (exported.has(rel)) continue;
      if (!fsh.isFile(path.join(gameDir, rel))) continue; // not staged (v1 project) / deleted
      revertList.push(rel);
    }
    let i = 0;
    for (const rel of revertList) {
      i++;
      exportLine("Unity export", "Including original " + i + "/" + revertList.length + "  " + rel);
      const dst = path.join(outDir, rel);
      try {
        fs.mkdirSync(path.dirname(dst), { recursive: true });
        fs.copyFileSync(path.join(gameDir, rel), dst);
      } catch (e) {
        endLoading(); // end the overlay first — no alert under it
        hideProgress();
        alert("Unity export failed: could not copy the original " + rel + "\n" +
              (e && e.message ? e.message : String(e)) +
              "\n\nThe partial export output is at:\n" + outDir);
        return true;
      }
      reverted++;
    }
  }

  // e2e-fix10 ZIP PHASE: announced in the overlay console ("Packing
  // archive…") — 7z over a whole game takes a while and the overlay must show
  // it is still working; every failure below ends the overlay BEFORE its alert.
  if (options.mode === "zip") {
    exportLine("Unity export", "Packing archive…");
    // Zip the CONTENTS of the staging folder so the patched game files land
    // at the archive ROOT. Task 5 review finding: common.pack(from, to)
    // (www/js/common.js:2232-2244) spawns 7z as ["a", to, from, "-r"], which
    // archives the FOLDER ITSELF as a wrapper directory — the tap/game would
    // unzip into <outDir-name>/... instead of the archive root — and
    // common.aSpawn RESOLVES even on non-zero exit, so pack() returning a
    // truthy path proves nothing. Fix per SLR's own precedent
    // (www/core/rmmv/rmmv.js:3046-3075): spawn 7z with cwd = the staging
    // folder and a bare "*" wildcard (portable; "dir\*" only matches on
    // Windows), and read the exit code from aSpawn's onDone(result, code)
    // callback (common.js:2133-2191 — the promise only rejects on spawn
    // errors like a missing binary).
    if (typeof common.aSpawn === "function" && typeof common.get7zipBin === "function") {
      let zipCode = -1;
      let zipStderr = "";
      try {
        await common.aSpawn(common.get7zipBin(), ["a", "-tzip", targetPath, "*"], {
          cwd: outDir,
          onDone: function (result, code) { zipCode = code; },
          onError: function (data) { zipStderr += (data ? data.toString() : ""); },
        });
      } catch (e) {
        zipStderr += "\n" + (e && e.message ? e.message : String(e));
      }
      if (zipCode !== 0) {
        endLoading(); // e2e-fix10: end the overlay first — the alert must be seen
        hideProgress();
        alert("Failed to create the zip archive (7z exited with code " + zipCode + ")." +
              (zipStderr ? "\n" + zipStderr : "") +
              "\n\nThe export output is still available at:\n" + outDir);
        return true; // halt; do NOT claim success
      }
      // Safety net (rmmv.js:3058-3075): verify the archive is readable and
      // not empty before declaring success — an empty zip is only 22 bytes.
      let verifyCode = -1;
      try {
        await common.aSpawn(common.get7zipBin(), ["t", targetPath], {
          onDone: function (result, code) { verifyCode = code; },
        });
      } catch (e) { /* spawn errors leave verifyCode at -1 -> failure path */ }
      let zipSize = -1;
      try { zipSize = fs.statSync(targetPath).size; } catch (e) {}
      if (verifyCode !== 0 || zipSize <= 22) {
        endLoading(); // e2e-fix10: end the overlay first — the alert must be seen
        hideProgress();
        alert("The generated zip archive is empty or failed its integrity check:\n" + targetPath +
              "\n\nThe export output is still available at:\n" + outDir);
        return true;
      }
      // Full success (zip created + "t" integrity check passed + non-empty):
      // remove the staging tree NOW. SLR's Save Project (.slrproj) packs the
      // ENTIRE staging cache and jobs/ is NOT excluded (trans-full.js:1649-
      // 1652), so a leftover zip-out-<ts> copy of the whole game would
      // balloon every subsequent save to multi-GB. Deletion happens ONLY on
      // this verified-success path — every failure above keeps the dir,
      // because its alert points the user at the staged game. The pack
      // fallback below also keeps it: its success signal is weaker (no
      // integrity check, no exit-code visibility) and its wrapper-folder
      // zip may make the staging dir the only correctly-rooted copy.
      try {
        fs.rmSync(outDir, { recursive: true, force: true });
      } catch (e) {
        // Best-effort cleanup only: the export has already succeeded, so a
        // locked file (AV scanner etc.) must not fail it — warn and leave
        // the dir in place.
        console.warn("unity export: could not remove the zip staging dir (left in place):", outDir,
          e && e.message ? e.message : e);
      }
    } else if (typeof common.pack === "function") {
      // Fallback ONLY when aSpawn / the bundled 7z binary are unavailable.
      // Known caveats (above): wrapper-folder archive root and no exit-code
      // visibility — pack() returns the target path on success, "" on a
      // spawn-side rejection.
      const packed = await common.pack(outDir, targetPath);
      if (!packed) {
        endLoading(); // e2e-fix10: end the overlay first — the alert must be seen
        hideProgress();
        alert("Zip creation failed for:\n" + targetPath + "\n\nThe export output is still available at:\n" + outDir);
        return true;
      }
    } else {
      endLoading(); // e2e-fix10: end the overlay first — the alert must be seen
      hideProgress();
      alert("Zip creation failed for:\n" + targetPath +
            "\n\nNo zip tool is available (common.aSpawn / 7-Zip / common.pack all missing)." +
            "\n\nThe export output is still available at:\n" + outDir);
      return true;
    }
  }

  // e2e-fix10 SUMMARY — console lines, not an alert (user request): the
  // overlay console already carries the whole run (preamble, backend
  // progress, revert lines, warnings, "Packing archive…"), the summary
  // extends it, and loadingEnd below is the FINAL statement — Close appears
  // only now that everything is done, and the log stays readable until the
  // user closes it. No success alert at all anymore.
  exportLine("Unity export", "Export complete");
  exportLine("Unity export", "mode: " + (fullCopy ? "full" : "patch"));
  if (res.strings_written != null)
    exportLine("Unity export", "strings written: " + res.strings_written);
  if (Array.isArray(res.exported_files))
    exportLine("Unity export", "files exported: " + res.exported_files.length);
  if (!fullCopy) // patch mode always reports it (0 = the backend covered every file)
    exportLine("Unity export", "reverted (originals): " + reverted);
  if (res.fonts_swapped != null) // only when the font stage actually ran
    exportLine("Unity export", "fonts swapped: " + res.fonts_swapped);
  // e2e-fix19: the atlas-bake stage reports the re-rendered TMP font atlases.
  if (res.fonts_baked != null)
    exportLine("Unity export", "TMP font atlases re-rendered: " + res.fonts_baked +
      (res.fonts_bake_failed > 0 ? " (skipped: " + res.fonts_bake_failed + " — atlas too small for the charset, kept original)" : ""));
  if (res.latin_scaled_glyphs > 0)
    exportLine("Unity export", "Font swap: Latin advances scaled (x" + fontLatinScale +
      ", " + res.latin_scaled_glyphs + " glyphs) — auto-sized boxes get narrower");
  // e2e-fix15: the TMP stage (runs with the font swap) reports the
  // TextMeshPro font-asset surgery; null-source assets now get a same-file
  // Font assigned (sources_assigned), so a skip means the file had no Font.
  if (res.tmp_assets_made_dynamic > 0)
    exportLine("Unity export", "TMP fonts made dynamic: " + res.tmp_assets_made_dynamic);
  if (res.tmp_assets_skipped_no_source > 0)
    exportLine("Unity export", "TMP fonts skipped (no source font): " + res.tmp_assets_skipped_no_source);
  // v2.5 demosaic summary: what the stage found and did, then up to 20
  // per-action lines. A demosaic-enabled run that found nothing says so —
  // silence would be indistinguishable from a broken stage.
  if (res.demosaic_objects != null || res.demosaic_materials != null) {
    if (res.demosaic_objects || res.demosaic_materials || res.demosaic_renderers
        || res.demosaic_revealed) {
      exportLine("Unity export", "Demosaic: " + (res.demosaic_objects || 0) + " object(s) hidden, " +
        (res.demosaic_renderers || 0) + " extra renderer(s) hidden, " +
        (res.demosaic_materials || 0) + " material(s) neutered" +
        (res.demosaic_revealed ? ", " + res.demosaic_revealed + " revealed (display shader, textures kept)" : "") +
        (res.demosaic_files ? " in " + res.demosaic_files + " file(s)" : ""));
      for (const line of (res.demosaic_log || []).slice(0, 20))
        exportLine("Unity export", "  " + line);
      const shown = (res.demosaic_log || []).length;
      if (shown > 20) exportLine("Unity export", "  … and " + (shown - 20) + " more (backend.log)");
    } else {
      exportLine("Unity export", "Demosaic: enabled, but no mosaic candidates were found in this project" +
        (unity.demosaicFound === undefined
          ? " — this project predates mosaic scanning; re-create it from the game folder for staged coverage (or use the full game copy export kind)"
          : " (this game may not use named mosaic layers)"));
    }
    if (res.sizing_rewritten != null) {
      if (res.sizing_rewritten > 0) {
        exportLine("Unity export", "Text-fit: recalibrated " + res.sizing_rewritten +
          " char-count sizing site(s) in " + (res.sizing_files || 0) + " managed DLL(s)" +
          " - check text boxes in game");
        for (const line of (res.sizing_log || []).slice(0, 12))
          exportLine("Unity export", "  " + line);
      } else if ((res.sizing_sites || 0) > 0) {
        exportLine("Unity export", "Text-fit: " + res.sizing_sites +
          " sizing site(s) found but none rewritten (see backend.log)");
      } else if ((res.sizing_log || []).length) {
        // armed but the stage died (CLI fail-soft sets only sizing_log) or
        // had something to say about detection: show it, never stay silent.
        exportLine("Unity export", "Text-fit: ran with nothing rewritten - " +
          (res.sizing_log[0] || "see backend.log"));
      } else {
        exportLine("Unity export", "Text-fit: no char-count sizing pattern found in this game");
      }
    }
    if (res.demosaic_code_methods != null || res.demosaic_code_types != null) {
      if (res.demosaic_code_methods) {
        exportLine("Unity export", "Demosaic code: " + res.demosaic_code_methods +
          " censor method(s) stubbed across " + (res.demosaic_code_types || 0) +
          " type(s) in " + (res.demosaic_code_files || 0) + " managed DLL(s)");
        for (const line of (res.demosaic_code_log || []).slice(0, 20))
          exportLine("Unity export", "  " + line);
      } else if (res.demosaic_code_il2cpp) {
        exportLine("Unity export", "Demosaic code: this game compiles to native (IL2CPP) — " +
          "code-level censorship cannot be patched statically (no BepInEx plugin exists for it either)");
      } else if ((res.demosaic_code_types || 0) === 0) {
        exportLine("Unity export", "Demosaic code: no mosaic-named types found in the managed DLLs");
      } else {
        exportLine("Unity export", "Demosaic code: " + (res.demosaic_code_types || 0) +
          " type(s) found but no stubbable methods (see backend.log)");
      }
    }
  }
  if (res.edits_missed)
    exportLine("Unity export", "WARNING: " + res.edits_missed + " staged edits did not land (see backend.log)");
  exportLine("Unity export", "output: " + outDir);
  if (options.mode === "zip")
    exportLine("Unity export", "archive: " + targetPath);
  if (!fullCopy)
    exportLine("Unity export", "apply: copy the files over the game folder");
  hideProgress(); // clears the bubble fallback (no-op when the overlay took every line)
  endLoading(); // FINAL — the overlay's Close appears here, on the finished log
  // Halt the built-in PHP export (truthy). common.halt() is the canonical
  // truthy token (CustomParserLoader.js:434); fall back to plain true if the
  // SLR build predates it.
  return (typeof common.halt === "function") ? common.halt() : true;

  } finally {
    // (d): the catch-all guarantee — every return above already ran its own
    // endLoading() (no-op by now), so this only acts when something THROWS
    // between the guards or in the post-backend tail (Trans.prototype.export
    // does not catch for us): the overlay still closes exactly once and the
    // rejection propagates.
    endLoading();
    hideProgress();
  }
});

// v2.6o REMEMBER LAST EXPORT OPTIONS: SLR rebuilds the export dialog's
// custom fields on every open (ui.js:2153 CustomFields <- getProperty(
// "exportOptionFields"), and CustomFields CALLS the registered function) and
// jsonform seeds every field from its schema "default" — so the defaults are
// the only addon-reachable place an initial value can come from. The export
// handler saves the raw FormData selection (unitySaveLastOptions) and the
// exportOptionFields thunk below merges it into a fresh schema clone.
// Storage: the app window's localStorage — NW.js (Chromium) persists it
// across restarts. Everything is best-effort: without a store (node-side
// tests, exotic embeds) the built-in defaults apply and nothing crashes.
const UNITY_OPT_STORE_KEY = "slrUnityExportOptions.v1";

// [schema key, isBoolean] — FormData delivers checkboxes as "on"/absent, so
// the save normalizes them into the schema's real booleans; everything else
// stays a string, the schema's other type.
const UNITY_OPT_FIELDS = [
  ["unityExportKind", false], ["unitySwapFonts", true],
  ["unityFontCjk", false], ["unityFontLatin", false],
  ["unityFontLatinScale", false], ["unityTextFit", true],
  ["unityTextFitRatio", false], ["unityDemosaic", true],
  ["unityDemosaicLevel", false], ["unityDemosaicReveal", true],
  ["unityDemosaicCode", true],
];

function _unityOptStore() {
  try {
    const s = window.localStorage;
    return s || null;
  } catch (e) {
    return null; // no window / storage disabled: no memory, defaults stand
  }
}

function unityLoadSavedOptions() {
  const store = _unityOptStore();
  if (!store) return {};
  try {
    const parsed = JSON.parse(store.getItem(UNITY_OPT_STORE_KEY) || "{}");
    return (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      ? parsed : {};
  } catch (e) {
    return {}; // corrupt payload: fall back to the built-in defaults
  }
}

function unitySaveLastOptions(custom) {
  const saved = {};
  for (const [key, isBool] of UNITY_OPT_FIELDS) {
    saved[key] = isBool ? !!(custom && custom[key])
                        : String((custom && custom[key] != null) ? custom[key] : "");
  }
  const store = _unityOptStore();
  if (store) {
    try { store.setItem(UNITY_OPT_STORE_KEY, JSON.stringify(saved)); }
    catch (e) { /* best-effort (quota, privacy mode): defaults stand */ }
  }
  return saved;
}

// The static option schema + form (v2.6m order: kind -> fonts -> text-fit ->
// demosaic). The exportOptionFields thunk clones these per dialog open and
// seeds the defaults from the saved selection.
const UNITY_EXPORT_SCHEMA = {
  "unityExportKind": {
    "title": "Export kind",
    "description": "Patch (default) exports only the files changed this run — a small file-replacement patch you copy over your game folder. Full game copy exports a complete standalone copy of the game (needs the original game folder).",
    "type": "string",
    "default": "patch",
    "enum": ["patch", "full"]
  },
  "unitySwapFonts": {
    "title": "Replace game fonts",
    "description": "Swap the game's embedded Japanese fonts with SLR's condensed CJK font (SLRCondensedCJK) AND re-render the game's TextMeshPro font atlases: Latin/UI text from SLR's Stint Ultra Condensed, Japanese from SLRCondensedCJK (recommended for English text). Use the two font fields below to supply your own fonts instead.",
    "type": "boolean",
    "default": false
  },
  "unityFontCjk": {
    "title": "Custom dialogue font (optional)",
    "description": "Absolute path to your own .ttf/.otf for JAPANESE text: the embedded-font swap AND re-baking of the game's Japanese TextMeshPro atlas. Empty = SLR's SLRCondensedCJK — SLR's Japanese-capable face, chosen here because untranslated Japanese must keep rendering (a Latin-only face would leave tofu). A proportional font works but dialogue may overflow its boxes; a condensed face fits best.",
    "type": "string",
    "default": ""
  },
  "unityFontLatin": {
    "title": "Custom UI font (optional)",
    "description": "Absolute path to your own .ttf/.otf for LATIN/UI text: re-baking of every non-Japanese TextMeshPro atlas (menus, buttons, labels). Empty = SLR's Stint Ultra Condensed — a second, narrower Latin-only face is used here (instead of the dialogue font for everything) because pure UI text never needs Japanese glyphs and this face fits translated English best. Leave both empty to use both SLR faces; put the same font in both fields to force one font everywhere.",
    "type": "string",
    "default": ""
  },
  "unityDemosaic": {
    "title": "Remove mosaic censorship (demosaic)",
    "description": "Detect the game's mosaic censorship layers in its data files and patch them out of the export: mosaic-named objects are hidden and mosaic materials are neutralized (shader swapped for the game's own transparent one). Detection matches names and shader properties and will not fit every game — check the result in game. Your original game is never modified; export again with this off to revert.",
    "type": "boolean",
    "default": false
  },
  "unityDemosaicLevel": {
    "title": "Demosaic detection level",
    "description": "High (recommended) patches only unambiguous candidates: mosaic-named objects/materials/shaders or shaders with explicit mosaic properties. Medium also patches weaker matches, e.g. a shader that only carries the typical mosaic size parameters — more coverage, higher false-positive risk.",
    "type": "string",
    "default": "high",
    "enum": ["high", "medium"]
  },
  "unityDemosaicReveal": {
    "title": "Reveal instead of hide (advanced)",
    "description": "For mosaic materials on modeled body meshes: instead of making them invisible, swap their shader for the game's own display shader and KEEP the textures — the treatment to try when the standard removal makes body parts disappear entirely (mosaic shader applied directly to the real mesh). Dedicated overlay objects are still hidden.",
    "type": "boolean",
    "default": false
  },
  "unityDemosaicCode": {
    "title": "Patch code-level censorship (Mono games, advanced)",
    "description": "Additionally stub mosaic-named censor components in the game's managed DLLs (Assembly-CSharp.dll): their Update/OnEnable/etc. methods are overwritten in place with an immediate return, so the censoring code never runs. Same-size edits, originals untouched, easy to revert by re-exporting — but a malformed patch could crash the game, so this is opt-in and should be tested on a copy. IL2CPP games (no Managed DLLs) are detected but cannot be patched (no static equivalent exists).",
    "type": "boolean",
    "default": false
  },
  "unityTextFit": {
    "title": "Recalibrate text-box sizing (Mono games, advanced)",
    "description": "Fixes speech bubbles / message windows that slide or overflow with English: some games size the box from the string's CHARACTER COUNT (one full-width Japanese glyph per character), which massively over-sizes English. Where that pattern is found in the game's code, the box width is rewritten to scale with actual English length. Same-size edits, originals untouched, revert by re-exporting — but a wrong calibration just looks wrong (cosmetic, iterate freely). IL2CPP games are not applicable.",
    "type": "boolean",
    "default": false
  },
  "unityTextFitRatio": {
    "title": "Text-box width ratio (calibration)",
    "description": "Ratio used by the recalibration above: each sizing site's own per-character price is scaled by this fraction (0.5 halves it). Japanese glyphs are full-width; Latin letters at the same font size run about half that, so 0.5 makes an English line occupy the width the designer gave the Japanese one — leave it unless bubbles come out too wide (lower it) or text overflows its bubble (raise it). 1 = no change. Only meaningful with the recalibration enabled.",
    "type": "string",
    "default": "0.5"
  },
  "unityFontLatinScale": {
    "title": "Latin width scale for the font swap (calibration)",
    "description": "Scales the WIDTH of every Latin letter in the swapped font (shapes stay readable, spacing tightens): eroge pixel fonts price Latin much tighter than the CJK face we swap in, so auto-sized bubbles/message boxes can come out far too wide for English. If auto-sized boxes or bubbles are too wide with the font swap on, set 0.6 and iterate. 1 = unchanged. Only meaningful with Replace game fonts enabled.",
    "type": "string",
    "default": "1"
  }
};

const UNITY_EXPORT_FORM = [
  { "type": "msg", "msg": "<h2>Unity Export Options</h2>" },
  { "key": "unityExportKind",
    "titleMap": {
      "patch": "patch (changed/selected files only)",
      "full": "full game copy"
    } },
  { "key": "unitySwapFonts" },
  { "key": "unityFontCjk" },
  { "key": "unityFontLatin" },
  { "key": "unityFontLatinScale" },
  { "key": "unityTextFit" },
  { "key": "unityTextFitRatio" },
  { "key": "unityDemosaic" },
  { "key": "unityDemosaicLevel",
    "titleMap": {
      "high": "high confidence only (recommended)",
      "medium": "include medium confidence"
    } },
  { "key": "unityDemosaicReveal" },
  { "key": "unityDemosaicCode" }
];

// Export-dialog option fields. Declared like the custom parser's
// (CustomParserLoader.js:444-496): addProperty("exportOptionFields", () =>
// ({ jsonForm: { schema, form } })). jsonform maps "type":"boolean" to a
// checkbox and a "type":"string" with "enum" to a <select> (jsonform.js
// ~3403: schema enum -> formElement.type 'select'). The select's DISPLAY
// labels come from a form-element titleMap — the same convention SLR's own
// options layer uses (www/js/options.js:905-917 builds titleMap for every
// enum option; jsonform.js ~3415 maps value->title through it). Because
// jsonform's "*" wildcard does NOT skip keys already laid out explicitly
// (jsonform.js buildTree expands it over EVERY schema property), the form
// lists both keys by hand instead of using "*" — no duplicated fields.
// v2.6o: the thunk runs on EVERY dialog open (ui.js:2153 + CustomFields
// calls its options argument when it is a function), so it seeds the schema
// defaults with the last export's values — absent/blank saved strings fall
// back to the built-in defaults.
try {
  engines.getEngine("unity").addProperty("exportOptionFields", () => {
    const saved = unityLoadSavedOptions();
    const schema = {};
    for (const key of Object.keys(UNITY_EXPORT_SCHEMA)) {
      const field = Object.assign({}, UNITY_EXPORT_SCHEMA[key]);
      if (Object.prototype.hasOwnProperty.call(saved, key) && saved[key] !== "")
        field.default = saved[key];
      schema[key] = field;
    }
    return {
      jsonForm: {
        schema,
        form: UNITY_EXPORT_FORM.map((f) => Object.assign({}, f))
      }
    };
  });
} catch (e) { console.warn("unity exportOptionFields failed:", e); }

// e2e-fix15 (issue 4, cosmetic): SLR hardcodes the addon-field wrapper as the
// LAST section of the export dialog (trans.html:899 `<form
// class="customFields">`, the last child of #dialogExportPreparation) and
// just renders exportOptionFields into it (ui.js:2149-2159) — the returned
// jsonForm's "form" array only orders fields WITHIN that wrapper, so the
// Unity options used to sit below every built-in field where users missed
// them. The wrapper position itself IS addon-reachable: ui.js:2158 calls
// engines.handler("onOpenExportDialogReady") — engine-scoped (only fires for
// the unity project's engine), AFTER CustomFields resolved — so this hook
// moves the wrapper (our rendered fields inside it) to the top of the
// dialog. Pure DOM reorder on the live document, no SLR file is touched;
// node moves keep listeners/contents, so jsonform's getValues() keeps
// reading the same inputs. Best-effort guarded: a failure here must never
// break opening the export dialog.
try {
  engines.addHandler("unity", "onOpenExportDialogReady", function ($dialogExport) {
    try {
      if (!$dialogExport || typeof $dialogExport.find !== "function" ||
          typeof $dialogExport.prepend !== "function") return;
      const $fields = $dialogExport.find("form.customFields");
      if ($fields && $fields.length) $dialogExport.prepend($fields);
      // v2.3 custom fonts: prefill the persisted paths and attach a Browse
      // button to each font input (jsonform renders them as plain text
      // inputs; NW.js <input type="file" accept=".ttf,.otf"> resolves to the
      // absolute path). Best-effort — an enhancement, never a dialog blocker.
      if (!$fields || !$fields.length || typeof $jq !== "function") return;
      const cfg = (k) => {
        try { return String(thisAddon.getConfig(k) || ""); } catch (e) { return ""; }
      };
      const fontFields = [
        ["unityFontCjk", cfg("fontCjk"), "empty = SLR's Japanese face (SLRCondensedCJK)"],
        ["unityFontLatin", cfg("fontLatin"), "empty = SLR's narrow Latin face (Stint Ultra Condensed)"],
      ];
      for (const [name, saved, placeholder] of fontFields) {
        const $inp = $fields.find('[name="' + name + '"]');
        if (!$inp || !$inp.length) continue;
        if (saved && !$inp.val()) $inp.val(saved);
        if (typeof $inp.attr === "function") $inp.attr("placeholder", placeholder);
        const $btn = $jq('<button type="button" class="unityFontBrowse">Browse…</button>');
        $btn.on("click", async function () {
          const picked = await unitySelectFontFile();
          if (picked) $inp.val(picked);
        });
        // insert after the input's field wrapper (jsonform wraps each field;
        // appending inside the wrapper keeps them on one line)
        ($inp.parent && $inp.parent().length ? $inp.parent() : $inp).append($btn);
      }
    } catch (e) { console.warn("unity export options reorder failed:", e); }
  });
} catch (e) { console.warn("unity onOpenExportDialogReady registration failed:", e); }

// ---------------------------------------------------------------------------
// v2.8 UNITY DSLR PATTERN OVERRIDE (SLRUnreal parity): SLR Translator is built
// for RPG Maker, so the DSLR engine's four pattern-family options are RPG
// Maker-specific (control codes, dialogue-plugin splits, script corners) —
// none of that fits Unity text, and Unity's own constructs (rich-text tags,
// String.Format braces) are not protected by them. While a UNITY project is
// the active project, the engine resolves those options to the Unity sets in
// lib/unityPatterns.js instead (see lib/unityEngine.js). Runtime switch only:
// the DSLR settings, stored patterns and the options tab are never modified,
// and non-Unity projects always use the user's own patterns. Options ->
// General -> SLRUnity: toggle + four editable copies (empty box = no
// patterns, DSL semantics; edits apply from the next translation on).
const UE_TO_UNITY_KEY = (id) => "unity" + id.charAt(0).toUpperCase() + id.slice(1);
const UNITY_PATTERN_FIELD = (key, title, description) => ({
  type: "string", default: UNITY_PATTERN_DEFAULTS[key],
  formType: "ace",
  formOptions: { aceMode: "javascript", aceTheme: "twilight", height: "200px" },
  title, description,
});
const UNITY_PATTERN_FORMAT = "Same format as DSLR's pattern boxes: comma-separated regular expression literals (e.g. /<Em>/gi,), one per line; // lines are comments. Clear completely for no patterns at all — same semantics as DSLR's own pattern options; the addon's built-in defaults ship in lib/unityPatterns.js inside the addon folder if you want them back. Takes effect on the next translation.";
thisAddon.optionsForm = {
  unityPatternOverride: {
    type: "boolean", default: true, formType: "checkbox",
    title: "Unity text-protection patterns",
    inlinetitle: "Use Unity patterns while a Unity project is open",
    description: "While a Unity project is the active project, translate with the Unity pattern sets below instead of DSLR's RPG Maker pattern sets. This is a runtime switch only — your DSLR settings and stored patterns are never modified, and non-Unity projects always use your own patterns. Uncheck to translate Unity projects with your DSLR patterns unchanged.",
  },
  unityProtectedPatterns: UNITY_PATTERN_FIELD("protectedPatterns",
    "Unity Protected Patterns",
    "Matched text is replaced by a placeholder before translation and restored after — this is where markup survives the LLM: Unity rich-text tags (<b>\u2026</b>, <color=#ff0000>, <sprite name=x/>), String.Format placeholders ({0}, {PlayerName}) and DSLR's batch dividers.\n" + UNITY_PATTERN_FORMAT),
  unitySplittingPatterns: UNITY_PATTERN_FIELD("splittingPatterns",
    "Unity Aggressive Splitting",
    "Matched text is split out and never sent to the translator: empty lines, and whole cells without Japanese (already-Latin UI labels are not re-translated).\n" + UNITY_PATTERN_FORMAT),
  unityIsolatePatterns: UNITY_PATTERN_FIELD("isolatePatterns",
    "Unity Isolation Patterns",
    "Matched sequences are translated separately (deduplicated). Empty by default — Unity text has no nameplate-escape structure to peel off.\n" + UNITY_PATTERN_FORMAT),
  unitySplitEndsPatterns: UNITY_PATTERN_FIELD("splitEndsPatterns",
    "Unity Cutting Corners Patterns",
    "Matched symbols at a sentence's start/end are removed before translation and re-attached after: filename-looking rows, stray whitespace and linebreak padding.\n" + UNITY_PATTERN_FORMAT),
};
(async () => {
  try {
    const res = await installUnityPatternOverride({
      window: (typeof window !== "undefined") ? window : undefined,
      trans: (typeof trans !== "undefined") ? trans : undefined,
      sys: (typeof sys !== "undefined") ? sys : undefined,
      maxWaitMs: 10000,
      isEnabled: () => {
        try { return thisAddon.getConfig("unityPatternOverride") !== false; }
        catch (e) { return true; }
      },
      // Live config read: the editable copies above, verbatim — an empty box
      // means NO patterns (same semantics as DSLR's own pattern options).
      // undefined (key never stored) falls back to lib/unityPatterns.js.
      getPatterns: (id) => {
        try {
          const v = thisAddon.getConfig(UE_TO_UNITY_KEY(id));
          if (typeof v === "string") return v;
        } catch (e) {}
        return undefined;   // unityEngine falls back to unityPatterns.js
      },
      log: (...a) => console.warn("unity patterns:", ...a),
    });
    if (!res.ok) console.info("unity: DSLR pattern override not installed:", res.reason);
  } catch (e) { console.warn("unity: pattern override setup failed:", e); }
})();
