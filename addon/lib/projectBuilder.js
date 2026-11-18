// Project builder: contract-v2 scan payload -> SLR Translator project object.
//
// Consumes the scan payload from docs/CONTRACT.md (v2; payload shape
// unchanged from v1):
//   strings: StringHit rows (text, source, path_id, context, ...; the JS
//            fixture rows carry `file`, live payloads blank it),
//   index:   { japanese_text: [{file, abs, path_id, source, offset, length,
//            key, tokens}] } — occurrences for raw/typetree sources carry NO
//            context; per-text context strings live in the strings[] rows
//            (each has a `context` field).
//
// Therefore context sourcing is two-phase:
//   1. text -> [unique context strings] is built from scan.strings FIRST
//      (all sources, in row order);
//   2. index occurrences are used ONLY for row ordering: per file, rows are
//      deduped by text (first occurrence wins) and ordered by the occurrence
//      sort key (path_id, offset, key-string).
//
// Pure-lib rule: only node builtins here (path); no NW.js / SLR globals.
"use strict";

const path = require("path");

// [0, N, key] for levelN basenames (scenes, numeric order), [1, 0, key] for
// everything else (tables, alphabetical via callers' tiebreak).
function sortKeyForFile(fileKey) {
  const base = path.basename(fileKey);
  const m = /^level(\d+)$/.exec(base);
  return m ? [0, parseInt(m[1], 10), fileKey] : [1, 0, fileKey];
}

function _rowOrder(o) { return [o.path_id || 0, o.offset || 0, o.key || ""]; }

// path_id compares exactly via BigInt: contract v2.1 carries it as a decimal
// STRING because Unity ids exceed Number's exact range (>2^53) and a JSON
// number is silently rounded by JSON.parse. Numbers from older payloads and
// non-integer junk fall back to a plain string compare — ordering only needs
// to be total and stable, never human-meaningful.
function _cmpPid(a, b) {
  try {
    const ba = BigInt(a), bb = BigInt(b);
    return ba < bb ? -1 : ba > bb ? 1 : 0;
  } catch (e) {
    const sa = String(a), sb = String(b);
    return sa < sb ? -1 : sa > sb ? 1 : 0;
  }
}

// Lexicographic compare for [path_id, offset, key] triples (numeric for the
// first two, string for the key). NOT `<`/`>` on the arrays — that would
// string-compare "1,10" < "1,2" style and mis-order offsets 9 vs 10.
function _cmpOrder(a, b) {
  return _cmpPid(a[0], b[0]) || (a[1] - b[1]) || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0);
}

// common.containJapanese's exact character class (SLR www/js/common.js:974)
// — the canonical "has Japanese" test every SLR engine/batch flow uses.
const JP_CHAR = /[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]/;

// text -> [unique context strings] from scan.strings rows, in row order.
function _contextsByText(scan) {
  const map = new Map();
  for (const row of scan.strings || []) {
    let list = map.get(row.text);
    if (!list) map.set(row.text, (list = []));
    if (row.context && !list.includes(row.context)) list.push(row.context);
  }
  return map;
}

// v2.8 risk tags (SLR Translator's scheme). The backend tags every hit
// (discovery.string_risk): "" display text, "green" code literals (both
// translate by default), "yellow" likely-safe-but-confirm (non-display
// fields, JSON/CSV values), "red" structural (names, data rows, filenames).
// The patch replaces a text EVERYWHERE, so a text that occurs in several
// tiers must wear its WORST tag: red 3 > yellow 2 > "" 1 > green 0 — a row
// tagged yellow/red is skipped by SLR's batch translation until the user
// promotes it, which keeps a risky occurrence from being rewritten silently.
const RISK_RANK = { red: 3, yellow: 2, "": 1, green: 0 };

function _riskByText(scan) {
  const map = new Map();
  for (const row of scan.strings || []) {
    const risk = RISK_RANK[row.risk] !== undefined ? row.risk : "";
    const prev = map.get(row.text);
    if (prev === undefined || RISK_RANK[risk] > RISK_RANK[prev]) map.set(row.text, risk);
  }
  return map;
}

// Trailing context-path segments that name the COMPONENT TYPE, not the game
// object: a row whose context is "Canvas/NewGameCheck/confirm/Text (TMP)" is
// about "confirm", not about a leaf literally called "Text (TMP)" (final-
// review finding: the scene-graph AI context rendered "Text (TMP): 開始").
// Matched case-insensitively, EXACT match only, scanning from the END.
const GENERIC_LEAF_SEGMENTS = [
  "text (tmp)", "text", "textmeshprougui", "tmp",
  "label", "image", "bg", "textmeshpro text",
];

// Leaf for a context path: the last NON-generic, non-empty segment. If every
// segment is generic (or empty), fall back to the plain last segment — a real
// (if generic) name beats an empty leaf. Subgroup selection (FIRST segment)
// is unaffected by this.
function _leafFor(segs, fallback) {
  for (let i = segs.length - 1; i >= 0; i--) {
    const seg = segs[i];
    if (seg && GENERIC_LEAF_SEGMENTS.indexOf(seg.toLowerCase()) === -1) return seg;
  }
  return segs[segs.length - 1] || fallback;
}

function buildSceneIndex(files) {
  const order = Object.keys(files).sort((a, b) => {
    const ka = sortKeyForFile(a), kb = sortKeyForFile(b);
    return (ka[0] - kb[0]) || (ka[1] - kb[1]) || a.localeCompare(b);
  });
  const out = { order, files: {} };
  for (const key of order) {
    const base = path.basename(key);
    const kind = /^level\d+$/.test(base) ? "scene" : "table";
    const groups = new Map();
    const rows = files[key].context || [];
    rows.forEach((ctxs, row) => {
      const first = (ctxs && ctxs[0]) || "";
      const segs = String(first).split("/");
      const name = kind === "scene" ? (segs[0] || "_") : "table";
      const leaf = _leafFor(segs, name);
      if (!groups.has(name)) groups.set(name, { name, rows: [] });
      groups.get(name).rows.push({ row, leaf });
    });
    out.files[key] = { kind, label: base, subgroups: [...groups.values()] };
  }
  return out;
}

function buildProject(scan, meta) {
  // Phase 1: contexts + worst risk per text, from scan.strings rows.
  const contextsByText = _contextsByText(scan);
  const riskByText = _riskByText(scan);

  // Phase 2: occurrence order -> per-file row order. First occurrence of a
  // text in a file wins the row; the row sits at its first occurrence's
  // (path_id, offset, key) position (stable sort keeps insertion order on
  // ties). A text occurring in several files gets a row in each file.
  const byFile = new Map(); // fileKey -> Map(text -> first occurrence order)
  for (const [text, occs] of Object.entries(scan.index || {})) {
    const sorted = [...occs].sort((a, b) => _cmpOrder(_rowOrder(a), _rowOrder(b)));
    for (const o of sorted) {
      let m = byFile.get(o.file);
      if (!m) byFile.set(o.file, (m = new Map()));
      if (!m.has(text)) m.set(text, _rowOrder(o));
    }
  }

  const files = {};
  for (const [fileKey, texts] of byFile) {
    const base = path.basename(fileKey);
    const data = [], context = [], tags = [], parameters = [];
    const ordered = [...texts.entries()].sort((a, b) => _cmpOrder(a[1], b[1]));
    // v2.2 collect-all-visible: the scan also yields strings WITHOUT Japanese
    // (TMP labels, official-English tables, credits). SLR's "Japanese source"
    // general option decides their tag; v2.8 adds the backend's risk tags on
    // top (the tier scheme in _riskByText above). SLR's batch blacklist
    // defaults skip red, yellow, gray, gold and darkred rows and translate
    // untagged and green rows, so:
    //   JP row:      risk as-is ("", green, yellow, red)
    //   no-JP row:   red/green -> darkred, yellow -> gold, else gray
    //   option off:  every row wears its plain risk color or nothing
    // (e2e-fix11 semantics stay: untagged = ordinary translatable row).
    const tagNoJp = meta.japaneseSource === true;
    for (const [text] of ordered) {
      const ctxs = (contextsByText.get(text) || []).slice(0, 20);
      data.push([text, ""]);
      context.push(ctxs.length ? ctxs : [base]);
      const risk = riskByText.get(text) || "";
      let tag = risk;
      if (tagNoJp && !JP_CHAR.test(text)) {
        tag = (risk === "red" || risk === "green") ? "darkred"
          : risk === "yellow" ? "gold" : "gray";
      }
      tags.push(tag ? [tag] : []);
      parameters.push([]);
    }
    files[fileKey] = { path: fileKey, dirname: path.dirname(fileKey), basename: base,
                       extension: path.extname(base).slice(1), data, context, tags, parameters };
  }
  const sceneIndex = buildSceneIndex(files);
  return {
    gameEngine: "unity", parser: "unity", gameTitle: meta.gameTitle,
    loc: meta.gameDir, projectId: meta.cache.cacheID, cache: meta.cache,
    files,
    // e2e-fix7 (contract v2): `gameDir` stays as a REFERENCE to the original
    // folder (only the full-copy export mode still reads it); `stagedGameDir`
    // is the self-contained copy under <cachePath>/data/game that the scan
    // staged and whose files the index's abs paths point into. Old (v1)
    // projects reopen without stagedGameDir — every reader must tolerate
    // that (patch export falls back to gameDir).
    options: { unity: { contract: 2, gameDir: meta.gameDir,
                        stagedGameDir: meta.stagedGameDir || "",
                        sceneIndex,
                        stats: scan.stats || {}, fontsFound: scan.fonts || [],
                        texturesFound: (scan.textures || []).length,
                        textAssetsFound: scan.text_assets || [],
                        monoReport: scan.mono || [], suspect: scan.suspect || [],
                        // v2.5 demosaic: the scan's mosaic-censorship candidate
                        // rows. Its PRESENCE (even as []) marks a project whose
                        // staging includes the demosaic file set — old projects
                        // lack the key, which the export uses to warn that
                        // mosaic assets may not be staged.
                        demosaicFound: scan.demosaic || [] } },
  };
}

module.exports = { buildProject, buildSceneIndex, sortKeyForFile };
