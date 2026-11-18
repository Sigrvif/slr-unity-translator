# E2E Checklist — SLRUnity addon in SLR Translator

Manual, user-assisted verification of `dist/SLRUnity-<version>-win.zip`
(built by `build/build_release.py`, structure asserted by
`build/verify_package.py`). Work top to bottom; every step has an expected
result. Any FAIL = stop and note the step number plus what you saw.

Environment used by the build/tests:

- SLR Translator at `<dev-root>/SLR Translator` (READ-ONLY — install only).
- Example game: `<example-games-root>\the example IL2CPP game`
  (IL2CPP Unity game, `UnityPlayer.dll` + `GameAssembly.dll` + `SampleGame_Data`).
- Known-good baseline from the automated smoke scan (build task 6/7):
  `ok:true, files_scanned=74, hits=3572, unique=375, scan_errors=0`.

## A. Install

**1. Install the package (manual extraction — the release format since
v2.7; the name carries the version since v2.8).**
With SLR Translator closed, extract `dist\SLRUnity-<version>-win.zip` INTO
`<dev-root>/SLR Translator/www\addons\` (Windows Explorer opens .zip
natively; the `.7z` twin needs 7-Zip and is only smaller, not different).
The archive already wraps everything in one `SLRUnity\` folder, so
extracting into `www\addons\` directly yields the correct layout —
extract into `www\addons\`, NOT into an extra nested folder (you would
get `SLRUnity\SLRUnity\`).

- Expected: `<dev-root>/SLR Translator/www\addons\SLRUnity\` now exists
  and DIRECTLY contains `package.json`, `UnityAddon.js`,
  `UnitySceneGraph.js`, `docs.html`, `lib\`, `backend\`, `runtime\` (with
  `runtime\python\python.exe`), `assets\` — and no stray `package.json` or
  `lib\` files scattered directly inside `www\addons\` itself.
- FAIL if the files land loose inside `www\addons\` (scattered layout) or no
  `SLRUnity` folder appears.

**2. (Spot-check) The `.7z` twin.**
Extract `dist\SLRUnity-<version>-win.7z` with 7-Zip into a TEMP folder.

- Expected: same single `SLRUnity\` top-level folder with the same
  contents as the `.zip` extraction. (The build already verifies the
  `.7z` by round-trip extraction — this confirms the artifact a
  7-Zip user downloads, through their tool.)

**3. Restart SLR Translator.**
Start it normally (no arguments).

- Expected: starts with no errors in the console (Ctrl+Shift+I / nw devtools if
  needed); no alert about a broken addon. `Loading addon  .../SLRUnity`
  appears in the console log.

## B. New Project: detect → scan → project rows

**4. New Project on the example game.**
*File → New Project* (or the New Project button). The dialog's selection list
now shows a **SLRUnity** entry (e2e-fix11: the shipped
`assets/icon.png` — before the fix the entry's icon 404'd because it pointed
at the addon root, so nothing appeared between the rmrgss and custom parser
entries). Click it: the slide shows FOUR action
sections — *Create a translation project*, *Extract textures for image
translation (SEP)*, *Insert translated images into a game copy* and
*Remove mosaic censorship (standalone)*. In the
first section press **Select Folder** and pick the game FOLDER
`...\the example IL2CPP game` (the `.exe` works too).

- Expected: the dialog closes and the project creation starts immediately —
  the engine is auto-detected as **Unity (IL2CPP / Mono)**, you never pick an
  engine by hand. (If you open the engine dropdown elsewhere, "Unity (IL2CPP /
  Mono)" is listed.)
- FAIL if the SLRUnity entry is missing from the New Project dialog
  (with the addon loaded per step 3), if the slide does not show all four
  action sections, or if picking the game folder alerts "No Unity game found".

**5. Scan + staging progress.**
Confirm the dialog.

- Expected: the scan progress streams into the project-creation console (a
  small dark progress toast may show in the bottom-right corner when the
  console is unavailable); it completes in well under a minute. After the scan
  lines, the SAME console shows **`Staging files i/n name`** lines (e2e-fix7:
  every string-bearing file is copied into the project — this is what makes
  the project self-contained) and then a **`Tags: N of M strings translate by
  default; …`** line (v2.8). No error dialog, no SLR hang. No
  texture-extraction offer appears after the scan anymore — image tools live
  on the New Project slide (section E).

**6. Project rows.**
The project opens.

- Expected: roughly **3.5k rows** (~3572 occurrences, 375 unique texts) — one
  row per Japanese string occurrence. The file list contains scene files with
  `level0` FIRST, the intro-dialog cluster adjacent to it, then
  `global-metadata` entries, TextAssets and text files. Row Properties on a
  level0 row shows the UI hierarchy context (e.g. Canvas/…/Text path), and
  metadata rows show mono/script contexts.
- Tags (v2.8): with SLR's *Japanese source* option ON (its default), rows
  without Japanese show **gray**; the IL2CPP `global-metadata.dat` rows show
  **green** (code literals — translated by default); any yellow/red rows sit
  tagged and skipped by batch translation. With *Japanese source* OFF the
  green/yellow/red tags remain and no gray/gold/darkred appear.

**7. First-run help offer (once).**
- Expected: on the FIRST Unity project only, a confirm offers to open the help
  page (with the path `...\SLRUnity\assets\help.html`). Either
  choice is fine; it must not reappear on the next project.

## C. Translate

**8. Glossary.**
Run glossary extraction on the project (standard SLR flow).

- Expected: completes without errors; glossary rows contain Japanese terms
  from the scanned strings (game title/character names if present).

**9. DSLR batch translate a SMALL selection.**
Select ~20–50 rows (e.g. the level0 dialog cluster), run DSLR batch
translation on the selection.

- Expected: translations fill the selected rows; blank/failed rows stay blank
  (they will simply not be written on export). No timeout/crash.

## D. Export + boot check

**10. Export — patch mode (the default), without a selection.**
*Export game*: choose a NEW output folder (or zip), leave **Export kind** at
**"patch (changed/selected files only)"** (the default) and CHECK
**"Replace game fonts"**.

- Expected: export progress runs via the backend; the summary — console lines
  in the overlay console since e2e-fix10, no success popup — reports rows
  written, `files exported: N` (N > 0), `reverted (originals): M` (M > 0 —
  with no selection ALL project files are in scope, so every file the patch
  did not change is copied verbatim from the project's staged copy) and
  `fonts swapped: N` (N > 0), plus the line
  "apply: copy the files over the game folder". The output contains the
  changed files plus the in-scope originals at their game-relative paths
  (`SampleGame_Data\...`) — the project's string-bearing files,
  not the whole game.
- Progress UI (e2e-fix9/fix10): the export runs under the SAME loading
  overlay/console as project creation — a "Processing" preamble line
  immediately, then "Unity export done/total name" console lines while the
  backend works, one "Including original i/N  <rel>" line BEFORE each revert
  copy, "Packing archive…" in zip mode, and the summary as console lines —
  the overlay's Close button appears only at the very END (AFTER the revert
  copies and the summary lines; the old behavior let it appear with "Log
  ended!" while the copies were still running). The overlay stays up with the
  finished log until the user closes it. Failure alerts (and guard alerts like
  "Nothing to export") fire only AFTER the overlay closed; the success path
  alerts NOTHING.
- Boot check (step 11) needs the patch applied by hand for this mode: copy the
  output folder's contents over a copy of the game first (or export again with
  Export kind = full game copy, which produces a runnable copy directly).
- No-translation variant: exporting a project with every translation still
  blank and the font swap off is VALID in patch mode (an all-originals
  baseline/revert patch) — it runs and reports `strings written: 0` plus
  `reverted (originals): M`; it must NOT alert "Nothing to export".

**10b. Export — patch mode WITH a file selection (scope + revert support).**
Check TWO files in the project's file list — one WITH translated rows and one
with NONE (e.g. a TextAsset you never touched) — then *Export game* again to
another folder with Export kind = patch.

- Expected: the export runs. The output contains EXACTLY the two selected
  files: the translated one with its new strings (backend bytes), the
  untranslated one at its game-relative path with its ORIGINAL bytes (summary
  line "reverted (originals): 1"). This is how you un-translate files: copy the
  patch over the game and the selected file reverts.
- Full-copy guard contrast: the "Nothing to export" alert now belongs to FULL
  game copy mode only (see 10c).

**10c. Export — full game copy mode + guards.**
Set **Export kind** to **"full game copy"** and export to a NEW folder.

- Expected: a FULL standalone copy of the game is produced (v1 behavior), with
  translations and the font swap applied in place; the summary has no
  "file-replacement patch" line and no "reverted" line (the whole game is
  copied anyway).
- No-changes guard: with Export kind = full game copy, every translation
  blanked AND "Replace game fonts" unchecked → alert "Nothing to export in
  full game copy mode: … Use the patch export kind instead …" and NOTHING
  runs (a full copy of an unchanged game would be a no-op; patch mode is the
  revert/baseline escape).
- Missing-original guard: temporarily rename the example game folder (or open
  the project on a machine without it), export with Export kind = full game
  copy → alert "Full copy export needs the original game folder (…). Use patch
  export — the project is self-contained." and NOTHING runs; export with
  Export kind = patch still works (reads the staged copy inside the project).
  Restore the folder afterwards.

**11. Boot check.**
Run the exported game (double-click the exported `.exe` — for the full-copy
export directly, for the patch export after copying its files over a game
copy).

- Expected: the game boots; the new-game/intro dialog text you translated
  shows in ENGLISH; text renders in the narrower SLRCondensedCJK font (no wide
  monospaced Japanese-font gaps, no tofu/missing glyphs for ASCII).

**12. Save → restart → reopen (project persistence).**
Save the project (*File → Save Project* — the `.slrproj`), close SLR
Translator completely, start it again, and reopen the project.

- Expected: the project comes back with the SAME rows (~3.5k, translations
  intact — this restores `index.json` AND the staged game files from the
  `.slrproj`'s `data/` folder); the scene context survives — the file list
  still leads with `level0` and the same clusters, Row Properties on a level0
  row still shows the UI hierarchy (Canvas/…) context, and a Selection
  Translate on level0 rows still shows the scene/dialogue context block in the
  prompt. Patch export still completes with the same summary shape (proving
  the STAGED game, not the original folder, backs the export).
- The saved `.slrproj` now contains the staged game files (e2e-fix7: only the
  string-bearing files, not the whole game) — expect it to be larger than a
  few MB but FAR smaller than the game (tens of MB at most for this example).
  A multi-GB `.slrproj` means something game-sized leaked into the staging
  cache.
- If you exported as a ZIP in step 10: the `.slrproj` must not ALSO contain
  the zip staging tree under `jobs/` — it is deleted once the archive passes
  its integrity check (pre-existing behavior).

## E. Optional (longer): textures + SEP round-trip (standalone tools)

The image workflow no longer touches the strings project: extraction is not
offered at project creation anymore, and the export dialog has no image field.
Both steps below run from the **SLRUnity slide of the New Project
dialog** — no project needs to be open.

**13. Standalone texture extraction.**
*File → New Project* → **SLRUnity** → section *Extract textures for
image translation (SEP)* → **Select Game Folder & Extract Textures** → pick
the example game folder.

- Expected: the New Project dialog closes and a loading overlay/console owned
  by the addon shows per-file extraction progress ("Unity textures
  done/total name" lines). `<Game>_unity_img\` appears next to the game with
  PNG textures + `unity_img_manifest.json`. When it finishes, the console
  shows the completion summary as lines ("Extraction complete", "output: …",
  the 4 next steps) and the overlay's Close button appears only THEN (e2e-fix10:
  the summary is console lines, no popup). The game itself is untouched; no
  project is created.
- FAIL if the overlay closes before extraction really finished, if the
  summary lines are missing, or if the manifest is missing.

**14. SEP round-trip + Insert.**
Create an SEP project on `<Game>_unity_img`, translate one or two images
(OCR → translate → render), export SEP's rendered images to a folder (e.g.
`<Game>_unity_img_translated`). Then *New Project* → **SLRUnity** →
section *Insert translated images into a game copy*: pick 1. the game folder,
2. the `<Game>_unity_img` folder (contains the manifest), 3. SEP's rendered
folder, 4. a NEW output folder; optionally tick *Replace game fonts*; press
**Insert translated images**.

- Row look (e2e-fix9): the four folder rows are SLR's native path-picking
  fields (a full-width text box + a `…` button each, centered like the
  section's other controls — NOT small left-aligned buttons), each with a
  hint line under it ("(not selected)" / "(with unity_img_manifest.json)" /
  "(SEP's export output)" / "(the patched copy lands here)") that switches to
  the picked path once chosen; the font checkbox aligns with the rows.
- Expected: the dialog closes and the addon-owned overlay streams the copy +
  patch progress; the completion summary is console lines (e2e-fix10: "Insert
  complete", "output: …", `textures imported: N` (> 0; plus `fonts swapped`
  when ticked) — no popup) and the overlay's Close button appears only after
  them. In the booted output copy the translated texture shows the edited
  image.
- Guard check: picking a folder WITHOUT `unity_img_manifest.json` as the
  extracted-images folder alerts "No unity_img_manifest.json found …", keeps
  the dialog open and starts nothing.

## F. v2.8: standalone demosaic + Unity DSLR patterns

**15. Standalone mosaic removal (no project).**
*File → New Project* → **SLRUnity** → section *Remove mosaic censorship
(standalone)*. Leave the options at their defaults (high confidence, both
advanced boxes off — your last selection is remembered, so first check they
ARE the defaults on a fresh install), press **Select Game Folder & Remove
Mosaic** and pick the example game (IL2CPP — mosaic assets exist here, but no
managed DLLs).

- Expected: the dialog closes and the addon-owned overlay streams the scan
  ("Unity demosaic done/total name" lines). On this game the run finds
  candidates and packs them: console lines report the per-action counts
  ("object(s) hidden … material(s) neutered …"), a "Packing N changed
  file(s) into <Game>_demosaic.zip…" line, "Demosaic complete", the archive
  path and the apply hint; the overlay's Close appears only after them.
  `<Game>_demosaic.zip` exists next to the game and contains game-relative
  files at the archive ROOT.
- A game with no candidates instead shows the honest "No mosaic candidates
  were patched in this game …" line and produces NO zip.
- Mono variant (the Mono test game, SLRUNITY_MONO_GAME): with *Patch code-level censorship* ticked the
  summary adds the "Demosaic code: N censor method(s) stubbed…" lines and the
  changed DLLs join the zip.

**16. Unity DSLR pattern override (Options → General → SLRUnity).**
Open *Options → General* and find the SLRUnity section: a **Unity
text-protection patterns** checkbox (on) plus four editable pattern boxes.

- Expected: the boxes carry the built-in Unity sets (rich-text tags,
  String.Format braces, batch dividers; empty isolation box). With a Unity
  project open, DSLR batch translation runs with these sets — a row
  containing `<b>太字</b>` or `{0}PT` keeps the markup/placeholder intact
  after translation. With an RPG Maker project open (or no project), your own
  DSLR patterns apply unchanged; the DSLR options tab never shows the Unity
  sets. Unchecking the toggle translates Unity projects with your DSLR
  patterns exactly as before v2.8. If SLRUnreal is also installed, both
  overrides coexist: whichever project engine is open decides whose patterns
  apply (SLRUnity installs its backend chained around SLRUnreal's; if
  SLRUnreal's loads last it covers the slot — uncheck its toggle only if you
  need Unity patterns in that setup and report it).

## Cleanup (after noting results)

- Delete `www\addons\SLRUnity\` (and the exported game copies if
  desired); restart SLR Translator → the Unity engine is gone from the picker.

## What was already verified automatically (do not re-test manually)

- Package structure on ALL packages (`build/verify_package.py`): the `.zip`
  is checked in place on both platforms, the `.7z` by round-trip
  extraction — single top-level `SLRUnity/` folder, all
  load/lib/backend/icon/help entries inside
  (no bundled font — the export font stage uses the host app's
  `www/core/SEP/SLRCondensedCJK-Regular.ttf`), OS-correct runtime payload, no
  tests/caches leaked, package.json `load` files present.
- The extracted WIN package's embedded python runs a REAL scan of the example game
  (`ok:true`, stats above, contract v2 manifest) — see the task-8 report.
- Risk tags (v2.8) are unit-tested on the rules (`backend/tests/test_risk.py`:
  per-source tiers, shape upgrades, worst-tag ranking, and that the tags ride
  a real scan payload) and on the project builder
  (`addon/tests/projectBuilder.test.js`: tag passthrough, worst-tag ranking,
  gray/gold/darkred no-Japanese variants) — as are the DSLR pattern sets
  (`addon/tests/unityEngine.test.js`: eval-parse contract, Unity/rmmv/no-project
  resolution, the SLRUnreal backend chain, the real SLROption class).
- Backend pytest (188) and addon `node --test` (152, incl. contract v2 on every
  job — scan/patch/extract/insert —, staging at creation with index abs
  rewriting, the patch-mode export manifest (`patch_mode` + stagedGameDir +
  `exported_files` reporting), revert copies of selected-but-unexported files
  from the staging cache, full-copy mode + the missing-original guard, the
  three-action slide, the standalone overlay lifecycle with
  loadingEnd-in-finally, the insert patch manifest + manifest guard, and the
  strings-only export dialog with the export-kind select) suites.
