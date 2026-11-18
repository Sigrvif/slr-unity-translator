const test = require("node:test");
const assert = require("node:assert");
const { detectUnity, isDirectUnity, resolveGameRoot } = require("../lib/detector");

// Synthetic fs helper doubles (see UnityAddon.js for the real injection):
// detectUnity consumes {isFile, isDir, listDir, join, dirname} and nothing else.
function makeFsh(dirs, files) {
  const norm = (p) => String(p).replace(/\\/g, "/");
  return {
    isDir: (p) => dirs.includes(norm(p)),
    isFile: (p) => files.includes(norm(p)),
    listDir: (p) => (dirs.concat(files).filter((f) => norm(f).startsWith(norm(p) + "/"))
      .map((f) => f.slice(norm(p).length + 1)).map((s) => s.split("/")[0])
      .filter((v, i, a) => a.indexOf(v) === i)),
    join: (...a) => a.join("/"),
    dirname: (p) => norm(p).slice(0, norm(p).lastIndexOf("/")),
  };
}

test("detectUnity accepts a Unity game folder and exe path", () => {
  const fsOk = {
    isDir: (p) => p.endsWith("SomeGame_Data"),
    isFile: (p) => p.endsWith("globalgamemanagers") || p.endsWith("UnityPlayer.dll"),
    join: (...a) => a.join("/"), dirname: (p) => p.slice(0, p.lastIndexOf("/")),
    listDir: (p) => p.endsWith("SomeGame") ? ["SomeGame_Data"] : [],
  };
  assert.equal(detectUnity(fsOk, "C:/Games/SomeGame"), "unity");
  assert.equal(detectUnity(fsOk, "C:/Games/SomeGame/SomeGame.exe"), "unity");
});

test("detectUnity rejects non-unity", () => {
  const fsNo = { isDir: () => false, isFile: () => false, join: (...a) => a.join("/"),
                 dirname: (p) => p.slice(0, p.lastIndexOf("/")), listDir: () => [] };
  assert.equal(detectUnity(fsNo, "C:/Games/RPGMV_Game"), false);
});

test("detectUnity recognises IL2CPP GameAssembly.dll and data.unity3d markers", () => {
  // GameAssembly.dll sits next to the exe / in the game root (IL2CPP).
  const il2cpp = makeFsh(["C:/G/MyGame", "C:/G/MyGame/MyGame_Data"],
                         ["C:/G/MyGame/GameAssembly.dll"]);
  assert.equal(detectUnity(il2cpp, "C:/G/MyGame"), "unity");
  // Mono-style packed data: <Game>_Data/data.unity3d instead of globalgamemanagers.
  const packed = makeFsh(["C:/G/Other", "C:/G/Other/Other_Data"],
                         ["C:/G/Other/Other_Data/data.unity3d"]);
  assert.equal(detectUnity(packed, "C:/G/Other/Other.exe"), "unity");
});

test("detectUnity does not misread a _Data dir that lacks Unity markers", () => {
  const fake = makeFsh(["C:/G/Fake", "C:/G/Fake/Fake_Data"],
                       ["C:/G/Fake/Fake_Data/tables.rpa"]);
  assert.equal(detectUnity(fake, "C:/G/Fake"), false);
});

test("detectUnity scans sibling entries so an exe-picked nested folder still matches", () => {
  // Picker handed the launcher exe one level above the actual game folder.
  const fsh = makeFsh(["C:/Launch/RealGame", "C:/Launch/RealGame/RealGame_Data"],
                      ["C:/Launch/RealGame/UnityPlayer.dll"]);
  assert.equal(detectUnity(fsh, "C:/Launch/Launcher.exe"), "unity");
});

// ---------------------------------------------------------------------------
// resolveGameRoot (e2e-fix1): SLR hands createProject {gameFolder:
// DIRNAME(picked), selectedFile: picked} — the resolver must find the actual
// Unity root instead of trusting gameFolder (the picked path's PARENT).
// ---------------------------------------------------------------------------

test("isDirectUnity is the shared primitive: _Data markers AND player DLLs, false for non-games", () => {
  const viaData = makeFsh(["C:/G/A", "C:/G/A/A_Data"], ["C:/G/A/A_Data/globalgamemanagers.assets"]);
  const viaDll = makeFsh(["C:/G/B"], ["C:/G/B/UnityPlayer.dll"]);
  const viaIl2cpp = makeFsh(["C:/G/C"], ["C:/G/C/GameAssembly.dll"]);
  const notGame = makeFsh(["C:/G/D", "C:/G/D/D_Data"], ["C:/G/D/D_Data/tables.rpa"]);
  assert.equal(isDirectUnity(viaData, "C:/G/A"), true);
  assert.equal(isDirectUnity(viaDll, "C:/G/B"), true);
  assert.equal(isDirectUnity(viaIl2cpp, "C:/G/C"), true);
  assert.equal(isDirectUnity(notGame, "C:/G/D"), false);
  assert.equal(isDirectUnity(notGame, "C:/G/DoesNotExist"), false); // missing dir
});

test("resolveGameRoot: picked Unity folder (direct hit via _Data) returns it, not the parent", () => {
  const fsh = makeFsh(["C:/Games/MyGame", "C:/Games/MyGame/MyGame_Data"],
                      ["C:/Games/MyGame/MyGame_Data/globalgamemanagers"]);
  // ui.startProject passes gameFolder = PARENT of the picked folder — the
  // resolver must NOT be fooled into scanning "C:/Games".
  assert.equal(resolveGameRoot(fsh, "C:/Games/MyGame", "C:/Games"), "C:/Games/MyGame");
});

test("resolveGameRoot: direct hit via UnityPlayer.dll", () => {
  const fsh = makeFsh(["C:/Games/MyGame"], ["C:/Games/MyGame/UnityPlayer.dll"]);
  assert.equal(resolveGameRoot(fsh, "C:/Games/MyGame", "C:/Games"), "C:/Games/MyGame");
});

test("resolveGameRoot: exe pick resolves to the containing game folder", () => {
  const fsh = makeFsh(["C:/Games/MyGame", "C:/Games/MyGame/MyGame_Data"],
                      ["C:/Games/MyGame/MyGame.exe", "C:/Games/MyGame/GameAssembly.dll"]);
  // picked = the exe; gameFolder = its dirname (the game root itself).
  assert.equal(resolveGameRoot(fsh, "C:/Games/MyGame/MyGame.exe", "C:/Games/MyGame"), "C:/Games/MyGame");
});

test("resolveGameRoot: single-child parent (launcher folder) resolves to the child", () => {
  const fsh = makeFsh(["C:/Launch", "C:/Launch/RealGame", "C:/Launch/RealGame/RealGame_Data"],
                      ["C:/Launch/RealGame/UnityPlayer.dll"]);
  assert.equal(resolveGameRoot(fsh, "C:/Launch", "C:/"), "C:/Launch/RealGame");
});

test("resolveGameRoot: ambiguous two Unity children -> throw listing both", () => {
  const fsh = makeFsh(
    ["C:/Dual", "C:/Dual/GameOne", "C:/Dual/GameOne/GameOne_Data",
     "C:/Dual/GameTwo", "C:/Dual/GameTwo/GameTwo_Data"],
    ["C:/Dual/GameOne/GameOne_Data/globalgamemanagers",
     "C:/Dual/GameTwo/GameTwo_Data/data.unity3d"]);
  assert.throws(() => resolveGameRoot(fsh, "C:/Dual", "C:/"), (e) => {
    assert.match(e.message, /Multiple Unity games/);
    assert.match(e.message, /GameOne/);
    assert.match(e.message, /GameTwo/);
    return true;
  });
});

test("resolveGameRoot: nothing at the pick -> falls back to a direct-Unity gameFolder", () => {
  const fsh = makeFsh(["C:/Games/MyGame", "C:/Games/MyGame/MyGame_Data"],
                      ["C:/Games/MyGame/UnityPlayer.dll"]);
  // picked = an unrelated empty folder (e.g. a stray drop); gameFolder IS the
  // game root (happens when the RPG Maker slide picked the folder first).
  assert.equal(resolveGameRoot(fsh, "C:/Empty", "C:/Games/MyGame"), "C:/Games/MyGame");
});

test("resolveGameRoot: nothing anywhere -> throw 'No Unity game found' with the picked path", () => {
  const fsh = makeFsh(["C:/Nothing/Here"], ["C:/Nothing/Here/readme.txt"]);
  assert.throws(() => resolveGameRoot(fsh, "C:/Nothing/Here", "C:/Nothing"), (e) => {
    assert.match(e.message, /No Unity game found/);
    assert.match(e.message, /C:\/Nothing\/Here/);
    return true;
  });
});
