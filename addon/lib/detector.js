// Unity game detection + game-root resolution — pure functions over injected
// fs helpers.
//
// Pure-lib rule: no NW.js / SLR globals and no direct fs use; the wiring file
// (UnityAddon.js) injects {isFile, isDir, listDir, join, dirname}. The same
// helpers are doubled in tests/detector.test.js.
//
// Recognition markers (any one wins), shared by BOTH consumers below:
//   - UnityPlayer.dll / GameAssembly.dll directly inside the game folder
//     (GameAssembly.dll => IL2CPP);
//   - a <Name>_Data subfolder containing globalgamemanagers,
//     globalgamemanagers.assets, or data.unity3d (packed/streamed Mono data).
"use strict";

// Primitive: does `dir` ITSELF look like a Unity game root? (No parent/sibling
// scanning — callers decide how far to search.) Shared by detectUnity (engine
// registration) and resolveGameRoot (scan-target resolution) so the two can
// never disagree about what counts as a Unity game.
function isDirectUnity(fsh, dir) {
  if (!fsh.isDir(dir)) return false;
  if (fsh.isFile(fsh.join(dir, "UnityPlayer.dll")) || fsh.isFile(fsh.join(dir, "GameAssembly.dll"))) return true;
  for (const sub of fsh.listDir(dir)) {
    const sd = fsh.join(dir, sub);
    if (sub.endsWith("_Data") && fsh.isDir(sd)) {
      if (fsh.isFile(fsh.join(sd, "globalgamemanagers")) ||
          fsh.isFile(fsh.join(sd, "globalgamemanagers.assets")) ||
          fsh.isFile(fsh.join(sd, "data.unity3d"))) return true;
    }
  }
  return false;
}

// Engine detection for Engines.registerDetector: boolean-ish "is this Unity?".
// The search covers the given folder itself and, when the picker handed an
// executable, its parent directory's siblings (one level) so launcher-style
// layouts still resolve. Public semantics unchanged (tests bind them).
function detectUnity(fsh, gamePath) {
  // A picked executable resolves to its containing folder. Real fs helpers
  // answer isFile(exe) = true; the extension fallback keeps the same result
  // when the helper cannot see the file (test doubles, stale picks). A dot-
  // named FOLDER still wins because isDir is checked first.
  const looksLikeFile = fsh.isFile(gamePath) ||
    (!fsh.isDir(gamePath) && /\.[^./\\]+$/.test(gamePath));
  const base = looksLikeFile ? fsh.dirname(gamePath) : gamePath;
  if (isDirectUnity(fsh, base)) return "unity";
  for (const child of fsh.listDir(base)) {
    if (isDirectUnity(fsh, fsh.join(base, child))) return "unity";
  }
  return false;
}

// Scan-target resolution (e2e-fix1): SLR hands the createProject handler
// {gameFolder: DIRNAME(picked), selectedFile: picked} — ui.startProject
// (www/js/ui.js:6171) computes gameFolder as the picked path's PARENT, so
// "gameFolder || selectedFile" would scan the WRONG folder whenever the user
// picks the game folder itself (the nwdirectory picker's normal case).
//
// resolveGameRoot returns the actual Unity game root for a picked path:
//   - picked is a file (e.g. the .exe)  -> start from dirname(picked);
//   - picked is a directory             -> start from picked itself;
//   - direct hit (isDirectUnity)        -> that folder;
//   - else scan the start folder's DIRECT children for Unity roots:
//       exactly one  -> that child (user picked a launcher/parent folder);
//       more than one -> throw Error listing them (ambiguous — never guess);
//       none          -> fall back to `gameFolder` when IT is a direct Unity
//                        root, else throw Error ("no Unity game found").
// The wiring file alerts the message; a throw aborts project creation.
function resolveGameRoot(fsh, picked, gameFolder) {
  const looksLikeFile = fsh.isFile(picked) ||
    (!fsh.isDir(picked) && /\.[^./\\]+$/.test(picked));
  const base = looksLikeFile ? fsh.dirname(picked) : String(picked || "");
  if (isDirectUnity(fsh, base)) return base;
  const children = fsh.listDir(base)
    .map((c) => fsh.join(base, c))
    .filter((c) => isDirectUnity(fsh, c));
  if (children.length === 1) return children[0];
  if (children.length > 1) {
    throw new Error("Multiple Unity games found inside:\n  " + base + "\n\nPick one of them directly:\n  " +
      children.join("\n  "));
  }
  if (gameFolder && isDirectUnity(fsh, gameFolder)) return gameFolder;
  throw new Error("No Unity game found at:\n  " + base +
    "\n\nA Unity game folder contains UnityPlayer.dll or GameAssembly.dll, or a <Game>_Data folder with Unity data files.");
}

module.exports = { detectUnity, isDirectUnity, resolveGameRoot };
