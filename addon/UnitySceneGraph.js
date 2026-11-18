// SLRUnity — scene graph dispatcher (Task 4).
//
// LOADER CONTRACT (www/js/addon.js:218-229, Addon.loadJS): this body is
// eval'd as an AsyncFunction with `this` = the Addon instance — top-level
// statements only, NO IIFE (an IIFE would shadow the `this` binding). No
// top-level await so `node --check` (CommonJS parse) stays valid.
//
// js/SceneGraph.js is a plain synchronous <script> in trans.html (:49) while
// addon.js is deferred (:53), so the stock window.SceneGraph ALWAYS exists
// before any addon file is eval'd — capture it once, then replace it with a
// dispatcher that routes Unity projects to lib/sceneGraphCore.js and delegates
// EVERYTHING else to the captured stock implementation, untouched. This file
// evaluates in PARALLEL with UnityAddon.js (Promise.all over package.load)
// and must not depend on it (and vice versa).
const thisAddon = (this && this.fullPath) ? this : { fullPath: process.cwd() };
const path = require("path");
const addonDir = path.resolve(thisAddon.fullPath);
const { createModel } = require(path.join(addonDir, "lib", "sceneGraphCore.js"));
const stock = (typeof window !== "undefined" && window.SceneGraph) || null;

let model = null;

function isUnity() {
	try {
		return typeof trans !== "undefined" && !!trans &&
			trans.project && trans.project.gameEngine === "unity";
	} catch (e) {
		return false;
	}
}

// Lazy model: built on first use from the CURRENT project so build()/reset()
// (trans-full.js:575, :1150 call them after project create/load) just drop
// the cache and the next access re-reads trans.project.
function m() {
	if (!model && isUnity() && trans.project.options && trans.project.options.unity &&
		trans.project.options.unity.sceneIndex) {
		try {
			model = createModel(trans.project.options.unity.sceneIndex, trans.project.files);
		} catch (e) {
			console.warn("[UnitySceneGraph] model build failed:", e);
			model = null;
		}
	}
	return model;
}

// Unity path -> route to the model; otherwise delegate to the captured stock
// implementation verbatim (args and all). Fallbacks mirror stock's
// not-built / missing returns for the impossible stock-absent case.
function route(unityFn, name, noStock) {
	return function (...args) {
		if (isUnity() && m()) return unityFn.apply(m(), args);
		if (stock && typeof stock[name] === "function") return stock[name].apply(stock, args);
		return noStock;
	};
}

window.SceneGraph = {
	// Both lifecycle calls drop the cached Unity model first; non-Unity
	// projects fall through to the stock implementation so RPG Maker
	// behavior is bit-for-bit unchanged.
	build: function (...a) {
		model = null;
		if (isUnity()) return !!m();
		return stock && typeof stock.build === "function" ? stock.build.apply(stock, a) : false;
	},
	reset: function (...a) {
		model = null;
		if (stock && typeof stock.reset === "function") stock.reset.apply(stock, a);
	},
	isAvailable: function (...a) {
		if (isUnity() && m()) return true;
		return stock && typeof stock.isAvailable === "function" ? stock.isAvailable.apply(stock, a) : false;
	},
	getRowSceneInfo: route(function (f, r) { return this.getRowSceneInfo(f, r); }, "getRowSceneInfo", null),
	getOrderedRows: route(function (ids) { return this.getOrderedRows(ids); }, "getOrderedRows", null),
	getScene: route(function (id) { return this.getScene(id); }, "getScene", null),
	getAllScenes: route(function () { return this.getAllScenes(); }, "getAllScenes", []),
	getSceneLabel: route(function (id) { return this.getSceneLabel(id); }, "getSceneLabel", ""),
	getSceneDialogue: route(function (id) { return this.getSceneDialogue(id); }, "getSceneDialogue", []),
	// No Common-Event graph exists in Unity projects.
	getLinkedSceneIds: route(function () { return []; }, "getLinkedSceneIds", []),
	getCallerScenes: route(function () { return []; }, "getCallerScenes", []),
	getSceneAIContext: route(function (id, textProcessor) { return this.getSceneAIContext(id, textProcessor); }, "getSceneAIContext", ""),
	getStats: route(function () { return this.getStats(); }, "getStats", {}),
	// Stock also exposes its command-code table (SceneGraph.js:1396); nothing
	// outside SceneGraph.js itself reads it, but forward it for surface parity.
	CODE: stock ? stock.CODE : undefined,
};
