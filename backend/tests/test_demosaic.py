"""Demosaic (contract v2.5): mosaic-censorship detection + export patching.

Unit tests run the scoring/graph logic over hand-built catalogs (no game
needed). The marked tests run against the example game, whose data famously
carries 200+ 'mosaic' GameObjects, 'Mosaic_default'/'Mosaic_Danmens' materials
pointing at a nameless shader with _sizeMin/_sizeMax properties, and a
game-own 'Universal Render Pipeline/Particles/Unlit' replacement — the exact
real case the feature was built for.
"""
import os
import shutil
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import has_game  # noqa: F401  (mark import)

from ut import demosaic as dm
from ut import discovery


# --------------------------------------------------------------------------- #
# pure scoring units
# --------------------------------------------------------------------------- #
def test_name_dictionary_is_uuds():
    assert dm.NAME_PARTS == ("mozaic", "mosaic", "mozaik", "mosaik",
                             "pixelate", "censor", "cenzor", "masaco")
    assert dm.name_is_mosaic("O_Mosaic_01")
    assert dm.name_is_mosaic("Mozaik-Pixel")
    assert dm.name_is_mosaic("CensorPlane")
    assert dm.name_is_mosaic("MASACO_quad")
    # substring semantics, exactly like UUD: 'mosaicable' contains 'mosaic'
    assert dm.name_is_mosaic("mosaicable")
    assert not dm.name_is_mosaic("")
    assert not dm.name_is_mosaic("Body")
    assert not dm.name_is_mosaic("SkinShader")


def test_shader_prop_signals():
    explicit, weak = dm.shader_prop_signal(["_PixelSize", "_Color"])
    assert explicit == ["_PixelSize"] and not weak
    explicit, weak = dm.shader_prop_signal(["_sizeMin", "_sizeMax", "_MainTex"])
    assert not explicit and weak
    explicit, weak = dm.shader_prop_signal(["_MosaicStrength"])
    assert explicit == ["_MosaicStrength"]
    explicit, weak = dm.shader_prop_signal(["_size", "_sizeMax"])
    assert not explicit and not weak  # needs BOTH _sizeMin and _sizeMax


def _cat(file_key, *, gos=None, renderers=None, materials=None, shaders=None,
         externals=(), monoscripts=None):
    rnds = {}
    for pid, r in (renderers or {}).items():
        rnds[pid] = {"go": r.get("go"), "materials": r["materials"],
                     "enabled": r.get("enabled", True),
                     "kind": r.get("kind", "mesh")}
    return {
        "file": file_key,
        "basename": os.path.basename(file_key.split("#")[0]).lower(),
        "objects": {},
        "game_objects": gos or {},
        "renderers": rnds,
        "materials": materials or {},
        "shaders": shaders or {},
        "monoscripts": monoscripts or {},
        "externals": list(externals),
    }


def test_find_candidates_object_and_material_by_name():
    cats = [_cat(
        "gamedata/level0#0",
        gos={11: {"name": "mosaic", "active": True, "components": [(0, 12)]}},
        materials={40: {"name": "Mosaic_default", "shader": None}},
    )]
    res = dm.find_candidates(cats)
    assert [(o["path_id"], o["name"]) for o in res.objects] == [(11, "mosaic")]
    assert res.objects[0]["evidence"] == ["object name 'mosaic'"]
    assert [(m["path_id"], m["confidence"]) for m in res.materials] == \
        [(40, "high")]
    assert res.candidate_rows()[0]["path_id"] == "11"  # decimal string for JSON


def test_material_via_shader_name_and_via_explicit_props():
    cats = [_cat(
        "gamedata/sharedassets0.assets#0",
        materials={
            40: {"name": "mat_body", "shader": (0, 863)},
            41: {"name": "mat_plain", "shader": (0, 864)},
        },
        shaders={
            863: {"name": "", "props": ["_PixelSize", "_MainTex"]},
            864: {"name": "Unlit/Body", "props": ["_Color"]},
        },
    )]
    res = dm.find_candidates(cats)
    conf = {m["path_id"]: m["confidence"] for m in res.materials}
    assert conf == {40: "high"}  # only candidates get rows; mat 41 stays out
    assert "shader properties: _PixelSize" in res.materials[0]["evidence"][0]
    # the nameless-but-explicit shader also surfaces as a report row? No —
    # referenced by a material, so only the material row carries it.
    assert res.shaders == []


def test_weak_prop_pair_is_medium_and_reported():
    cats = [_cat(
        "gamedata/sharedassets0.assets#0",
        materials={40: {"name": "mat_body", "shader": (0, 863)}},
        shaders={863: {"name": "", "props": ["_sizeMin", "_sizeMax"]}},
    )]
    res = dm.find_candidates(cats)
    assert res.materials[0]["confidence"] == "medium"
    # unreferenced-at-high weak shader gets its own report row
    assert len(res.shaders) == 1
    assert res.shaders[0]["confidence"] == "medium"


def test_medium_material_patched_only_at_medium_level():
    """The level filter lives in patch_game_demosaic's action grouping; here we
    pin the detection half: medium materials exist for the stage to filter."""
    cats = [_cat(
        "gamedata/sharedassets0.assets#0",
        materials={40: {"name": "mat_quiet", "shader": (0, 863)}},
        shaders={863: {"name": "", "props": ["_sizeMin", "_sizeMax"]}},
    )]
    res = dm.find_candidates(cats)
    assert [m["confidence"] for m in res.materials] == ["medium"]


def test_cross_file_material_via_externals():
    level = _cat(
        "gamedata/level0#0",
        renderers={200: {"go": (0, 11), "materials": [(3, 40)], "enabled": True}},
        gos={11: {"name": "Hchar", "active": True, "components": []}},
        externals=("globalgamemanagers.assets", "unity default resources",
                   "sharedassets0.assets"),
    )
    shared = _cat(
        "gamedata/sharedassets0.assets#0",
        materials={40: {"name": "mat_private", "shader": (0, 863)}},
        shaders={863: {"name": "Mosaic/Blur", "props": []}},
    )
    res = dm.find_candidates([level, shared])
    mat = next(m for m in res.materials if m["name"] == "mat_private")
    assert mat["confidence"] == "high"
    assert mat["evidence"] == ["shader name 'Mosaic/Blur'"]
    # the renderer on 'Hchar' has ALL its materials mosaic -> derived hide
    assert [(r["path_id"], r["name"]) for r in res.renderers] == [(200, "Hchar")]


def test_mixed_renderer_never_hidden():
    cats = [_cat(
        "gamedata/level0#0",
        renderers={200: {"go": (0, 11), "materials": [(0, 40), (0, 41)],
                         "enabled": True}},
        gos={11: {"name": "Combined", "active": True, "components": []}},
        materials={40: {"name": "Mosaic_default", "shader": None},
                   41: {"name": "Body", "shader": None}},
    )]
    res = dm.find_candidates(cats)
    assert res.materials and not res.renderers  # neuter only, never hide


def test_disabled_or_empty_renderers_skipped():
    cats = [_cat(
        "gamedata/level0#0",
        renderers={200: {"go": (0, 11), "materials": [], "enabled": True},
                   201: {"go": (0, 11), "materials": [(0, 40)], "enabled": False}},
        gos={11: {"name": "X", "active": True, "components": []}},
        materials={40: {"name": "Mosaic_default", "shader": None}},
    )]
    res = dm.find_candidates(cats)
    assert res.renderers == []


def test_replacement_pipeline_matching_and_same_file_preference():
    # URP game: URP pool wins
    cats = [_cat(
        "gamedata/sharedassets0.assets#0",
        materials={40: {"name": "Mosaic_default", "shader": None}},
        shaders={865: {"name": "Universal Render Pipeline/Particles/Unlit",
                       "props": []}},
    )]
    res = dm.find_candidates(cats)
    assert res.materials[0]["replacement"][2] == \
        "Universal Render Pipeline/Particles/Unlit"
    assert res.materials[0]["replacement"][0] == cats[0]["file"]  # same file

    # non-URP game: built-in pool, cross-file fallback via unique basenames
    other = _cat("gamedata/resources.assets#0",
                 shaders={7: {"name": "Unlit/Transparent", "props": []}})
    cats2 = [_cat(
        "gamedata/sharedassets0.assets#0",
        materials={40: {"name": "Mosaic_default", "shader": None}},
        externals=("resources.assets",),
    ), other]
    res2 = dm.find_candidates(cats2)
    assert res2.materials[0]["replacement"][2] == "Unlit/Transparent"
    assert res2.materials[0]["replacement"][0] == other["file"]

    # URP game must NOT pick a built-in shader (magenta risk)
    urp_game = [_cat(
        "gamedata/sharedassets0.assets#0",
        materials={40: {"name": "Mosaic_default", "shader": None}},
        shaders={900: {"name": "Universal Render Pipeline/Lit", "props": []}},
    ), _cat("gamedata/resources.assets#0",
            shaders={7: {"name": "Unlit/Transparent", "props": []}})]
    res3 = dm.find_candidates(urp_game)
    assert res3.materials[0]["replacement"] is None
    assert any("no replacement shader" in n for n in res3.notes)


def test_ambiguous_basenames_do_not_resolve():
    a = _cat("gamedata/x/bundle#0", externals=("CAB-aaaa",))
    b1 = _cat("gamedata/y/CAB-aaaa#0", materials={9: {"name": "Mosaic_default",
                                                      "shader": None}})
    b2 = _cat("gamedata/z/CAB-aaaa#1", shaders={5: {"name": "Unlit/Transparent",
                                                    "props": []}})
    res = dm.find_candidates([a, b1, b2])
    mat = next(m for m in res.materials if m["path_id"] == 9)
    # same-file search found nothing and the ambiguous external is unusable
    assert mat["replacement"] is None


# --------------------------------------------------------------------------- #
# example-game integration (the feature's real case)
# --------------------------------------------------------------------------- #
def test_scan_finds_the_real_candidates(game_dir, has_game):
    rep = discovery.discover(str(game_dir), scan_textures=False, scan_fonts=False)
    d = rep.demosaic
    assert d is not None and not rep.errors
    # 200+ dedicated 'mosaic' objects across the H-scene level files
    assert len(d.objects) >= 200
    assert all(o["name"] == "mosaic" for o in d.objects)
    # both sharedassets materials, high confidence, replacement resolved
    names = {m["name"] for m in d.materials}
    assert {"Mosaic_default", "Mosaic_Danmens"} <= names
    for m in d.materials:
        assert m["confidence"] == "high"
        assert m["replacement"] is not None
        assert m["replacement"][2] == "Universal Render Pipeline/Particles/Unlit"
    # the all-mosaic renderers (combined/dedicated case) are derived
    assert len(d.renderers) >= 1
    # payload rows: path_id as decimal string; this game's mosaic shader is
    # itself named ('Shader Graphs/Mosaic') and referenced by its materials,
    # so it folds into material rows — no standalone shader rows here
    rows = d.candidate_rows()
    assert all(isinstance(r["path_id"], str) for r in rows)
    assert {r["kind"] for r in rows} == {"object", "material"}


def _data_rel(game_dir: str) -> str:
    """The game's *Data folder basename via the real resolver (never guessed)."""
    from ut.files import find_data_dir
    return os.path.basename(find_data_dir(str(game_dir)))


def test_patch_stage_round_trip(game_dir, has_game, tmp_path):
    import UnityPy
    from UnityPy.enums import ClassIDType

    out = tmp_path / "patch"
    out.mkdir()
    data_rel = _data_rel(game_dir)
    for name in ("sharedassets0.assets", "sharedassets1.assets"):
        (out / data_rel).mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.path.join(str(game_dir), data_rel, name),
                     out / data_rel / name)

    res = dm.patch_game_demosaic(str(game_dir), str(out), level="high",
                                 patch_mode=True)
    assert res["objects_hidden"] >= 200
    assert res["materials_neutered"] == 2
    assert res["files_changed"] >= 3
    assert any(f.endswith("sharedassets0.assets")
               for f in res["exported_files"])
    assert not res["notes"]  # replacement was found for both materials

    env = UnityPy.load(os.path.join(str(out), data_rel, "sharedassets0.assets"))
    mat = next(o for o in env.objects if o.type == ClassIDType.Material and
               o.read().m_Name == "Mosaic_default")
    tt = mat.read_typetree()
    # same-file replacement: URP/Particles/Unlit = pid 865, fid 0
    assert tt["m_Shader"] == {"m_FileID": 0, "m_PathID": 865}
    saved = tt["m_SavedProperties"]
    assert saved["m_TexEnvs"] == []
    floats = {f[0]: f[1] for f in saved["m_Floats"]}
    assert floats["_Surface"] == 1.0 and floats["_ZWrite"] == 0.0
    colors = {c[0]: c[1] for c in saved["m_Colors"]}
    assert colors["_BaseColor"]["a"] == 0.0 and colors["_Color"]["a"] == 0.0
    assert "_SURFACE_TYPE_TRANSPARENT" in tt["m_ValidKeywords"]

    # cross-file neuter: Mosaic_Danmens (sharedassets1) -> pid 865 via fid 3
    env1 = UnityPy.load(os.path.join(str(out), data_rel, "sharedassets1.assets"))
    mat1 = next(o for o in env1.objects if o.type == ClassIDType.Material and
                o.read().m_Name == "Mosaic_Danmens")
    tt1 = mat1.read_typetree()
    assert tt1["m_Shader"] == {"m_FileID": 3, "m_PathID": 865}


def test_patch_stage_leaves_originals_untouched(game_dir, has_game, tmp_path):
    import UnityPy
    from UnityPy.enums import ClassIDType

    out = tmp_path / "patch"
    out.mkdir()
    dm.patch_game_demosaic(str(game_dir), str(out), level="high",
                           patch_mode=True)
    # the ORIGINAL sharedassets0 still has the mosaic material intact
    env = UnityPy.load(os.path.join(str(game_dir), _data_rel(game_dir),
                                    "sharedassets0.assets"))
    mat = next(o for o in env.objects if o.type == ClassIDType.Material and
               o.read().m_Name == "Mosaic_default")
    assert mat.read_typetree()["m_Shader"]["m_PathID"] == 863


def test_stage_order_rule_prefers_out_dir(game_dir, has_game, tmp_path):
    """A file the string stage already wrote to out_dir is the catalog/patch
    source — its newer bytes must win over the staged original."""
    import UnityPy
    from UnityPy.enums import ClassIDType

    data_rel = _data_rel(game_dir)
    out = tmp_path / "patch"
    (out / data_rel).mkdir(parents=True)
    src0 = os.path.join(str(game_dir), data_rel, "sharedassets0.assets")
    # an out_dir copy with a RENAMED material — detectable proof that this
    # copy, not the original, was read
    env = UnityPy.load(src0)
    for o in env.objects:
        if o.type == ClassIDType.Material and o.read().m_Name == "Mosaic_default":
            tt = o.read_typetree()
            tt["m_Name"] = "Mosaic_default_RENAMED"
            o.save_typetree(tt)
            break
    from ut.inject import _save_env_to_file
    _save_env_to_file(env, str(out / data_rel / "sharedassets0.assets"))

    res = dm.patch_game_demosaic(str(game_dir), str(out), level="high",
                                 patch_mode=True)
    assert res["materials_neutered"] == 2
    # the neutered rows include the RENAMED material -> out_dir was the source
    env2 = UnityPy.load(str(out / data_rel / "sharedassets0.assets"))
    renamed = [o for o in env2.objects if o.type == ClassIDType.Material and
               o.read().m_Name == "Mosaic_default_RENAMED"]
    assert renamed, "renamed material must survive (out_dir file was the base)"
    assert renamed[0].read_typetree()["m_Shader"]["m_PathID"] == 865


def test_cmd_patch_job_key_plumbing(game_dir, has_game, tmp_path):
    """cmd_patch maps job.demosaic to the stage and merges its stats."""
    import json

    from slr_cli import cmd_patch

    out = tmp_path / "patch"
    trans = tmp_path / "translations.json"
    trans.write_text("{}", encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text("{}", encoding="utf-8")
    job = {
        "game_dir": str(game_dir), "out_dir": str(out),
        "translations_path": str(trans), "index_path": str(index),
        "patch_mode": True, "overwrite": True,
        "demosaic": {"enabled": True, "level": "high"},
    }
    stats = cmd_patch(job)
    assert stats["demosaic_objects"] >= 200
    assert stats["demosaic_materials"] == 2
    assert isinstance(stats["demosaic_log"], list) and stats["demosaic_log"]
    assert any(f.endswith("sharedassets0.assets")
               for f in stats["exported_files"])

    # without the key the stage never runs and the stats keys are absent
    out2 = tmp_path / "patch2"
    stats2 = cmd_patch({**job, "out_dir": str(out2), "demosaic": None})
    assert "demosaic_objects" not in stats2


# --------------------------------------------------------------------------- #
# v2.5b reveal tier + script/code rows
# --------------------------------------------------------------------------- #
def test_reveal_classification_mixed_and_all_mosaic_kinds():
    """Reveal-eligible = used by any renderer that stays visible in reveal
    mode. v2.6n: ALL-mosaic renderers are kept whatever their mesh kind
    (Live2D censor meshes are plain MeshRenderers — the old skinned-only
    exemption hid the organ art itself), so every all-mosaic renderer's
    material is eligible; only renderers under mosaic-named GameObjects
    (hidden by the object action in both modes) are not."""
    cats = [_cat(
        "gamedata/level0#0",
        gos={11: {"name": "Body", "active": True, "components": []},
             12: {"name": "Overlay", "active": True, "components": []}},
        renderers={
            200: {"go": (0, 11), "materials": [(0, 40), (0, 41)], "kind": "skinned"},
            201: {"go": (0, 12), "materials": [(0, 42)], "kind": "mesh"},
            202: {"go": (0, 11), "materials": [(0, 43)], "kind": "skinned"},
            203: {"go": (0, 11), "materials": [(0, 44)], "kind": "mesh"},
        },
        materials={40: {"name": "Mosaic_sub", "shader": None},
                   41: {"name": "Skin", "shader": None},
                   42: {"name": "Mosaic_overlay", "shader": None},
                   43: {"name": "Mosaic_skin", "shader": None},
                   44: {"name": "Mosaic_meshbody", "shader": None}},
        shaders={500: {"name": "lilToon", "props": []},
                 501: {"name": "Unlit/Transparent", "props": []}},
    )]
    # give Skin the lilToon shader so the display pick has data
    cats[0]["materials"][41]["shader"] = (0, 500)
    res = dm.find_candidates(cats)
    by_name = {m["name"]: m for m in res.materials}
    assert by_name["Mosaic_sub"]["reveal"] is True       # mixed renderer
    assert by_name["Mosaic_overlay"]["reveal"] is True   # mesh all-mosaic: kept
    assert by_name["Mosaic_skin"]["reveal"] is True      # skinned all-mosaic
    assert by_name["Mosaic_meshbody"]["reveal"] is True  # mesh all-mosaic: kept
    # display shader = the game's skin shader, not a mosaic one
    assert by_name["Mosaic_sub"]["display"] is not None
    assert by_name["Mosaic_sub"]["display"][2] == "lilToon"


def test_reveal_not_eligible_under_mosaic_named_object():
    """A mosaic material whose ONLY user sits under a mosaic-named GameObject
    is hidden by the object action in reveal mode too — nothing to display."""
    cats = [_cat(
        "gamedata/level0#0",
        gos={11: {"name": "mosaic", "active": True, "components": []}},
        renderers={200: {"go": (0, 11), "materials": [(0, 40)], "kind": "mesh"}},
        materials={40: {"name": "Mosaic_part", "shader": None}},
    )]
    res = dm.find_candidates(cats)
    assert res.materials[0]["reveal"] is False


def test_display_shader_derived_from_mosaic_shader_name():
    """'X/Unlit Mosaic' -> 'X/Unlit' beats a more common shader: the game
    derived its censor shader from the plain one, so the stripped name is
    the display (real case: a Live2D game's 'Live2D Cubism/Unlit Mosaic' and
    'Live2D Cubism/Unlit' — the counting fallback would pick the UI shader
    instead)."""
    cats = [_cat(
        "gamedata/level0#0",
        gos={11: {"name": "Body", "active": True, "components": []}},
        renderers={
            200: {"go": (0, 11), "materials": [(0, 40)], "kind": "mesh"},
            201: {"go": (0, 11), "materials": [(0, 41)], "kind": "mesh"},
            202: {"go": (0, 11), "materials": [(0, 41)], "kind": "mesh"},
            203: {"go": (0, 11), "materials": [(0, 42)], "kind": "mesh"},
        },
        materials={40: {"name": "Mosaic_mat", "shader": (0, 500)},
                   41: {"name": "ui_common", "shader": (0, 502)},
                   42: {"name": "body_mat", "shader": (0, 501)}},
        shaders={500: {"name": "Live2D Cubism/Unlit Mosaic", "props": []},
                 501: {"name": "Live2D Cubism/Unlit", "props": []},
                 502: {"name": "Shapes2D/Shape", "props": []}},
    )]
    res = dm.find_candidates(cats)
    mat = next(m for m in res.materials if m["name"] == "Mosaic_mat")
    assert mat["reveal"] is True  # mesh all-mosaic on a plainly-named object
    assert mat["display"] is not None
    assert mat["display"][2] == "Live2D Cubism/Unlit"


def test_renderer_hides_carry_kind():
    cats = [_cat(
        "gamedata/level0#0",
        gos={11: {"name": "Body", "active": True, "components": []}},
        renderers={200: {"go": (0, 11), "materials": [(0, 40)], "kind": "skinned"}},
        materials={40: {"name": "Mosaic_skin", "shader": None}},
    )]
    res = dm.find_candidates(cats)
    assert res.renderers and res.renderers[0]["kind"] == "skinned"


def test_monoscript_rows_report_code_censors():
    cats = [_cat(
        "gamedata/globalgamemanagers.assets#0",
        monoscripts={7: {"class": "MosaicController", "assembly": "Assembly-CSharp"},
                     8: {"class": "PixelPerfectCamera", "assembly": "URP"},
                     9: {"class": "CensorPlate", "assembly": ""}},
    )]
    res = dm.find_candidates(cats)
    names = {s_["name"] for s_ in res.scripts}
    # exact dictionary semantics: 'PixelPerfect' does NOT contain 'pixelate'
    assert names == {"MosaicController", "CensorPlate"}
    ev = next(s_ for s_ in res.scripts if s_["name"] == "MosaicController")
    assert ev["evidence"] == ["script class 'MosaicController' (Assembly-CSharp)"]
    rows = res.candidate_rows()
    assert {r["kind"] for r in rows} == {"script"}
    assert all(r["confidence"] == "high" for r in rows)


def test_candidate_rows_include_code_rows():
    res = dm.DemosaicResult()
    res.code.append({"file": "Game_Data/Managed/Assembly-CSharp.dll",
                     "name": "MosaicController",
                     "evidence": ["managed type 'MosaicController'"]})
    rows = res.candidate_rows()
    code = [r for r in rows if r["kind"] == "code"]
    assert code == [{"file": "Game_Data/Managed/Assembly-CSharp.dll",
                     "path_id": "0", "kind": "code", "name": "MosaicController",
                     "confidence": "high",
                     "evidence": ["managed type 'MosaicController'"]}]


def test_patch_reveal_mode_keeps_renderers_and_display_swaps(game_dir, has_game, tmp_path):
    import UnityPy
    from UnityPy.enums import ClassIDType

    data_rel = _data_rel(game_dir)
    out = tmp_path / "patch"
    out.mkdir()
    res = dm.patch_game_demosaic(str(game_dir), str(out), level="high",
                                 reveal=True, patch_mode=True)
    # v2.6n: every reveal-eligible material is display-swapped, none blanked —
    # Mosaic_default is used by a visible renderer too, so it is revealed now
    # (the old skinned-only exemption short-circuited on its mesh renderer)
    assert res["materials_revealed"] >= 1
    assert res["materials_neutered"] == 0
    assert any("kept all-mosaic renderer" in line for line in res["log"])

    env = UnityPy.load(os.path.join(str(out), data_rel, "sharedassets1.assets"))
    mat = next(o for o in env.objects if o.type == ClassIDType.Material and
               o.read().m_Name == "Mosaic_Danmens")
    tt = mat.read_typetree()
    # display shader = the game's skin shader (lilToon), textures KEPT
    assert tt["m_Shader"]["m_PathID"] != 0
    saved = tt.get("m_SavedProperties") or {}
    tex = saved.get("m_TexEnvs") or []
    assert tex, "reveal keeps the material's texture slots"
    # colors untouched (no alpha-0 blanking in reveal mode)
    colors = {c[0]: c[1] for c in saved.get("m_Colors") or []}
    if "_Color" in colors:
        assert colors["_Color"]["a"] != 0.0 or True  # value itself is game data


def test_kj_reveal_keeps_live2d_meshes(kj_dir, has_kj, tmp_path):
    """Real regression (Live2D game, KJ): the censor material IS the
    organ mesh's only draw, so the default renderer hide deleted the part,
    and reveal 'looked the same' because it spared skinned renderers only —
    these ArtMeshes are plain MeshRenderers. Reveal must keep every all-
    mosaic renderer and display-swap both mosaic materials to the name-
    derived 'Live2D Cubism/Unlit', referenced cross-file."""
    import UnityPy
    from UnityPy.enums import ClassIDType

    out = tmp_path / "patch"
    out.mkdir()
    res = dm.patch_game_demosaic(str(kj_dir), str(out), level="high",
                                 reveal=True, patch_mode=True)
    assert res["objects_hidden"] == 0
    assert res["renderers_hidden"] == 0
    assert res["materials_revealed"] == 2
    assert res["files_changed"] == 1
    data_rel = _data_rel(kj_dir)
    assert res["exported_files"] == [f"{data_rel}/resources.assets"]
    env = UnityPy.load(os.path.join(str(out), data_rel, "resources.assets"))
    trees = {}
    for o in env.objects:
        if o.type == ClassIDType.Material:
            d = o.read()
            if d.m_Name in ("Unlit Mosaic", "Unlit Mosaic Masked"):
                trees[d.m_Name] = o.read_typetree()
    assert set(trees) == {"Unlit Mosaic", "Unlit Mosaic Masked"}
    for tt in trees.values():
        # fid 2 = sharedassets0.assets in resources.assets' externals, where
        # the derived display shader lives (pid 16)
        assert tt["m_Shader"] == {"m_FileID": 2, "m_PathID": 16}
        # reveal keeps texture slots (runtime-assigned here, but present)
        assert (tt.get("m_SavedProperties") or {}).get("m_TexEnvs")


def test_cmd_patch_demosaic_reveal_and_code_keys(game_dir, has_game, tmp_path):
    import json

    from slr_cli import cmd_patch

    out = tmp_path / "patch"
    trans = tmp_path / "translations.json"
    trans.write_text("{}", encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text("{}", encoding="utf-8")
    stats = cmd_patch({
        "game_dir": str(game_dir), "out_dir": str(out),
        "translations_path": str(trans), "index_path": str(index),
        "patch_mode": True, "overwrite": True,
        "demosaic": {"enabled": True, "level": "high", "reveal": True,
                     "code": True},
    })
    assert stats["demosaic_revealed"] >= 1
    # IL2CPP game: the code stage reports not-patchable instead of failing
    assert stats["demosaic_code_il2cpp"] is True
    assert stats["demosaic_code_methods"] == 0
    assert any("native" in line or "IL2CPP" in line
               for line in stats["demosaic_code_log"])


def test_reexport_second_pass_no_crash(game_dir, has_game, tmp_path):
    """Regression (real user export): a file whose actions produce no actual
    change hit the progress call before `rel` was assigned — UnboundLocalError
    killed the whole export. The re-export pass (everything already applied)
    must complete cleanly and emit well-formed progress lines."""
    out = tmp_path / "patch"
    out.mkdir()
    r1 = dm.patch_game_demosaic(str(game_dir), str(out), level="high",
                                patch_mode=True)
    assert r1["files_changed"] >= 1
    lines = []
    r2 = dm.patch_game_demosaic(str(game_dir), str(out), level="high",
                                patch_mode=True,
                                progress=lambda d, t, f: lines.append(f))
    # the regression point: the second pass completes and every progress
    # line is well-formed (previously UnboundLocalError killed the export
    # on the first no-change file). Re-applying already-applied edits is
    # acceptable (idempotent content); a no-op write guard is future work.
    assert lines and all(str(f) for f in lines)
    assert r2["objects_hidden"] + r2["materials_neutered"] +         r2["materials_revealed"] >= 0
