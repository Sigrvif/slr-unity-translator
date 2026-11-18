"""Font replacement for Unity games.

Two common font storage forms, both handled here by swapping the embedded
source TTF:

  * Legacy `Font` object            -> bytes live in `m_FontData`.
  * TextMeshPro `TMP_FontAsset`     -> bytes live in `m_SourceFontFile`.

Replacing the embedded TTF with a narrow font makes the
game render Latin text in that font. Unity computes glyph metrics dynamically
from the TTF at runtime, so this sidesteps atlas generation entirely and is
what solves the "English is too wide" problem without per-component resizing.

Notes / caveats:
  * Keep the font object's m_Name unchanged so existing references resolve.
  * Prefer a universal-glyph TTF when replacing a Japanese
    font, so any text you did not translate still has glyphs to render
    (the JS addon passes SLR Translator's own SLRCondensedCJK-Regular.ttf).
  * Some legacy fonts also ship a small baked glyph atlas (m_CharacterRects);
    dynamic rendering ignores it for characters outside the baked set. If a
    few characters still show old glyphs, use --clear-atlas.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass

import UnityPy

from .files import assert_inside, assert_not_nested, find_asset_files, find_data_dir

# field name that holds the embedded TTF, per object type.
DATA_FIELDS = {
    "Font": "m_FontData",
    # TMP fonts are MonoBehaviours; the field is detected dynamically below.
}
TMP_SOURCE_FIELDS = ("m_SourceFontFile", "m_SourceFontFileData")


@dataclass
class FontEntry:
    file: str
    path_id: int
    obj_type: str
    name: str
    field: str        # which field holds the TTF
    data_size: int


def _detect_font_kind(obj, tree: dict) -> str | None:
    """Return 'Font' or 'TMP' for a font-bearing object, else None."""
    t = obj.type.name
    if t == "Font":
        return "Font"
    if t == "MonoBehaviour" and isinstance(tree, dict):
        for f in TMP_SOURCE_FIELDS:
            if f in tree:
                return "TMP"
        if "m_FaceInfo" in tree or "m_GlyphTable" in tree:
            return "TMP"
    return None


def _source_field(tree: dict, kind: str) -> str | None:
    if kind == "Font":
        return "m_FontData"
    for f in TMP_SOURCE_FIELDS:
        if f in tree:
            return f
    return None


def _byte_len(v) -> int:
    if isinstance(v, (bytes, bytearray)):
        return len(v)
    if isinstance(v, list):
        return len(v)
    return 0


def list_fonts(game_dir: str) -> list[FontEntry]:
    """Enumerate every font object (legacy Font + TMP) in a game."""
    data_dir = find_data_dir(game_dir) or game_dir
    files = find_asset_files(data_dir)
    out: list[FontEntry] = []
    for af in files:
        try:
            env = UnityPy.load(af.path)
        except Exception:  # noqa: BLE001
            continue
        for obj in env.objects:
            if obj.type.name not in ("Font", "MonoBehaviour"):
                continue
            tree = None
            try:
                tree = obj.read_typetree()
            except Exception:  # noqa: BLE001
                tree = None
            kind = _detect_font_kind(obj, tree if isinstance(tree, dict) else {})
            if kind is None:
                continue
            field = _source_field(tree if isinstance(tree, dict) else {}, kind)
            if field is None:
                continue
            data_size = _byte_len(tree.get(field)) if isinstance(tree, dict) and field else 0
            name = (tree.get("m_Name") if isinstance(tree, dict) else "") or ""
            out.append(FontEntry(af.path, obj.path_id, kind, name, field, data_size))
    return out


def _matches(name: str, targets: list[str]) -> bool:
    if not targets:
        return True  # empty target list == all fonts
    name_l = (name or "").lower()
    for t in targets:
        tl = t.lower().strip()
        if not tl:
            continue
        if tl == name_l or tl in name_l or name_l in tl:
            return True
    return False


def scale_latin_advances(ttf_bytes: bytes, scale: float) -> tuple[bytes, int]:
    """Scale the ADVANCE of every ASCII glyph in a TTF (shapes untouched).

    Auto-sized UI (ContentSizeFitter-style bubbles) derives its box width
    from the font's per-glyph advances. Eroge pixel fonts (azukiB and kin)
    have very tight Latin; the CJK face we swap in prices Latin much wider,
    so such boxes inflate ~2-3x for English text. Scaling the advance table
    shrinks those boxes without touching glyph shapes. Unity does not verify
    TTF checksums, and the edit is metrics-only — worst case is cramped
    spacing, never a broken font.

    Returns (new_bytes, scaled_glyph_count).
    """
    import io

    from fontTools.ttLib import TTFont

    f = TTFont(io.BytesIO(ttf_bytes))
    cmap = f.getBestCmap()
    if cmap is None or "hmtx" not in f:
        return ttf_bytes, 0
    hmtx = f["hmtx"]
    scaled = 0
    for cp in range(0x20, 0x7F):
        g = cmap.get(cp)
        if not g or g not in hmtx.metrics:
            continue
        adv, lsb = hmtx.metrics[g]
        new_adv = max(1, round(adv * scale))
        if new_adv != adv:
            hmtx.metrics[g] = (new_adv, lsb)
            scaled += 1
    out = io.BytesIO()
    f.save(out)
    return out.getvalue(), scaled


def replace_source_font(
    game_dir: str,
    out_dir: str,
    new_ttf_path: str,
    *,
    target_names: list[str] | None = None,
    clear_atlas: bool = False,
    overwrite: bool = False,
    in_place: bool = False,
    patch_mode: bool = False,
    latin_scale: float = 1.0,
    progress=None,
) -> dict:
    """Copy the game and overwrite embedded TTF data of matching fonts.

    With ``in_place=True`` the directory is edited directly: ``game_dir`` must
    equal ``out_dir`` (else ValueError) and NOTHING is removed or copied —
    this is the stage mode the CLI uses on an already-patched output copy.

    With ``patch_mode=True`` (contract v2) nothing is copied either: fonts
    are listed and loaded from ``game_dir`` (the SOURCE — pass the original
    game via the job's ``font_source_dir`` to cover font files the strings
    project never staged) and only the font-bearing files that changed are
    written to ``out_dir/<rel>`` as fresh whole-file replacements.
    ``game_dir`` is never touched. Mutually exclusive with ``in_place``.

    Stage-order rule (e2e-fix13): in patch mode, when ``out_dir/<rel>`` is
    already present (an earlier stage of the same patch run exported that
    file — a file may bear BOTH strings and fonts), the swap builds on THAT
    file instead of re-reading the pristine source, so the font swap never
    reverts earlier stages' edits.
    """
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if in_place:
        if game_dir != out_dir:
            raise ValueError(
                f"in_place=True requires game_dir == out_dir "
                f"(got {game_dir!r} vs {out_dir!r})"
            )
    elif patch_mode:
        # I1 guard still applies: writing patched files into the original
        # tree (out_dir == game_dir / nested) must fail before any write.
        assert_not_nested(game_dir, out_dir, "out_dir")
    else:
        # I1 guard: rmtree below must never be able to delete the source.
        assert_not_nested(game_dir, out_dir, "out_dir")
        if os.path.exists(out_dir):
            if not overwrite:
                raise FileExistsError(f"Output exists: {out_dir}")
            shutil.rmtree(out_dir)
        shutil.copytree(game_dir, out_dir)

    with open(new_ttf_path, "rb") as f:
        new_bytes = f.read()
    latin_scaled_glyphs = 0
    if latin_scale != 1.0:
        new_bytes, latin_scaled_glyphs = scale_latin_advances(new_bytes, latin_scale)

    targets = target_names or []
    entries = list_fonts(game_dir)
    to_edit = [e for e in entries if _matches(e.name, targets)]

    # group by file
    by_file: dict[str, list[FontEntry]] = defaultdict(list)
    for e in to_edit:
        by_file[e.file].append(e)

    changed_files = 0
    replaced = 0
    exported: list[str] = []  # rel paths written to out_dir this run
    for i, (abs_file, fentries) in enumerate(by_file.items()):
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        assert_inside(out_dir, out_file, f"output for {rel!r}")  # C1 guard
        # e2e-fix13 stage-order rule (patch_mode only): build on this run's
        # earlier output when present, never revert it with the pristine
        # source (see the docstring). Other modes already read exactly the
        # file they are about to overwrite (in_place / full-copy: same dir;
        # full-game copy: a fresh copytree of game_dir).
        src_file = abs_file
        if patch_mode and os.path.isfile(out_file):
            src_file = out_file
        env = UnityPy.load(src_file)
        target_ids = {e.path_id for e in fentries}
        changed = False
        for obj in env.objects:
            if obj.path_id not in target_ids:
                continue
            try:
                tree = obj.read_typetree()
            except Exception:  # noqa: BLE001
                continue
            kind = _detect_font_kind(obj, tree)
            if kind is None:
                continue
            field = _source_field(tree, kind)
            if not field:
                continue
            tree[field] = new_bytes
            if clear_atlas and kind == "Font":
                for k in ("m_CharacterRects", "m_CharacterSpacing", "m_LineSpacing"):
                    if k in tree:
                        tree[k] = [] if "Rects" in k else 0
            obj.save_typetree(tree)
            replaced += 1
            changed = True
        if changed:
            # patch_mode: no copied tree exists — create the parent dirs and
            # write the file fresh (in_place / full-copy modes: dirs exist).
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                env.save(pack="lz4", out_path=td)
                for name in os.listdir(td):
                    shutil.copy2(os.path.join(td, name), os.path.join(os.path.dirname(out_file), name))
            changed_files += 1
            exported.append(rel.replace("\\", "/"))
        if progress:
            progress(i + 1, len(by_file), rel)

    return {
        "fonts_replaced": replaced,
        "files_modified": changed_files,
        "latin_scaled_glyphs": latin_scaled_glyphs,
        "out_dir": out_dir,
        "considered": len(entries),
        "exported_files": sorted(exported),
    }
