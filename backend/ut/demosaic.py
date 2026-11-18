"""Demosaic: find mosaic-censorship layers in the serialized assets and patch
them out of an export — the in-place equivalent of the UniversalUnityDemosaics
BepInEx plugins, with static evidence instead of a per-frame name guess.

How mosaic censorship is built (and why this works): Japanese devs ship the
UNCENSORED mesh/texture in the game data and add the mosaic ON TOP at runtime —
a dedicated overlay GameObject with a mosaic material, or a mosaic shader on
the modeled mesh itself. UUD hides those layers live, every frame, matching a
hardcoded substring list ("mosaic", "censor", "pixelate", ...) against
object/material/shader names. Here the same layers are found ONCE in the
serialized assets and patched out of the export copy:

  object   GameObject whose name matches -> m_IsActive=0 AND every renderer
           component m_Enabled=0 + m_Materials=[] (renders nothing even if
           the game's code re-activates the object by reference).
  renderer Renderer whose materials are ALL mosaic candidates -> m_Enabled=0
           + m_Materials=[] (combined-mesh safe: a renderer mixing mosaic and
           normal materials is never hidden — the material path handles it).
  material Material marked by its own name, its shader's name or its shader's
           property table -> m_Shader re-pointed to the game's OWN
           unlit/transparent replacement shader, texture slots cleared,
           _Color/_BaseColor alpha 0, transparent-surface floats/keyword set
           (the static version of UUD CombinedMesh's additive-clear recipe).

Evidence, strongest first (UUD only ever had the first line):
  * object / material / shader NAME matches the mosaic dictionary
  * the shader's serialized PROPERTY table names mosaic parameters
    (_PixelSize, _MosaicStrength, ...) — catches the stripped-name shaders
    whose real names never ship (real case: this game's mosaic shader is
    nameless in the build but carries _sizeMin/_sizeMax)
  * graph propagation: a renderer whose materials are all candidates

Confidence: "high" patches by default; "medium" (weak-only evidence, e.g. a
shader that merely carries the _sizeMin/_sizeMax pair) patches only when the
export option asks for it. Detection is deterministic, so the scan-time
candidate list and the export-time decisions always agree.

Everything writes into out_dir only; the original game is never touched.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import UnityPy

from .files import find_asset_files, find_data_dir
from .inject import _save_env_to_file

# UUD MozaicTools._mozaicNameParts, verbatim — the battle-tested dictionary.
NAME_PARTS = ("mozaic", "mosaic", "mozaik", "mosaik", "pixelate",
              "censor", "cenzor", "masaco")

# Shader property names that are explicit mosaic parameters (any hit = strong
# evidence). Matched against the serialized shader's property table.
_EXPLICIT_PROP_RE = re.compile(r"mosa|moza|pixel|cens|cenz", re.I)

# The weak pair: _sizeMin/_sizeMax together are typical mosaic block-size
# parameters (real case) but also plausible elsewhere, so alone they only
# produce a "medium" candidate.
_WEAK_PROP_PAIR = ("_sizemin", "_sizemax")

# Replacement shaders for the material neuter, best first. URP-native entries
# only count in URP games (a built-in shader in a URP game renders magenta),
# and vice versa — the pool is picked by pipeline (see _pick_replacement).
_REPLACEMENT_URP = (
    "Universal Render Pipeline/Particles/Unlit",
    "Universal Render Pipeline/Unlit",
)
_REPLACEMENT_BUILTIN = (
    "Mobile/Particles/Additive",   # UUD CombinedMesh's own choice
    "Unlit/Transparent",
    "Unlit/Color",
    "Unlit/Texture",
    "Sprites/Default",
    "UI/Default",
)

_RENDERER_TYPES = ("MeshRenderer", "SkinnedMeshRenderer")

# Sanity caps per serialized file — far above any real game (the test game's
# biggest scene has <100k GameObjects); guards only corrupt/absurd files.
_CATALOG_CAP = 250_000
_LOG_CAP = 80  # human log lines carried in the stats


def name_is_mosaic(s: str) -> bool:
    """UUD IsMozaicName: case-insensitive substring against the dictionary."""
    if not s:
        return False
    s = s.lower()
    return any(p in s for p in NAME_PARTS)


def shader_prop_signal(props: list) -> tuple[list, bool]:
    """(explicit_matches, weak_pair_present) from a shader's property names."""
    explicit = [p for p in props if _EXPLICIT_PROP_RE.search(p or "")]
    low = [str(p or "").lower() for p in props]
    weak = all(w in low for w in _WEAK_PROP_PAIR)
    return explicit, weak


# --------------------------------------------------------------------------- #
# catalog: one serialized file's GameObject/Renderer/Material/Shader objects
# --------------------------------------------------------------------------- #
def _iter_serialized_files(env):
    """SerializedFiles inside a loaded Environment (bundles hold several)."""
    for f in (getattr(env, "files", None) or {}).values():
        if hasattr(f, "objects"):
            yield f
        else:  # BundleFile / WebFile container
            for sub in (getattr(f, "files", None) or {}).values():
                if hasattr(sub, "objects"):
                    yield sub


def _pptr(v) -> tuple[int, int] | None:
    """(m_FileID, m_PathID) from a PPtr-ish object or plain dict; None when 0."""
    if v is None:
        return None
    fid = getattr(v, "m_FileID", None)
    pid = getattr(v, "m_PathID", None)
    if fid is None and isinstance(v, dict):
        fid, pid = v.get("m_FileID"), v.get("m_PathID")
    try:
        fid, pid = int(fid), int(pid)
    except (TypeError, ValueError):
        return None
    if fid == 0 and pid == 0:
        return None
    return (fid, pid)


def catalog_serialized_file(sf, file_key: str) -> dict:
    """Read the four mosaic-relevant object classes out of one SerializedFile.

    `file_key` must be '<abs path>#<index>' — unique per serialized file (a
    bundle holds several); the matching basename for external references is
    derived from the part before the '#'.

    Catalog-only reads (obj.read(); engine types parse natively even in
    stripped IL2CPP builds), shaders via read_typetree for the parsed form.
    Everything is best-effort: a broken object is skipped, never fatal.
    """
    cat = {
        "file": file_key,
        "basename": os.path.basename(file_key.split("#")[0]).lower(),
        "objects": {},       # pid -> type name
        "game_objects": {},  # pid -> {name, active, components: [(fid, pid)]}
        "renderers": {},     # pid -> {go, materials: [(fid, pid)], enabled}
        "materials": {},     # pid -> {name, shader: (fid, pid) | None}
        "shaders": {},       # pid -> {name, props: [str]}
        "monoscripts": {},   # pid -> {class, assembly} (IL2CPP keeps stubs)
        "externals": [],     # m_FileID i resolves to externals[i-1]
    }
    for e in getattr(sf, "externals", None) or []:
        name = getattr(e, "name", None) or os.path.basename(getattr(e, "path", "") or "")
        cat["externals"].append(str(name).lower())

    for obj in (getattr(sf, "objects", None) or {}).values():
        tname = getattr(getattr(obj, "type", None), "name", "")
        pid = obj.path_id
        try:
            if tname == "GameObject" and len(cat["game_objects"]) < _CATALOG_CAP:
                d = obj.read()
                comps = []
                for entry in getattr(d, "m_Component", None) or []:
                    p = _pptr(getattr(entry, "component", entry))
                    if p:
                        comps.append(p)
                cat["objects"][pid] = tname
                cat["game_objects"][pid] = {
                    "name": getattr(d, "m_Name", "") or "",
                    "active": bool(getattr(d, "m_IsActive", 1)),
                    "components": comps,
                }
            elif tname in _RENDERER_TYPES and len(cat["renderers"]) < _CATALOG_CAP:
                d = obj.read()
                mats = []
                for m in getattr(d, "m_Materials", None) or []:
                    p = _pptr(m)
                    if p:
                        mats.append(p)
                cat["objects"][pid] = tname
                cat["renderers"][pid] = {
                    "go": _pptr(getattr(d, "m_GameObject", None)),
                    "materials": mats,
                    "enabled": bool(getattr(d, "m_Enabled", 1)),
                    "kind": "skinned" if tname == "SkinnedMeshRenderer" else "mesh",
                }
            elif tname == "Material" and len(cat["materials"]) < _CATALOG_CAP:
                d = obj.read()
                cat["objects"][pid] = tname
                cat["materials"][pid] = {
                    "name": getattr(d, "m_Name", "") or "",
                    "shader": _pptr(getattr(d, "m_Shader", None)),
                }
            elif tname == "MonoScript" and len(cat["monoscripts"]) < _CATALOG_CAP:
                d = obj.read()
                cls = getattr(d, "m_ClassName", "") or ""
                if cls:
                    cat["objects"][pid] = tname
                    cat["monoscripts"][pid] = {
                        "class": cls,
                        "assembly": getattr(d, "m_AssemblyName", "") or "",
                    }
            elif tname == "Shader" and len(cat["shaders"]) < _CATALOG_CAP:
                tree = obj.read_typetree()
                parsed = tree.get("m_ParsedForm") or {}
                props = []
                for p in (parsed.get("m_PropInfo") or {}).get("m_Props") or []:
                    n = p.get("m_Name") if isinstance(p, dict) else None
                    if n:
                        props.append(n)
                cat["objects"][pid] = tname
                cat["shaders"][pid] = {
                    "name": parsed.get("m_Name") or tree.get("m_Name") or "",
                    "props": props[:64],
                }
        except Exception:  # noqa: BLE001 — one broken object never kills the catalog
            continue
    return cat


def catalog_env(env, file_key: str) -> list[dict]:
    """Catalogs for every serialized file inside a loaded Environment, in
    enumeration order (stable for the same file — patch_game_demosaic relies
    on zip-pairing these with the env it reloads from the same bytes)."""
    return [catalog_serialized_file(sf, f"{file_key}#{i}")
            for i, sf in enumerate(_iter_serialized_files(env))]


# --------------------------------------------------------------------------- #
# candidate finding over the joined per-file catalogs
# --------------------------------------------------------------------------- #
@dataclass
class DemosaicResult:
    objects: list = field(default_factory=list)    # {file, path_id, name, evidence}
    materials: list = field(default_factory=list)  # + confidence, replacement
    renderers: list = field(default_factory=list)  # derived hide actions
    shaders: list = field(default_factory=list)    # report-only standalones
    scripts: list = field(default_factory=list)    # mosaic-named MonoScripts (report)
    code: list = field(default_factory=list)       # managed-DLL censor types (patch tier)
    notes: list = field(default_factory=list)

    def candidate_rows(self) -> list:
        """Scan-payload rows: everything worth staging/reporting."""
        rows = []
        for kind, items in (("object", self.objects), ("material", self.materials),
                            ("shader", self.shaders)):
            for it in items:
                rows.append({"file": it["file"], "path_id": str(it["path_id"]),
                             "kind": kind, "name": it.get("name") or "",
                             "confidence": it.get("confidence", "high"),
                             "evidence": it.get("evidence", [])})
        for sc in self.scripts:
            rows.append({"file": sc["file"], "path_id": str(sc["path_id"]),
                         "kind": "script", "name": sc["name"],
                         "confidence": "high", "evidence": sc["evidence"]})
        for cd in self.code:
            rows.append({"file": cd["file"], "path_id": "0", "kind": "code",
                         "name": cd["name"], "confidence": "high",
                         "evidence": cd["evidence"]})
        return rows


class _Graph:
    """Cross-file PPtr resolution over the joined catalogs.

    m_FileID 0 is the same serialized file, 1..N its externals list; an
    external name is matched against the other catalogs' basenames (lower).
    Ambiguous basenames (two catalogs sharing one name) resolve to nothing.
    """

    def __init__(self, catalogs: list):
        self.by_key = {c["file"]: c for c in catalogs}
        by_base: dict[str, list] = {}
        for c in catalogs:
            by_base.setdefault(c["basename"], []).append(c)
        self.by_base = {k: v[0] for k, v in by_base.items() if len(v) == 1}

    def resolve(self, cat: dict, pptr: tuple[int, int]):
        """(catalog, path_id) or (None, pid) when the target is outside scope."""
        fid, pid = pptr
        if fid == 0:
            return self.by_key.get(cat["file"]), pid
        if 1 <= fid <= len(cat["externals"]):
            target = self.by_base.get(cat["externals"][fid - 1])
            if target is not None:
                return target, pid
        return None, pid


def _shader_confidence(shader: dict) -> tuple[str, list]:
    """('high'|'medium'|'', evidence) for one catalogued shader."""
    if name_is_mosaic(shader["name"]):
        return "high", [f"shader name '{shader['name']}'"]
    explicit, weak = shader_prop_signal(shader["props"])
    if explicit:
        return "high", ["shader properties: " + ", ".join(explicit[:4])]
    if weak:
        return "medium", ["shader properties: " + ", ".join(_WEAK_PROP_PAIR)]
    return "", []


def find_candidates(catalogs: list) -> DemosaicResult:
    """Deterministic candidate finding over the joined catalogs."""
    res = DemosaicResult()
    if not catalogs:
        return res
    graph = _Graph(catalogs)

    # --- shaders: evidence table + report-only rows for standalones ---
    shader_conf: dict[tuple, tuple[str, list]] = {}  # (file_key, pid) -> (conf, ev)
    for cat in catalogs:
        referenced_pids = {m["shader"][1] for m in cat["materials"].values()
                           if m["shader"] and m["shader"][0] == 0}
        for pid, sh in cat["shaders"].items():
            conf, ev = _shader_confidence(sh)
            if not conf:
                continue
            shader_conf[(cat["file"], pid)] = (conf, ev)
            # high shaders surface through their materials' own rows; they get
            # a row of their own only when nothing references them (report-
            # only anyway — shaders are never patched directly).
            if conf == "medium" or pid not in referenced_pids:
                res.shaders.append({"file": cat["file"], "path_id": pid,
                                    "name": sh["name"] or "<unnamed>",
                                    "confidence": conf, "evidence": ev})

    # --- materials: own name, else the confidence of their shader ---
    material_conf: dict[tuple, tuple[str, list]] = {}
    for cat in catalogs:
        for pid, mat in cat["materials"].items():
            if name_is_mosaic(mat["name"]):
                material_conf[(cat["file"], pid)] = (
                    "high", [f"material name '{mat['name']}'"])
                continue
            if mat["shader"] is None:
                continue
            tcat, tpid = graph.resolve(cat, mat["shader"])
            if tcat is None:
                continue
            scon = shader_conf.get((tcat["file"], tpid))
            if scon:
                material_conf[(cat["file"], pid)] = scon

    # --- objects ---
    for cat in catalogs:
        for pid, go in cat["game_objects"].items():
            if name_is_mosaic(go["name"]):
                res.objects.append({"file": cat["file"], "path_id": pid,
                                    "name": go["name"],
                                    "evidence": [f"object name '{go['name']}'"]})

    # --- scripts: mosaic-named MonoScript classes (report rows; patching is
    # the opt-in code tier's job — these rows only say the censor lives in
    # code). IL2CPP builds keep MonoScript stubs, so this sees both worlds.
    for cat in catalogs:
        for pid, ms in cat["monoscripts"].items():
            if name_is_mosaic(ms["class"]):
                res.scripts.append({
                    "file": cat["file"], "path_id": pid, "name": ms["class"],
                    "evidence": [f"script class '{ms['class']}'"
                                 + (f" ({ms['assembly']})" if ms["assembly"] else "")]})

    # --- material usage classification (drives the reveal tier) ---
    # Every renderer's resolved materials. In reveal mode ALL-mosaic
    # renderers are kept (not hidden) WHATEVER their mesh kind and their
    # materials display-swapped — Live2D/2D games draw censor regions with
    # plain MeshRenderers, so the old skinned-only exemption hid the organ
    # art itself (real case: a Live2D game's ArtMesh renderers: hiding made
    # the whole part disappear, and reveal "looked the same"). A renderer
    # under a mosaic-named GameObject is hidden by the object action in
    # both modes, so its materials are never reveal-eligible.
    renderer_materials: list = []  # (kind, [(cat, pid) ...], go_mosaic, key)
    for cat in catalogs:
        for rpid, rnd in cat["renderers"].items():
            if not rnd["materials"]:
                continue
            resolved = [graph.resolve(cat, m) for m in rnd["materials"]]
            if any(t[0] is None for t in resolved):
                continue
            go_mosaic = False
            if rnd["go"] and rnd["go"][0] == 0:
                go = cat["game_objects"].get(rnd["go"][1]) or {}
                go_mosaic = name_is_mosaic(go.get("name", ""))
            renderer_materials.append((rnd["kind"], resolved, go_mosaic,
                                       (cat["file"], rpid)))

    def _reveal_eligible(file_key: str, pid: int) -> bool:
        # eligible when any renderer that STAYS VISIBLE in reveal mode uses
        # it: mixed renderers are never hidden, and all-mosaic renderers are
        # now kept regardless of kind — unless a mosaic-named object owns
        # them (the object action hides those in reveal mode too).
        for _kind, resolved, go_mosaic, _key in renderer_materials:
            uses = any(t[0] is not None and (t[0]["file"], t[1]) == (file_key, pid)
                       for t in resolved)
            if uses and not go_mosaic:
                return True
        return False

    # --- material rows + replacement + display shaders ---
    urp = any(sh["name"].startswith("Universal Render Pipeline/")
              for c in catalogs for sh in c["shaders"].values())
    display = _pick_display_shader(graph, catalogs, urp, shader_conf,
                                   renderer_materials, material_conf)
    for (file_key, pid), (conf, ev) in sorted(material_conf.items()):
        cat = graph.by_key[file_key]
        mat = cat["materials"][pid]
        repl = _pick_replacement(graph, cat, urp)
        eligible = _reveal_eligible(file_key, pid)
        if repl is None and not (eligible and display):
            # a reveal-eligible material with a display shader doesn't need
            # the blank-neuter path, so a missing replacement is no problem
            res.notes.append(
                f"no replacement shader in scope for material "
                f"'{mat['name'] or '?'}' ({os.path.basename(file_key)} #{pid}) "
                f"— it can only be hidden, not neutered")
        res.materials.append({"file": file_key, "path_id": pid,
                              "name": mat["name"], "confidence": conf,
                              "evidence": ev, "replacement": repl,
                              "reveal": eligible,
                              "display": display})

    # --- renderer hide actions: ALL materials (>=1) high candidates ---
    for cat in catalogs:
        for rpid, rnd in cat["renderers"].items():
            if not rnd["materials"] or not rnd["enabled"]:
                continue
            resolved = [graph.resolve(cat, m) for m in rnd["materials"]]
            if any(t[0] is None for t in resolved):
                continue  # an unresolvable material: cannot prove all-mosaic
            if all(material_conf.get((t[0]["file"], t[1]), ("",))[0] == "high"
                   for t in resolved):
                goname = ""
                under_candidate = False
                if rnd["go"] and rnd["go"][0] == 0:
                    go = cat["game_objects"].get(rnd["go"][1]) or {}
                    goname = go.get("name", "")
                    under_candidate = name_is_mosaic(goname)
                if not under_candidate:
                    # dedicated mosaic objects already hide their renderers
                    # via the object action. In reveal mode these all-mosaic
                    # renderers are kept and their materials display-swapped
                    # (UUD ShaderReplace/MaterialReplace's target class) —
                    # the patch stage reads `reveal` to decide.
                    res.renderers.append({"file": cat["file"], "path_id": rpid,
                                          "name": goname,
                                          "kind": rnd["kind"]})
    return res


def _pick_display_shader(graph, catalogs, urp, shader_conf, renderer_materials,
                         material_conf):
    """The reveal tier's display shader — the thing UUD makes the user name
    by hand, picked statically. Best first:
    1. derived: a mosaic-named shader minus its mosaic tokens ('X/Unlit
       Mosaic' -> 'X/Unlit') — the plain variant the game itself derived the
       censor shader from, preferred when visible renderers reference it
       (real case: 'Live2D Cubism/Unlit Mosaic' / 'Live2D Cubism/Unlit');
    2. the game's most common non-mosaic shader on visible renderer
       materials, any renderer kind (its body/skin shader — Live2D/2D games
       draw their models with plain MeshRenderers, so a skinned-only count
       finds nothing);
    3. the pipeline's plain display shader, if the game ships one.
    Returns (file_key, pid, name) or None."""
    # 1. derived-from-name: mosaic shader names with their mosaic tokens
    #    stripped, matched against the game's plain (non-candidate) shaders.
    derived: dict[tuple, int] = {}
    mosaic_names = [sh["name"] for c in catalogs
                    for sh in c["shaders"].values()
                    if sh["name"] and name_is_mosaic(sh["name"])]
    if mosaic_names:
        plain: dict[str, list] = {}
        for c in catalogs:
            for pid, sh in c["shaders"].items():
                if sh["name"] and not name_is_mosaic(sh["name"]):
                    plain.setdefault(sh["name"], []).append((c["file"], pid))
        for name in mosaic_names:
            base = " ".join(t for t in name.split()
                            if not name_is_mosaic(t)).strip()
            for fk, pid2 in plain.get(base, ()):
                key = (fk, pid2, base)
                derived[key] = derived.get(key, 0) + 1
    if derived:
        referenced = set()  # shader keys on visible renderer materials
        for _kind, resolved, _go_mos, _key in renderer_materials:
            for t in resolved:
                if t[0] is None:
                    continue
                mat = t[0]["materials"].get(t[1])
                if not mat or mat["shader"] is None:
                    continue
                if (t[0]["file"], t[1]) in material_conf:
                    continue  # a mosaic candidate: not a display shader
                target = graph.resolve(t[0], mat["shader"])
                if target[0] is not None:
                    referenced.add((target[0]["file"], target[1]))
        return max(sorted(derived),
                   key=lambda k: (k[:2] in referenced, derived[k]))
    # 2. most common on visible renderers, any kind
    counts: dict[tuple, int] = {}
    for _kind, resolved, _go_mos, _key in renderer_materials:
        for t in resolved:
            if t[0] is None:
                continue
            mat = t[0]["materials"].get(t[1])
            if not mat or mat["shader"] is None:
                continue
            if (t[0]["file"], t[1]) in material_conf:
                continue  # a mosaic candidate: not a display shader
            scat = t[0]
            target = graph.resolve(scat, scat["materials"][t[1]]["shader"])
            if target[0] is None:
                continue
            if (target[0]["file"], target[1]) in shader_conf:
                continue
            sh = target[0]["shaders"].get(target[1]) or {}
            if not sh.get("name"):
                continue
            key = (target[0]["file"], target[1], sh["name"])
            counts[key] = counts.get(key, 0) + 1
    if counts:
        best = max(sorted(counts), key=lambda k: counts[k])
        return best
    # fallback: the pipeline's plain display shader, if the game ships one
    want = ("universal render pipeline/unlit", "unlit/texture")
    for c in catalogs:
        for pid, sh in c["shaders"].items():
            if (sh["name"] or "").lower() in want:
                return (c["file"], pid, sh["name"])
    return None


def _pick_replacement(graph, cat, urp):
    """First available replacement shader for a material in `cat`'s file,
    pipeline-matched. Same file wins over externals; the pool order decides
    the rest. Returns (file_key, pid, shader_name) or None."""
    pool = _REPLACEMENT_URP if urp else _REPLACEMENT_BUILTIN
    want = {n.lower(): n for n in pool}

    def search(target_cat):
        for pid, sh in target_cat["shaders"].items():
            name = want.get((sh["name"] or "").lower())
            if name:
                return (target_cat["file"], pid, name)
        return None

    hit = search(cat)
    if hit:
        return hit
    for other in sorted(graph.by_base.values(), key=lambda c: c["basename"]):
        if other is cat:
            continue
        hit = search(other)
        if hit:
            return hit
    return None


# --------------------------------------------------------------------------- #
# export-time patching
# --------------------------------------------------------------------------- #
def patch_game_demosaic(
    game_dir: str,
    out_dir: str,
    *,
    level: str = "high",
    reveal: bool = False,
    patch_mode: bool = False,
    in_place: bool = False,
    progress=None,
) -> dict:
    """Detect mosaic candidates over the export scope and patch them.

    patch_mode reads sources from game_dir EXCEPT where an earlier stage of
    this run already wrote out_dir/<rel> (the stage-order rule — string edits
    must survive), and writes only changed files to out_dir/<rel>.
    in_place (full-copy mode) edits the copied tree at out_dir directly.

    reveal=True switches the ShaderReplace/MaterialReplace treatment on:
    all-mosaic renderers are kept (not hidden) — whatever their mesh kind —
    and their reveal-eligible materials get the game's display shader
    KEEPING textures, instead of being blanked. The tier to try when hiding
    makes body parts disappear entirely: 2D/Live2D games draw censor regions
    with plain MeshRenderers whose mosaic material IS the part's only draw,
    so hiding deletes the part (real case: a Live2D game's ArtMesh renderers).

    Returns {objects_hidden, renderers_hidden, materials_neutered,
             materials_revealed, files_changed, notes, log, exported_files}.
    """
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if in_place and game_dir != out_dir:
        raise ValueError("in_place=True requires game_dir == out_dir")
    from .files import assert_not_nested
    if not in_place:
        assert_not_nested(game_dir, out_dir, "out_dir")

    data_dir = find_data_dir(game_dir) or game_dir
    files = find_asset_files(data_dir)

    # pass 1: catalog every file, from the SOURCE OF TRUTH for each (the
    # stage-order rule: a file the string stage already patched lives in
    # out_dir and carries the newer bytes). rel is derived from af.path
    # against the GAME dir (af.rel is data-dir-relative).
    catalogs: list = []
    cats_for_src: dict[str, list] = {}
    src_of: dict[str, str] = {}
    for i, af in enumerate(files):
        src = af.path
        if patch_mode:
            out_file = os.path.join(out_dir, os.path.relpath(af.path, game_dir))
            if os.path.isfile(out_file):
                src = out_file
        src_of[af.path] = src
        try:
            env = UnityPy.load(src)
            cats = catalog_env(env, af.path)
        except Exception:  # noqa: BLE001
            continue
        catalogs.extend(cats)
        cats_for_src[src] = cats
        if progress:
            progress(i + 1, len(files) * 2, af.rel)

    result = find_candidates(catalogs)

    # group actions per source file
    actions: dict[str, dict] = {}

    def _entry(src):
        return actions.setdefault(src, {"objects": [], "renderers": [], "materials": []})

    for o in result.objects:
        _entry(src_of.get(o["file"].split("#")[0], o["file"]))["objects"].append(o)
    for r in result.renderers:
        _entry(src_of.get(r["file"].split("#")[0], r["file"]))["renderers"].append(r)
    for m in result.materials:
        if m["confidence"] == "high" or (level == "medium" and m["confidence"] == "medium"):
            _entry(src_of.get(m["file"].split("#")[0], m["file"]))["materials"].append(m)

    stats = {"objects_hidden": 0, "renderers_hidden": 0, "materials_neutered": 0,
             "materials_revealed": 0, "files_changed": 0, "notes": result.notes,
             "log": list(result.notes[:_LOG_CAP]), "exported_files": []}
    if not actions:
        return stats

    from .files import assert_inside
    done = len(files)
    for src, acts in sorted(actions.items()):
        done += 1
        # the game-relative identity is defined BEFORE anything can skip the
        # iteration — the progress line must never see an unbound name
        # (real bug: a file whose actions changed nothing crashed the whole
        # export with UnboundLocalError on `rel`)
        rel = (os.path.relpath(src, out_dir) if src.startswith(out_dir)
               else os.path.relpath(src, game_dir))
        cats = cats_for_src.get(src) or []
        try:
            env = UnityPy.load(src)
        except Exception as e:  # noqa: BLE001
            stats["log"].append(f"! could not load {os.path.basename(src)}: {e!r}")
            if progress:
                progress(done, len(files) * 2, rel)
            continue
        changed = _patch_env(env, cats, acts, stats, reveal=reveal)
        if changed:
            out_file = os.path.join(out_dir, rel)
            assert_inside(out_dir, out_file, f"demosaic output for {rel!r}")
            _save_env_to_file(env, out_file)
            stats["files_changed"] += 1
            stats["exported_files"].append(rel.replace("\\", "/"))
        if progress:
            progress(done, len(files) * 2, rel)
    stats["log"] = stats["log"][:_LOG_CAP]
    return stats


def _patch_env(env, cats, acts, stats, reveal: bool = False) -> bool:
    """Apply one file's object/renderer/material actions to a loaded env.

    `cats` are this file's catalogs in _iter_serialized_files order (the
    pairing patch_game_demosaic built them in), so every serialized file
    knows its own catalog key and externals for cross-file shader pointers.
    reveal=True keeps ALL-mosaic renderers (any mesh kind) and display-swaps
    their materials instead of hiding/blanking (UUD ShaderReplace/
    MaterialReplace behavior — the tier for games where hiding makes parts
    disappear; Live2D censor meshes are plain MeshRenderers).
    """
    changed = False
    if not cats:
        return False
    for sf, cat in zip(_iter_serialized_files(env), cats):
        by_pid = {o.path_id: o for o in (getattr(sf, "objects", None) or {}).values()}
        externals = list(cat["externals"])

        # --- objects: deactivate + strip their renderer components ---
        for o in acts.get("objects", []):
            obj = by_pid.get(o["path_id"])
            if obj is None:
                continue
            try:
                tree = obj.read_typetree()
            except Exception:  # noqa: BLE001
                continue
            tree["m_IsActive"] = 0
            try:
                obj.save_typetree(tree)
            except Exception:  # noqa: BLE001
                continue
            changed = True
            stats["objects_hidden"] += 1
            _log(stats["log"], f"hidden object '{o['name']}' "
                               f"({o['evidence'][0] if o['evidence'] else 'mosaic'})")
            for entry in tree.get("m_Component") or []:
                p = entry.get("component") if isinstance(entry, dict) else None
                if not p or int(p.get("m_FileID", 0)) != 0:
                    continue
                robj = by_pid.get(int(p.get("m_PathID", 0)))
                if robj is not None and _is_renderer(robj) and _hide_renderer(robj):
                    stats["renderers_hidden"] += 1

        # --- renderers whose materials are all mosaic candidates ---
        for r in acts.get("renderers", []):
            if reveal:
                _log(stats["log"], f"kept all-mosaic renderer on '{r['name'] or '?'}' "
                                   f"(reveal mode — its materials are display-swapped)")
                continue
            robj = by_pid.get(r["path_id"])
            if robj is None or not _is_renderer(robj):
                continue
            if _hide_renderer(robj):
                changed = True
                stats["renderers_hidden"] += 1
                _log(stats["log"], f"hid renderer on '{r['name'] or '?'}' "
                                   f"(all its materials are mosaic)")

        # --- materials: blank (default) or display-swap (reveal tier) ---
        for m in acts.get("materials", []):
            obj = by_pid.get(m["path_id"])
            if obj is None:
                continue
            try:
                tree = obj.read_typetree()
            except Exception:  # noqa: BLE001
                continue
            if reveal and m.get("reveal") and m.get("display"):
                repl_name = _retarget_shader(tree, m["display"], externals, cat)
                if repl_name is None:
                    _log(stats["log"], f"material '{m['name'] or '?'}': display "
                                       f"shader not referenceable from this file "
                                       f"(kept as-is)")
                    continue
                try:
                    obj.save_typetree(tree)
                except Exception as e:  # noqa: BLE001
                    stats["log"].append(f"! material '{m['name']}' save failed: {e!r}")
                    continue
                changed = True
                stats["materials_revealed"] += 1
                _log(stats["log"], f"revealed material '{m['name'] or '?'}' "
                                   f"(shader -> {repl_name}, textures kept)")
                continue
            repl_name = _neuter_material_tree(tree, m.get("replacement"), externals,
                                              cat, by_pid)
            if repl_name is None:
                _log(stats["log"], f"material '{m['name'] or '?'}' left visible "
                                   f"(no usable replacement shader in scope)")
                continue
            try:
                obj.save_typetree(tree)
            except Exception as e:  # noqa: BLE001
                stats["log"].append(f"! material '{m['name']}' save failed: {e!r}")
                continue
            changed = True
            stats["materials_neutered"] += 1
            _log(stats["log"], f"neutered material '{m['name'] or '?'}' "
                               f"(shader -> {repl_name}; {'; '.join(m['evidence'])})")
    return changed


def _read_pairs(saved: dict, key: str):
    """Saved-properties pairs as {name: (index, value)}. UnityPy returns the
    (name, value) pairs either as 2-tuples (Unity 2021+ typetrees — the real
    game) or {'first','second'} dicts (older), tracked so the write-back can
    mirror the shape it read."""
    out = {}
    for i, pair in enumerate(saved.get(key) or []):
        if isinstance(pair, dict) and "first" in pair:
            out[pair["first"]] = (i, pair["second"], "dict", pair)
        elif isinstance(pair, (tuple, list)) and len(pair) == 2:
            out[pair[0]] = (i, pair[1], "tuple", pair)
    return out


def _pairs_shape(pairs: dict) -> str:
    """The serialized pair shape of an existing list ('tuple'|'dict'); new
    Unity types serialize as tuples, so that is the default for empty lists."""
    for _n, (_i, _v, shape, _o) in pairs.items():
        return shape
    return "tuple"


def _write_pairs(saved: dict, key: str, pairs: dict) -> None:
    """Write {name: (index, value, shape, original)} back in the read shape,
    original list order for existing entries, new entries appended."""
    out = [None] * len(saved.get(key) or [])
    appended = []
    for name, (i, val, shape, _orig) in pairs.items():
        item = {"first": name, "second": val} if shape == "dict" else (name, val)
        if i < len(out):
            out[i] = item
        else:
            appended.append(item)
    saved[key] = [x for x in out if x is not None] + appended


def _retarget_shader(tree, replacement, externals, cat):
    """Point a Material typetree's m_Shader at `replacement`
    ((file_key, pid, name)). Returns the shader's name, or None when this
    file has no reference path to it (same file or an external entry)."""
    if not replacement:
        return None
    repl_file_key, repl_pid, repl_name = replacement
    fid = 0
    if repl_file_key != cat["file"]:
        base = os.path.basename(repl_file_key.split("#")[0]).lower()
        fid = next((i + 1 for i, n in enumerate(externals) if n == base), 0)
        if not fid:
            return None  # this file has no external reference to that shader
    tree["m_Shader"] = {"m_FileID": fid, "m_PathID": repl_pid}
    return repl_name


def _neuter_material_tree(tree, replacement, externals, cat, by_pid):
    """Rewrite `tree` (a Material typetree) to render nothing. Returns the
    replacement shader's name, or None when it cannot be applied."""
    repl_name = _retarget_shader(tree, replacement, externals, cat)
    if repl_name is None:
        return None

    saved = tree.get("m_SavedProperties") or {}
    saved["m_TexEnvs"] = []

    floats = _read_pairs(saved, "m_Floats")
    fshape = _pairs_shape(floats)
    for name, val in (("_Surface", 1.0), ("_Blend", 0.0), ("_ZWrite", 0.0)):
        entry = floats.get(name)
        floats[name] = (entry[0], val, entry[2], entry[3]) if entry else \
            (len(floats), val, fshape, None)
    _write_pairs(saved, "m_Floats", floats)

    colors = _read_pairs(saved, "m_Colors")
    cshape = _pairs_shape(colors)
    for name in ("_BaseColor", "_Color"):
        zero = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 0.0}
        entry = colors.get(name)
        colors[name] = (entry[0], zero, entry[2], entry[3]) if entry else \
            (len(colors), zero, cshape, None)
    _write_pairs(saved, "m_Colors", colors)
    tree["m_SavedProperties"] = saved

    # Transparent-surface switch: URP-era materials carry keyword LISTS
    # (m_ValidKeywords), older ones the m_ShaderKeywords string — set both
    # when the field exists, skip silently when neither does.
    found_kw = False
    if isinstance(tree.get("m_ValidKeywords"), list):
        if "_SURFACE_TYPE_TRANSPARENT" not in tree["m_ValidKeywords"]:
            tree["m_ValidKeywords"].append("_SURFACE_TYPE_TRANSPARENT")
        found_kw = True
    if "m_ShaderKeywords" in tree:
        kw = str(tree.get("m_ShaderKeywords") or "")
        if "_SURFACE_TYPE_TRANSPARENT" not in kw:
            tree["m_ShaderKeywords"] = (kw + " _SURFACE_TYPE_TRANSPARENT").strip()
        found_kw = True
    if not found_kw:
        tree["m_ValidKeywords"] = ["_SURFACE_TYPE_TRANSPARENT"]
    return repl_name


def _is_renderer(obj) -> bool:
    return getattr(getattr(obj, "type", None), "name", "") in _RENDERER_TYPES


def _hide_renderer(robj) -> bool:
    """m_Enabled=0 + m_Materials=[] on a renderer object. True when written."""
    try:
        tree = robj.read_typetree()
    except Exception:  # noqa: BLE001
        return False
    if not tree.get("m_Enabled", 0) and not tree.get("m_Materials"):
        return False  # already invisible, nothing to write
    tree["m_Enabled"] = 0
    tree["m_Materials"] = []
    try:
        robj.save_typetree(tree)
        return True
    except Exception:  # noqa: BLE001
        return False


def _log(log: list, line: str) -> None:
    if len(log) < _LOG_CAP:
        log.append(line)
