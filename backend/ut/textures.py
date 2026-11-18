"""Texture extraction & re-import for image-based (baked) text.

Many games (especially stylised/3D ones) draw their Japanese text into
Texture2D assets instead of using live strings. Translating those means
exporting the images, editing them in any image editor, and writing them back.

Workflow (spec §5.1):
  extract_textures(game_dir, out_dir)
      -> writes one PNG per Texture2D named
         <assetfile>__<texture name>__<path_id>.png
         (the asset-file component keeps path_id collisions in different
         files from overwriting each other), plus manifest.json mapping
         each entry to {file, rel, path_id, name, w, h, format, png}. A
         'candidates' flag highlights textures that are likely to hold UI
         text (small, non-square, has alpha) so translators don't drown in
         tilesets. Sprites are SKIPPED: a Sprite renders its source
         Texture2D, so translating the texture covers it — exporting both
         would hand the translator the same pixels twice (and re-importing
         a Sprite used to write Texture2D serialization onto the Sprite
         slot, corrupting it).

  import_textures(game_dir, out_dir_game, manifest_dir)
      -> for every PNG that changed (or all, with overwrite), repack the
         Texture2D and write the game copy. Manifest `file` paths are
         extract-time ABSOLUTE paths; they are re-resolved against the
         game_dir being patched via the manifest's relative `rel` entry,
         so a manifest from the original game patches a copied game
         without ever writing back into the original (fix C1).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass

import UnityPy

from .files import assert_inside, assert_not_nested, find_asset_files, find_data_dir


# Spec §5.1: Sprites are skipped on purpose — they duplicate their Texture2D.
TEXTURE_TYPES = {"Texture2D"}


@dataclass
class TexEntry:
    file: str        # absolute path in the game the manifest was extracted from
    rel: str         # path relative to that game's data dir, '/'-separated
    path_id: int
    name: str
    tex_type: str
    width: int
    height: int
    format: int
    png: str
    candidate: bool = False


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _likely_text_texture(w: int, h: int, fmt: int) -> bool:
    """Heuristic: small-ish, non-power-of-two dimensions, with alpha channel
    tend to be UI/text overlays rather than tilesets or photos."""
    if w <= 0 or h <= 0:
        return False
    area = w * h
    if area > 1024 * 1024:  # large textures are usually backgrounds/tilesets
        return False
    if w == h and _is_pow2(w):  # square power-of-two -> often sprite/tileset
        return False
    # Alpha formats: RGBA32(3), ARGB32(5), RGBA4444(15), RGBA5551(16),
    # BC7(24, has alpha), ASTC_RGBA_*, DXT5(10), ETC2_RGBA(45..). These are
    # the formats UI overlays typically use.
    alpha_formats = {3, 5, 12, 13, 15, 16, 24, 25, 28, 29, 10, 45, 46, 47, 48}
    return fmt in alpha_formats


def _sanitize(s: str, limit: int = 96) -> str:
    """Filesystem-safe, separator-free component for PNG names."""
    base = "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or ""))
    return (base[:limit] or "x")


def _png_name(asset_rel: str, name: str, path_id: int) -> str:
    """<assetfile>__<sanitized name>__<path_id>.png (spec §5.1; unique per
    (file, path_id) so same-path_id objects in different files can't
    overwrite each other's PNGs)."""
    return f"{_sanitize(asset_rel)}__{_sanitize(name) or 'tex'}__{path_id}.png"


def extract_textures(
    game_dir: str,
    out_dir: str,
    *,
    candidates_only: bool = False,
    max_area: int | None = None,
    progress=None,
) -> dict:
    """Export every Texture2D to PNG + a manifest.json (Sprites skipped)."""
    game_root = os.path.abspath(game_dir)
    data_dir = find_data_dir(game_root) or game_root
    files = find_asset_files(data_dir)
    entries: list[TexEntry] = []
    errors: list[str] = []
    os.makedirs(out_dir, exist_ok=True)

    for idx, af in enumerate(files):
        try:
            env = UnityPy.load(af.path)
        except Exception as e:  # noqa: BLE001
            errors.append(f"load {af.rel}: {e!r}")
            continue
        for obj in env.objects:
            if obj.type.name not in TEXTURE_TYPES:
                continue  # Sprite included: skipped by design (see module doc)
            try:
                d = obj.read()
            except Exception as e:  # noqa: BLE001
                errors.append(f"read {af.rel}#{obj.path_id}: {e!r}")
                continue
            w = getattr(d, "m_Width", 0)
            h = getattr(d, "m_Height", 0)
            fmt = getattr(d, "m_TextureFormat", -1)
            area = (w or 0) * (h or 0)
            if max_area and area > max_area:
                continue
            cand = _likely_text_texture(w, h, fmt)
            if candidates_only and not cand:
                continue
            try:
                img = d.image
            except Exception as e:  # noqa: BLE001
                errors.append(f"image {af.rel}#{obj.path_id}: {e!r}")
                continue
            if img is None:
                continue
            rel_fslash = os.path.relpath(af.path, game_root).replace("\\", "/")
            png_name = _png_name(rel_fslash, getattr(d, "m_Name", ""), obj.path_id)
            png_path = os.path.join(out_dir, png_name)
            # C1 guard: destinations are built from sanitized names, but the
            # invariant is checked structurally anyway — an escape is a bug.
            assert_inside(out_dir, png_path, "PNG output")
            img.save(png_path)
            entries.append(
                TexEntry(af.path, rel_fslash, obj.path_id, getattr(d, "m_Name", ""),
                         obj.type.name, w, h, fmt, png_name, cand)
            )
        if progress:
            progress(idx + 1, len(files), af.rel)

    manifest = [
        {
            "file": e.file,
            "rel": e.rel,
            "path_id": e.path_id,
            "name": e.name,
            "type": e.tex_type,
            "width": e.width,
            "height": e.height,
            "format": e.format,
            "png": e.png,
            "candidate": e.candidate,
        }
        for e in entries
    ]
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return {"count": len(entries), "errors": errors, "out": out_dir}


def _resolve_entry_file(entry: dict, game_dir: str) -> tuple[str | None, str | None]:
    """Resolve a manifest entry against the game being patched (fix C1).

    Manifest `file` paths are extract-time absolute paths; when the game has
    moved / been copied they point outside game_dir and relpath() against
    game_dir would escape. Prefer an existing `file` that lies inside
    game_dir; otherwise rebuild from the manifest's relative `rel` (tried
    against the game root AND its *_Data dir — extract stores root-relative,
    hand-made manifests often use data-relative paths).
    Returns (abs_path, rel_with_slashes) or (None, None) when unresolvable.
    """
    bases = [os.path.abspath(game_dir)]
    data_dir = find_data_dir(bases[0])
    if data_dir and os.path.abspath(data_dir) != bases[0]:
        bases.append(os.path.abspath(data_dir))
    f = entry.get("file") or ""
    if f and os.path.isfile(f):
        try:
            rel = os.path.relpath(f, game_dir)
        except ValueError:  # cross-drive: cannot be inside game_dir
            rel = None
        if rel is not None and not rel.startswith(".."):
            return f, rel.replace("\\", "/")
    rel = (entry.get("rel") or "").replace("\\", "/")
    if rel and not rel.startswith("/") and ":" not in rel:
        for base in bases:
            cand = os.path.normpath(os.path.join(base, *rel.split("/")))
            if os.path.isfile(cand):
                try:
                    back = os.path.relpath(cand, game_dir)
                except ValueError:
                    continue
                if not back.startswith(".."):
                    return cand, back.replace("\\", "/")
    return None, None


def import_textures(
    game_dir: str,
    out_dir: str,
    manifest_dir: str,
    *,
    overwrite: bool = False,
    import_all: bool = False,
    in_place: bool = False,
    patch_mode: bool = False,
    png_dir: str | None = None,
    progress=None,
) -> dict:
    """Copy the game to `out_dir` and overwrite edited textures from PNGs.

    `import_all` -> import every PNG; otherwise only PNGs newer than the
    manifest (i.e. those the user edited).

    `png_dir` -> look PNGs up in this directory instead of `manifest_dir`
    (the SEP-render folder holds rendered PNGs under the same filenames; the
    manifest itself still comes from `manifest_dir`). In this mode every
    entry whose PNG exists is force-imported regardless of mtime — SEP's
    rendered files may predate our manifest.

    `in_place=True` -> edit `out_dir` directly (must equal `game_dir`); the
    directory is never removed or copied. Stage mode for the CLI, which runs
    this after patch_game produced the output copy.

    `patch_mode=True` (contract v2) -> no copy: source files are read from
    their original locations (manifest abs, re-resolved against `game_dir`)
    and only the files whose textures were re-imported are written to
    `out_dir/<rel>` as fresh whole-file replacements. Mutually exclusive
    with `in_place`.
    """
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if in_place and game_dir != out_dir:
        # Fail fast, before any file is touched.
        raise ValueError(
            f"in_place=True requires game_dir == out_dir "
            f"(got {game_dir!r} vs {out_dir!r})"
        )
    if not in_place:
        # I1 guard: the rmtree below must never be able to delete the source;
        # in patch mode nothing is removed, but writing into the original
        # tree must still fail before any write.
        assert_not_nested(game_dir, out_dir, "out_dir")
    manifest_path = os.path.join(manifest_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # PNGs come from png_dir when given (SEP-render folder), else manifest_dir.
    png_root = png_dir if png_dir is not None else manifest_dir

    # Decide which entries to import. png_dir mode forces every entry whose
    # PNG exists there (import_all semantics): rendered files may be older
    # than the manifest, so mtime says nothing about their freshness.
    to_import = []
    unresolved: list[str] = []
    for e in manifest:
        png = os.path.join(png_root, e["png"])
        if not os.path.exists(png):
            continue
        src, rel = _resolve_entry_file(e, game_dir)
        if src is None:
            # Not part of THIS game copy (stage mode patches a subset).
            unresolved.append(e.get("rel") or e.get("file") or "?")
            continue
        e = dict(e, _src=src, _rel=rel)
        to_import.append(e)
    if not import_all and png_dir is None:
        mt = os.path.getmtime(manifest_path)
        to_import = [
            e for e in to_import
            if os.path.getmtime(os.path.join(png_root, e["png"])) > mt
        ]

    if not in_place and not patch_mode:
        if os.path.exists(out_dir):
            if not overwrite:
                raise FileExistsError(f"Output exists: {out_dir}")
            shutil.rmtree(out_dir)
        shutil.copytree(game_dir, out_dir)

    # group by source file
    by_file: dict[str, list[dict]] = {}
    for e in to_import:
        by_file.setdefault(e["_src"], []).append(e)

    from PIL import Image

    written = 0
    exported: list[str] = []  # rel paths written to out_dir this run
    errors: list[str] = [f"unresolved entry: {u}" for u in unresolved]
    for i, (src_file, entries) in enumerate(by_file.items()):
        rel = entries[0]["_rel"]
        out_file = os.path.join(out_dir, *rel.split("/"))
        # C1 guard: whatever the manifest said, the write must land inside
        # out_dir (raises on any .. escape instead of copying elsewhere).
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        env = UnityPy.load(src_file)
        path_ids = {e["path_id"] for e in entries}
        png_by_id = {e["path_id"]: os.path.join(png_root, e["png"]) for e in entries}
        changed = False
        for obj in env.objects:
            # Sprites never match TEXTURE_TYPES: their Texture2D is edited
            # directly under its own entry (spec §5.1).
            if obj.type.name not in TEXTURE_TYPES or obj.path_id not in path_ids:
                continue
            png = png_by_id.get(obj.path_id)
            if not png:
                continue
            try:
                d = obj.read()
                img = Image.open(png)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                d.set_image(img)  # encode into the texture's original format
                obj.save_typetree(d)  # persist the object's typetree + image data
                changed = True
                written += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"import {rel}#{obj.path_id}: {e!r}")
        if changed:
            # patch_mode: no copied tree exists — create the parent dirs and
            # write the file fresh (in_place / full-copy modes: dirs exist).
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with __import__("tempfile").TemporaryDirectory(ignore_cleanup_errors=True) as td:
                env.save(pack="lz4", out_path=td)
                for name in os.listdir(td):
                    dest = os.path.join(os.path.dirname(out_file), name)
                    assert_inside(out_dir, dest, f"resave of {rel!r}")  # C1 guard
                    shutil.copy2(os.path.join(td, name), dest)
            exported.append(rel)
        if progress:
            progress(i + 1, len(by_file), rel)

    return {"imported": written, "out_dir": out_dir, "errors": errors,
            "unresolved": len(unresolved),
            "exported_files": sorted(set(exported))}
