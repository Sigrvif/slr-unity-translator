"""SLRUnity backend CLI — manifest/result JSON envelope.

All paths travel inside the manifest file (non-ASCII-safe); progress is
one JSON object per line on stdout; logs go to backend.log next to the
manifest. See spec §2.2.

The JS addon spawns:  python slr_cli.py --manifest job.json --result result.json
and reads result.json for the outcome plus stdout for progress lines.
Anything a library prints to stdout mid-run is swallowed into the log so the
addon's line-oriented JSON stream stays clean.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from dataclasses import asdict

# Embedded-runtime support (Task 6 packaging): the Windows embeddable Python
# bundled in the .tap ships with a python310._pth file, which forces isolated
# mode — the script's own directory is then NOT on sys.path and `import ut`
# below would raise ModuleNotFoundError. Re-adding it explicitly is a no-op
# under a normal interpreter (dev / backend tests).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

CONTRACT_VERSION = 2

# The real stdout, captured at import: library prints are redirected into the
# log while a command runs, so progress lines are the ONLY stdout output.
_REAL_STDOUT = sys.stdout


def _emit(obj: dict) -> None:
    if _REAL_STDOUT is None:  # e.g. pythonw — nothing to report to
        return
    _REAL_STDOUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _REAL_STDOUT.flush()


def _progress(done: int, total: int, rel: str) -> None:
    _emit({"type": "progress", "done": done, "total": total, "file": rel})


class _SwallowStdout:
    """sys.stdout replacement used while a command runs: every library
    print/warning lands in the log instead of polluting the JSON stream."""

    def __init__(self, log):
        self._log = log

    def write(self, s):
        if s and s.strip():
            self._log.write(s if s.endswith("\n") else s + "\n")
        return len(s)

    def flush(self):
        self._log.flush()

    def isatty(self):
        return False


def _json_safe_obj(d: dict) -> dict:
    """Make a hit dataclass dict survive a JS JSON.parse round-trip.

    Unity path ids are int64 and routinely exceed 2^53 (JS Number's exact
    range); as JSON numbers they are SILENTLY rounded by JSON.parse (real
    case: 103263707529466115 -> ...110), after which no patch edit can ever
    match the object again. Every path_id crossing into the JS layer travels
    as a decimal string; the patch side converts back with int().
    """
    if "path_id" in d:
        d["path_id"] = str(d["path_id"])
    return d


def cmd_scan(job: dict) -> dict:
    from ut import discovery
    rep = discovery.discover(job["game_dir"], progress=_progress)
    occ = {}
    for h in rep.strings:
        # `risk` (v2.8): the hit's SLR risk tag — the JS project builder ranks
        # it per unique text into SLR's per-row tags (red > yellow > "" >
        # green, then the no-Japanese variants gray/gold/darkred).
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, job["game_dir"]).replace("\\", "/"),
            "abs": h.file,
            "path_id": str(h.path_id), "source": h.source, "offset": h.offset,
            "length": h.length, "key": h.key, "tokens": h.tokens, "risk": h.risk})
    return {
        "strings": [_json_safe_obj(asdict(h)) | {"file": ""} for h in rep.strings],  # file lives in the index; keep the list light
        "index": occ,
        "fonts": [_json_safe_obj(asdict(f)) for f in rep.fonts],
        "textures": [_json_safe_obj(asdict(t)) for t in rep.textures],
        "text_assets": [_json_safe_obj(asdict(t)) | {"file": os.path.relpath(t.file, job["game_dir"]).replace("\\", "/")}
                        for t in rep.text_assets],
        "mono": rep.mono_report,   # spec §3.3: [{dll, count, samples}] report-only
        "suspect": rep.suspect,
        # Demosaic candidates (contract v2.5): mosaic censorship layers found
        # statically. Rows carry the game-relative `file` (the staging set is
        # derived from them in the JS layer) and path_id as a decimal string
        # like every other row; the payload side strips the per-serialized-
        # file '#index' suffix of the internal catalog keys.
        "demosaic": [
            {**row, "file": (os.path.relpath(row["file"].split("#")[0],
                                             job["game_dir"]).replace("\\", "/")
                             if os.path.isabs(row["file"].split("#")[0])
                             else row["file"].split("#")[0])}
            for row in (rep.demosaic.candidate_rows()
                        if rep.demosaic is not None else [])
        ],
        "stats": {"files_scanned": rep.files_scanned, "hits": len(rep.strings),
                  "unique": len(occ),
                  # I3: the FULL error list travels with the payload so
                  # silent partial failures surface to the JS layer.
                  "errors": list(rep.errors)},
    }


def _resolve_index_abs(index: dict, game_dir: str) -> dict:
    """Make every occurrence's `abs` path valid for THIS machine: keep it when
    the file exists, else rebuild from game_dir + the relative `file` (the
    index may have been produced on another machine / user account)."""
    for occs in index.values():
        for o in occs:
            if not os.path.isfile(o.get("abs") or ""):
                rel = o.get("file") or ""
                if rel:
                    cand = os.path.normpath(os.path.join(game_dir, rel))
                    if os.path.isfile(cand):
                        o["abs"] = cand
    return index


def cmd_patch(job: dict) -> dict:
    import tempfile

    from ut import fonts, inject, textures
    from ut.files import assert_not_nested
    # v2: patch_mode skips the whole-game copy — out_dir receives ONLY the
    # files actually modified this run (whole-file replacements at their
    # game-relative paths), so the JS layer can stage/zip just those.
    patch_mode = bool(job.get("patch_mode"))
    # e2e-fix13: optional dir to discover/patch fonts from when it differs
    # from game_dir — the ORIGINAL game (the staged copy only carries the
    # string-bearing files, so a staged-only font scan misses fonts living in
    # string-less files, e.g. resources.assets). Only font-bearing files are
    # written to out_dir, and the source tree is never touched. Guard fires
    # BEFORE any stage writes (the font stage would only raise after the
    # strings had already landed in out_dir).
    font_source_dir = job.get("font_source_dir") or None
    if font_source_dir:
        assert_not_nested(font_source_dir, job["out_dir"], "out_dir")
    # e2e-fix19: optional TMP atlas re-baking stage. job "font_bake" maps asset
    # roles to TTFs: {"latin_ttf": abs, "cjk_ttf": abs} — assets whose ORIGINAL
    # baked charset contains CJK code points bake from cjk_ttf, others from
    # latin_ttf. Fail fast on missing fonts, BEFORE any stage writes.
    font_bake = job.get("font_bake") or None
    if font_bake:
        for key in ("latin_ttf", "cjk_ttf"):
            path = font_bake.get(key)
            if not path or not os.path.isfile(path):
                raise FileNotFoundError(f"font_bake.{key} not found: {path!r}")
    with open(job["translations_path"], encoding="utf-8") as f:
        raw_translations = json.load(f)
    # M2: blank/empty translations can never land — filter them out before
    # patch_game (mirrors inject.load_translations' blank-skipping).
    translations = {
        jp: en for jp, en in raw_translations.items()
        if jp and isinstance(en, str) and en.strip()
    }
    with open(job["index_path"], encoding="utf-8") as f:
        index = _resolve_index_abs(json.load(f), job["game_dir"])
    # patch_game takes an index FILE; hand it the normalized copy (valid abs
    # paths for this machine) via a temp file.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        json.dump(index, tf, ensure_ascii=False)
        norm_index_path = tf.name
    try:
        stats = inject.patch_game(job["game_dir"], job["out_dir"], translations,
                                  norm_index_path, overwrite=True, progress=_progress,
                                  patch_mode=patch_mode)
    finally:
        os.unlink(norm_index_path)
    exported = list(stats.get("exported_files", []))
    if job.get("font_ttf"):
        if patch_mode:
            # e2e-fix13: fonts come from font_source_dir (the ORIGINAL game,
            # complete catalog) when provided, else from game_dir (staged —
            # the pre-fix13 behavior, kept for projects without the original).
            # Either way only font-bearing files land in out_dir.
            font_src = font_source_dir or job["game_dir"]
            latin_scale = float(job.get("font_latin_scale", 1.0) or 1.0)
            rep = fonts.replace_source_font(
                font_src, job["out_dir"], job["font_ttf"], patch_mode=True,
                latin_scale=latin_scale)
        else:
            font_src = job["out_dir"]
            latin_scale = float(job.get("font_latin_scale", 1.0) or 1.0)
            rep = fonts.replace_source_font(job["out_dir"], job["out_dir"], job["font_ttf"],
                                            in_place=True, latin_scale=latin_scale)
        stats["fonts_swapped"] = rep.get("fonts_replaced", 0)
        exported += rep.get("exported_files", [])
        # e2e-fix18 REVERT of the e2e-fix14 TMP stage: converting this game's
        # TMP_FontAssets to Dynamic rendered NO text in the player (three
        # controlled exports: dynamic-converted assets render blank while
        # static ones show their baked glyphs — this TMP build does not
        # re-rasterize from the source font at runtime). The stage remains
        # available for opt-in experiments via "tmp_dynamic": true in the job
        # manifest, but is OFF by default so exports keep working text.
        if job.get("tmp_dynamic"):
            from ut import tmpfont
            tmp_rep = tmpfont.make_game_fonts_dynamic(
                font_src, job["out_dir"],
                patch_mode=patch_mode, in_place=not patch_mode)
            stats["tmp_assets_made_dynamic"] = tmp_rep.get("made_dynamic", 0)
            stats["tmp_assets_skipped_no_source"] = tmp_rep.get("skipped_no_source", 0)
            exported += tmp_rep.get("exported_files", [])
    # e2e-fix19 TMP ATLAS RE-BAKE: replace the baked glyph/character tables and
    # atlas pixels of static TMP_FontAssets with ones generated from the chosen
    # fonts, so the game renders text in the new font WITHOUT runtime
    # re-rendering (the dynamic path is dead for this player — e2e-fix18).
    # Runs AFTER the legacy font swap and builds on its out_dir files
    # (stage-order rule). Extra charset: every unique character of the
    # translated strings, so translated text is always covered by the baked
    # glyphs (capped to keep tiny atlases viable).
    if font_bake:
        from ut import fontbake
        extra = "".join(sorted({ch for s in translations.values() for ch in s}))[:1500]
        bake_src = (font_source_dir or job["game_dir"]) if patch_mode else job["out_dir"]
        bake_rep = fontbake.bake_game_fonts(
            bake_src, job["out_dir"], font_bake["latin_ttf"], font_bake["cjk_ttf"],
            extra_chars=extra, patch_mode=patch_mode, in_place=not patch_mode,
            progress=_progress)
        stats["fonts_baked"] = bake_rep.get("fonts_baked", 0)
        stats["fonts_bake_failed"] = bake_rep.get("bake_failed", 0)
        stats["atlas_textures_replaced"] = bake_rep.get("atlas_textures_replaced", 0)
        exported += bake_rep.get("exported_files", [])
    if job.get("textures_dir") and job.get("textures_manifest_dir"):
        if patch_mode:
            res = textures.import_textures(job["game_dir"], job["out_dir"],
                                           job["textures_manifest_dir"],
                                           png_dir=job["textures_dir"], patch_mode=True)
        else:
            res = textures.import_textures(job["out_dir"], job["out_dir"],
                                           job["textures_manifest_dir"],
                                           png_dir=job["textures_dir"], in_place=True)
        stats["textures_imported"] = res.get("imported", 0)
        exported += res.get("exported_files", [])
    # Demosaic stage (contract v2.5, opt-in): hide/neutralize the game's
    # mosaic censorship layers in the export output. Runs LAST and follows
    # the stage-order rule (reads out_dir/<rel> where an earlier stage of
    # this run already wrote it), so string/font edits survive. Full-copy
    # mode edits the copied tree in place.
    demosaic = job.get("demosaic") or None
    if demosaic and demosaic.get("enabled"):
        from ut import demosaic as _demosaic
        dres = _demosaic.patch_game_demosaic(
            job["game_dir"], job["out_dir"],
            level=("medium" if demosaic.get("level") == "medium" else "high"),
            reveal=bool(demosaic.get("reveal")),
            patch_mode=patch_mode, in_place=not patch_mode, progress=_progress)
        stats["demosaic_objects"] = dres["objects_hidden"]
        stats["demosaic_renderers"] = dres["renderers_hidden"]
        stats["demosaic_materials"] = dres["materials_neutered"]
        stats["demosaic_revealed"] = dres["materials_revealed"]
        stats["demosaic_files"] = dres["files_changed"]
        stats["demosaic_log"] = dres["log"]
        exported += dres["exported_files"]
        # deep opt-in: stub mosaic-named Behaviour types in the managed DLLs
        # (Mono games; IL2CPP reports not-patchable). Reads the staged tree
        # (code rows joined the staging set) and follows the stage-order rule.
        if demosaic.get("code"):
            from ut import monopatch as _monopatch
            cres = _monopatch.patch_managed_code(
                job["game_dir"], job["out_dir"],
                patch_mode=patch_mode, in_place=not patch_mode)
            stats["demosaic_code_types"] = cres["code_types"]
            stats["demosaic_code_methods"] = cres["code_methods"]
            stats["demosaic_code_files"] = cres["code_files"]
            stats["demosaic_code_log"] = cres["code_log"]
            stats["demosaic_code_il2cpp"] = cres["code_il2cpp"]
            exported += cres["exported_files"]
    # Text-fit calibration (contract v2.6, opt-in advanced): recalibrate
    # char-count window sizing in managed DLLs for Latin text. Reads the
    # staged game and follows the stage-order rule like the other stages.
    textfit = job.get("textfit") or None
    if textfit and textfit.get("enabled"):
        from ut.monopatch import calibrate_text_sizing
        try:
            tres = calibrate_text_sizing(
                job["game_dir"], job["out_dir"],
                char_ratio=float(textfit.get("char_ratio", 0.5)),
                swing_scale=float(textfit.get("swing_scale", 0.25)),
                patch_mode=patch_mode, in_place=not patch_mode,
                progress=_progress)
            stats["sizing_rewritten"] = tres["sizing_rewritten"]
            stats["sizing_sites"] = tres["sizing_sites"]
            stats["sizing_files"] = tres["sizing_files"]
            stats["sizing_log"] = tres["sizing_log"]
            exported += tres["exported_files"]
        except Exception as e:  # noqa: BLE001 — the stage fails soft
            # sizing_rewritten=0 keeps the addon's summary chain entered, so
            # an armed-but-failed stage prints its reason instead of silence.
            stats["sizing_rewritten"] = 0
            stats["sizing_log"] = [f"text-fit calibration failed: {e!r}"]

    # Every file written to out_dir this run across all three stages
    # (deduped: one file may be touched by strings AND fonts).
    stats["exported_files"] = sorted(set(exported))
    return stats


def cmd_extract_textures(job: dict) -> dict:
    from ut import textures
    res = textures.extract_textures(job["game_dir"], job["out_dir"], progress=_progress)
    # Spec §5.1 names the mapping unity_img_manifest.json; the extract step
    # already writes manifest.json with exactly that mapping (file, rel,
    # path_id, png — png names are <assetfile>__<name>__<path_id>.png, unique
    # per file+path_id) — copy it under the spec name for the JS layer / SEP
    # handoff.
    mj = os.path.join(job["out_dir"], "manifest.json")
    if os.path.isfile(mj):
        shutil.copy2(mj, os.path.join(job["out_dir"], "unity_img_manifest.json"))
    return res


def cmd_demosaic_patch(job: dict) -> dict:
    """Standalone demosaic (v2.8): patch the game's mosaic censorship layers
    WITHOUT a translation project — the same stages the export's demosaic
    block runs, but as its own command so already-translated games can be
    treated directly. Always patch_mode: out_dir receives ONLY the files
    changed this run (the JS layer zips them into a drop-in patch).
    Manifest: {"version":2,"command":"demosaic_patch","game_dir","out_dir",
    "demosaic":{"enabled":true,"level":"high|medium","reveal":bool?,"code":bool?}}.
    """
    from ut import demosaic as _demosaic
    demosaic = job.get("demosaic") or {}
    dres = _demosaic.patch_game_demosaic(
        job["game_dir"], job["out_dir"],
        level=("medium" if demosaic.get("level") == "medium" else "high"),
        reveal=bool(demosaic.get("reveal")),
        patch_mode=True, in_place=False, progress=_progress)
    stats = {
        "demosaic_objects": dres["objects_hidden"],
        "demosaic_renderers": dres["renderers_hidden"],
        "demosaic_materials": dres["materials_neutered"],
        "demosaic_revealed": dres["materials_revealed"],
        "demosaic_files": dres["files_changed"],
        "demosaic_log": dres["log"],
    }
    exported = list(dres["exported_files"])
    # deep opt-in: stub mosaic-named Behaviour types in the managed DLLs
    # (Mono games; IL2CPP is reported not-patchable), same as the export.
    if demosaic.get("code"):
        from ut import monopatch as _monopatch
        cres = _monopatch.patch_managed_code(
            job["game_dir"], job["out_dir"], patch_mode=True, in_place=False)
        stats["demosaic_code_types"] = cres["code_types"]
        stats["demosaic_code_methods"] = cres["code_methods"]
        stats["demosaic_code_files"] = cres["code_files"]
        stats["demosaic_code_log"] = cres["code_log"]
        stats["demosaic_code_il2cpp"] = cres["code_il2cpp"]
        exported += cres["exported_files"]
    stats["exported_files"] = sorted(set(exported))
    return stats


COMMANDS = {"scan": cmd_scan, "patch": cmd_patch, "extract_textures": cmd_extract_textures,
            "demosaic_patch": cmd_demosaic_patch}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--result", required=True)
    args = ap.parse_args(argv)

    log_path = os.path.join(os.path.dirname(os.path.abspath(args.manifest)), "backend.log")
    result = {"ok": False, "version": CONTRACT_VERSION}
    try:
        with open(args.manifest, encoding="utf-8") as f:
            job = json.load(f)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(json.dumps(job, ensure_ascii=False) + "\n")
            if job.get("version") != CONTRACT_VERSION:
                result["error"] = (
                    f"unsupported contract version {job.get('version')!r} "
                    f"(this backend speaks v{CONTRACT_VERSION})"
                )
            else:
                fn = COMMANDS.get(job.get("command"))
                if fn is None:
                    result["error"] = f"unknown command {job.get('command')!r}"
                else:
                    # Library prints must never interleave with the progress
                    # stream: stdout goes to the log for the command's run.
                    sys.stdout = _SwallowStdout(log)
                    try:
                        payload = fn(job)
                    finally:
                        sys.stdout = _REAL_STDOUT
                    result = {"ok": True, "version": CONTRACT_VERSION, **payload}
    except ImportError as e:  # noqa: BLE001 — envelope must never crash without a result
        # the runtime ships WITHOUT the FMOD payload (stripped at build time,
        # see THIRD-PARTY-NOTICES.md): if a UnityPy path ever reaches for it,
        # say so instead of a bare traceback
        if "fmod" in str(e).lower():
            result["error"] = (
                "FMOD runtime not bundled: this SLRUnity build ships without "
                "the proprietary FMOD libraries (audio-bank export is not a "
                "supported feature; see THIRD-PARTY-NOTICES.md)")
        else:
            result["error"] = f"{type(e).__name__}: {e}"
            result["traceback"] = traceback.format_exc()
    except Exception as e:  # noqa: BLE001 — envelope must never crash without a result
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(result["traceback"] + "\n")
    with open(args.result, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
