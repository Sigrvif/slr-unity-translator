const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { buildProject, buildSceneIndex, sortKeyForFile } = require("../lib/projectBuilder");
const scan = require("./fixtures/stealth-scan-sample.json");

const META = { gameDir: "E:/Games/SampleGame", gameTitle: "SampleGame", cache: { cacheID: "abc123", cachePath: "E:/stage/abc123" } };

test("buildProject produces SLR-shaped project with unity options", () => {
  const proj = buildProject({ ...scan, index: rebuildIndex(scan.strings) }, META);
  assert.equal(proj.gameEngine, "unity");
  assert.ok(proj.files["SampleGame_Data/level0"]);
  const lv = proj.files["SampleGame_Data/level0"];
  assert.equal(lv.data.length, lv.context.length);
  assert.equal(lv.data.length, lv.tags.length);
  assert.deepEqual(lv.data[0].slice(1), [""]);           // translation blank
  // e2e-fix11: rows are UNTAGGED — SLR's "gray" tag means "dialogue cell
  // WITHOUT Japanese" and batch translation blacklists it, so tagging every
  // row gray filtered everything out. Untagged = ordinary translatable row.
  assert.ok(lv.tags.every(t => Array.isArray(t) && t.length === 0));
  assert.equal(proj.options.unity.contract, 2);
  assert.equal(proj.options.unity.stagedGameDir, "", "no stagedGameDir passed -> empty string (old-project-safe)");
  assert.ok(proj.options.unity.sceneIndex.order.includes("SampleGame_Data/level0"));
});

test("buildProject (e2e-fix7): stagedGameDir lands in options.unity next to the reference gameDir", () => {
  const proj = buildProject({ ...scan, index: rebuildIndex(scan.strings) },
    { ...META, stagedGameDir: "E:/stage/abc123/data/game" });
  assert.equal(proj.options.unity.stagedGameDir, "E:/stage/abc123/data/game");
  assert.equal(proj.options.unity.gameDir, "E:/Games/SampleGame", "original gameDir kept as reference");
  assert.equal(proj.options.unity.contract, 2);
});

test("buildSceneIndex orders levelN numerically before tables", () => {
  const idx = buildSceneIndex(makeFiles({ "Data/level0": 1, "Data/level10": 1, "Data/level2": 1, "Data/zz.bundle": 1 }));
  assert.deepEqual(idx.order, ["Data/level0", "Data/level2", "Data/level10", "Data/zz.bundle"]);
  assert.equal(idx.files["Data/level0"].kind, "scene");
  assert.equal(idx.files["Data/zz.bundle"].kind, "table");
});

test("subgroups come from the first context path segment", () => {
  const proj = buildProject({ ...scan, index: rebuildIndex(scan.strings) }, META);
  const lv = proj.files["SampleGame_Data/level0"];
  const idx = proj.options.unity.sceneIndex;
  const sub = idx.files["SampleGame_Data/level0"].subgroups.find(s => s.name === "Canvas");
  assert.ok(sub, "Canvas subgroup exists");
  assert.ok(sub.rows.every(r => Number.isInteger(r.row) && typeof r.leaf === "string"));
});

// Final-review finding I3: the leaf fed to the scene-graph AI context must
// skip GENERIC trailing segments (component-type names), or every row
// renders as "Text (TMP): 開始" instead of "confirm: 開始".
test("leaf skips generic trailing segments (final-review I3)", () => {
  // The exact regression case: the fixture carries this context verbatim.
  const files = { "Data/level0": { context: [["Canvas/NewGameCheck/confirm/Text (TMP)"]] } };
  const idx = buildSceneIndex(files);
  const rows = idx.files["Data/level0"].subgroups.find(s => s.name === "Canvas").rows;
  assert.equal(rows[0].leaf, "confirm");

  // Case-insensitive exact match; several stacked generic segments are all
  // skipped from the end.
  const multi = buildSceneIndex({ "Data/level1": { context: [
    ["Canvas/A/confirm/text (tmp)"],
    ["Canvas/B/settings/Label/Image"],
    ["Canvas/C/title/TextMeshProUGUI"],
    ["Canvas/D/TEXT"],           // case-insensitive exact match skips it too
  ] } });
  const leaves = multi.files["Data/level1"].subgroups.find(s => s.name === "Canvas").rows.map(r => r.leaf);
  assert.deepEqual(leaves, ["confirm", "settings", "title", "D"]);

  // All segments generic -> fall back to the plain last segment.
  const allGeneric = buildSceneIndex({ "Data/level2": { context: [["Text (TMP)/Text"]] } });
  assert.equal(allGeneric.files["Data/level2"].subgroups[0].rows[0].leaf, "Text");

  // Empty/blank segments never win as the leaf; the subgroup is untouched.
  const trailing = buildSceneIndex({ "Data/level3": { context: [["Group/item/"]] } });
  const sub3 = trailing.files["Data/level3"].subgroups[0];
  assert.equal(sub3.name, "Group", "subgroup stays the FIRST segment");
  assert.equal(sub3.rows[0].leaf, "item");
});

test("duplicate texts dedupe per file with merged contexts", () => {
  const f = makeFiles({ "Data/level1": 2 });
  // force a duplicate text across two occurrences
  const strings = [
    { text: "こんにちは", source: "raw", path_id: 1, offset: 10, length: 9, key: "", tokens: [], file: "", context: "Canvas/A/Text (TMP)" },
    { text: "こんにちは", source: "raw", path_id: 2, offset: 20, length: 9, key: "", tokens: [], file: "", context: "Canvas/B/Text (TMP)" },
  ];
  const proj = buildProject({ version: 1, strings, index: rebuildIndex(strings), stats: { unique: 1 } }, META);
  const rows = Object.values(proj.files)[0];
  assert.equal(rows.data.length, 1);
  assert.deepEqual(rows.context[0], ["Canvas/A/Text (TMP)", "Canvas/B/Text (TMP)"]);
});

test("sortKeyForFile orders levelN numerically before non-level keys", () => {
  assert.deepEqual(sortKeyForFile("Data/level10"), [0, 10, "Data/level10"]);
  assert.deepEqual(sortKeyForFile("Data/zz.bundle"), [1, 0, "Data/zz.bundle"]);
  assert.ok(sortKeyForFile("Data/level2")[1] < sortKeyForFile("Data/level10")[1]); // 2 < 10, not "2" < "10"
});

// fix21: path_id crosses the JSON boundary as a decimal STRING (Unity ids
// exceed Number's exact range; as numbers JSON.parse rounds them silently
// and the staged index can never match again — the localization-bundle
// revert bug). Rows must still order deterministically with string ids,
// including >2^53 values, negative values and legacy numbers.
test("row order is total and stable with string path_ids (fix21)", () => {
  const mk = (pid, off) => ({ text: "テキスト" + off, source: "typetree",
    path_id: pid, offset: off, length: 9, key: "", tokens: [], file: "Data/x",
    context: "Canvas/L" });
  // the REAL ids from the broken staged index (post-corruption values shown;
  // here the TRUE values as the backend now emits them)
  const strings = [
    mk("2081800417054789123", 30),   // > 2^53
    mk("-2933933604927357922", 10),  // negative, > 2^53 in magnitude
    mk("-1532361865029228001", 20),  // negative
    mk("7", 40),                     // small string (legacy-looking)
    mk(12, 50),                      // legacy number
  ];
  const proj = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} }, META);
  const rows = proj.files["Data/x"].data.map(r => r[0]);
  // BigInt order: -2933... < -1532... < 7 < 12 < 2081...
  assert.deepEqual(rows, [
    "テキスト10", "テキスト20", "テキスト40", "テキスト50", "テキスト30",
  ]);
});

// v2.2 collect-all-visible: with SLR's "Japanese source" option on, rows
// without Japanese carry SLR's gray no-Japanese tag (batch blacklists gray
// by default, so JP rows stay the batch target while the EN rows — e.g. the
// test game's "Achievement" menu button — remain available for manual
// fixes). With the option off every row stays untagged (e2e-fix11).
test("non-Japanese rows gray-tagged only when japaneseSource is on", () => {
  const strings = [
    { text: "陽動", source: "typetree", path_id: 1, offset: 0, length: 0, key: "", tokens: ["m_TableData", 0, "m_Localized"], file: "Data/t", context: "" },
    { text: "Achievement", source: "raw", path_id: 2, offset: 8, length: 11, key: "", tokens: [], file: "Data/t", context: "Canvas/Achievement/Text (TMP)" },
    { text: "陽動一", source: "typetree", path_id: 3, offset: 0, length: 0, key: "", tokens: ["m_TableData", 1, "m_Localized"], file: "Data/t", context: "" },
  ];
  const on = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} },
    { ...META, japaneseSource: true });
  const rowsOn = on.files["Data/t"];
  assert.ok(rowsOn.tags.every(t => Array.isArray(t)), "tags stay arrays");
  const byText = new Map(rowsOn.data.map((r, i) => [r[0], rowsOn.tags[i]]));
  assert.deepEqual(byText.get("陽動"), []);
  assert.deepEqual(byText.get("陽動一"), []);
  assert.deepEqual(byText.get("Achievement"), ["gray"]);

  const off = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} },
    { ...META });  // option absent (old callers) -> untagged
  const rowsOff = off.files["Data/t"];
  assert.ok(rowsOff.tags.every(t => Array.isArray(t) && t.length === 0));
});

// --- v2.8 risk tags (SLR Translator's tag scheme) ---------------------------
// The backend tags every hit: "" display text and "green" code literals are
// translated by default (SLR's batch blacklist defaults only skip yellow,
// red, gray, gold, darkred); "yellow" likely-safe-but-confirm; "red"
// structural. A text occurring in several tiers wears its WORST tag, and the
// no-Japanese variants map to gold/darkred/gray when japaneseSource is on.

const RISK_ROW = (text, risk, source, tokens) => ({
  text, risk, source: source || "typetree", path_id: 1, offset: 0, length: 0,
  key: "", tokens: tokens || ["narration"], file: "Data/t", context: "",
});

test("risk tags land on the rows exactly as the backend tagged them", () => {
  const strings = [
    RISK_ROW("安全なセリフ", ""),                  // display field -> untagged
    RISK_ROW("たぶん安全", "yellow"),              // non-display field -> yellow
    RISK_ROW("構造体です", "red"),                 // identifier leaf -> red
    RISK_ROW("コードのセリフ", "green", "mono"),   // code literal -> green
  ];
  const proj = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} }, META);
  const rows = proj.files["Data/t"];
  const byText = new Map(rows.data.map((r, i) => [r[0], rows.tags[i]]));
  assert.deepEqual(byText.get("安全なセリフ"), []);
  assert.deepEqual(byText.get("たぶん安全"), ["yellow"]);
  assert.deepEqual(byText.get("構造体です"), ["red"]);
  assert.deepEqual(byText.get("コードのセリフ"), ["green"]);
  // green translates by default like untagged (SLR's blacklist defaults)
  // — asserted via SLR's shipped blacklist in the E2E checklist, not here.
});

test("a text with several risk tiers wears its WORST tag (red > yellow > '' > green)", () => {
  const strings = [
    { ...RISK_ROW("まりさ", "green", "mono"), path_id: 1 },
    { ...RISK_ROW("まりさ", "yellow"), path_id: 2 },   // same text, other field
    { ...RISK_ROW("まりさ", "red"), path_id: 3 },
    { ...RISK_ROW("ダイアログ", "green", "mono"), path_id: 4 },
    { ...RISK_ROW("ダイアログ", ""), path_id: 5 },
  ];
  const proj = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} }, META);
  const rows = proj.files["Data/t"];
  const byText = new Map(rows.data.map((r, i) => [r[0], rows.tags[i]]));
  assert.deepEqual(byText.get("まりさ"), ["red"]);
  assert.deepEqual(byText.get("ダイアログ"), []);   // display beats green
});

test("no-Japanese risk rows map to gold/darkred/gray when japaneseSource is on", () => {
  const strings = [
    RISK_ROW("確認", "yellow"),                       // JP -> yellow stays yellow
    RISK_ROW("Yes", "yellow"),                        // no-JP yellow -> gold
    RISK_ROW("chara_a", "red"),                       // no-JP red -> darkred
    RISK_ROW("new game", ""),                         // no-JP untagged -> gray
    RISK_ROW("Lv1", "green", "mono"),                 // no-JP green -> darkred
  ];
  const proj = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} },
    { ...META, japaneseSource: true });
  const rows = proj.files["Data/t"];
  const byText = new Map(rows.data.map((r, i) => [r[0], rows.tags[i]]));
  assert.deepEqual(byText.get("確認"), ["yellow"]);
  assert.deepEqual(byText.get("Yes"), ["gold"]);
  assert.deepEqual(byText.get("chara_a"), ["darkred"]);
  assert.deepEqual(byText.get("new game"), ["gray"]);
  assert.deepEqual(byText.get("Lv1"), ["darkred"]);

  // Option off: plain risk colors, no no-JP variants.
  const off = buildProject({ version: 2, strings, index: rebuildIndex(strings), stats: {} }, META);
  const rowsOff = off.files["Data/t"];
  const byTextOff = new Map(rowsOff.data.map((r, i) => [r[0], rowsOff.tags[i]]));
  assert.deepEqual(byTextOff.get("確認"), ["yellow"]);
  assert.deepEqual(byTextOff.get("Yes"), ["yellow"]);
  assert.deepEqual(byTextOff.get("chara_a"), ["red"]);
  assert.deepEqual(byTextOff.get("new game"), []);
  assert.deepEqual(byTextOff.get("Lv1"), ["green"]);
});

test("rows without a risk field (old payloads/fixtures) stay untagged", () => {
  const strings = [
    { text: "ノーリスク", source: "raw", path_id: 1, offset: 0, length: 0, key: "", tokens: [], file: "Data/t", context: "" },
  ];
  const proj = buildProject({ version: 1, strings, index: rebuildIndex(strings), stats: {} }, META);
  const rows = proj.files["Data/t"];
  assert.deepEqual(rows.tags[0], []);
});

// --- helpers ---------------------------------------------------------------

// Build a contract-v1 scan `index` from `strings` rows: group by text, using
// each row's real `file` (the fixture rows carry it; live backend payloads
// blank it and put file info only in index occurrences).
function rebuildIndex(strings) {
  const index = {};
  for (const row of strings || []) {
    (index[row.text] = index[row.text] || []).push({
      file: row.file, abs: "x", path_id: row.path_id, source: row.source,
      offset: row.offset, length: row.length, key: row.key, tokens: row.tokens,
    });
  }
  return index;
}

// Minimal SLR-shaped files map: { fileKey: entry with N dummy rows }.
function makeFiles(map) {
  const files = {};
  for (const [key, n] of Object.entries(map)) {
    const base = path.basename(key);
    const entry = { path: key, dirname: path.dirname(key), basename: base,
                    extension: path.extname(base).slice(1),
                    data: [], context: [], tags: [], parameters: [] };
    for (let i = 0; i < n; i++) {
      entry.data.push([`ダミー${i}`, ""]);
      entry.context.push([`Sub${i}/Leaf${i}`]);
      entry.tags.push([]);
      entry.parameters.push([]);
    }
    files[key] = entry;
  }
  return files;
}
