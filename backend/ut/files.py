"""Enumerate the real Unity asset files of a game directory.

A game folder contains a lot of binary noise (.resource/.resS streams, native
DLLs, the engine itself). Only a subset of files hold *serialized* text that we
can read and rewrite. This module finds exactly those.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


# Extensions / name patterns that contain serialized Unity objects.
SERIALIZED_EXT = {
    ".assets",
    ".bundle",
    ".content",  # Addressables content files
    ".resource",  # NOTE: excluded below; these are raw streaming data, not serialized
}

# Serialized standalone file basename patterns (no extension) inside _Data.
STANDALONE_PATTERNS = ("level", "sharedassets", "resources.assets", "globalgamemanagers")

# Extensions to never scan as serialized assets.
SKIP_EXT = {
    ".resource", ".ress",  # streaming data blobs
    ".dll", ".so", ".dylib", ".exe",  # code
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".psd", ".tif", ".tiff",  # images
    ".wav", ".mp3", ".ogg", ".bank", ".fsb", ".bnk",  # audio
    ".txt", ".json", ".xml", ".csv", ".bytes", ".cginc", ".shader", ".compute",  # text/code
    ".zip", ".7z", ".rar",
    ".dat", ".cache", ".bin",  # opaque binaries (catalog.bin handled separately if needed)
    ".ttf", ".otf", ".fnt",
    ".meta", ".manifest",
    ".lnk",
}


@dataclass
class AssetFile:
    path: str       # absolute path
    rel: str        # path relative to the game dir
    kind: str       # 'assets' | 'bundle' | 'standalone' | 'datafile'


def _looks_serialized(path: str, data_dir: str) -> str | None:
    base = os.path.basename(path).lower()
    ext = os.path.splitext(base)[1]

    if ext in (".bundle", ".content"):
        return "bundle"
    # Addressables sometimes have no extension under StandaloneWindows64; detect by folder.
    if "standalonewindows" in data_dir.lower() and ext == "":
        return "bundle"

    if base.startswith("level") and (ext == "" or ext == ".assets"):
        return "standalone"
    if base.startswith("sharedassets") and (ext == "" or ext == ".assets"):
        return "standalone"
    if base in ("resources.assets", "globalgamemanagers", "globalgamemanagers.assets"):
        return "standalone"
    if ext == ".assets":
        return "assets"

    return None


def find_asset_files(game_dir: str) -> list[AssetFile]:
    """Walk a game directory and return every serialised asset file.

    `game_dir` may be the game root (containing the exe and *_Data) or the
    *_Data folder directly.
    """
    game_dir = os.path.abspath(game_dir)
    out: list[AssetFile] = []
    for root, _dirs, files in os.walk(game_dir):
        for name in files:
            full = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXT and ext:
                # .assets handled below regardless; otherwise skip noise.
                if ext != ".assets":
                    continue
            kind = _looks_serialized(full, root)
            if kind is None:
                continue
            out.append(
                AssetFile(
                    path=full,
                    rel=os.path.relpath(full, game_dir),
                    kind=kind,
                )
            )
    out.sort(key=lambda a: a.rel)
    return out


# Plain-text extensions worth scanning under StreamingAssets (source 5).
TEXT_EXTS = {".json", ".jsonc", ".txt", ".csv", ".bytes"}
MAX_TEXT_FILE = 4 * 1024 * 1024


def find_text_files(data_dir: str) -> list[str]:
    """Candidate plain-text files under StreamingAssets."""
    sa = os.path.join(data_dir, "StreamingAssets")
    out: list[str] = []
    if not os.path.isdir(sa):
        return out
    for root, _dirs, names in os.walk(sa):
        for n in names:
            p = os.path.join(root, n)
            if os.path.splitext(n)[1].lower() in TEXT_EXTS and os.path.getsize(p) <= MAX_TEXT_FILE:
                out.append(p)
    return out


def assert_inside(base: str, dest: str, what: str = "destination") -> None:
    """Structural guard: `dest` must name a path inside `base`'s tree.

    Compares realpaths so ``..`` segments and symlinked components cannot
    smuggle a write outside `base` (fix C1: a destination computed from a
    stale absolute path used to escape the output dir and overwrite the
    ORIGINAL game). Raises ValueError when dest is not inside base.
    """
    base_r = os.path.realpath(base)
    dest_r = os.path.realpath(dest)
    prefix = base_r if base_r.endswith(os.sep) else base_r + os.sep
    if dest_r != base_r and not dest_r.startswith(prefix):
        raise ValueError(
            f"{what} path escapes its base directory: {dest!r} (base {base!r})"
        )


def assert_not_nested(a: str, b: str, what: str = "output directory") -> None:
    """Guard: neither directory may be equal to or inside the other.

    Fix I1: patch_game(out_dir == game_dir) used to rmtree the ORIGINAL game
    before copytree could fail. Cross-drive pairs cannot nest (relpath raises
    ValueError) and are allowed through.
    """
    try:
        ra = os.path.relpath(a, b)
        rb = os.path.relpath(b, a)
    except ValueError:  # different drives: nesting is impossible
        return
    # a inside b (or equal) -> ra has no leading ".."; same test for rb.
    if not ra.startswith("..") or not rb.startswith(".."):
        raise ValueError(
            f"{what} must not equal or sit inside the game directory: {a!r} vs {b!r}"
        )


def find_data_dir(game_dir: str) -> str | None:
    """Locate the *_Data folder for a game root, or accept a Data dir directly."""
    game_dir = os.path.abspath(game_dir)
    if os.path.basename(game_dir).endswith("_Data") and os.path.isdir(game_dir):
        return game_dir
    for name in os.listdir(game_dir):
        if name.endswith("_Data") and os.path.isdir(os.path.join(game_dir, name)):
            return os.path.join(game_dir, name)
    return None
