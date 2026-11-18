// Scene graph core tests (Task 4). The four tests from the brief are kept
// verbatim; the rest pin the SLR stock SceneGraph return shapes that real
// consumers rely on (www/js/SceneGraph.js:1382-1397 surface, consumed at
// www/js/trans-full.js:6600-6629, :5683-5800 and www/core/SLRbatch/transredbatch.js:394-415).
const test = require("node:test");
const assert = require("node:assert");
const { createModel } = require("../lib/sceneGraphCore");
const scan = require("./fixtures/stealth-scan-sample.json");
const { buildProject, buildSceneIndex } = require("../lib/projectBuilder");

function proj() {
  const strings = scan.strings;
  const index = {};
  for (const s of strings) (index[s.text] = index[s.text] || []).push(
    { file: s.file, abs: "x", path_id: s.path_id, source: s.source, offset: s.offset, length: s.length, key: s.key, tokens: s.tokens });
  return buildProject({ ...scan, index }, { gameDir: "E:/G", gameTitle: "T", cache: { cacheID: "c", cachePath: "p" } });
}

const LV_KEY = "SampleGame_Data/level0";
const META_KEY = "SampleGame_Data/il2cpp_data/Metadata/global-metadata.dat";

test("rows map to scenes with subgroup speakers", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const lvKey = "SampleGame_Data/level0";
  const info = m.getRowSceneInfo(lvKey, 0);
  assert.ok(info && info.sceneId === lvKey && ["ui"].includes(info.kind) && typeof info.speaker === "string");
});

test("getSceneAIContext renders dialog with its buttons", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const ctx = m.getSceneAIContext("SampleGame_Data/level0");
  assert.ok(ctx.startsWith("[Scene: level0"));
  assert.ok(/\[UI: /.test(ctx));
  assert.ok(ctx.includes(": ") && ctx.length > 50 && ctx.length < 20000);
});

test("ordered rows list scene files first", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const ids = Object.keys(p.files);
  const ordered = m.getOrderedRows(ids);
  assert.equal(ordered.length, Object.values(p.files).reduce((n, f) => n + f.data.length, 0));
});

test("metadata pseudo-scene labelled as table", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const key = Object.keys(p.files).find(k => k.includes("global-metadata.dat"));
  assert.ok(key);
  assert.match(m.getSceneLabel(key), /literal|metadata|table/i);
});

// --- stock-shape parity (binding: real consumers, see file header) ---

test("getRowSceneInfo carries the stock field set and nulls unknown rows", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const info = m.getRowSceneInfo(LV_KEY, 0);
  assert.deepStrictEqual(Object.keys(info).sort(),
    ["fileKey", "kind", "orderInScene", "rowIndex", "sceneId", "speaker"]);
  assert.strictEqual(info.fileKey, LV_KEY);
  assert.strictEqual(typeof info.rowIndex, "number");
  assert.strictEqual(typeof info.orderInScene, "number");
  const info7 = m.getRowSceneInfo(LV_KEY, 7);
  assert.ok(info7.orderInScene !== info.orderInScene || info7.rowIndex === info.rowIndex);
  assert.strictEqual(m.getRowSceneInfo(LV_KEY, 99999), null);
  assert.strictEqual(m.getRowSceneInfo("no/such/file", 0), null);
});

test("getOrderedRows returns stock-shaped entries, scenes first, tail last", () => {
  const p = proj();
  // A file added after the index was built (no scene) must keep its rows,
  // appended last with sceneId null (stock getOrderedRows tail pass shape).
  const files = Object.assign(Object.create(null), p.files, {
    "Extras/late.txt": { data: [["a", ""], ["b", ""]], context: [["late.txt"], ["late.txt"]] },
  });
  const m = createModel(p.options.unity.sceneIndex, files);
  const ordered = m.getOrderedRows([...Object.keys(p.files), "Extras/late.txt"]);
  // buildProject dedupes per-file texts: level0 45 raw strings -> 43 rows,
  // metadata 50 -> 50; +2 tail rows from the late file.
  assert.strictEqual(ordered.length, 93 + 2);
  for (const e of ordered.slice(0, 93)) {
    assert.deepStrictEqual(Object.keys(e).sort(),
      ["file", "kind", "orderInScene", "rowIndex", "sceneId", "speaker"]);
    assert.ok(e.sceneId === LV_KEY || e.sceneId === META_KEY);
  }
  // scene files come in sceneIndex.order: level0 before the metadata table
  assert.strictEqual(ordered[0].file, LV_KEY);
  assert.ok(ordered.slice(0, 43).every(e => e.file === LV_KEY));
  assert.ok(ordered.slice(43, 93).every(e => e.file === META_KEY));
  const tail = ordered.slice(93);
  assert.ok(tail.every(e => e.file === "Extras/late.txt" && e.sceneId === null && e.orderInScene === -1));
  assert.deepStrictEqual(tail.map(e => e.rowIndex), [0, 1]);
});

test("getScene/getAllScenes carry the fields the stats panel reads", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const all = m.getAllScenes();
  assert.deepStrictEqual(all.map(s => s.id), p.options.unity.sceneIndex.order);
  const sc = m.getScene(LV_KEY);
  // showSceneGraphPanel (trans-full.js:5683+) reads .ceCalls.length unguarded,
  // plus .rowCount/.label/.id/.type; _scenesAreRelated reads .file/.eventIndex.
  assert.strictEqual(sc.id, LV_KEY);
  assert.ok(typeof sc.label === "string" && sc.label.length > 0);
  assert.strictEqual(sc.rowCount, 43);
  assert.deepStrictEqual(sc.ceCalls, []);
  assert.ok(Array.isArray(sc.transfers));
  assert.ok(typeof sc.file === "string" && typeof sc.eventIndex === "number");
  assert.strictEqual(m.getScene("nope"), null);
});

test("getSceneDialogue returns stock row objects sorted by order", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const d = m.getSceneDialogue(LV_KEY);
  assert.strictEqual(d.length, 43);
  for (const r of d) {
    assert.deepStrictEqual(Object.keys(r).sort(),
      ["file", "kind", "order", "rowIndex", "speaker", "text"]);
    assert.strictEqual(r.file, LV_KEY); // jumpToScene does selectFile(d[0].file)
    assert.ok(typeof r.text === "string" && r.text.length > 0);
  }
  const orders = d.map(r => r.order);
  assert.deepStrictEqual(orders, [...orders].sort((a, b) => a - b));
  assert.deepStrictEqual(m.getSceneDialogue("nope"), []);
});

test("getStats exposes both the brief keys and the stock keys", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  const st = m.getStats();
  assert.strictEqual(st.scenes, 2);
  assert.strictEqual(st.mappedRows, 93);
  assert.strictEqual(st.totalScenes, 2);        // transredbatch.js:415, stats window
  assert.strictEqual(st.totalMapped, 93);       // transredbatch.js:415, stats window
  assert.strictEqual(st.totalUnmapped, 0);
  assert.strictEqual(st.scenesWithCECalls, 0);  // stats window reads these
  assert.strictEqual(st.scenesWithCallers, 0);
});

test("AI context lines truncate at 120 chars and link queries are empty", () => {
  const files = { "f": { data: [["y".repeat(300), ""]], context: [["G/leaf"]] } };
  const sceneIndex = { order: ["f"], files: { "f": {
    kind: "scene", label: "f", subgroups: [{ name: "G", rows: [{ row: 0, leaf: "leaf" }] }] } } };
  const m = createModel(sceneIndex, files);
  const lines = m.getSceneAIContext("f").split("\n");
  assert.strictEqual(lines[0], "[Scene: f]");
  assert.strictEqual(lines[1], "[UI: G]");
  assert.ok(lines[2].length <= 120, "row line must be <= 120 chars");
  assert.ok(lines[2].endsWith("..."));
  assert.strictEqual(m.getLinkedSceneIds("f").length, 0);
  assert.strictEqual(m.getCallerScenes("f").length, 0);
});

test("unknown scene ids degrade like stock", () => {
  const p = proj();
  const m = createModel(p.options.unity.sceneIndex, p.files);
  assert.strictEqual(m.getSceneLabel("nope"), "");
  assert.strictEqual(m.getSceneAIContext("nope"), "");
  assert.deepStrictEqual(m.getSceneDialogue("nope"), []);
  assert.deepStrictEqual(m.getLinkedSceneIds("nope"), []);
  assert.deepStrictEqual(m.getCallerScenes("nope"), []);
});

test("degenerate inputs do not throw", () => {
  let m = createModel(null, null);
  assert.strictEqual(m.getStats().scenes, 0);
  assert.deepStrictEqual(m.getAllScenes(), []);
  assert.strictEqual(m.getRowSceneInfo("f", 0), null);
  assert.deepStrictEqual(m.getOrderedRows(["f"]), []);
  m = createModel({ order: ["f"], files: { f: { kind: "scene", label: "f",
    subgroups: [{ name: "G", rows: [{ row: 9, leaf: "x" }] }] } } }, {});
  assert.strictEqual(m.getStats().mappedRows, 0); // row 9 out of range is skipped
  assert.deepStrictEqual(m.getOrderedRows(["f"]), []);
});
