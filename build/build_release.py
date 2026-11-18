"""Assemble the dist/SLRUnity-<version>-<os> release packages (.zip + .7z).

v2.7 release format: plain archives users extract into www/addons/
themselves. The old `.tap` (SLR's one-click drag-install zip) was retired
— a custom extension nothing opens by double-click confused exactly the
"never reads a manual" user it was meant to help, and manual extraction
leaves users knowing where the addon lives and how to remove it (delete
the folder). SLR's installer still only accepts .tap (main.js:220 gates on
the extension), but the AddonLoader never cared: www/js/addon.js (~475-493)
scans SUBFOLDERS of www/addons for package.json.
v2.8: archive names carry the addon version (addon/package.json),
e.g. SLRUnity-2.8.0-win.zip, so releases are trackable by filename alone.

Package layout (binding): EVERYTHING lives inside one top-level folder
`SLRUnity/` — extracting either archive into www/addons/ produces
www/addons/SLRUnity/, which is what the AddonLoader loads (the folder name
becomes the addon id). This is transparent to the code: lib/backend.js
resolves via thisAddon.fullPath, which includes the addon folder name.

    SLRUnity/
      package.json            <- addon/package.json
      UnityAddon.js, UnitySceneGraph.js, docs.html, lib/*.js
      assets/icon.png         (New Project dialog menu icon)
      assets/help.html
      backend/slr_cli.py, backend/ut/*.py, backend/requirements.txt
                              (NO tests/, NO __pycache__/, NO .pytest_cache/)
      runtime/python/python.exe          (win package)
      runtime/bin/python3                (linux package)

No font is bundled (e2e-fix1): the export font stage uses the HOST app's
own www/core/SEP/SLRCondensedCJK-Regular.ttf, resolved at runtime through
common.getRootDir().

Pipeline per OS: stage a tree under dist/stage-<os>/SLRUnity/ ->
zip the STAGE ROOT (so entries keep the SLRUnity/ prefix, deflate) to
dist/SLRUnity-<version>-<os>.zip -> 7z the same stage root to the .7z twin
(needs any 7-Zip CLI: the host app's bundled 7zip-bin, PATH, or
SLRUNITY_7ZA; skipped with a warning when none exists — the .zip is the
primary artifact anyway) -> structurally verify via build/verify_package.py
(the .7z by round-trip extraction) -> (win, unless --skip-runtime)
smoke-test the STAGED runtime + backend against the example game and
require ok:true.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_package  # noqa: E402  (single source of truth for package structure)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, "addon")
BACKEND_DIR = os.path.join(ROOT, "backend")
DIST_DIR = os.path.join(ROOT, "dist")
EXAMPLE_GAME = os.environ.get("SLRUNITY_EXAMPLE_GAME", "")  # local dev convenience; smoke check skips when unset

ADDON_FOLDER = verify_package.ADDON_FOLDER  # "SLRUnity"

PRUNE_DIRS = {"__pycache__", ".pytest_cache"}          # anywhere in the staged tree
ADDON_ROOT_PRUNE = {"tests"}                           # addon/ only (fixtures live there)


def rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def addon_version() -> str:
    with open(os.path.join(ADDON_DIR, "package.json"), encoding="utf-8") as f:
        return json.load(f)["version"]


def stage_addon(stage: str) -> None:
    def ignore(dirpath: str, names: list) -> set:
        drop = {n for n in names if n in PRUNE_DIRS}
        if os.path.abspath(dirpath) == os.path.abspath(ADDON_DIR):
            drop |= ADDON_ROOT_PRUNE
        return drop

    shutil.copytree(ADDON_DIR, stage, dirs_exist_ok=True, ignore=ignore)


def stage_backend(stage: str) -> None:
    dst = os.path.join(stage, "backend")
    os.makedirs(dst)
    for item in ("slr_cli.py", "requirements.txt"):
        shutil.copy2(os.path.join(BACKEND_DIR, item), os.path.join(dst, item))
    ut_src = os.path.join(BACKEND_DIR, "ut")

    def ignore(dirpath, names):
        return {n for n in names if n in PRUNE_DIRS}

    shutil.copytree(ut_src, os.path.join(dst, "ut"), ignore=ignore)


def stage_runtime(stage: str, os_name: str, skip_runtime: bool) -> bool:
    src = os.path.join(ROOT, "runtime", os_name)
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(stage, "runtime"),
                        ignore=lambda dirpath, names: {n for n in names if n in PRUNE_DIRS})
        return True
    if skip_runtime:
        print(f"WARNING: runtime/{os_name} missing - building a STRUCTURAL-ONLY package "
              f"without a runtime payload (the addon will not activate until a runtime is fetched)",
              file=sys.stderr, flush=True)
        return False
    print(f"ERROR: runtime/{os_name} missing (run build/fetch_runtime.py --os {os_name} first, "
          f"or pass --skip-runtime for a structure-only build)", file=sys.stderr, flush=True)
    raise SystemExit(2)


def zip_stage(stage_root: str, package: str) -> None:
    """Zip the STAGE ROOT (not the content folder) so every entry keeps the
    SLRUnity/ prefix — exactly what extracting into www/addons/ produces:
    a single www/addons/SLRUnity/ folder."""
    import zipfile
    if os.path.exists(package):
        os.remove(package)
    print(f"[zip] {stage_root}{os.sep}{ADDON_FOLDER}{os.sep} -> {package}", flush=True)
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(stage_root):
            dirs[:] = sorted(d for d in dirs if d not in PRUNE_DIRS)
            for f in sorted(files):
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, stage_root).replace(os.sep, "/"))


def sevenzip_bin() -> str | None:
    """The host app ships a 7za for its own SEP/archiving needs — prefer it,
    then whatever is on PATH / SLRUNITY_7ZA."""
    candidates = [
        os.path.join(os.path.dirname(ROOT), "SLR Translator",
                     "node_modules", "7zip-bin", "win", "x64", "7za.exe"),
    ]
    return verify_package.find_sevenzip(extra_candidates=candidates)


def sevenzip_stage(stage_root: str, package: str, sevenzip: str) -> None:
    """7z the STAGE ROOT so entries keep the SLRUnity/ prefix, same as the
    zip. Solid-compress: the payload is already-deflated wheels/zips inside
    an embedded python, so -mx=9 buys a little and costs seconds."""
    if os.path.exists(package):
        os.remove(package)
    print(f"[7z ] {stage_root}{os.sep}{ADDON_FOLDER}{os.sep} -> {package}", flush=True)
    r = subprocess.run([sevenzip, "a", "-t7z", "-mx=9", os.path.abspath(package), ADDON_FOLDER],
                       capture_output=True, text=True, cwd=stage_root)
    if r.returncode != 0:
        print(f"ERROR: 7za failed (exit {r.returncode}):\n"
              + "\n".join(r.stdout.splitlines()[-15:] + r.stderr.splitlines()[-5:]),
              file=sys.stderr)
        raise SystemExit(2)


def build_release(os_name: str, skip_runtime: bool) -> bool:
    stage_root = os.path.join(DIST_DIR, f"stage-{os_name}")
    stage = os.path.join(stage_root, ADDON_FOLDER)
    rmtree(stage_root)
    os.makedirs(stage)

    stage_addon(stage)
    stage_backend(stage)
    # License paperwork rides every package: GPLv3 + third-party notices.
    for fname in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        shutil.copy2(os.path.join(ROOT, fname), os.path.join(stage, fname))
    has_runtime = stage_runtime(stage, os_name, skip_runtime)

    stem = os.path.join(DIST_DIR, f"SLRUnity-{addon_version()}-{os_name}")
    zip_stage(stage_root, stem + ".zip")
    if not verify_package.verify_zip(stem + ".zip", os_name, has_runtime=has_runtime):
        return False

    sevenzip = sevenzip_bin()
    if sevenzip:
        sevenzip_stage(stage_root, stem + ".7z", sevenzip)
        if not verify_package.verify_7z(stem + ".7z", os_name, has_runtime=has_runtime,
                                        sevenzip=sevenzip):
            return False
    else:
        print("WARNING: no 7-Zip CLI found (host app 7zip-bin / PATH / SLRUNITY_7ZA) - "
              "built the .zip only", file=sys.stderr, flush=True)

    if os_name == "win" and not skip_runtime and has_runtime:
        if not smoke_scan(stage):
            return False
    elif os_name == "linux":
        print("[smoke] skipped: the linux interpreter cannot run on a Windows host "
              "(structural validation only)", flush=True)
    else:
        print("[smoke] skipped (--skip-runtime)", flush=True)
    return True


def smoke_scan(stage: str) -> bool:
    """Run the STAGED runtime + STAGED backend over the example game and
    require ok:true in the result envelope (contract v2 — the backend rejects
    v1 manifests). `stage` is the SLRUnity content folder — same
    runtime/backend depth as an installed addon, so this also proves the
    embedded python's ._pth bootstrap and slr_cli's sys.path setup survive the
    wrapped layout."""
    exe = os.path.join(stage, "runtime", "python", "python.exe")
    cli = os.path.join(stage, "backend", "slr_cli.py")
    if not os.path.isfile(exe):
        print(f"[smoke] no staged win runtime ({exe}) - skipped", flush=True)
        return True
    if not os.path.isdir(EXAMPLE_GAME):
        print("WARNING: example game not found - smoke skipped: " + EXAMPLE_GAME, file=sys.stderr, flush=True)
        return True
    with tempfile.TemporaryDirectory(prefix="slrunity-smoke-") as tmp:
        manifest = os.path.join(tmp, "scan.json")
        result = os.path.join(tmp, "scan.result.json")
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "command": "scan", "game_dir": EXAMPLE_GAME}, f)
        r = subprocess.run([exe, cli, "--manifest", manifest, "--result", result],
                           capture_output=True, text=True, cwd=tmp, timeout=3600)
        if r.returncode != 0:
            print(f"ERROR: smoke scan exited with code {r.returncode}\n"
                  f"stdout tail: {r.stdout[-3000:]}\nstderr tail: {r.stderr[-3000:]}", file=sys.stderr)
            return False
        try:
            with open(result, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as e:
            print(f"ERROR: smoke produced no parseable result ({e}); stdout tail: {r.stdout[-3000:]}", file=sys.stderr)
            return False
        if payload.get("ok") is not True:
            print("ERROR: smoke scan returned ok:false - " + str(payload.get("error"))
                  + "\ntraceback: " + str(payload.get("traceback"))[-3000:], file=sys.stderr)
            return False
        stats = payload.get("stats") or {}
        errors = stats.get("errors") or []
        # v2.5: the mosaic-censorship candidate list must ride the payload —
        # the example game carries 200+ 'mosaic' objects, so an empty/absent
        # list means the demosaic scan broke silently.
        demos = payload.get("demosaic") or []
        kinds = {}
        for row in demos:
            kinds[row.get("kind")] = kinds.get(row.get("kind"), 0) + 1
        print(f"[smoke] scan ok:true - files_scanned={stats.get('files_scanned')} "
              f"hits={stats.get('hits')} unique={stats.get('unique')} scan_errors={len(errors)} "
              f"demosaic={len(demos)} ({kinds})", flush=True)
        if not demos:
            print("ERROR: smoke scan carried no demosaic candidates "
                  "(expected 200+ on the example game)", file=sys.stderr)
            return False
        if errors:
            for e in errors[:5]:
                print("  scan error: " + str(e), file=sys.stderr)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--os", dest="target", choices=["win", "linux", "all"], default="all",
                    help="which package to build (default: all)")
    ap.add_argument("--skip-runtime", action="store_true",
                    help="structure-only build: do not require/verify a runtime payload and skip the smoke test")
    args = ap.parse_args()
    targets = ["win", "linux"] if args.target == "all" else [args.target]

    os.makedirs(DIST_DIR, exist_ok=True)
    failures = []
    for t in targets:
        print(f"=== building {t} package ===", flush=True)
        if not build_release(t, args.skip_runtime):
            failures.append(t)
    if failures:
        print("FAILED: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
