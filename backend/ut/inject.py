"""Injection: write translations (and later fonts/textures) back into assets.

Write-back strategy:
  * typetree source  -> reload file, read_typetree, set the field by its saved
    token path, save_typetree, reserialize the whole file. Handles any length
    (English longer than Japanese is fine).
  * raw source       -> rebuild the object payload: every hit is a validated
    Unity string record (int32 UTF-8 byte length + payload + zero padding), so
    a replacement of any length is written as a fresh record and everything
    after it shifts. The rebuilt payload goes back via set_raw_data() and
    UnityPy reserializes the file (object offsets are recomputed on save).
  * metadata source  -> ut.metadata.patch_literals on the copied
    global-metadata.dat: literal tables are relocated to EOF, any length.
  * textasset source -> TextAsset m_Script rewritten via ut.textfiles.apply
    (the typed item keys survive reserialization), same file resave as above.
    When one object carries BOTH typetree and textasset edits they share a
    single read_typetree/save_typetree cycle: read_typetree always re-reads
    the original buffer, so a second read/save pair would discard the first
    edit's stashed bytes.
  * textfile source  -> ut.textfiles.apply on the copied plain-text file.
  * mono source      -> ut.monopatch.translate_literals on the Managed DLL:
    the WHOLE #US heap is rebuilt compacted (translated Japanese bytes and
    dead records are reclaimed, every ldstr reference — translated or not —
    is repointed via real IL-boundary decoding), so the practical budget is
    the entire old heap plus the section slack. Overflow drops whole
    translations in sorted order; leftovers are counted as missed.

The output is a complete, standalone copy of the game in `out_dir` with the
modified asset files overwritten in place. The original is never touched.

``patch_mode=True`` (contract v2) skips the whole-game copy: every source is
loaded from its original location (the index's `abs` path) and ONLY the files
that were actually modified are written to `out_dir/<game-relative path>` —
a patch folder of whole-file replacements. Nothing else is created, so the
caller can stage/zip just the string-bearing files.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from collections import defaultdict

import UnityPy

from . import rawstrings
from .files import assert_inside, assert_not_nested
from .metadata import patch_literals
from .textfiles import apply as tf_apply


def load_translations(csv_path: str) -> dict[str, str]:
    """Read a strings.csv (japanese,english) into {jp: en}, blanks skipped.

    Uses the csv module so embedded commas/quotes/newlines in dialogue are
    handled. Expects UTF-8 (with or without BOM).
    """
    out: dict[str, str] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return out
        for row in reader:
            if len(row) < 2:
                continue
            jp, en = row[0], row[1]
            if jp and en.strip():
                out[jp] = en
    return out


def _set_by_tokens(tree, tokens: list, value: str) -> bool:
    """Navigate a nested dict/list by `tokens` and set the leaf to `value`."""
    cur = tree
    for t in tokens[:-1]:
        if isinstance(cur, dict):
            if t not in cur:
                return False
            cur = cur[t]
        elif isinstance(cur, list):
            if not isinstance(t, int) or t < 0 or t >= len(cur):
                return False
            cur = cur[t]
        else:
            return False
    last = tokens[-1]
    if isinstance(cur, dict) and last in cur:
        cur[last] = value
        return True
    if isinstance(cur, list) and isinstance(last, int) and 0 <= last < len(cur):
        cur[last] = value
        return True
    return False


def _missed_keys(new_content: bytes, edits: dict[str, str]) -> int:
    """Count tf/ta keys whose translation did not land in `new_content`.

    tf_apply reports a single bool for the whole call (I3: silent partial
    failure), so re-extract the rewritten payload and check every key's
    location now carries its intended value.
    """
    from .textfiles import extract as tf_extract

    by_key = {item.key: item.text for item in tf_extract(new_content).items}
    return sum(1 for k, v in edits.items() if by_key.get(k) != v)


def _save_env_to_file(env, out_file: str, pack: str = "lz4") -> None:
    """Reserialize changed files and overwrite `out_file`.

    UnityPy's Environment.save() writes every changed container by basename
    into a directory, so we save into a temp dir and move the result into the
    exact output path (preserving the game's folder structure).
    """
    target_dir = os.path.dirname(out_file)
    os.makedirs(target_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        env.save(pack=pack, out_path=td)
        for name in os.listdir(td):
            # shutil.copy2 is cross-drive safe (temp may be on C:, game on E:).
            shutil.copy2(os.path.join(td, name), os.path.join(target_dir, name))


def _rebuild_payload(
    payload: bytes,
    edits: list[tuple[int, int, str]],
    label: str,
) -> tuple[bytes, int, int]:
    """Apply raw string edits to an object payload.

    `edits` is a list of (offset, old_byte_len, new_text). Records are rebuilt
    highest offset first so earlier offsets stay valid while sizes shift.
    Returns (new_payload, applied, skipped).
    """
    out = payload
    applied = skipped = 0
    for offset, old_len, new_text in sorted(edits, key=lambda e: -e[0]):
        old_total = rawstrings.record_size(old_len)
        if offset < 0 or offset + old_total > len(out):
            print(f"  ! raw offset out of range {label} @ {offset:#x}")
            skipped += 1
            continue
        out = out[:offset] + rawstrings.encode_record(new_text) + out[offset + old_total :]
        applied += 1
    return out, applied, skipped


def patch_game(
    game_dir: str,
    out_dir: str,
    translations: dict[str, str],
    index_path: str,
    *,
    overwrite: bool = False,
    patch_mode: bool = False,
    progress=None,
) -> dict:
    """Create a translated copy of the game.

    With ``patch_mode=True`` no copy is made: out_dir receives ONLY the files
    that were actually modified, at their game-relative paths (whole-file
    replacements). Sources are always loaded from the index's abs paths (the
    original files), so the original is never touched in either mode.

    Returns a stats dict: {copied, files_modified, strings_written,
    skipped_raw, metadata_patched, textassets_patched, textfiles_patched,
    edits_missed, exported_files, out_dir}

    edits_missed counts every staged edit that did NOT land: bad token
    paths, tf keys whose location vanished, UnityPy load/read failures and
    occurrences with an unknown source. 0 means every dispatched edit wrote.
    """
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    # I1 guard: out_dir == game_dir (or nested either way) would make the
    # rmtree below delete the original game before copytree ever runs — and
    # in patch mode it would scatter patched files into the original tree.
    # Runs in BOTH modes, before any write.
    assert_not_nested(game_dir, out_dir, "out_dir")
    if os.path.exists(out_dir):
        if not overwrite:
            raise FileExistsError(f"Output exists: {out_dir} (use overwrite=True)")
        shutil.rmtree(out_dir)

    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    # Build work lists: abs_file -> path_id -> edits, per write-back strategy.
    # typetree: [(tokens, new_text)]   raw: [(offset, old_byte_len, new_text)]
    # textasset: {TextItem key: new_text}
    tt_edits: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    raw_edits: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    ta_edits: dict[str, dict[int, dict[str, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    # Non-asset sources (no UnityPy object): file -> literal-index / key -> en.
    meta_edits: dict[str, dict[int, str]] = defaultdict(dict)  # file -> {literal_index: en}
    tf_edits: dict[str, dict[str, str]] = defaultdict(dict)    # file -> {key: en}
    mono_edits: dict[str, dict[str, str]] = defaultdict(dict)  # file -> {literal: en}
    mono_seen: set[tuple] = set()  # (file, text) — one edit per unique literal
    staged = 0        # edit operations dispatched (I3: edits_missed = staged - landed)
    edits_missed = 0  # occurrences that matched no write-back branch at all
    for jp, en in translations.items():
        occs = index.get(jp)
        if not occs:
            continue
        for o in occs:
            # path_id crosses the JSON boundary as a decimal string (it can
            # exceed 2^53 — a JSON number gets rounded by the JS layer's
            # JSON.parse and then matches nothing); ints from pre-v2.1
            # indexes still work.
            pid = int(o["path_id"])
            if o["source"] == "typetree" and o.get("tokens"):
                tt_edits[o["abs"]][pid].append((o["tokens"], en))
                staged += 1
            elif o["source"] == "raw":
                raw_edits[o["abs"]][pid].append(
                    (o["offset"], o["length"], en)
                )
                staged += 1
            elif o["source"] == "metadata":
                meta_edits[o["abs"]][pid] = en
                staged += 1
            elif o["source"] == "textasset":
                ta_edits[o["abs"]][pid][o.get("key", "")] = en
                staged += 1
            elif o["source"] == "textfile":
                tf_edits[o["abs"]][o.get("key", "")] = en
                staged += 1
            elif o["source"] == "mono":
                # C# literals live in the DLL's #US heap; the writer matches
                # by string text, so repeated occurrences collapse to one
                # edit per (file, text) — count them once for the accounting.
                if (o["abs"], jp) not in mono_seen:
                    mono_seen.add((o["abs"], jp))
                    mono_edits[o["abs"]][jp] = en
                    staged += 1
            else:
                edits_missed += 1  # unknown source: reported, never silent

    # v1 (default): copy the whole game first. v2 patch_mode: no copy —
    # every write below creates its own parent dirs and lands as a fresh
    # file at out_dir/<rel> (whole-file replacement).
    if not patch_mode:
        shutil.copytree(game_dir, out_dir)

    files_modified = 0
    strings_written = 0
    skipped_raw = 0
    metadata_patched = 0
    textassets_patched = 0
    textfiles_patched = 0
    mono_patched = 0
    exported: list[str] = []  # every rel path written to out_dir this run
    all_files = sorted(set(tt_edits) | set(raw_edits) | set(meta_edits)
                       | set(ta_edits) | set(tf_edits))
    for i, abs_file in enumerate(all_files):
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        # C1 guard: a stale index `abs` outside game_dir must never write
        # outside out_dir (the join above would follow the .. escape).
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        if abs_file not in tt_edits and abs_file not in raw_edits \
                and abs_file not in ta_edits:
            # metadata / plain-text targets are not Unity assets; the blocks
            # below own their write-back. Still report progress for them.
            if progress:
                progress(i + 1, len(all_files), rel)
            continue
        try:
            env = UnityPy.load(abs_file)
        except Exception as e:  # noqa: BLE001
            print(f"  ! cannot load {rel}: {e!r}")
            continue

        changed = False
        ta_changed = False
        for obj in env.objects:
            tt = tt_edits.get(abs_file, {}).get(obj.path_id)
            raw = raw_edits.get(abs_file, {}).get(obj.path_id)
            ta = ta_edits.get(abs_file, {}).get(obj.path_id)

            # typetree and textasset edits share ONE read/save cycle on the
            # same tree. UnityPy 1.25.2's save_typetree only stashes the
            # serialized bytes (set_raw_data); read_typetree keeps reading the
            # ORIGINAL buffer, so separate read/save pairs on one object lose
            # the first edit (real case: sharedassets0.assets pid 885 has a
            # m_Name typetree hit AND m_Script-line textasset hits).
            if tt or ta:
                try:
                    tree = obj.read_typetree()
                except Exception as e:  # noqa: BLE001
                    tree = None
                    print(f"  ! typetree read failed {rel}#{obj.path_id}: {e!r}")
                if tree is not None:
                    tree_changed = False
                    if tt:
                        for tokens, new_text in tt:
                            if _set_by_tokens(tree, tokens, new_text):
                                changed = True
                                tree_changed = True
                                strings_written += 1
                    if ta:
                        script = tree.get("m_Script", b"")
                        # m_Script comes back as str or bytes depending on the
                        # UnityPy version / parse path; tf_apply works on bytes.
                        was_str = isinstance(script, str)
                        script_b = (
                            script.encode("utf-8", "surrogateescape") if was_str
                            else bytes(script)
                        )
                        new_script, ta_applied, _fmt = tf_apply(script_b, ta)
                        if ta_applied:
                            tree["m_Script"] = (
                                new_script.decode("utf-8", "surrogateescape")
                                if was_str else new_script
                            )
                            changed = True
                            tree_changed = True
                            ta_changed = True
                            # only keys whose value verifiably landed count
                            # as written; the rest surface in edits_missed
                            strings_written += len(ta) - _missed_keys(new_script, ta)
                    if tree_changed:
                        try:
                            obj.save_typetree(tree)
                        except Exception as e:  # noqa: BLE001
                            print(
                                f"  ! save_typetree failed {rel}#{obj.path_id}: {e!r}"
                            )

            # raw stays in its own branch: it is the typetree-read-FAILED
            # fallback (MonoBehaviour only), so discovery never emits raw and
            # tt/ta hits for the same object — no stash collision possible.
            if raw:
                try:
                    payload = obj.get_raw_data()
                except Exception as e:  # noqa: BLE001
                    print(f"  ! raw read failed {rel}#{obj.path_id}: {e!r}")
                    continue
                new_payload, applied, skipped = _rebuild_payload(
                    payload, raw, f"{rel}#{obj.path_id}"
                )
                skipped_raw += skipped
                if applied:
                    obj.set_raw_data(new_payload)
                    changed = True
                    strings_written += applied

        if changed:
            _save_env_to_file(env, out_file)  # makedirs its own parent dirs
            files_modified += 1
            exported.append(rel.replace("\\", "/"))
        if ta_changed:
            textassets_patched += 1
        if progress:
            progress(i + 1, len(all_files), rel)

    # --- source 3: IL2CPP string literals (reload the original metadata,
    # patch, write the result — a fresh file in patch mode, the copy in v1) ---
    for abs_file, per_literal in meta_edits.items():
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(abs_file, "rb") as f:
            data = f.read()
        with open(out_file, "wb") as f:
            f.write(patch_literals(data, per_literal))
        strings_written += len(per_literal)
        files_modified += 1
        metadata_patched += 1
        exported.append(rel.replace("\\", "/"))

    # --- source 5: plain text files (reload the original, patch, write) ---
    for abs_file, keys in tf_edits.items():
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        with open(abs_file, "rb") as f:
            content = f.read()
        new_content, tf_changed, _fmt = tf_apply(content, keys)
        if tf_changed:
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with open(out_file, "wb") as f:
                f.write(new_content)
            strings_written += len(keys) - _missed_keys(new_content, keys)
            files_modified += 1
            textfiles_patched += 1
            exported.append(rel.replace("\\", "/"))

    # --- source 6: Mono C# literals (Managed DLLs, contract v2.6) — grown
    # #US heap + repointed ldstr tokens, bounded by the DLL's metadata
    # slack; follows the stage-order rule so earlier stages' edits compose
    # (the demosaic code tier re-reads out_dir afterwards and stubs on top).
    for abs_file, per_text in mono_edits.items():
        rel = os.path.relpath(abs_file, game_dir)
        out_file = os.path.join(out_dir, rel)
        assert_inside(out_dir, out_file, f"output for {rel!r}")
        src_file = abs_file
        if patch_mode and os.path.isfile(out_file):
            src_file = out_file  # stage-order rule
        with open(src_file, "rb") as f:
            data = f.read()
        from .monopatch import translate_literals
        new_data, mstats = translate_literals(data, per_text)
        if new_data is None:
            edits_missed += len(per_text)
            continue
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "wb") as f:
            f.write(new_data)
        strings_written += mstats["texts_applied"]
        files_modified += 1
        mono_patched += 1
        edits_missed += len(per_text) - mstats["texts_applied"]
        exported.append(rel.replace("\\", "/"))
        for note in mstats["notes"]:
            print(f"  mono {rel}: {note}")

    # I3: every staged edit either landed (strings_written) or is reported.
    edits_missed += staged - strings_written
    return {
        "copied": not patch_mode,
        "files_modified": files_modified,
        "strings_written": strings_written,
        "skipped_raw": skipped_raw,
        "metadata_patched": metadata_patched,
        "textassets_patched": textassets_patched,
        "textfiles_patched": textfiles_patched,
        "mono_patched": mono_patched,
        "edits_missed": edits_missed,
        "exported_files": sorted(exported),
        "out_dir": out_dir,
    }
