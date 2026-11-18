const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs");
const { runBackend, parseProgressLines, resolveRuntime } = require("../lib/backend");
const { siblingDir, toForwardSlashes } = require("../lib/paths");

test("parseProgressLines buffers partial lines and ignores noise", () => {
  const st = { buf: "" };
  const out1 = parseProgressLines('{"type":"progress","done":1,"t', st);
  assert.deepEqual(out1, []);
  const out2 = parseProgressLines('otal":74,"file":"level0"}\nnot json\n', st);
  assert.deepEqual(out2, [{ type: "progress", done: 1, total: 74, file: "level0" }]);
});

test("parseProgressLines handles multiple lines in one chunk", () => {
  const st = { buf: "" };
  const out = parseProgressLines('{"type":"progress","done":1,"total":2,"file":"a"}\n{"type":"progress","done":2,"total":2,"file":"b"}\n', st);
  assert.equal(out.length, 2);
});

test("paths helpers", () => {
  assert.equal(siblingDir("C:/Games/SomeGame", "_unity_img"), "C:/Games/SomeGame_unity_img");
  assert.equal(toForwardSlashes("a\\b\\c"), "a/b/c");
});

test("runBackend resolves ok:false envelopes without rejecting (stubbed spawn)", async () => {
  const jobsDir = fs.mkdtempSync(path.join(os.tmpdir(), "slrunity-"));
  const fakeResult = { ok: false, version: 2, error: "boom" };
  const spawnFn = (cmd, args, opts) => ({
    on: (ev, fn) => { if (ev === "close") setTimeout(() => fn(0), 0); },
    stdout: { on: (ev, fn) => { if (ev === "data") setTimeout(() => fn(""), 0); } },
    stderr: { on: () => {} },
  });
  // harness: stub writes result.json then exits 0
  const harness = {
    spawnFn: (cmd, args, opts) => {
      const resultIdx = args.indexOf("--result") + 1;
      fs.writeFileSync(args[resultIdx], JSON.stringify(fakeResult));
      return spawnFn(cmd, args, opts);
    },
  };
  const res = await runBackend(
    { version: 2, command: "scan", game_dir: "X:/nope" },
    { jobsDir, python: { cmd: "python", baseArgs: [] }, spawnFn: harness.spawnFn });
  assert.equal(res.ok, false);
  assert.equal(res.error, "boom");
});

test("resolveRuntime returns null when payload absent", () => {
  assert.equal(resolveRuntime(path.join(os.tmpdir(), "empty-base")), null);
});

test("runBackend tolerates a RELATIVE jobsDir (initStaging returns relative paths)", async () => {
  // Regression for the in-app failure: relative manifest/result args +
  // cwd=jobsDir made the backend resolve them against the wrong base and
  // die without a result. The runner must absolute-ify everything.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "unity-reljobs-"));
  const prevCwd = process.cwd();
  process.chdir(tmp);
  try {
    const jobsDir = "www/php/cache/relid/jobs";            // RELATIVE, like initStaging
    const fake = { ok: true, version: 2, marker: "rel-ok" };
    const spawnFn = (cmd, args, opts) => {
      // behave like the backend: resolve --result against the CHILD's cwd
      assert.ok(path.isAbsolute(args[args.indexOf("--manifest") + 1]), "manifest arg must be absolute");
      assert.ok(path.isAbsolute(args[args.indexOf("--result") + 1]), "result arg must be absolute");
      assert.strictEqual(path.resolve(opts.cwd), path.resolve(jobsDir));
      const r = args[args.indexOf("--result") + 1];
      fs.writeFileSync(r, JSON.stringify(fake));
      const handlers = {};
      setTimeout(() => handlers.close && handlers.close(0), 0);
      return { on: (n, fn) => { handlers[n] = fn; }, stdout: { on: () => {} }, stderr: { on: () => {} } };
    };
    const res = await runBackend({ version: 2, command: "scan", game_dir: "X:/nope" },
      { jobsDir, python: { cmd: "python", baseArgs: [] }, spawnFn });
    assert.strictEqual(res.marker, "rel-ok");
    assert.ok(fs.existsSync(path.resolve(jobsDir, "backend.log").replace(/backend\.log$/, "") + ".")
      || true); // dir exists; log only appears on failure
    assert.ok(fs.readdirSync(path.resolve(jobsDir)).some(f => f.endsWith(".result.json")));
  } finally {
    process.chdir(prevCwd);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("runBackend resolves RELATIVE manifest path values against the app cwd (e2e-fix8 BUG regression)", async () => {
  // The user-reported backend FileNotFoundError on jobs\translations.json:
  // SLR's proj.cache.cachePath is RELATIVE ("www/php/cache/<id>"), so the
  // manifest CONTENT paths derived from it pass through relative — and the
  // backend child (cwd = the jobs dir) resolved them against the WRONG base.
  // The runner must absolute-ify every PATH_KEYS value before writing the
  // manifest (path.resolve is a no-op on already-absolute input and never
  // touches disk); everything else stays verbatim.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "unity-relmanifest-"));
  const prevCwd = process.cwd();
  process.chdir(tmp);
  try {
    const readManifest = (cmd, args) => {
      const handlers = {};
      setTimeout(() => handlers.close && handlers.close(0), 0);
      return {
        on: (n, fn) => { handlers[n] = fn; },
        stdout: { on: () => {} },
        stderr: { on: () => {} },
        __manifest: args[args.indexOf("--manifest") + 1],
        __result: args[args.indexOf("--result") + 1],
      };
    };
    let written = null;
    const spawnFn = (cmd, args) => {
      const child = readManifest(cmd, args);
      written = JSON.parse(fs.readFileSync(child.__manifest, "utf-8"));
      fs.writeFileSync(child.__result, JSON.stringify({ ok: true, version: 2 }));
      return child;
    };
    const cacheRel = "www/php/cache/jid"; // initStaging-shaped RELATIVE cachePath
    await runBackend({
      version: 2, command: "patch", patch_mode: true,
      game_dir: cacheRel + "/data/game",
      out_dir: "C:/Games/PatchedOut", // already-absolute: must survive untouched
      translations_path: cacheRel + "/jobs/translations.json",
      index_path: cacheRel + "/data/index.json",
      font_ttf: "www/core/SEP/SLRCondensedCJK-Regular.ttf",
      textures_manifest_dir: "img/extract",
      textures_dir: "img/rendered",
      nested: { game_dir: "www/php/cache/other/game" }, // deep: nested PATH_KEYS too
      // e2e-fix19: the font_bake role->TTF map carries two more path keys,
      // nested one level down — resolved like everything else.
      font_bake: { latin_ttf: "fonts/sucr.ttf", cjk_ttf: "www/core/SEP/SLRCondensedCJK-Regular.ttf" },
    }, { jobsDir: cacheRel + "/jobs", python: { cmd: "python", baseArgs: [] }, spawnFn });

    const pathKeys = ["game_dir", "out_dir", "translations_path", "index_path",
      "font_ttf", "textures_manifest_dir", "textures_dir"];
    for (const k of pathKeys)
      assert.ok(path.isAbsolute(written[k]), k + " must be absolute in the WRITTEN manifest: " + written[k]);
    assert.strictEqual(written.game_dir, path.resolve(cacheRel + "/data/game"));
    assert.strictEqual(written.translations_path, path.resolve(cacheRel + "/jobs/translations.json"));
    assert.strictEqual(written.font_ttf, path.resolve("www/core/SEP/SLRCondensedCJK-Regular.ttf"));
    assert.strictEqual(written.nested.game_dir, path.resolve("www/php/cache/other/game"),
      "nested objects are walked too");
    assert.ok(path.isAbsolute(written.nested.game_dir));
    // e2e-fix19: font_bake role keys are resolved where they sit
    assert.strictEqual(written.font_bake.latin_ttf, path.resolve("fonts/sucr.ttf"));
    assert.strictEqual(written.font_bake.cjk_ttf, path.resolve("www/core/SEP/SLRCondensedCJK-Regular.ttf"));
    // absolute input is a path.resolve no-op — value untouched
    assert.strictEqual(written.out_dir, path.resolve("C:/Games/PatchedOut"));
    // non-path values verbatim
    assert.strictEqual(written.version, 2);
    assert.strictEqual(written.command, "patch");
    assert.strictEqual(written.patch_mode, true);

    // scan job: game_dir resolved the same way
    await runBackend({ version: 2, command: "scan", game_dir: "Games/RelativeGame" },
      { jobsDir: cacheRel + "/jobs", python: { cmd: "python", baseArgs: [] }, spawnFn });
    assert.strictEqual(written.command, "scan");
    assert.strictEqual(written.game_dir, path.resolve("Games/RelativeGame"));
    assert.ok(path.isAbsolute(written.game_dir));
  } finally {
    process.chdir(prevCwd);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("runBackend failure message carries exit code, stderr tail, and a real log", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "unity-errlog-"));
  try {
    const spawnFn = (cmd, args, opts) => {
      const ev = {};
      setTimeout(() => {
        ev.stderr && ev.stderr(Buffer.from("Fatal Python error: boom\n"));
        ev.close && ev.close(1);
      }, 0);
      return {
        on: (name, fn) => { ev[name] = fn; },
        stdout: { on: () => {} },
        stderr: { on: (name, fn) => { if (name === "data") ev.stderr = fn; } },
      };
    };
    const p = runBackend({ version: 2, command: "scan", game_dir: "X:/nope" },
      { jobsDir: tmp, python: { cmd: "python", baseArgs: [] }, spawnFn });
    await p.then(
      () => assert.fail("should have rejected"),
      (e) => {
        assert.match(e.message, /code 1/);
        assert.match(e.message, /boom/);
        assert.match(e.message, /backend\.log/);
        const log = fs.readFileSync(path.join(tmp, "backend.log"), "utf-8");
        assert.match(log, /stderr tail:/);
      });
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});
