"""Structural verifier for the SLRUnity release packages (zip + 7z).

v2.7: the release format is plain archives — SLRUnity-<version>-<os>.zip and
its .7z twin (v2.8 added the version to the names, from
addon/package.json) — that users extract into www/addons/ THEMSELVES. The
one-click `.tap` installer flow was retired: a file extension no user has
ever seen was more confusing than helpful (nothing opens a .tap by
double-click), and manual extraction leaves users knowing exactly where
the addon lives and how to remove it (delete the folder). The layout
binding itself is unchanged: everything must live inside ONE top-level
`SLRUnity/` folder, because the AddonLoader (www/js/addon.js, ~475-493)
scans only SUBFOLDERS of www/addons for package.json — entries loose at
the archive root would scatter over www/addons/ and never load.

This module is the single source of truth for package structure. It is
invoked by build/build_release.py after every build, and can be run
standalone:

    python build/verify_package.py dist/SLRUnity-2.8.0-win.zip dist/SLRUnity-2.8.0-win.7z

The .zip is checked in place with zipfile. The .7z is extracted with a
7-Zip binary (7z is not natively readable from Python without extra
dependencies) into a temp dir and the same rule set runs over the
extracted tree — which also proves the archive round-trips through the
exact kind of tool users will use. Exits non-zero on any violation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

# The one allowed top-level folder inside a package (== addon folder name == id).
ADDON_FOLDER = "SLRUnity"

# Entries required INSIDE the wrapper folder (forward-slash, no leading slash).
REQUIRED_ENTRIES = [
    "package.json",
    "UnityAddon.js",
    "UnitySceneGraph.js",
    "docs.html",
    "lib/backend.js",
    "lib/paths.js",
    "lib/detector.js",
    "lib/projectBuilder.js",
    "lib/sceneGraphCore.js",
    "backend/slr_cli.py",
    "backend/requirements.txt",
    "backend/ut/__init__.py",
    # e2e-fix1: no font ships inside the package — the export font stage uses
    # the HOST app's www/core/SEP/SLRCondensedCJK-Regular.ttf at runtime. The
    # menu icon and the addon's own help page are still bundled.
    "assets/icon.png",
    "assets/help.html",
]

# Nothing may SHIP as a bundled font anymore (e2e-fix1): a leftover
# assets/fonts/ entry means a stale build tree or an old fetch_runtime run.
FORBIDDEN_ENTRIES = ["assets/fonts/"]

# Entry that proves the per-OS runtime payload made it into the package.
RUNTIME_MARKER = {"win": "runtime/python/python.exe", "linux": "runtime/bin/python3"}

# Nothing from the dev tree may leak into a package. OUR test trees are anchored
# to the addon/backend roots — a plain "/tests/" substring is WRONG because
# runtime site-packages legitimately carry third-party tests (e.g. archspec's
# json/tests data files, verified in both payloads). Caches and dev dirs are
# forbidden anywhere.
FORBIDDEN_ANCHORED = ["tests/", "backend/tests/"]      # directly under ADDON_FOLDER
FORBIDDEN_ANYWHERE = ["__pycache__/", ".pytest_cache/", "node_modules/", ".sdd/", "_snapshots/",
                      # proprietary FMOD payload must never ship (fmod_toolkit/pyfmodex
                      # are stripped from the runtime at build time - build/fetch_runtime.py)
                      "fmod_toolkit", "pyfmodex", "libfmod", "fmod.dll", "fmodL.dll"]


def _inside(entry: str) -> str:
    return f"{ADDON_FOLDER}/{entry}"


def find_sevenzip(extra_candidates: list | None = None) -> str | None:
    """Locate a 7-Zip CLI. Order: SLRUNITY_7ZA env override, explicit
    extra candidates (build_release passes the host app's bundled
    7zip-bin), then PATH. Returns None when nothing is installed."""
    env = os.environ.get("SLRUNITY_7ZA")
    if env and os.path.isfile(env):
        return env
    for cand in extra_candidates or []:
        if cand and os.path.isfile(cand):
            # candidates are aimed at the TARGET os; a win 7za.exe is
            # useless when the build host is Linux — fall through to PATH
            if cand.lower().endswith(".exe") and os.name != "nt":
                continue
            return cand
    for name in ("7z", "7za"):
        found = shutil.which(name)
        if found:
            return found
    return None


def rules_errors(names: list, read_file, os_name: str, has_runtime: bool) -> list:
    """Run the structural rule set over an entry-name list (forward-slash
    paths already prefixed with ADDON_FOLDER/). `read_file(name)` returns
    the bytes of one entry (zip member or extracted file). Returns the
    violation list — empty means valid."""
    import json
    errors: list = []

    # --- Rule 1: exactly one top-level folder, and it is ADDON_FOLDER. -------
    tops = sorted({n.split("/", 1)[0].rstrip("/") for n in names if n.strip("/")})
    if tops != [ADDON_FOLDER]:
        errors.append(
            f"top-level entries must be exactly [{ADDON_FOLDER}/], found: {tops} "
            f"(the AddonLoader scans only SUBFOLDERS of www/addons/ for "
            f"package.json; a root-level package.json would scatter and never load)")
    root_files = [n for n in names if "/" not in n.rstrip("/")]
    if root_files:
        errors.append(f"entries at the archive ROOT are forbidden (would scatter over www/addons/): {root_files}")

    # --- Rule 2: all required addon/backend/asset entries inside the folder. --
    required = [_inside(e) for e in REQUIRED_ENTRIES]
    if has_runtime:
        required.append(_inside(RUNTIME_MARKER[os_name]))
    missing = [n for n in required if n not in names]
    if missing:
        errors.append(f"missing required entries: {missing}")

    # --- Rule 3: runtime payload present (when claimed) and OS-correct. ------
    runtime_entries = [n for n in names if n.startswith(_inside("runtime/"))]
    if has_runtime and not runtime_entries:
        errors.append("runtime payload claimed but no runtime/ entries found")
    if os_name == "win" and any(n.startswith(_inside("runtime/bin/")) for n in names):
        errors.append("win package must not carry the linux runtime (runtime/bin/)")
    if os_name == "linux" and any(n.startswith(_inside("runtime/python/")) for n in names):
        errors.append("linux package must not carry the win runtime (runtime/python/)")

    # --- Rule 4: no dev-tree leakage (our tests, caches, snapshots). ---------
    prefix = ADDON_FOLDER + "/"
    for n in names:
        anchored = [p for p in FORBIDDEN_ANCHORED if n == prefix + p or n.startswith(prefix + p)]
        anywhere = [p for p in FORBIDDEN_ANYWHERE if p in n]
        forbidden = [p for p in FORBIDDEN_ENTRIES if n.startswith(prefix + p)]
        for pat in anchored + anywhere + forbidden:
            errors.append(f"forbidden entry: {n} (matches {pat})")

    # --- Rule 5: the load files referenced by package.json actually exist. ---
    pkg_path = _inside("package.json")
    try:
        pkg = json.loads(read_file(pkg_path).decode("utf-8"))
        for load_file in pkg.get("load", []):
            if _inside(load_file) not in names:
                errors.append(f"package.json lists load file not present in package: {load_file}")
        if pkg.get("name") != ADDON_FOLDER:
            errors.append(f"package.json name {pkg.get('name')!r} != folder name {ADDON_FOLDER!r} "
                          f"(SLR derives the addon id from the FOLDER name)")
    except (KeyError, ValueError, OSError) as e:
        errors.append(f"cannot read/parse {pkg_path}: {e}")
    return errors


def _report(path: str, os_name: str, has_runtime: bool, names: list,
            errors: list, runtime_entries: list, kind: str) -> bool:
    if errors:
        for e in errors:
            print(f"ERROR: {kind} {path}: " + e, file=sys.stderr)
        return False
    size = os.path.getsize(path)
    print(f"[verify:{kind}] OK: {path} {size:,} bytes, {len(names)} entries, "
          f"single top-level folder '{ADDON_FOLDER}/', runtime entries: "
          f"{len(runtime_entries)} (os={os_name}, runtime={'yes' if has_runtime else 'no'})",
          flush=True)
    return True


def verify_zip(package: str, os_name: str, has_runtime: bool = True) -> bool:
    """Assert the structural contract on a .zip package. Returns True when valid."""
    print(f"[verify:zip] {package} (os={os_name}, runtime={'yes' if has_runtime else 'no'})", flush=True)
    if not os.path.isfile(package):
        print(f"ERROR: package not found: {package}", file=sys.stderr)
        return False

    # Cross-check the archive with the stdlib CLI listing before the real
    # namelist() pass (the namelist check is the authoritative one).
    r = subprocess.run([sys.executable, "-m", "zipfile", "-l", package],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("ERROR: python -m zipfile -l failed:\n" + r.stdout + r.stderr, file=sys.stderr)
        return False

    with zipfile.ZipFile(package) as zf:
        names = zf.namelist()
        bad_entry = zf.testzip()
    if bad_entry is not None:
        print(f"ERROR: corrupt entry in {package}: {bad_entry}", file=sys.stderr)
        return False

    with zipfile.ZipFile(package) as zf:
        errors = rules_errors(names, zf.read, os_name, has_runtime)
    runtime_entries = [n for n in names if n.startswith(_inside("runtime/"))]
    return _report(package, os_name, has_runtime, names, errors, runtime_entries, "zip")


def verify_tree(root: str, os_name: str, has_runtime: bool = True,
                label: str = "") -> bool:
    """Run the same rules over an EXTRACTED package tree (root is the folder
    that CONTAINS ADDON_FOLDER)."""
    names = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".pytest_cache"})
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            names.append(os.path.relpath(full, root).replace(os.sep, "/"))

    def read_file(name: str) -> bytes:
        with open(os.path.join(root, name), "rb") as f:
            return f.read()

    errors = rules_errors(names, read_file, os_name, has_runtime)
    runtime_entries = [n for n in names if n.startswith(_inside("runtime/"))]
    return _report(label or root, os_name, has_runtime, names, errors, runtime_entries, "tree")


def verify_7z(package: str, os_name: str, has_runtime: bool = True,
              sevenzip: str | None = None) -> bool:
    """Extract `package` with 7za into a temp dir and verify the tree — this
    both checks the rules AND proves the archive round-trips through 7-Zip."""
    print(f"[verify:7z] {package} (os={os_name}, runtime={'yes' if has_runtime else 'no'})", flush=True)
    if not os.path.isfile(package):
        print(f"ERROR: package not found: {package}", file=sys.stderr)
        return False
    sevenzip = sevenzip or find_sevenzip()
    if not sevenzip:
        print("ERROR: no 7-Zip CLI found (SLRUNITY_7ZA, PATH) — cannot verify the .7z",
              file=sys.stderr)
        return False
    import tempfile
    with tempfile.TemporaryDirectory(prefix="slrunity-7z-verify-") as tmp:
        r = subprocess.run([sevenzip, "x", "-y", f"-o{tmp}", package],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"ERROR: 7za extraction failed (exit {r.returncode}):\n"
                  + "\n".join(r.stdout.splitlines()[-15:] + r.stderr.splitlines()[-5:]),
                  file=sys.stderr)
            return False
        return verify_tree(tmp, os_name, has_runtime=has_runtime, label=package)


def _infer_os(filename: str) -> str | None:
    base = os.path.basename(filename).lower()
    if "linux" in base:
        return "linux"
    if "win" in base:
        return "win"
    return None


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Verify release package structure (single wrapper folder, "
                    "required entries, no dev leakage; .zip in place, .7z via extraction)")
    ap.add_argument("packages", nargs="+", help=".zip / .7z release packages")
    ap.add_argument("--os", dest="target", choices=["win", "linux", "all"], default="all",
                    help="which OS the arguments are (default: derive from file name)")
    ap.add_argument("--no-runtime", action="store_true",
                    help="verify as structure-only packages (no runtime payload required)")
    ap.add_argument("--sevenzip", help="path to a 7z/7za CLI (default: SLRUNITY_7ZA / PATH)")
    args = ap.parse_args(argv)

    failures = []
    for pkg in args.packages:
        os_name = args.target if args.target != "all" else _infer_os(pkg)
        if os_name is None:
            print(f"ERROR: cannot infer OS from package name {pkg!r}; pass --os explicitly",
                  file=sys.stderr)
            failures.append(pkg)
            continue
        ext = os.path.splitext(pkg)[1].lower()
        if ext == ".zip":
            ok = verify_zip(pkg, os_name, has_runtime=not args.no_runtime)
        elif ext == ".7z":
            ok = verify_7z(pkg, os_name, has_runtime=not args.no_runtime,
                           sevenzip=args.sevenzip)
        else:
            print(f"ERROR: unsupported package type {ext!r} (.zip / .7z only)", file=sys.stderr)
            ok = False
        if not ok:
            failures.append(pkg)
    if failures:
        print("FAILED: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
