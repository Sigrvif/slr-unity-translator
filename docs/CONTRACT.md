# Backend Contract v2

Source of truth: `backend/slr_cli.py`. This document is what the JS addon
(Plan 2) builds against. Any change here requires a contract-version bump in
the backend.

## v2 changes (vs v1)

- Manifest/result envelope `version` is now **2** (was 1).
- `patch` accepts an optional `"patch_mode": true`. When set, the backend
  does NOT copy the whole game: `out_dir` receives ONLY the files actually
  modified this run, as whole-file replacements at their game-relative paths
  (forward slashes). Apply the result by overlaying `out_dir` onto a copy of
  the game.
- `patch` payload now always carries `exported_files`: sorted list of every
  game-relative path written to `out_dir` this run (strings/metadata/
  textasset/textfile + font stage + texture stage, deduped) — report and zip
  from it. `copied` is `false` in patch mode.
- Everything else is unchanged from v1. When `patch_mode` is absent (or
  false), `patch` behaves exactly like v1: full game copy, patched in place
  (`copied: true`); the font/texture stages run in place on that copy.

## Invocation

```
<runtime-python> slr_cli.py --manifest job.json --result result.json
```

- All paths travel inside `job.json` (non-ASCII-safe; never argv).
- Exit code is always 0; outcomes live in `result.json`.
- `backend.log` is appended next to `job.json`.
- stdout during a command is EXACTLY zero or more progress lines, each one
  JSON object: `{"type":"progress","done":<int>,"total":<int>,"file":"<rel>"}`.
  Library prints are swallowed into the log.
- Result envelope: `{"ok":true,"version":2,...payload}` or
  `{"ok":false,"version":2,"error":"<Type: msg>","traceback":"..."}`.
- Manifest `version` != 2 → `ok:false`, `"unsupported contract version ..."`.
- Unknown `command` → `ok:false`.

## Commands

### scan

Manifest: `{"version":2,"command":"scan","game_dir":"<abs>"}`

Payload:
- `strings`: array of StringHit dicts (NOTE: each row's `file` field is
  blanked — per-file info lives in `index`; use `index`).
  - v2.2 collect-all-visible: Japanese strings are all collected as before;
    NON-Japanese strings are collected from display text only — typetree
    leaves in display fields (m_Text / m_Localized / titles / descriptions /
    messages / captions), raw records inside text components (GameObject
    leaf named Text/Text (TMP)/TextMeshPro/TextMeshProUGUI/TMP), TextAsset /
    text-file lines passing the visible-text shape gate (license/package
    boilerplate files skipped wholesale). IL2CPP `metadata` literals stay
    Japanese-only (the literal blob is ~95% framework text with no reliable
    visibility signal). The JS layer tags rows without Japanese gray when
    SLR's "Japanese source" general option is on (projectBuilder takes
    `meta.japaneseSource`).
- `index`: `{japanese_text: [occurrence, ...]}`; occurrence =
  `{file: "<game-rel, forward slashes>", abs: "<abs>", path_id: "<dec string>",
    source: "typetree"|"raw"|"metadata"|"textasset"|"textfile"|"mono",
    offset: <int>, length: <int>, key: "<str>", tokens: [<str|int>...],
    risk: ""|"green"|"yellow"|"red"}`
  - v2.8 `risk` **(also on every `strings` row)**: the hit's SLR risk tag
    (ut/discovery.py `string_risk`). `""` = display text (m_Text etc.,
    line-format scripts); `green` = code literals (`mono`/`metadata` — the
    purpose-built writers make them confirmed-safe script text); `yellow` =
    likely safe, confirm first (JP from non-display typetree fields, JSON/CSV
    values of TextAssets/StreamingAssets files — game DATA in some titles);
    `red` = likely structural (identifier leaves like m_Name/key/cue,
    multi-cell data-table rows, asset filenames, strings equal to a game
    asset file stem). The JS project builder ranks per unique text into
    SLR's per-row tags — red > yellow > "" > green, then the no-Japanese
    variants (`red`/`green` → darkred, `yellow` → gold, else gray) when
    SLR's "Japanese source" option is on — so everything is collected while
    only the safe tiers are batch-translated by default.
  - `path_id` is the literal index for `source:"metadata"`, the object id
    for `textasset`/`typetree`/`raw`, 0 for `textfile`. **v2.1: every
    `path_id` in this payload is a decimal STRING** — Unity ids are int64
    and routinely exceed 2^53, and a JSON number is silently rounded by the
    JS layer's `JSON.parse` (real case: `103263707529466115` → `...110`),
    after which no patch edit can match the object. The patch side accepts
    both the string form and legacy ints.
  - `key` is set (`$.a[0].b` / `r1c1` / `L2`) only for `textasset`/`textfile`.
  - v2.6, `source:"mono"`: Japanese C# literals from the Managed DLLs' #US
    heaps (Mono games; the intro cinematics and one-off lines many games
    keep in code). JP-only gate. `path_id` is the record's #US offset
    (informational — the patcher matches by text). Translated at export by
    rebuilding the heap compacted (translated Japanese bytes reclaimed) and
    repointing every `ldstr` reference (real IL-boundary decoding; a method
    that fails to decode aborts the whole DLL, fail-closed). The DLL GROWS
    to fit (.text section extension + trailing-section shift) — no size
    cap. JP occurrences of the same text collapse to one edit per DLL.
    Projects created before v2.6 must be re-created to collect these rows.
- `fonts`: FontInfo dicts `{file, path_id: "<dec string>", name, font_type}` (file is ABS).
- `textures`: TextureInfo dicts `{file, path_id: "<dec string>", name, tex_type, width, height, format}` (file is ABS).
- `text_assets`: `{file: <game-rel>, path_id: "<dec string>", name, has_japanese, size}`.
- `mono`: report-only `[{dll, count, samples:[...]}]` (JP literals found in
  Managed DLLs; NOT translatable in v1).
- `suspect`: list of human-readable strings (JP-containing files with no
  extractable items).
- `demosaic` **(v2.5)**: mosaic-censorship candidate rows found statically in
  the serialized assets (see ut/demosaic.py). Row:
  `{file: "<game-rel, forward slashes>", path_id: "<dec string>",
    kind: "object"|"material"|"shader"|"script"|"code", name: "<str>",
    confidence: "high"|"medium", evidence: [<human strings>]}`. `script`
  rows are mosaic-named MonoScript classes (report-only, never staged);
  `code` rows are managed-DLL censor types (Mono games; `path_id` "0",
  staged so the code tier can patch them; IL2CPP games surface through
  `script` rows instead). The JS layer
  adds every candidate `file` to the creation-time staging set (the export
  stage re-detects over the staged tree, so candidate-bearing files must ride
  along) and stores the rows in `options.unity.demosaicFound` (its presence —
  even `[]` — marks a project as demosaic-aware; older projects lack the key).
  Detection is deterministic: the export-time run over the same files finds
  the same candidates.
- `stats`: `{files_scanned, hits, unique, errors: [<full error strings>]}`.

### patch

Manifest:
```
{"version":2,"command":"patch",
 "game_dir":"<abs>", "out_dir":"<abs>",
 "translations_path":"<json {jp:en}>", "index_path":"<index from scan>",
 "patch_mode":<bool, optional — default false = v1 full-copy behavior>,
 "font_ttf":"<abs, optional>",
 "font_source_dir":"<abs, optional — dir to discover/patch fonts from when different from game_dir, e.g. the original game for patch-mode exports>",
 "font_bake":{"latin_ttf":"<abs>","cjk_ttf":"<abs>"}, "<optional — e2e-fix19 TMP atlas re-bake>",
 "textures_manifest_dir":"<extract folder with manifest.json, optional>",
 "textures_dir":"<SEP-render folder, optional — needs manifest dir too>",
 "demosaic":{"enabled":true,"level":"high"|"medium","reveal":true,"code":true}, "<optional — v2.5 mosaic-censorship removal stage; tier keys present only when armed>",
 "textfit":{"enabled":true,"char_ratio":0.5,"swing_scale":0.25}, "<optional — v2.6 text-box sizing calibration (kind M per-char width price scaled to char_ratio of the site's own constant + kind S swing amplitude) and text-alignment normalization; keys present only when armed>"}
```

Behavior notes:
- Blank/whitespace/non-string translations are filtered out (never land).
- Index `abs` paths are re-resolved against `game_dir` when stale
  (portable projects).
- Guarded: `out_dir == game_dir`, or either nested inside the other →
  `ok:false` with ValueError BEFORE any filesystem change (in patch mode
  too — a patch folder must never scatter into the original tree).
- `patch_mode` absent/false (v1 default): the whole game is copied to
  `out_dir` and patched in place; font stage runs in place on the copy when
  `font_ttf` is set; texture stage re-imports rendered PNGs in place when
  both texture paths are set.
- `patch_mode: true`: no copy. Sources are read from the original game via
  the index; ONLY modified files are written to `out_dir` at their
  game-relative paths (parent dirs created as needed). The font stage lists
  fonts in `font_source_dir` when set (complete catalog — the staged
  `game_dir` only carries the string-bearing files), else in the original
  `game_dir`, and writes only the font-bearing files; the texture stage
  writes only the files whose textures were re-imported. When a font-bearing
  file was already exported to `out_dir` by an earlier stage of the same run
  (a file may bear both strings and fonts), the font swap builds on that
  output instead of reverting it. The original game is never touched in
  either mode.
- `font_source_dir` guard: equal to or nested inside/outside `out_dir` →
  `ok:false` with ValueError BEFORE any filesystem change (checked up front,
  before the strings stage).
- `font_bake` (e2e-fix19): re-bakes the glyph atlas + glyph/character tables
  of every static TMP_FontAsset from the given TTFs — assets whose ORIGINAL
  baked charset contains CJK code points bake from `cjk_ttf`, the rest from
  `latin_ttf`. Charset = the asset's original unicodes ∪ ASCII ∪ the
  translated strings' characters; missing glyphs are skipped and counted.
  Mode stays STATIC (no runtime re-rendering) and each asset's point size /
  atlas dims / padding / render mode / atlas page PPtrs are kept; the atlas
  textures are re-encoded in their original format. Charset that does not
  fit the existing atlas → that asset is skipped with a logged reason
  (original bytes kept). Runs after the font swap, builds on its out_dir
  files (stage-order rule). Missing font files → `ok:false` before any
  stage. Payload stats: `fonts_baked`, `fonts_bake_failed`,
  `atlas_textures_replaced`.

Payload: patch stats — `{copied, files_modified, strings_written,
skipped_raw, edits_missed, metadata_patched, textassets_patched,
textfiles_patched, exported_files, out_dir}` plus `fonts_swapped` /
`textures_imported` when those stages ran. `exported_files` is the sorted,
deduped list of every game-relative path (forward slashes) written to
`out_dir` this run across all stages; in patch mode it is EXACTLY the
content of the patch folder. `edits_missed > 0` means staged edits that did
not land — surface to the user.

- `demosaic` **(v2.5)**: when the job carries `demosaic.enabled`, the export
  also detects and patches the game's mosaic-censorship layers (ut/
  demosaic.py — the static equivalent of the UniversalUnityDemosaics
  heuristics): mosaic-named GameObjects are deactivated with their renderer
  components stripped (m_Enabled=0, m_Materials=[]), renderers whose every
  material is a high-confidence mosaic candidate are hidden the same way,
  and mosaic materials are "neutered" (m_Shader re-pointed to the game's own
  pipeline-matched unlit/transparent replacement, texture slots cleared,
  _Color/_BaseColor alpha 0, transparent-surface floats/keyword set).
  Detection is by name dictionary (UUD's list) + shader property-table
  evidence + renderer-graph propagation; `level:"medium"` adds weak-only
  evidence (e.g. a shader carrying just _sizeMin/_sizeMax). Runs LAST and
  follows the stage-order rule (catalogs and edits read out_dir/<rel> where
  an earlier stage of the run already wrote it, so string/font edits
  survive); patch mode writes only changed files, full-copy mode edits the
  copied tree in place. Fails soft: a missing replacement shader or an
  unloadable file becomes a note in the log, never a failed export.
  `reveal:true` **(v2.6n)** switches the ShaderReplace/MaterialReplace
  treatment on: all-mosaic renderers are kept — whatever their mesh kind,
  Live2D/2D censor meshes included — and their reveal-eligible materials
  get the game's display shader KEEPING textures, instead of being blanked
  (renderers under mosaic-named GameObjects stay hidden: the object action
  applies in both modes). The display shader is picked statically: first a
  mosaic-named shader's own name with its mosaic tokens stripped
  ("X/Unlit Mosaic" → "X/Unlit", preferred when visible renderers reference
  it), else the game's most-used non-mosaic shader on visible renderers of
  any kind, else the pipeline's unlit entry. `code:true`
  runs the deep tier (ut/monopatch.py, Mono games only): mosaic-named
  Behaviour types in the managed DLLs get their Unity lifecycle methods
  overwritten in place with return-default stubs — same-size writes; EH
  bodies and unsafe return types are skipped with notes; IL2CPP games
  report `demosaic_code_il2cpp: true` and patch nothing. Payload stats:
  `demosaic_objects`, `demosaic_renderers`, `demosaic_materials`,
  `demosaic_revealed`, `demosaic_files`, `demosaic_log` (≤80 human lines);
  the code tier adds `demosaic_code_types`, `demosaic_code_methods`,
  `demosaic_code_files`, `demosaic_code_log`, `demosaic_code_il2cpp`.

### extract_textures

Manifest: `{"version":2,"command":"extract_textures","game_dir":"<abs>","out_dir":"<abs>"}`

Writes into `out_dir`: one PNG per Texture2D named
`<assetfile>__<sanitized name>__<path_id>.png` (unique; Sprites skipped),
plus `manifest.json` AND a copy at `unity_img_manifest.json` mapping each
PNG back to `{file, rel, path_id, png}`. Payload: extract summary dict.

### demosaic_patch (v2.8)

Manifest:
```
{"version":2,"command":"demosaic_patch",
 "game_dir":"<abs>", "out_dir":"<abs>",
 "demosaic":{"enabled":true,"level":"high"|"medium","reveal":true?,"code":true?}}
```

The export's demosaic stage as a standalone command — mosaic-censorship
removal WITHOUT a translation project, for already-translated games. Always
patch-mode behavior: `out_dir` receives ONLY the files changed this run (the
JS layer zips them into a drop-in patch; the game itself is never modified).
`level`, `reveal` and `code` behave exactly like the `patch` command's
`demosaic` block (detection re-runs over the whole game; the deep `code`
tier stubs mosaic-named censor types in the managed DLLs of Mono games and
reports `demosaic_code_il2cpp` for IL2CPP ones).

Payload: `demosaic_objects`, `demosaic_renderers`, `demosaic_materials`,
`demosaic_revealed`, `demosaic_files`, `demosaic_log` (≤80 human lines),
plus the code-tier stats when `code` was armed, and `exported_files` (the
sorted game-relative list of every file written — empty when the game had
no patchable candidates).
