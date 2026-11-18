# SLRUnity

A [SLR Translator] addon that adds a
**Unity (IL2CPP / Mono)** game engine, so Unity games can be translated with
SLR's DSLR engine, glossaries and QC pipeline the same way RPG Maker games are.

It turns the proven `unity_translator` proof of concept into a scan/patch
pipeline that works **in place** on game files — no runtime hooks, no BepInEx,
no patcher installed into the game:

- **Scan** finds every translatable Japanese string: serialized type-tree
  fields, raw length-prefixed records, IL2CPP `global-metadata.dat` literals,
  Mono DLL code literals, `TextAsset` payloads (JSON/CSV/...) and plain text
  files, each tagged by risk (`""`/green translate by default, yellow/red
  wait for the user). It also catalogs fonts and textures.
- **Translate** in SLR as usual (DSLR batch, glossary, manual rows; blank rows
  are never written).
- **Patch** writes translations into a full standalone copy of the game —
  folder or zip — optionally swapping embedded fonts with SLR Translator's own
  SLRCondensedCJK font and re-importing SEP-rendered texture images.
- **Standalone tools** on the New Project slide need no project: SEP texture
  extract / insert, and demosaic (mosaic-censorship removal) packed as a
  drop-in zip for already-translated games.

User-facing help lives inside the addon: [`addon/assets/help.html`](addon/assets/help.html)
(offered once on the first Unity project).

## Layout

```
addon/                  JS addon (what ships to SLR users)
  UnityAddon.js         engine wiring: New Project dialog entry, detector,
                        createProject (with game-root resolver), exportHandler,
                        export option fields, first-run help offer (eval'd by
                        SLR's addon loader; the only file touching NW/SLR globals
                        together with UnitySceneGraph.js)
  UnitySceneGraph.js    scene-graph inspector UI
  lib/                  pure CommonJS: backend runner, detector + game-root
                        resolver, project builder, path helpers, scene-graph
                        core (no NW globals; requireable from plain Node)
  assets/icon.png       New Project dialog menu icon (64x64)
  assets/help.html      self-contained user help page
  tests/                node --test suite (lib units + loader-eval harness +
                        the real-backend integration test; skips cleanly
                        without a UnityPy python / the example game)
backend/                Python backend (scan / patch / extract_textures)
  slr_cli.py            single CLI entry point (contract v1, below)
  ut/                   implementation modules
  tests/                pytest suite
build/                  build scripts
  fetch_runtime.py      downloads + verifies the per-OS embedded Python runtime
                        (build-time only; no downloads at install; no font —
                        the export font is SLR's own SLRCondensedCJK)
  build_release.py      stages the addon tree, writes
                        dist/SLRUnity-<version>-<os>.zip AND .7z (version
                        from addon/package.json), verifies both, smoke-tests
                        the staged win runtime against the example game
  verify_package.py     single source of truth for the package structure
                        (runnable standalone; the .7z is verified by
                        round-trip extraction)
  build_source.py       builds the minimal shareable source archive
                        dist/SLRUnity-<version>-source.zip (addon/ + backend/
                        + build/ + docs/ + README.md, no caches/payloads)
runtime/                fetched payloads (win/, linux/) — not committed
                        anywhere; produced by fetch_runtime.py
dist/                   build output: SLRUnity-<version>-<os>.zip / .7z and
                        SLRUnity-<version>-source.zip (+ stage trees)
docs/
  CONTRACT.md           the frozen v1 JSON contract between JS and Python
  fixtures/             synthetic scan fixture (95 rows, no game data)
  superpowers/specs/    design spec (2026-10-13-unity-addon-design.md)
  superpowers/plans/    per-phase implementation plans
_snapshots/             local-only archive of the pre-git task zips (gitignored)
.sdd/                   task briefs/reports + review packages (gitignored)
```

## The contract (read this first)

`backend/slr_cli.py` is invoked as

```
<python> slr_cli.py --manifest job.json --result result.json
```

with all paths inside `job.json`, exit code always 0, outcomes in
`result.json`, and progress as one JSON object per stdout line. Commands:
`scan`, `patch`, `extract_textures`, `demosaic_patch`. The full shape —
manifest fields, result envelopes, occurrence format — is
[`docs/CONTRACT.md`](docs/CONTRACT.md).
The JS layer consumes exactly those shapes; any change requires a
contract-version bump. Design rationale: [`docs/superpowers/specs/2026-10-13-unity-addon-design.md`](docs/superpowers/specs/2026-10-13-unity-addon-design.md).

## Building

Requires a local Python ≥ 3.10 (for the build scripts) and network access at
BUILD time only:

```
python build/fetch_runtime.py --os win     # embedded win python + UnityPy + Pillow
python build/fetch_runtime.py --os linux   # linux runtime (cross-installed deps on a win host)
python build/build_release.py --os win     # -> dist/SLRUnity-<version>-win.zip + .7z (+ staged smoke test)
python build/build_release.py --os all     # both platforms
```

`fetch_runtime.py` is idempotent (re-runs are cheap). `build_release.py --os win`
also runs a smoke scan of the staged runtime against the example game (skipped
with `--skip-runtime`, which produces a structure-only package). The `.7z`
needs any 7-Zip CLI (the host app's bundled 7zip-bin is found automatically);
without one the `.zip` is still built and verified.

## Testing

```
cd backend && python -m pytest tests -q      # 188 tests (~35 min with the games present)
cd addon   && node --test tests/             # 152 tests
```

The addon suite runs on plain Node (no NW.js). Its last test is a REAL-backend
integration scan over the example game through `lib/backend.runBackend` with
the system python; it **skips cleanly** unless `python` (≥ 3.10) imports
UnityPy and the example game is present. `SLRUNITY_INTEG_PYTHON=<bogus-cmd>`
forces the skip for testing the skip.

Python deps for the backend suite: `UnityPy==1.25.2`, `Pillow>=10.0`,
`pytest>=7.0` (see `backend/requirements.txt`).

## Installing into SLR Translator

v2.7 ships plain archives on purpose: users extract them into the addons
folder themselves, so they always know where the addon lives and how to
remove it (delete the folder). The old `.tap` one-click installer format
was retired — a custom extension nothing opens by double-click confused
more users than it helped. (SLR's own installer still accepts `.tap`
drops, but the addon never depended on it: the loader just scans
subfolders of `www/addons/`.)

Both packages wrap everything in a single top-level `SLRUnity/` folder —
that is what SLR's addon loader expects to find under `www/addons/`.

- Download `dist/SLRUnity-<version>-win.zip` (or `-linux`; the `.7z` is the
  same content, smaller, and needs 7-Zip to open — the `.zip` opens natively
  everywhere).
- Extract the archive INTO `www/addons/` (the folder inside your SLR
  Translator installation that contains `SLR Translator.exe`). The
  result must be `www/addons/SLRUnity/` directly containing
  `package.json` with `lib/`, `backend/`, `runtime/`, `assets/` beside
  it — not `www/addons/SLRUnity/SLRUnity/`.
- To remove or update: delete (or overwrite) `www/addons/SLRUnity/`.

Then restart SLR Translator: the New Project dialog shows a **SLRUnity**
entry (Select Folder → pick the game folder; the Unity engine
auto-detects). Full usage walkthrough: the addon's built-in help page; full
manual test plan: [`docs/E2E-CHECKLIST.md`](docs/E2E-CHECKLIST.md).

## Version control

This repository's git history starts with the complete source import; day-to-day
work uses normal commits. The pre-git development phase was tracked with a
task-snapshot discipline (per-task zip archives in `_snapshots/` plus `.sdd/`
brief/ledger directories). Those remain only as a local, gitignored archive of
how the project evolved before the import — they are not part of the repository
and nothing depends on them.

## Constraints honored (for contributors)

- `docs/CONTRACT.md` is frozen; the JS side adapts, never the backend shapes.
- Zero modification of SLR Translator — everything goes through the documented
  addon surface (`Engines.registerDetector`, `engines.addHandler`,
  `addProperty("exportOptionFields", ...)`, `common.initStaging`,
  `common.aSpawn`/`get7zipBin`, browser `alert`/`confirm`).
- `addon/lib/*.js` stay pure CommonJS (plain-Node testable); NW/SLR globals
  live only in `UnityAddon.js` / `UnitySceneGraph.js`.
- Runtime payloads are fetched at build time only — installs and users never
  download anything.
- No font is bundled: the export font stage uses the HOST app's own
  `www/core/SEP/SLRCondensedCJK-Regular.ttf`, resolved at runtime through
  `common.getRootDir()` (SLR already ships that font for SEP). A missing font
  alerts and skips only the font stage.

## License

Copyright (C) 2026 Shisaye.

GPLv3 or later — see [LICENSE](LICENSE); the components bundled into release
packages (CPython runtime, pip packages) are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). No FMOD runtime is bundled
(see the notices for the build-time strip). Runtime payloads are fetched at
build time by `build/fetch_*.py`, never committed.
