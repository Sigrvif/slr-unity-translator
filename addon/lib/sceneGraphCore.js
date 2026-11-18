// Unity scene graph core (Task 4) — pure model + AI-context rendering.
//
// Consumes the frozen shapes produced by lib/projectBuilder.js:
//   sceneIndex = { order: [fileKey...],
//                  files: { [fileKey]: { kind: "scene"|"table", label: basename,
//                          subgroups: [{ name, rows: [{row, leaf}] }] } } }
//   files      = trans.project.files — { [fileKey]: { data: [[text, ""]...], ... } }
//
// Return shapes deliberately mirror the STOCK RPG-Maker SceneGraph
// (<dev-root>/SLR Translator\www\js\SceneGraph.js:1382-1397) everywhere the two
// intersect, because SLR consumes this dispatcher through the same call
// sites (trans-full.js:2300/5683/5793/6025/6600, transredbatch.js:394-415,
// GlossaryExtractor.js:635-706):
//   getRowSceneInfo -> { sceneId, orderInScene, kind, speaker, fileKey, rowIndex } | null
//   getOrderedRows  -> [{ file, rowIndex, sceneId, orderInScene, kind, speaker }, ...
//                       { file, rowIndex, sceneId: null, orderInScene: -1 }]   (tail rows)
//   getScene        -> scene object (superset of stock fields; ceCalls always [],
//                       because the stats panel reads scene.ceCalls.length unguarded)
//   getSceneDialogue-> [{ file, rowIndex, text, speaker, kind, order }]
//   getSceneAIContext -> "\n"-joined string, "" for unknown scenes
//   getStats        -> { scenes, mappedRows, totalScenes, totalMapped,
//                        totalUnmapped, scenesWithCECalls: 0, scenesWithCallers: 0 }
//   getLinkedSceneIds / getCallerScenes -> [] (no CE graph in Unity projects)
//
// Pure-lib rule: no require() at all — plain data in, plain data out.
"use strict";

// Stock truncation (SceneGraph.js:1366): a rendered line never exceeds 120
// chars, cut to 117 + "...".
function _truncate(line) {
  return line.length > 120 ? line.substring(0, 117) + "..." : line;
}

function createModel(sceneIndex, files) {
  const index = (sceneIndex && sceneIndex.files) || {};
  const order = (sceneIndex && Array.isArray(sceneIndex.order) && sceneIndex.order.length)
    ? sceneIndex.order.slice()
    : Object.keys(index);
  const projectFiles = files || {};

  const scenes = {};          // sceneId -> scene object (superset of stock shape)
  const rowMap = {};          // "fileKey|row" -> row info (stock _rowMap shape)
  let mappedRows = 0;

  for (const id of order) {
    const entry = index[id];
    if (!entry) continue;
    const base = String(entry.label || id);
    const fileObj = projectFiles[id];
    const dataLen = fileObj && Array.isArray(fileObj.data) ? fileObj.data.length : 0;

    const isTable = entry.kind === "table";
    const subgroups = [];
    let counter = 0;          // orderInScene: position within the scene traversal
    const speakers = {};

    for (const g of entry.subgroups || []) {
      const name = String(g.name || "_");
      const rows = [];
      for (const r of g.rows || []) {
        // Stale-index guard: rows beyond the current data length are skipped.
        if (!r || typeof r.row !== "number" || r.row < 0 || r.row >= dataLen) continue;
        rowMap[id + "|" + r.row] = {
          sceneId: id,
          orderInScene: counter++,
          kind: "ui",
          speaker: name,
          fileKey: id,
          rowIndex: r.row,
        };
        rows.push({ row: r.row, leaf: String(r.leaf || name) });
        speakers[name] = true;
        mappedRows++;
      }
      subgroups.push({ name, rows });
    }

    const rowCount = counter;
    scenes[id] = {
      id: id,
      type: isTable ? "table" : "scene",
      file: base,
      fileKey: id,
      label: isTable ? base + " table" : base,
      rowCount: rowCount,
      totalOrders: rowCount,
      subgroups: subgroups,
      speakers: Object.keys(speakers),
      // Stock-compat fields so SLR UI/batch code can read them unguarded.
      eventIndex: 0,
      pageIndex: 0,
      eventName: "",
      mapName: "",
      speakersByListIndex: {},
      ceCalls: [],
      transfers: [],
      callers: [],
      hasBranches: false,
    };
  }

  function textAt(fileKey, row) {
    const fileObj = projectFiles[fileKey];
    const rowData = fileObj && Array.isArray(fileObj.data) ? fileObj.data[row] : null;
    return rowData && rowData[0] != null ? String(rowData[0]) : "";
  }

  function getRowSceneInfo(fileId, row) {
    if (typeof row !== "number") return null;
    return rowMap[fileId + "|" + row] || null;
  }

  // Stock getOrderedRows shape (SceneGraph.js:1162-1222): scene-mapped rows
  // first, in scene order then in-scene order; rows with no scene appended
  // last in project-file order with sceneId null / orderInScene -1.
  function getOrderedRows(fileIds) {
    if (!Array.isArray(fileIds)) return null;
    const fileSet = new Set(fileIds);
    const result = [];
    const seen = new Set();

    for (const id of order) {
      const sc = scenes[id];
      if (!sc || !fileSet.has(id)) continue;
      for (const g of sc.subgroups) {
        for (const r of g.rows) {
          const key = id + "|" + r.row;
          if (seen.has(key)) continue;
          seen.add(key);
          const info = rowMap[key];
          result.push({
            file: id,
            rowIndex: r.row,
            sceneId: info.sceneId,
            orderInScene: info.orderInScene,
            kind: info.kind,
            speaker: info.speaker,
          });
        }
      }
    }

    for (const fk of Object.keys(projectFiles)) {
      if (!fileSet.has(fk)) continue;
      const fileObj = projectFiles[fk];
      if (!fileObj || !Array.isArray(fileObj.data)) continue;
      for (let ri = 0; ri < fileObj.data.length; ri++) {
        const key = fk + "|" + ri;
        if (seen.has(key)) continue;
        const info = rowMap[key];
        if (!info || !info.sceneId) {
          result.push({ file: fk, rowIndex: ri, sceneId: null, orderInScene: -1 });
        }
      }
    }

    return result;
  }

  function getScene(sceneId) {
    return scenes[sceneId] || null;
  }

  function getAllScenes() {
    return order.map((id) => scenes[id]).filter(Boolean);
  }

  function getSceneLabel(sceneId) {
    const sc = scenes[sceneId];
    return sc ? sc.label : "";
  }

  // Stock shape (SceneGraph.js:1241-1270) — trans.jumpToScene reads [0].file.
  function getSceneDialogue(sceneId) {
    const sc = scenes[sceneId];
    if (!sc) return [];
    const result = [];
    for (const g of sc.subgroups) {
      for (const r of g.rows) {
        const info = rowMap[sceneId + "|" + r.row];
        result.push({
          file: sceneId,
          rowIndex: r.row,
          text: textAt(sceneId, r.row),
          speaker: info ? info.speaker : g.name,
          kind: "ui",
          order: info ? info.orderInScene : 0,
        });
      }
    }
    result.sort((a, b) => (a.order || 0) - (b.order || 0));
    return result;
  }

  function getLinkedSceneIds() { return []; }   // no Common-Event graph in Unity
  function getCallerScenes() { return []; }

  // One "[Scene: <label>]" header, then per subgroup a "[UI: <name>]" line
  // followed by its rows as "leaf: text". textProcessor is accepted for
  // signature parity with stock (trans-full.js:6621 passes one) but Unity rows
  // are already plain strings, so it is intentionally not applied.
  function getSceneAIContext(sceneId) {
    const sc = scenes[sceneId];
    if (!sc) return "";
    const lines = ["[Scene: " + sc.label + "]"];
    for (const g of sc.subgroups) {
      if (!g.rows.length) continue;
      lines.push("[UI: " + g.name + "]");
      for (const r of g.rows) {
        lines.push(_truncate(r.leaf + ": " + textAt(sceneId, r.row)));
      }
    }
    return lines.join("\n");
  }

  let totalUnmapped = 0;
  for (const fk of Object.keys(projectFiles)) {
    const fileObj = projectFiles[fk];
    const data = fileObj && Array.isArray(fileObj.data) ? fileObj.data : [];
    for (let ri = 0; ri < data.length; ri++) {
      const info = rowMap[fk + "|" + ri];
      if (!info || !info.sceneId) totalUnmapped++;
    }
  }

  function getStats() {
    const sceneCount = getAllScenes().length;
    return {
      scenes: sceneCount,
      mappedRows: mappedRows,
      totalScenes: sceneCount,
      totalMapped: mappedRows,
      totalUnmapped: totalUnmapped,
      scenesWithCECalls: 0,
      scenesWithCallers: 0,
    };
  }

  return {
    getRowSceneInfo: getRowSceneInfo,
    getOrderedRows: getOrderedRows,
    getScene: getScene,
    getAllScenes: getAllScenes,
    getSceneLabel: getSceneLabel,
    getSceneDialogue: getSceneDialogue,
    getLinkedSceneIds: getLinkedSceneIds,
    getCallerScenes: getCallerScenes,
    getSceneAIContext: getSceneAIContext,
    getStats: getStats,
  };
}

module.exports = { createModel };
