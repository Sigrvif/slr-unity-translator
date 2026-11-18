"""Build the minimal SLRUnity source archive (dist/SLRUnity-<version>-source.zip).

The shareable source tree: everything needed to read, build, test and
re-derive the addon — and nothing that regenerates or records:

  README.md              project overview, layout, build, test, constraints
  addon/                 the JS addon, complete (tests + fixtures included —
                         they are source; the RUNTIME payloads are fetched at
                         build time and ship only in the release packages)
  backend/               Python backend: slr_cli.py, ut/, tests/ (no caches)
  build/                 the build scripts themselves (incl. this one)
  docs/                  contract, E2E checklist, fixtures, specs/plans

Left out on purpose (the bulk of the dev folder): runtime/ (412 MB —
refetch with build/fetch_runtime.py), dist/ (build outputs + the stage
trees), _snapshots/ (frozen task states), .sdd/ (working ledgers), and all
__pycache__/.pytest_cache/.pyc litter.

The archive name carries the addon version (addon/package.json), matching
the release packages, so releases are trackable by filename alone.
"""

import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT, "dist")

WALK_TOPS = ("addon", "backend", "build", "docs")
PRUNE_DIRS = {"__pycache__", ".pytest_cache"}


def collect():
    files = []
    for top in WALK_TOPS:
        for dp, dns, fns in os.walk(os.path.join(ROOT, top)):
            dns[:] = sorted(d for d in dns if d not in PRUNE_DIRS)
            for fn in sorted(fns):
                if fn.endswith(".pyc"):
                    continue
                files.append(os.path.join(dp, fn))
    readme = os.path.join(ROOT, "README.md")
    if os.path.isfile(readme):
        files.append(readme)
    return sorted(files)


def main() -> int:
    os.makedirs(DIST_DIR, exist_ok=True)
    with open(os.path.join(ROOT, "addon", "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    out_zip = os.path.join(DIST_DIR, f"SLRUnity-{version}-source.zip")
    files = collect()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            z.write(p, os.path.relpath(p, ROOT))
    size = os.path.getsize(out_zip)
    print(f"[source] {out_zip}")
    print(f"[source] OK: {size:,} bytes, {len(files)} entries")

    # Verify: no dev-tree bulk leaked, and the package.json inside is the
    # versioned one this archive is named after.
    leaks = {"runtime", "dist", "_snapshots", ".sdd"}
    with zipfile.ZipFile(out_zip) as z:
        names = z.namelist()
        bad = [n for n in names
               if n.split("/")[0] in leaks
               or "__pycache__" in n or n.endswith((".pyc", ".log"))]
        if bad:
            print("FAILED — leaked entries:", bad[:10], file=sys.stderr)
            return 1
        pkg = json.loads(z.read("addon/package.json").decode("utf-8"))
        if pkg["version"] != version:
            print(f"FAILED — package.json version {pkg['version']} != {version}",
                  file=sys.stderr)
            return 1
        print(f"[source] addon version: {pkg['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
