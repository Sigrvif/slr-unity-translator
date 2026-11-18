# SLRUnity — Python backend

The Python half of the SLRUnity addon (spec
`docs/superpowers/specs/2026-10-13-unity-addon-design.md`): it turns the
proven `unity_translator` proof of concept into a scan/patch backend for
translating Unity games in place — no runtime hooks, no BepInEx — so Unity
games can be translated with SLR Translator's DSLR engine, glossary system,
and QC pipeline the way RPG Maker games are today. It finds every
translatable Japanese string (type-tree fields, raw length-prefixed records,
IL2CPP `global-metadata.dat` literals, Mono Managed-DLL code literals,
TextAsset payloads, plain text files) — every hit carries a risk tag
(`""`/green/yellow/red, the SLR tag scheme) so the JS layer can tag rows and
only the safe tiers are batch-translated by default — catalogs fonts and
textures, finds mosaic-censorship layers, and writes translations back into
a copy of the game (full copy, or the changed files only in patch mode).
The JS addon layer owns SLR's schema; this backend never learns it — the
only contract is the versioned JSON envelope below.

## CLI

Single entry point; all paths travel inside the manifest file (never argv,
which keeps non-ASCII Windows paths safe):

```
python slr_cli.py --manifest job.json --result result.json
```

- `job.json` = `{"version": 2, "command": "...", ...args}`
- `result.json` = `{"ok": true, "version": 2, ...payload}` or
  `{"ok": false, "version": 2, "error": "...", "traceback": "..."}`
- Progress: one JSON object per line on stdout
  (`{"type":"progress","done":n,"total":N,"file":"..."}`); library print
  noise is swallowed into `backend.log`, written next to the manifest.

**Contract version rule:** the manifest's `version` must equal
`CONTRACT_VERSION = 2` in `slr_cli.py`, and every result carries the same
number back. A mismatch fails cleanly with `unsupported contract version`
(the JS side shows "re-create the project with the matching addon
version"). Bump it only with a coordinated addon change.
The authoritative per-command shapes live in
[`docs/CONTRACT.md`](../docs/CONTRACT.md); in brief:

```jsonc
// scan — catalog every translatable string / font / texture in a game
{"version": 2, "command": "scan", "game_dir": "C:/Games/SomeUnityGame"}
// -> payload: strings[] (each row carries its SLR `risk` tag), index
//    {text: [{file, abs, path_id, source, offset, length, key, tokens,
//    risk}]}, fonts[], textures[], demosaic[] (v2.5 candidates), mono[],
//    suspect[], stats {files_scanned, hits, unique, errors}

// patch — write translations into a copy of the game
{"version": 2, "command": "patch",
 "game_dir": "C:/Games/SomeUnityGame",
 "out_dir": "C:/Games/SomeUnityGame_EN",
 "translations_path": "trans.json",      // {japanese: english}
 "index_path": "index.json"}             // the index saved from scan
// optional: "patch_mode": true          (write ONLY changed files — v2),
//           "font_ttf", "font_source_dir", "font_bake",
//           "textures_dir", "textures_manifest_dir",
//           "demosaic": {"enabled":true,"level":"high|medium",
//                        "reveal":bool,"code":bool},       (v2.5)
//           "textfit": {"enabled":true,"char_ratio":0.5}    (v2.6)
// -> payload: stats {copied, files_modified, strings_written,
//    edits_missed, ..., exported_files, out_dir, + per-stage stats}

// extract_textures — dump every Texture2D/Sprite as PNG for SEP editing
{"version": 2, "command": "extract_textures",
 "game_dir": "C:/Games/SomeUnityGame",
 "out_dir": "extract/"}                  // PNGs + manifest.json +
                                         // unity_img_manifest.json

// demosaic_patch (v2.8) — mosaic removal WITHOUT a translation project;
// always patch-mode (out_dir gets only the changed files)
{"version": 2, "command": "demosaic_patch",
 "game_dir": "C:/Games/SomeUnityGame", "out_dir": "out/",
 "demosaic": {"enabled": true, "level": "high", "code": true}}
// -> payload: demosaic_* stats + exported_files
```

A typical flow: `scan` → save `result.index` as `index.json` → build
`translations.json` (`{japanese: english}`) → `patch` with both files.

## Tests

From this directory:

```
python -m pytest tests -v
```

Tests that need the example game (under
`<example-games-root>\`) **skip cleanly when it is
absent** — the suite stays green on any machine; the backend itself is fully
importable and unit-testable with no game files present. The end-to-end test
(`tests/test_e2e_game.py`) scans, patches a full game copy, and rescans it —
budget ~10 minutes when the game is present.

## Packaging

Runtime packaging — the embedded Python interpreters and release-archive
assembly (`runtime/`, `build/`) — happens in Plan 2. This directory is the pure
Python source plus tests; dependencies are `UnityPy==1.25.2` and
`Pillow>=10.0` (see `requirements.txt`).
