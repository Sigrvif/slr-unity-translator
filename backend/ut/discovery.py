"""Discovery: find every translatable string, texture and font in a game.

Strategy per object:
  * MonoBehaviour / TextAsset WITH a usable type tree  -> recurse the tree,
    collect every string leaf that contains Japanese, plus non-Japanese
    leaves from display-text fields (m_Text / m_Localized / titles /
    descriptions). Precise & writable.
  * MonoBehaviour WITHOUT a type tree (standalone Mono files) -> fall back to
    length-prefix-validated raw scanning of the object payload. Japanese
    records come from anywhere; non-Japanese records only from text
    components (a GameObject whose leaf name is a Text/TMP component), so
    TMP button labels (e.g. a main-menu "Achievement") are collected while
    the internal-name noise stays out. Universal.

Other object types are catalogued (textures, fonts, text assets) for the
image/font stages.
"""
from __future__ import annotations

import csv
import json
import os
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any

import UnityPy

from . import jp as _jp
from . import rawstrings
from .files import find_asset_files, find_data_dir


# Object types whose string fields are *displayed* game text worth translating.
TEXT_TYPES = {"MonoBehaviour", "TextAsset"}
# Font types to catalogue (handled by the font stage, not string-scan).
FONT_TYPES = {"Font"}
# TMP_FontAsset / TMP_SpriteAsset are MonoBehaviours; identify by script later.
TEXTURE_TYPES = {"Texture2D", "Sprite"}

# Typetree leaves a NON-Japanese string may come from (lowercased). A
# Japanese string is user-visible by construction and keeps the old
# any-field behavior; without that signal only explicit display fields are
# trusted — everything else (m_Name, keys, GUIDs, labels) is internal naming.
DISPLAY_TT_FIELDS = {
    "m_text", "text", "m_localized", "localized",
    "m_title", "title", "m_description", "description",
    "m_message", "message", "m_caption", "caption",
}

# GameObject leaf names that mark the component as a text component, so a RAW
# (typetree-less) payload's non-Japanese record is its displayed m_Text.
# Mirrors the addon's GENERIC_LEAF_SEGMENTS (the component-type names).
TEXT_COMPONENT_LEAVES = {"text", "text (tmp)", "textmeshpro", "textmeshprougui", "tmp"}

# A TextAsset whose content carries these markers is a license/package
# notice; its non-Japanese lines are boilerplate, never game text.
_LICENSE_MARK = re.compile(
    r"licen[cs]e|copyright|creative commons|apache|permission is hereby|"
    r"is licensed|warrant|merchantability|https?://",
    re.I,
)


def _raw_ctx_is_text_component(ctx: str) -> bool:
    """True when the GameObject path's leaf names a text component
    ('Canvas/Options/Back/Text (TMP)' -> 'text (tmp)')."""
    if not ctx:
        return False
    leaf = ctx.rstrip("/").rsplit("/", 1)[-1].strip().lower()
    return leaf in TEXT_COMPONENT_LEAVES


# --------------------------------------------------------------------------- #
# v2.8 risk tagging (SLR Translator's tag scheme). Every hit leaves the
# collector with a risk tag; the JS project builder turns it into SLR's
# per-row tags so EVERYTHING is collected while only the safe tiers are
# translated by default (SLR's batch blacklist defaults skip yellow, red,
# gray, gold and darkred rows; untagged and green rows translate):
#   ""        display text — always safe (m_Text of a UI component, plain
#             dialogue scripts), translated by default
#   "green"   script text — confirmed safe (IL2CPP metadata / Mono DLL code
#             literals; ut.monopatch/ut.metadata are purpose-built writers
#             for them), translated by default
#   "yellow"  likely safe, confirm first (NOT translated by default): JP text
#             from non-display typetree fields, JSON/CSV values of TextAssets
#             and StreamingAssets files — in many games those are game DATA
#             (name tables, lookup values), not display text
#   "red"     likely structural (NOT translated by default): object/asset
#             names and other identifier leaves, data-table rows, asset
#             filenames — rewriting them breaks lookups and references
# The JS layer ranks per unique text (a text patched everywhere must wear its
# WORST tag): red > yellow > "" > green.
_IDENTIFIER_LEAVES = {
    "m_name", "name", "key", "m_key", "id", "m_id", "guid", "m_guid",
    "event", "event_name", "m_eventname", "cue", "cue_name", "cueid",
    "path", "m_path", "tag", "m_tag", "type", "m_type",
    "category", "m_category", "label_id", "message_id", "messageid",
}

# Text shaped like an asset filename ('girl.psd（対応無し）') — a resource
# reference, not prose.
_ASSET_EXT_RE = re.compile(
    r"\.(png|jpe?g|tga|psd|tif|bmp|gif|webp|prefab|fbx|obj|unity|asset|anim|"
    r"controller|mat|ttf|otf|ttc|fon|csv|tsv|json|xml|html|bytes|bin|dat|"
    r"mp3|ogg|wav|mp4|mov|bnk|wem|acb|awb|usm)\b", re.I)


def _shape_risk(text: str) -> str:
    """Content-shape upgrades that apply on top of the per-source rules: a
    multi-cell data-table row ('"楓","陽葵","葵",…' — a name list, real case)
    or an asset filename is structural no matter which field it sits in."""
    if text.count('","') >= 2 or text.count("\t") >= 2:
        return "red"
    if _ASSET_EXT_RE.search(text):
        return "red"
    return ""


def string_risk(source: str, leaf: str, key: str, text: str) -> str:
    """The risk tag for one hit. `leaf` is the typetree leaf name (lowercased
    by the caller; "" for raw/code hits), `key` the textasset/textfile item
    key ('L42' line, 'rNcM' CSV cell, '$.a.b' JSON path)."""
    if source in ("mono", "metadata"):
        return "green"
    shape = _shape_risk(text)
    if source == "typetree":
        leaf = (leaf or "").lower()
        if leaf in DISPLAY_TT_FIELDS:
            return shape              # display field; shapes can still flag junk
        if leaf in _IDENTIFIER_LEAVES:
            return "red"              # object identity — never by default
        return shape if shape == "red" else "yellow"
    if source == "raw":
        # raw JP records ARE the displayed text of typetree-less components
        # (the main dialogue of many games) — safe by default, red shapes only.
        return shape
    if source in ("textasset", "textfile"):
        if key[:1] == "L" and key[1:].isdigit():
            return shape              # line-based script: dialogue by default
        # JSON/CSV values are game data in some titles (name/stat tables) —
        # confirm first unless a red shape already flagged them.
        return shape if shape == "red" else "yellow"
    return ""


# Per-text worst-tag ranking for summary counts (the JS layer mirrors this:
# red 3 > yellow 2 > "" 1 > green 0).
RISK_RANK = {"red": 3, "yellow": 2, "": 1, "green": 0}


def worst_risk(risks) -> str:
    return max(risks, key=lambda r: RISK_RANK.get(r, 1)) if risks else ""


@dataclass
class StringHit:
    file: str          # absolute asset file path
    path_id: int
    obj_type: str
    field: str         # human-readable field path
    tokens: list       # machine field path (for write-back)
    source: str        # 'typetree' | 'raw' | 'metadata' | 'textasset' | 'textfile'
    offset: int        # raw offset within object payload (0 for typetree)
    length: int        # raw: UTF-8 byte count of the stored payload (0 typetree)
    text: str
    context: str = ""  # owning GameObject hierarchy path, e.g. Canvas/Dialog/Text (TMP)
    key: str = ""      # textasset/textfile: TextItem key; unused elsewhere
    # v2.8 risk tag (SLR's tag scheme, see string_risk below): "" | "green" |
    # "yellow" | "red". Travels to the JS layer in BOTH payload spots (strings
    # rows via asdict, index occurrences via cmd_scan) so the project builder
    # can write SLR's per-row tags.
    risk: str = ""


# --------------------------------------------------------------------------- #
# GameObject hierarchy context (works without type trees: engine types are
# always readable, even in stripped IL2CPP builds)
# --------------------------------------------------------------------------- #
class ContextResolver:
    """Resolves the scene-hierarchy path of the GameObject a component
    belongs to, e.g. `Canvas/NewGamePlusCheck/Button_Start/Text (TMP)`.

    GameObject names turn an opaque list of strings into something a
    translator can navigate; memoized per file to keep rescans cheap.
    One resolver per serialized file — path_ids are only unique within a
    file, and a bundle can contain many of them.
    """

    MAX_DEPTH = 32

    def __init__(self, container: Any):
        objs = getattr(container, "objects", [])
        if isinstance(objs, dict):
            objs = objs.values()
        self._obj_by_pid = {o.path_id: o for o in objs}
        self._go_path: dict[int, str] = {}

    def _read(self, path_id: int):
        obj = self._obj_by_pid.get(path_id)
        if obj is None:
            return None
        try:
            return obj.read()
        except Exception:  # noqa: BLE001
            return None

    def go_path(self, go_pid: int, depth: int = 0) -> str:
        if go_pid in self._go_path:
            return self._go_path[go_pid]
        if depth > self.MAX_DEPTH:  # cycle guard
            return ""
        self._go_path[go_pid] = ""  # pre-fill so cycles terminate
        d = self._read(go_pid)
        if d is None:
            return ""
        name = getattr(d, "m_Name", "") or ""
        parent = ""
        for entry in getattr(d, "m_Component", None) or []:
            pptr = getattr(entry, "component", entry)
            comp = self._read(getattr(pptr, "m_PathID", 0))
            if comp is None or type(comp).__name__ not in ("Transform", "RectTransform"):
                continue
            father = getattr(comp, "m_Father", None)
            if father is not None and getattr(father, "m_FileID", 1) == 0 and father.m_PathID:
                ft = self._read(father.m_PathID)
                if ft is not None:
                    fgo = getattr(ft, "m_GameObject", None)
                    if fgo is not None and getattr(fgo, "m_FileID", 1) == 0 and fgo.m_PathID:
                        parent = self.go_path(fgo.m_PathID, depth + 1)
            break
        path = f"{parent}/{name}" if parent else name
        self._go_path[go_pid] = path
        return path

    def from_pptr(self, file_id: Any, path_id: Any) -> str:
        """Context for a m_GameObject PPtr (same-file references only)."""
        try:
            if int(file_id) == 0 and int(path_id):
                return self.go_path(int(path_id))
        except (TypeError, ValueError):
            pass
        return ""

    @staticmethod
    def gameobject_pptr_of_payload(payload: bytes) -> tuple[int, int] | None:
        """m_GameObject PPtr is the first field of a serialized MonoBehaviour:
        int32 m_FileID + int64 m_PathID."""
        if len(payload) < 12:
            return None
        return struct.unpack_from("<iq", payload, 0)


def _natural_key(s: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s)]


@dataclass
class TextureInfo:
    file: str
    path_id: int
    name: str
    tex_type: str
    width: int
    height: int
    format: int


@dataclass
class FontInfo:
    file: str
    path_id: int
    name: str
    font_type: str     # 'Font' | 'TMP_FontAsset' (script name)


@dataclass
class TextAssetInfo:
    file: str
    path_id: int
    name: str
    has_japanese: bool
    size: int


@dataclass
class DiscoveryReport:
    strings: list[StringHit] = field(default_factory=list)
    textures: list[TextureInfo] = field(default_factory=list)
    fonts: list[FontInfo] = field(default_factory=list)
    text_assets: list[TextAssetInfo] = field(default_factory=list)
    files_scanned: int = 0
    data_dir: str = ""
    errors: list[str] = field(default_factory=list)
    suspect: list[str] = field(default_factory=list)
    # Spec §3.3 Mono detection (report-only): [{dll, count, samples}] per
    # Managed DLL carrying Japanese #US-heap literals.
    mono_report: list[dict] = field(default_factory=list)
    # Demosaic (report/decision support): per-file asset catalogs for the
    # mosaic-relevant classes, joined into `demosaic` candidates at the end
    # of discover(). See ut.demosaic — the same detection runs at export.
    demosaic_catalog: dict = field(default_factory=dict)
    demosaic: Any = None


# --------------------------------------------------------------------------- #
# string field walking
# --------------------------------------------------------------------------- #
def _iter_string_leaves(value: Any, tokens: tuple = ()):
    """Yield (tokens, str) for every string leaf in a nested dict/list tree."""
    if isinstance(value, str):
        yield tokens, value
    elif isinstance(value, list):
        # Avoid descending into absurdly long arrays of non-strings.
        for i, v in enumerate(value):
            yield from _iter_string_leaves(v, tokens + (i,))
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_string_leaves(v, tokens + (str(k),))


def _tokens_to_field(tokens) -> str:
    parts = []
    for t in tokens:
        if isinstance(t, int):
            parts.append(f"[{t}]")
        else:
            parts.append(("." if parts else "") + t)
    return "".join(parts) or "<root>"


def _get_by_tokens(tree: dict, tokens) -> Any:
    cur = tree
    for t in tokens:
        cur = cur[t]
    return cur


def _set_by_tokens(tree: dict, tokens, value) -> None:
    cur = tree
    for t in tokens[:-1]:
        cur = cur[t]
    cur[tokens[-1]] = value


# --------------------------------------------------------------------------- #
# per-file scanning
# --------------------------------------------------------------------------- #
def _scan_file(asset_file, report: DiscoveryReport, scan_textures: bool, scan_fonts: bool,
               scan_demosaic: bool = True):
    try:
        env = UnityPy.load(asset_file.path)
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"load {asset_file.rel}: {e!r}")
        return

    # demosaic catalog: GameObjects/Renderers/Materials/Shaders of this file
    # (types disjoint from everything scanned below — never extra loads).
    if scan_demosaic:
        try:
            from . import demosaic as _demosaic
            report.demosaic_catalog[asset_file.path] = _demosaic.catalog_env(
                env, asset_file.path)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"demosaic catalog {asset_file.rel}: {e!r}")

    resolvers: dict[int, ContextResolver] = {}  # per serialized file

    def _context_for(obj, tree: dict | None = None, payload: bytes | None = None) -> str:
        # One resolver per serialized file: path_ids collide across the
        # sub-files of a bundle.
        af = getattr(obj, "assets_file", None)
        key = id(af)
        if key not in resolvers:
            resolvers[key] = ContextResolver(af if af is not None else env)
        resolver = resolvers[key]
        if payload is not None:
            pptr = ContextResolver.gameobject_pptr_of_payload(payload)
            if pptr is None:
                return ""
            return resolver.from_pptr(*pptr)
        if isinstance(tree, dict):
            go = tree.get("m_GameObject")
            if isinstance(go, dict):
                return resolver.from_pptr(go.get("m_FileID"), go.get("m_PathID"))
        return ""

    for obj in env.objects:
        tname = obj.type.name

        # --- textures / sprites ---
        if scan_textures and tname in TEXTURE_TYPES:
            try:
                d = obj.read()
                report.textures.append(
                    TextureInfo(
                        file=asset_file.path,
                        path_id=obj.path_id,
                        name=getattr(d, "m_Name", "") or "",
                        tex_type=tname,
                        width=getattr(d, "m_Width", 0),
                        height=getattr(d, "m_Height", 0),
                        format=getattr(d, "m_TextureFormat", -1),
                    )
                )
            except Exception:  # noqa: BLE001
                pass
            continue

        # --- fonts ---
        if scan_fonts and tname in FONT_TYPES:
            try:
                d = obj.read()
                report.fonts.append(
                    FontInfo(asset_file.path, obj.path_id, getattr(d, "m_Name", "") or "", "Font")
                )
            except Exception:  # noqa: BLE001
                pass
            continue

        if tname not in TEXT_TYPES:
            continue

        # --- try precise type-tree read first ---
        hit_via_tt = False
        try:
            tree = obj.read_typetree()
            if isinstance(tree, dict) and tree:
                # Identify TMP fonts for catalogue even though they're MBs.
                if scan_fonts and tname == "MonoBehaviour":
                    _maybe_catalog_tmp_font(obj, tree, asset_file.path, report)
                ctx = _context_for(obj, tree=tree) if tname == "MonoBehaviour" else ""
                for tokens, s in _iter_string_leaves(tree):
                    # e2e-fix15: a TextAsset's whole m_Script is ALSO captured
                    # by the textasset source below, which breaks the content
                    # into finer per-line/CSV keys. Emitting the full-content
                    # hit here made the patcher rewrite the whole m_Script
                    # FIRST (real credits asset: the blob translation landed,
                    # then every L-key missed — 18 edits_missed), so the
                    # typetree leaf is skipped in its favor.
                    if tname == "TextAsset" and tokens and tokens[0] == "m_Script":
                        continue
                    if not s:
                        continue
                    if _jp.contains_japanese(s):
                        if not _jp.looks_like_text(s):
                            continue
                    else:
                        # v2.2 collect-all-visible: a non-Japanese leaf is
                        # only display text when its FIELD says so; every
                        # other leaf (m_Name of objects/clips, table keys,
                        # locale names) is internal naming.
                        leaf = str(tokens[-1]).lower() if tokens else ""
                        if leaf not in DISPLAY_TT_FIELDS:
                            continue
                        if not _jp.looks_like_visible_text(s):
                            continue
                    report.strings.append(
                        StringHit(
                            file=asset_file.path,
                            path_id=obj.path_id,
                            obj_type=tname,
                            field=_tokens_to_field(tokens),
                            tokens=list(tokens),
                            source="typetree",
                            offset=0,
                            length=0,
                            text=s,
                            context=ctx,
                            risk=string_risk("typetree",
                                             str(tokens[-1]) if tokens else "",
                                             "", s),
                        )
                    )
                hit_via_tt = True
        except Exception:  # noqa: BLE001
            pass

        # --- fallback: raw length-prefix scan of the object payload ---
        if not hit_via_tt and tname == "MonoBehaviour":
            try:
                payload = obj.get_raw_data()
            except Exception:  # noqa: BLE001
                payload = None
            if payload:
                ctx = _context_for(obj, payload=payload)
                for rs in rawstrings.scan_strings(payload, only_japanese=False):
                    if _jp.contains_japanese(rs.text):
                        if not _jp.looks_like_text(rs.text):
                            continue
                    else:
                        # v2.2 collect-all-visible: a non-JP record is display
                        # text only inside a text component (the TMP m_Text of
                        # the button/label it belongs to — real case: the
                        # main-menu "Achievement" button, which has no
                        # typetree and no Localization entry). Everywhere else
                        # the records are component-internal keys/names.
                        if not _raw_ctx_is_text_component(ctx):
                            continue
                        if not _jp.looks_like_visible_text(rs.text):
                            continue
                    report.strings.append(
                        StringHit(
                            file=asset_file.path,
                            path_id=obj.path_id,
                            obj_type=tname,
                            field=f"raw@0x{rs.offset:x}",
                            tokens=[],
                            source="raw",
                            offset=rs.offset,
                            length=rs.length,
                            text=rs.text,
                            context=ctx,
                            risk=string_risk("raw", "", "", rs.text),
                        )
                    )

        # --- TextAsset contents ---
        if tname == "TextAsset":
            try:
                from .textfiles import extract as tf_extract

                d = obj.read()
                name = getattr(d, "m_Name", "") or ""
                script = getattr(d, "m_Script", b"")
                if isinstance(script, (bytes, bytearray)):
                    content = bytes(script)
                else:
                    content = str(script).encode("utf-8")
                text = content.decode("utf-8", "ignore")
                has_jp = _jp.contains_japanese(text)
                report.text_assets.append(
                    TextAssetInfo(asset_file.path, obj.path_id, name, has_jp, len(text))
                )
                # Source 4: structured text inside the TextAsset payload.
                for item in tf_extract(content).items:
                    if not item.text:
                        continue
                    if _jp.contains_japanese(item.text):
                        if not _jp.looks_like_text(item.text):
                            continue
                    else:
                        # v2.2 collect-all-visible: non-JP lines only from
                        # content assets — license/package boilerplate (the
                        # bulk of English TextAssets) is skipped wholesale.
                        if _LICENSE_MARK.search(text):
                            continue
                        if not _jp.looks_like_visible_text(item.text):
                            continue
                    report.strings.append(
                        StringHit(
                            file=asset_file.path,
                            path_id=obj.path_id,
                            obj_type="TextAsset",
                            field=item.key,
                            tokens=[],
                            source="textasset",
                            offset=0,
                            length=0,
                            text=item.text,
                            context=f"{name} {item.key}",
                            key=item.key,
                            risk=string_risk("textasset", "", item.key, item.text),
                        )
                    )
            except Exception:  # noqa: BLE001
                pass


def _maybe_catalog_tmp_font(obj, tree: dict, file: str, report: DiscoveryReport):
    """Detect TextMeshPro fonts (MonoBehaviours) by their script and field shape."""
    name = tree.get("m_Name") or ""
    if "m_FaceInfo" in tree or "m_GlyphTable" in tree or "m_AtlasTextures" in tree:
        report.fonts.append(FontInfo(file, obj.path_id, name, "TMP_FontAsset"))


# --------------------------------------------------------------------------- #
# sources 3-5: IL2CPP literals, TextAsset contents, plain text files
# --------------------------------------------------------------------------- #
def _passes_text_filters(s: str) -> bool:
    """Japanese-only gate (metadata literals + the suspect report). IL2CPP
    literal blobs are ~95% framework/engine text — for NON-Japanese strings
    there is no reliable visibility signal, so the metadata source stays
    Japanese-only on purpose."""
    return bool(s) and _jp.contains_japanese(s) and _jp.looks_like_text(s)


def _passes_textfile_filters(s: str, file_text: str) -> bool:
    """Source-5 gate: Japanese items as before; non-Japanese items must pass
    the visible-text shape gate and the file must not be license/boilerplate."""
    if not s:
        return False
    if _jp.contains_japanese(s):
        return _jp.looks_like_text(s)
    if _LICENSE_MARK.search(file_text or ""):
        return False
    return _jp.looks_like_visible_text(s)


def _scan_text_sources(game_dir: str) -> list[StringHit]:
    """Sources 4 (TextAsset contents) and 5 (StreamingAssets text files)."""
    from .files import find_data_dir, find_text_files
    from .textfiles import extract as tf_extract

    hits: list[StringHit] = []
    data_dir = find_data_dir(game_dir) or game_dir

    # Source 5: plain text files.
    for path in find_text_files(data_dir):
        rel = os.path.relpath(path, game_dir).replace("\\", "/")
        try:
            content = open(path, "rb").read()
        except OSError:
            continue
        file_text = content.decode("utf-8", "ignore")
        ex = tf_extract(content)
        for item in ex.items:
            if _passes_textfile_filters(item.text, file_text):
                hits.append(StringHit(file=path, path_id=0, obj_type="TextFile",
                                      field=item.key, tokens=[], source="textfile",
                                      offset=0, length=0, text=item.text,
                                      context=f"{rel} {item.key}", key=item.key,
                                      risk=string_risk("textfile", "", item.key, item.text)))
    return hits


def _scan_metadata_literals(game_dir: str, data_dir: str) -> list[StringHit]:
    from .metadata import read_literals, MetadataError
    path = os.path.join(data_dir, "il2cpp_data", "Metadata", "global-metadata.dat")
    if not os.path.isfile(path):
        return []
    try:
        lits = read_literals(open(path, "rb").read())
    except (MetadataError, OSError):
        raise            # discover() catches and records it in report.errors
    out = []
    for lit in lits:
        if _passes_text_filters(lit.text):
            out.append(StringHit(file=path, path_id=lit.index, obj_type="Metadata",
                                  field=f"literal[{lit.index}]", tokens=[], source="metadata",
                                  offset=lit.data_offset, length=lit.byte_len, text=lit.text,
                                  context=f"literal #{lit.index}",
                                  risk=string_risk("metadata", "", "", lit.text)))
    return out


def _scan_mono_literals(data_dir: str, game_dir: str) -> list[StringHit]:
    """Mono Managed-DLL string literals (source 6, contract v2.6).

    Japanese C# literals from the #US heaps of <data_dir>/Managed/*.dll —
    the intro cinematics and one-off lines many Mono games keep in code
    rather than assets (real case: one test game's entire intro monologue).
    They are collected like every other string and TRANSLATED at export by
    ut.monopatch.translate_literals (grown #US heap + repointed ldstr
    tokens), bounded by the DLL's metadata slack. JP-only gate: code
    literals have no display-field signal, so the v2.2 non-JP collection
    does not apply here.
    """
    from .monopatch import DotNetAssembly, us_records, _SKIP_DLL_PREFIXES

    managed = os.path.join(data_dir, "Managed")
    if not os.path.isdir(managed):
        return []
    out: list[StringHit] = []
    for name in sorted(os.listdir(managed)):
        low = name.lower()
        if not low.endswith(".dll") or low.startswith(_SKIP_DLL_PREFIXES):
            continue
        path = os.path.join(managed, name)
        try:
            asm = DotNetAssembly(open(path, "rb").read())
        except Exception:  # noqa: BLE001 — not a CLI image
            continue
        rel = os.path.relpath(path, game_dir).replace("\\", "/")
        for off, x, s in us_records(asm.us_heap):
            if not _passes_text_filters(s):
                continue
            out.append(StringHit(
                file=path, path_id=off, obj_type="MonoLiteral",
                field=f"usheap@{off:#x}", tokens=[], source="mono",
                offset=off, length=x, text=s,
                context=f"{name} code literal",
                risk=string_risk("mono", "", "", s)))
    return out


def _suspect_files(game_dir: str) -> list[str]:
    """Plain files / TextAssets with Japanese chars but zero translatable items.

    Spec §3.3 source 5's safety report: anything that *looks* like it should
    contain translatable Japanese (the raw decoded content has JP characters)
    yet produced no passing items is listed for manual inspection, so silent
    misses surface instead of disappearing.
    """
    from .files import find_asset_files, find_data_dir, find_text_files
    from .textfiles import extract as tf_extract

    out: list[str] = []
    data_dir = find_data_dir(game_dir) or game_dir
    suffix = "(jp chars present, no translatable items)"

    def _any_pass(content: bytes) -> bool:
        return any(_passes_text_filters(item.text) for item in tf_extract(content).items)

    # Source 5: plain text files under StreamingAssets.
    for path in find_text_files(data_dir):
        try:
            content = open(path, "rb").read()
        except OSError:
            continue
        if _jp.contains_japanese(content.decode("utf-8", "ignore")) and not _any_pass(content):
            rel = os.path.relpath(path, game_dir).replace("\\", "/")
            out.append(f"{rel} {suffix}")

    # Source 4: TextAssets — a second, slower UnityPy pass (TextAssets only).
    for af in find_asset_files(data_dir):
        try:
            env = UnityPy.load(af.path)
        except Exception:  # noqa: BLE001
            continue
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            try:
                d = obj.read()
                script = getattr(d, "m_Script", b"")
                content = script if isinstance(script, (bytes, bytearray)) \
                    else str(script).encode("utf-8")
                if _jp.contains_japanese(content.decode("utf-8", "ignore")) \
                        and not _any_pass(content):
                    name = getattr(d, "m_Name", "") or ""
                    rel = os.path.relpath(af.path, game_dir).replace("\\", "/")
                    out.append(f"{rel} #{obj.path_id} {name} {suffix}")
            except Exception:  # noqa: BLE001
                continue
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def discover(
    game_dir: str,
    *,
    scan_textures: bool = True,
    scan_fonts: bool = True,
    scan_demosaic: bool = True,
    progress=None,
) -> DiscoveryReport:
    """Scan an entire game and return a DiscoveryReport.

    Progress: one line per asset file (i/N), then one line per post-pass
    phase — emitted BEFORE the phase runs, so the UI always shows what the
    scan is currently doing and nothing ever reads as a silent hang (real
    case: the Mono DLL literal scan took minutes per big game with zero
    feedback after the last file line).
    """
    data_dir = find_data_dir(game_dir) or game_dir
    files = find_asset_files(data_dir)
    report = DiscoveryReport()
    report.data_dir = data_dir
    report.files_scanned = len(files)
    total = len(files) + 4
    for i, af in enumerate(files):
        _scan_file(af, report, scan_textures, scan_fonts, scan_demosaic)
        if progress:
            progress(i + 1, total, af.rel)
    # v2.8 risk upgrade: a string that IS one of the game's own asset file
    # names (stem match, e.g. the SLRUnreal BGM-cue case) is asset identity,
    # not display text — red no matter which field it was collected from.
    if files:
        stems = {os.path.splitext(os.path.basename(af.rel))[0] for af in files}
        for h in report.strings:
            if h.risk != "red" and h.text.strip() in stems:
                h.risk = "red"
    if progress:
        progress(len(files) + 1, total, "mosaic candidates")
    if scan_demosaic:
        # Join the per-file catalogs into mosaic candidates (deterministic —
        # export re-runs the same detection over the staged files).
        try:
            from . import demosaic as _demosaic
            cats: list = []
            for cl in report.demosaic_catalog.values():
                cats.extend(cl)
            res_d = _demosaic.find_candidates(cats)
            # Code tier detection (report rows; patching is the opt-in
            # demosaic.code stage): mosaic-named Behaviour types in the
            # game's managed DLLs (Mono games; IL2CPP surfaces through the
            # MonoScript rows instead and is reported not-patchable).
            try:
                from . import monopatch as _monopatch
                for f in _monopatch.scan_managed_code(data_dir, game_dir=game_dir):
                    res_d.code.append({
                        "file": f["rel"], "name": f["type"],
                        "evidence": [f"managed type '{f['type']}' with methods "
                                     + ", ".join(f["methods"][:6])]})
            except Exception as e:  # noqa: BLE001
                report.errors.append(f"demosaic code scan: {e!r}")
            report.demosaic = res_d
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"demosaic scan: {e!r}")
        report.demosaic_catalog = {}  # payload never carries the raw catalogs
    if progress:
        progress(len(files) + 2, total, "text files")
    try:
        report.strings.extend(_scan_text_sources(game_dir))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"text sources: {e!r}")
    if progress:
        progress(len(files) + 3, total, "code & metadata")
    meta_path = os.path.join(data_dir, "il2cpp_data", "Metadata", "global-metadata.dat")
    if os.path.isfile(meta_path):
        try:
            report.strings.extend(_scan_metadata_literals(game_dir, data_dir))
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"metadata literals: {e!r}")
    try:
        report.strings.extend(_scan_mono_literals(data_dir, game_dir))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"mono literals: {e!r}")
    # Spec §3.3: Mono Managed DLL literals are detected and reported (never
    # translated — they live in the DLLs' #US heaps, outside our writers).
    try:
        from .mono import scan_managed_dir
        report.mono_report = scan_managed_dir(data_dir)
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"mono scan: {e!r}")
    if progress:
        progress(len(files) + 4, total, "finalizing")
    report.suspect = _suspect_files(game_dir)
    return report


def export_report(report: DiscoveryReport, out_dir: str, game_label: str = "game"):
    """Write human-editable translation files + machine index to `out_dir`.

    Outputs:
      strings.csv      unique JP,EN  (UTF-8 with BOM for Excel; EN blank)
      index.json       JP string -> occurrences (for patch write-back)
      textures.tsv     image catalogue
      fonts.tsv        font catalogue
      textassets.tsv   text-asset catalogue
      report.txt       human summary
    """
    os.makedirs(out_dir, exist_ok=True)
    base = report.data_dir or os.getcwd()

    def rel(p: str) -> str:
        try:
            return os.path.relpath(p, base)
        except ValueError:
            return p

    # unique strings -> occurrences
    occ: dict[str, list[dict]] = defaultdict(list)
    freq: Counter[str] = Counter()
    for h in report.strings:
        occ[h.text].append(
            {
                "file": rel(h.file),
                "abs": h.file,
                "path_id": h.path_id,
                "type": h.obj_type,
                "field": h.field,
                "tokens": h.tokens,
                "source": h.source,
                "offset": h.offset,
                "length": h.length,
                "context": h.context,
                "key": h.key,
            }
        )
        freq[h.text] += 1

    # strings.csv — one row per unique string, ordered by where it lives
    # (scene file, then object, then position inside the object) so the file
    # reads like a walkthrough of the game instead of a random word list.
    # `english` is the only column to edit; the rest is context.
    # utf-8-sig (BOM) so spreadsheet apps detect UTF-8; quoting handles
    # commas and embedded newlines in dialogue.
    def _occ_sort_key(o: dict):
        return (_natural_key(o["file"]), o["path_id"], o["offset"])

    csv_path = os.path.join(out_dir, "strings.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["japanese", "english", "context", "field", "file", "path_id", "hits"])
        for s, _c in sorted(freq.items(), key=lambda kv: min(_occ_sort_key(o) for o in occ[kv[0]])):
            occs = sorted(occ[s], key=_occ_sort_key)
            contexts: list[str] = []
            for o in occs:
                c = o.get("context") or ""
                if c and c not in contexts:
                    contexts.append(c)
                if len(contexts) == 3:
                    break
            ctx = " | ".join(contexts) + (" | …" if len(contexts) == 3 else "")
            first = occs[0]
            w.writerow([s, "", ctx, first["field"], first["file"], first["path_id"], len(occs)])

    index_path = os.path.join(out_dir, "index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({s: occ[s] for s in freq}, f, ensure_ascii=False, indent=1)

    def _write_tsv(name, header, rows):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(header + "\n")
            for r in rows:
                f.write("\t".join(str(x) for x in r) + "\n")

    _write_tsv(
        "textures.tsv",
        "file\tpath_id\tname\ttype\twidth\theight\tformat",
        [
            (rel(t.file), t.path_id, t.name, t.tex_type, t.width, t.height, t.format)
            for t in report.textures
        ],
    )
    _write_tsv(
        "fonts.tsv",
        "file\tpath_id\tname\tfont_type",
        [
            (rel(ft.file), ft.path_id, ft.name, ft.font_type)
            for ft in report.fonts
        ],
    )
    _write_tsv(
        "textassets.tsv",
        "file\tpath_id\tname\thas_japanese\tsize",
        [
            (rel(ta.file), ta.path_id, ta.name, int(ta.has_japanese), ta.size)
            for ta in report.text_assets
        ],
    )

    summary = [
        f"SLRUnity scan report: {game_label}",
        f"Files scanned:        {report.files_scanned}",
        f"String hits:          {len(report.strings)}",
        f"Unique strings:       {len(freq)}",
        f"Textures/Sprites:     {len(report.textures)}",
        f"Fonts:                {len(report.fonts)}",
        f"TextAssets:           {len(report.text_assets)}"
        + f" ({sum(1 for t in report.text_assets if t.has_japanese)} with Japanese)",
        f"Load errors:          {len(report.errors)}",
        "",
        "Font types found:",
    ]
    # v2.8 risk tags, per unique text at its worst occurrence (the JS layer
    # writes the same ranking into SLR's per-row tags — a text patched
    # everywhere must wear its most conservative tag).
    _per_text = defaultdict(list)
    for h in report.strings:
        _per_text[h.text].append(h.risk)
    _tc = Counter(worst_risk(rs) for rs in _per_text.values())
    summary.append(
        "String tags (SLR scheme): "
        f"{_tc.get('', 0) + _tc.get('green', 0)} translate by default"
        f" ({_tc.get('green', 0)} code literals),"
        f" {_tc.get('yellow', 0)} confirm-first (yellow/gold),"
        f" {_tc.get('red', 0)} flagged (red/darkred)")
    fcount = Counter(f.font_type for f in report.fonts)
    for k, c in fcount.most_common():
        summary.append(f"  {c:5d}  {k}")
    if report.mono_report:
        summary.append("")
        summary.append("Mono DLL code literals (collected; translated at export while the DLL's metadata slack lasts):")
        for m in report.mono_report:
            summary.append(f"  {m['count']:5d}  {m['dll']}")
    if report.demosaic is not None:
        d = report.demosaic
        summary.append("")
        summary.append(
            "Demosaic candidates (mosaic censorship layers, patched only when"
            " the export option is on):")
        summary.append(
            f"  Objects:   {len(d.objects)}    Materials: {len(d.materials)}"
            f"    Shaders: {len(d.shaders)}")
        for row in d.candidate_rows()[:25]:
            ev = "; ".join(row["evidence"])
            summary.append(f"  [{row['confidence']:6s}] {row['kind']:8s} "
                           f"{row['name'] or '?'} — {row['file'].split('#')[0]}"
                           f" #{row['path_id']} ({ev})")
        if len(d.candidate_rows()) > 25:
            summary.append(f"  … and {len(d.candidate_rows()) - 25} more")
        if d.code:
            summary.append("  code-level censor types (Mono DLLs; patched only"
                           " with the deep opt-in):")
            for c in d.code[:10]:
                summary.append(f"    {c['name']} ({c['file']})")
    if report.errors:
        summary.append("")
        summary.append("First errors:")
        for e in report.errors[:20]:
            summary.append("  " + e)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary) + "\n")

    return {
        "strings_csv": csv_path,
        "index_json": index_path,
        "unique": len(freq),
        "hits": len(report.strings),
    }
