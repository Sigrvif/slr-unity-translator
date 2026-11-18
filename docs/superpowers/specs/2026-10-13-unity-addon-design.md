# SLR Unity Translator — Design

Date: 2026-10-13
Status: Approved design (brainstorming complete) — pending implementation plan

## 1. Purpose

Turn the proven `unity_translator` proof of concept (in-place Unity game
translation without runtime hooks) into a drop-in addon for SLR Translator,
so Unity games can be translated with the DSLR engine, glossary system, and
QC pipeline the way RPG Maker games are today.

Hard requirements:

- **Drop-in**: ships as a `.tap` (zip) that extracts into `www/addons/`.
  After restart, "Unity" appears in the New Project dialog. Nothing else
  required — no Python installed, no pip, no network, no SLR modifications.
- **Zero modification of SLR Translator itself**: every integration goes
  through the documented addon/engine API. SLR's built-in docs are never
  touched; the addon ships its own help page.
- **Full scope v1**: strings, fonts, and image (texture) translation via
  the existing SEP workflow.

Decisions made during brainstorming:

| Question | Decision |
|---|---|
| Python distribution | Embedded Python runtime inside the addon package |
| Platforms | Two separate packages: `SLRUnity-win.tap`, `SLRUnity-linux.tap` |
| v1 scope | Strings + fonts + textures (SEP collect/prune pattern) |
| Home | `<dev-root>/SLR Unity Translator\` (new folder) |
| Architecture | Approach A: thin JSON-CLI backend, smart JS addon |
| Version control | None. Milestone snapshots as zips into `_snapshots/` |

## 2. Architecture

The Python backend never learns SLR's schema; the JS layer never parses
Unity files. The only contract is a versioned JSON envelope over a
manifest-file CLI (the pattern `rmrgss` already uses for Ruby, which keeps
non-ASCII Windows paths out of argv).

```
New Project → pick game folder → JS detector ("unity")
  → JS spawns backend: scan(manifest) ──result.json──► scan payload
  → JS builds SLR project object (files/data/context) → openFromTransObj
  → user translates with DSLR (glossary + Unity SceneGraph active)
  → Export → JS gathers translations → backend: patch(...)
  → full translated game copy at user-picked target (dir or zip)
```

### 2.1 Repository layout

```
<dev-root>/SLR Unity Translator\
  addon/                  ← ships as the .tap root
    package.json          (autoload:false, load:[...] — see §2.3)
    UnityAddon.js         engine registration, detector, createProject,
                          exportHandler, menu entries, help page opener
    UnitySceneGraph.js    window.SceneGraph replacement (Unity flavor)
    lib/                  spawn wrapper, project builder, SEP handoff,
                           manifest/result I/O
    assets/               bundled narrow replacement font(s), help page
  backend/                ← Python, refactored from unity_translator
    slr_cli.py            manifest/result JSON entry point
    ut/                   scanner/patcher/fonts/textures (moved as-is)
    requirements.txt      UnityPy, Pillow
    tests/                pytest
  runtime/
    win/                  python embeddable + site-packages (BUILT)
    linux/                portable CPython + site-packages (BUILT)
  build/
    fetch_runtime.py      per-OS interpreter download + pip install
    build_tap.py          assembles dist/SLRUnity-{win,linux}.tap
  _snapshots/             milestone zips (excluded from .tap)
  dist/                   build output (excluded from .tap inputs)
  docs/superpowers/specs/
```

Runtime payloads (~100 MB per OS) are built by `fetch_runtime.py`, never
committed. Each `.tap` embeds only its own OS payload under the same
internal path (`runtime/`) so the JS resolves the interpreter identically
on both platforms. Shipped `.tap` files are fully self-contained.

### 2.2 Backend CLI contract

Single entry point; everything through files:

```
<runtime>/python slr_cli.py --manifest job.json --result result.json
```

- `job.json` = `{"version": 1, "command": "...", ...args}`. All paths
  (game dir, output dir, index file, translation map, SEP render dir)
  travel inside this file — never as argv.
- `result.json` = `{"ok": true, ...payload}` or
  `{"ok": false, "error": "...", "traceback": "..."}`.
- Progress: one JSON object per line on stdout
  (`{"type":"progress","done":n,"total":N,"file":"..."}`); anything
  non-JSON on stdout is ignored (forwarded to the log).
- Logs: `backend.log` written next to the manifest; manifests live under
  `cachePath/jobs/` in the project's staging cache, so the log is always
  findable at `cachePath/jobs/backend.log`. JS error dialogs point there.

Commands:

| Command | Args | Payload |
|---|---|---|
| `scan` | game_dir | strings from all five sources (text, file key, path_id/line/literal index, byte length, source, context), per-file grouping, font catalogue, texture catalogue, TextAsset/plain-file report incl. skipped-but-suspect items, stats |
| `patch` | game_dir, out_dir, translations `{jp:en}`, index (from scan), font_ttf (optional), textures_dir (optional) | stats: files modified, strings written, textures imported, fonts swapped, per-source breakdown |
| `extract_textures` | game_dir, out_dir | manifest of extracted PNGs (filename → asset file + path_id) |
| (fonts handled as `patch` args) | | |

Contract `version` is written by the backend and checked by JS at
createProject and export. Mismatch produces a clean
"re-create the project with the matching addon version" error.

### 2.3 Addon package manifest

`addon/package.json`:

- `name: "SLRUnityTranslator"`, `title`, `version`, `author`
- `autoload: false`, `load: ["UnityAddon.js", "UnitySceneGraph.js", ...lib files]`
- `builtin` NOT set (user addon)
- no `dependencies` (bundled runtime instead)

Engine id: `"unity"` (verified free in stock SLR engines). The JS layer
resolves the interpreter as `thisAddon.basePath + "/runtime/..."` per OS;
if the payload for the running OS is absent the addon stays silent (no
Unity entry appears).

## 3. Text pipeline

### 3.1 Detection

`Engines.registerDetector(async (gamepath) => "unity" | false)`:
true when the picked path (folder or .exe) sits next to a `*_Data` folder
containing `globalgamemanagers` / `globalgamemanagers.assets` /
`data.unity3d`, or a sibling `UnityPlayer.dll` / `GameAssembly.dll`.

### 3.2 createProject

`engines.addHandler("unity", "createProject", async (args) => projectObj)`:

1. `common.initStaging()` — stage **metadata only**, never the multi-GB
   game. The game stays at `loc`; export reads from there.
2. Spawn backend `scan`; progress events feed SLR's progress UI.
3. Persist the scan index into `cachePath/data/` (needed at export).
4. Build the project object:
   - `gameEngine: "unity"`, `parser: "unity"`, `loc: gameFolder`,
     `projectId/cache` from staging.
   - `files`: one entry per asset file that yielded ≥1 string
     (`SampleGame_Data/level0`,
     `.../localization-...bundle`, …). Rows deduped per file, ordered by
     (path_id, offset) — scene/creation order, "walkthrough order".
   - `data: [[jp, ""], ...]`; `context[row]` = occurrence strings
     (`Canvas/NewGameCheck/confirm/Text (TMP)` for scene objects,
     `m_TableData[12].m_Localized` for localization rows).
   - `tags`: all rows tagged as dialogue-class so glossary extraction's
     dialogue filter includes them (verify exact tag semantics during
     implementation; fallback: leave untagged if the extractor includes
     untagged rows).
   - `project.options.unity`: backend version, game path, scene index
     (file keys + row ranges), catalogues (fonts, textures, TextAssets).
5. Return → `openFromTransObj`.

Re-open works from SLR's normal autosave. Game updated by its developer →
create a new project in v1 (re-scan action is future work).

### 3.3 String coverage (scope)

Principle: **if text can show up in-game, it is in scope.** Five sources:

1. MonoBehaviour string fields with type trees (bundles, Addressables,
   Unity Localization string tables) — typetree read/write.
2. Typetree-less IL2CPP scene objects — validated raw scanner (UTF-8
   length-prefix records, zero-padding discriminator, GameObject
   hierarchy context) and payload-rebuild write-back via `set_raw_data`.
3. **IL2CPP code string literals** (`global-metadata.dat`): parse the
   metadata header (v24.1–v39 layouts), extract the string literal table
   (entries of data-offset + length into a UTF-8 blob), filter through
   the same Japanese-display-text gate. Write-back rebuilds the literal
   blob with translated strings at any length: new blob appended, entry
   offsets/lengths repointed, table structure and count unchanged —
   code references literals by index, so nothing else moves. Japanese
   literals found this way join the project as their own pseudo-file
   (`GameAssembly literals`), context = literal index + text.
4. **TextAsset contents**: the serialized byte payload decoded and
   extracted by format — JSON (parse, walk string values, reserialize
   preserving key order), CSV (round-trip), line-based fallback for
   everything else. Write-back rewrites the payload via type tree (any
   length). One pseudo-file per TextAsset, context = JSON path or line
   number.
5. **Plain-text files in StreamingAssets** (`*.json`, `*.jsonc`,
   `*.txt`, `*.csv`, `*.bytes`-as-text): same format handlers as
   TextAssets, direct file rewrite. The Japanese-display-text gate is
   the safety filter — save/config files almost never contain display
   Japanese and fall out naturally; anything skipped-but-suspect is
   listed in the scan report for manual review.

Remaining limitation, stated honestly: **Mono code literals** inside
`Assembly-CSharp.dll` (.NET `#US` heap). Any size change shifts every
subsequent string offset, and repointing them means rewriting IL bodies —
a project of its own from Python. These are *detected and reported*
(scan lists Japanese literals found in managed DLLs) but not translated
in v1. In practice text-heavy Mono games keep dialogue in assets or
text files (covered by 1–5); IL2CPP games — the common hard case — are
fully covered by 3.

Proven baseline (from the PoC sessions): sources 1–2 are implemented and
validated — the example IL2CPP game scan yields 280 unique strings incl.
the new-game dialogs and their buttons; patch round-trip verified
byte-level (object counts, externals, only intended strings changed) and
the translated game boots with the dialog in English. Sources 3–5 are
new work specified here: a crude pass over the example game's
`global-metadata.dat` already surfaced 217+ Japanese literals, so the
added coverage is material.

## 4. Unity Scene Graph

`UnitySceneGraph.js` installs a replacement `window.SceneGraph` exposing
the stock API surface: `build, reset, isAvailable, getRowSceneInfo,
getOrderedRows, getScene, getAllScenes, getSceneLabel, getSceneDialogue,
getLinkedSceneIds, getCallerScenes, getSceneAIContext, getStats`.

- **Capture-then-delegate**: the stock implementation is captured at
  load; any call while the open project is not a Unity project delegates
  to it untouched. RPG Maker behavior must be bit-identical.
- **Scene = one asset file with strings.** `levelN` scenes order by build
  index; localization-table bundles, the IL2CPP-literal pseudo-file,
  TextAssets, and StreamingAssets text files are pseudo-scenes after
  them (rows inside stay in file order, which is author order for
  tables/literals/JSON — the best ordering signal those formats have).
- **Subgroup = top hierarchy root** of the context path (`Canvas`,
  `AchievementPopup`, `Required/DontDestroyOnLoad`, …).
- `getSceneAIContext` renders per subgroup the member strings in
  hierarchy order with their leaf node names, e.g. the new-game dialog
  together with its confirm/cancel labels — the context that makes
  single-word rows like 開始 translatable correctly.
- `getLinkedSceneIds`/`getCallerScenes`: empty in v1.
- **No invented speakers**: leaf node names are real data; the addon does
  not guess who "says" a line.
- Scene metadata lives in `project.options.unity.sceneIndex` (compact),
  so it survives autosave and `.slrproj` export without the staging cache.

This one integration point lights up DSLR scene-aware batching,
`enableSceneContext` prompts, glossary scene-ordering, and review scene
context — all with zero SLR changes.

## 5. Images (SEP handoff), fonts, export

### 5.1 Textures → SEP

- Addon menu action **"Extract textures for image translation"**
  (enabled while a Unity project is open): backend extracts every
  `Texture2D` to a flat folder **`<game>_unity_img/`** next to the game.
  Flat/top-level-only is deliberate — SEP's New Project slide scans
  exactly that as a "plain image folder" project type. No SEP internals
  are touched.
- `Sprite` objects are skipped (they share pixel data with their
  Texture2D; including them only duplicates images).
- Filenames: `<assetfile>__<sanitized hierarchy>__<path_id>.png`,
  plus `unity_img_manifest.json` mapping filename → asset file + path_id.
- User prunes by deleting files, creates an SEP project on the folder,
  translates, exports rendered images to a folder (suggested default:
  `<game>_unity_img_translated/`).

### 5.2 Fonts

Export-option checkbox *"Replace embedded Japanese fonts with a narrow
font"*: swaps every embedded `Font`/TMP source TTF for the bundled narrow
TTF (CJK-covered so untranslated text still renders; font object names
preserved so references resolve).

### 5.3 Export

`exportHandler(targetPath, options)` (mode dir|zip):

1. Build `{jp: en}` from project rows (blank = skipped, original kept).
2. Write job manifest: game dir, index from staging cache, translations,
   font flag, SEP render dir if provided (remembered per project in
   `options.unity.sepRenderDir`).
3. Spawn backend `patch`: copy game → target, patch strings, swap fonts,
   re-import rendered PNGs found in the SEP render dir (matched by
   filename via the image manifest).
4. Zip mode: zip the output after the dir export.
5. Return truthy (halts PHP export).

## 6. Error handling

- Result envelope always lands in `result.json`; JS turns failures into a
  normal SLR error dialog pointing at `cachePath/jobs/backend.log`.
- Missing result file / non-JSON / non-zero exit → clean error, never
  silent failure.
- Contract version mismatch at create or export → explicit message.
- Long scans/patches: progress lines into SLR's progress UI; user cancel
  kills the process tree (partial output dir is left and reported).

## 7. Testing

- **Python (pytest)**: unit tests on synthetic payloads (raw scanner
  validation rules, record rebuild, context resolver, metadata literal
  table parse/rebuild for each supported metadata version layout, JSON/
  CSV/line extraction round-trips); round-trip tests (scan → patch →
  re-scan) against the two example games, auto-skipped when the games
  are absent — including an IL2CPP literal round-trip on
  the example IL2CPP game (`global-metadata.dat` patched, re-parsed,
  translated literals present, file loads) and a TextAsset/plain-file
  round-trip.
- **JS (node --test)**: pure functions only — project-object builder and
  scene-graph mapping against a committed scan-result fixture (small JSON
  carved from the real the example IL2CPP game scan).
- **E2E acceptance (documented checklist + scripted backend steps)**:
  1. Create project on the example IL2CPP game → 280 strings with
     contexts, dialog cluster adjacent.
  2. Translate (incl. 開始/やめる) → export → copy boots, dialog English.
  3. うさぎの (Mono, text-light) → project creation works; texture
     extraction produces the SEP-compatible folder.
  Booting games cannot be automated here; that step stays manual.

## 8. Known risks / open items for the implementation plan

1. Verify GlossaryExtractor's treatment of untagged rows (tag choice in
   §3.2 depends on it).
2. Windows embeddable Python: `._pth` edit to enable site-packages;
   confirm UnityPy's binary wheels (brotli/lz4) install cleanly.
3. Linux portable CPython distribution choice (python-build-standalone
   vs distro package); glibc target.
4. Menu integration point for "Extract textures" (which SLR menu/toolbar
   API an addon may extend without modifying SLR).
5. Non-ASCII addon base path (app installed to a Japanese folder name):
   interpreter path resolution and manifest-relative logging.
6. Unity version variance: type-tree presence differs across
   engine/release types; scanner must degrade to raw mode per-object
   (already does).
7. `global-metadata.dat` layout differs across metadata versions
   (v24.1–v39 field ordering); support the common range explicitly and
   fail loudly on unknown versions (never guess offsets).
8. JSON reserialization changes formatting (whitespace/key order
   preserved where possible, escaping normalized); games doing naive
   substring matching on these files would break — scan report flags
   every file whose formatting changed. JSONC comments block strict
   parsing → those files fall to the line-based handler.
9. StreamingAssets false positives (a save/config file containing
   display Japanese would be offered for translation); mitigated by the
   display-text gate + the skipped-but-suspect report, plus the
   translator simply leaving rows blank.
