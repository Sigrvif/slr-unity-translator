"""Fetch the runtime payloads and assets for the SLRUnity release packages.

Per Task 6 (docs/.sdd brief):
  win   : CPython 3.10.11 embeddable (amd64) -> runtime/win/python/
          + python310._pth + pip + UnityPy==1.25.2 + Pillow>=10.0
  linux : python-build-standalone CPython 3.10.13 install-only tar
          -> runtime/linux/ (tar root python/ hoisted one level so
          runtime/linux/bin/python3 exists) + same pip pins
No font is fetched anymore (e2e-fix1): the export font stage uses the HOST
app's own www/core/SEP/SLRCondensedCJK-Regular.ttf, resolved at runtime via
common.getRootDir(), so the addon ships no font payload.

No FMOD runtime is shipped: UnityPy's FMOD audio-bank export path is never
exercised by the backend, so the fmod_toolkit/pyfmodex packages (and their
bundled proprietary libfmod binaries) are stripped from the runtime
site-packages after install — see strip_fmod().

Idempotent: any target whose verification already passes is skipped, so this
script is safe to re-run. Exits non-zero when a requested target cannot be
verified. The only sanctioned degradation: building the LINUX runtime on a
Windows host — the ELF interpreter cannot run there, so the pip pins are
cross-installed with the HOST pip (manylinux wheels, --target) and the result
is verified structurally; if even that fails the runtime is left
STRUCTURAL-ONLY with a loud warning (build_release.py --skip-runtime then
builds a structure-only linux package).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
WIN_PY_ZIP = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
# Latest get-pip may drop 3.10 support; bootstrap.pypa.io keeps a pinned variant.
GET_PIP = "https://bootstrap.pypa.io/get-pip.py"
GET_PIP_310 = "https://bootstrap.pypa.io/pip/3.10/get-pip.py"
# python-build-standalone moved from indygreg to astral-sh; both URLs serve
# the same release (the old one redirects), the mirror is the fallback.
LINUX_PY_TAR = "https://github.com/indygreg/python-build-standalone/releases/download/20240107/cpython-3.10.13+20240107-x86_64-unknown-linux-gnu-install_only.tar.gz"
LINUX_PY_TAR_MIRROR = "https://github.com/astral-sh/python-build-standalone/releases/download/20240107/cpython-3.10.13+20240107-x86_64-unknown-linux-gnu-install_only.tar.gz"
PIP_PINS = ["UnityPy==1.25.2", "Pillow>=10.0", "fontTools>=4.0", "numpy>=1.22"]

# The four path entries are mandated by the task brief. The trailing
# "import site" line is the standard embeddable-distribution activation
# required for pip/site-packages to behave (without it, get-pip and
# python -m pip misbehave under the ._pth isolated mode).
WIN_PTH_NAME = "python310._pth"
WIN_PTH_CONTENT = "python310.zip\n.\nLib/site-packages\n..\nimport site\n"

LINUX_SITE_PACKAGES = os.path.join("lib", "python3.10", "site-packages")

# Statuses: "ok" (fully verified), "structural" (usable layout, host could
# not execute/verify), "fail".
def _note(status):  # pragma: no cover - trivial
    return status


def download(url: str, dest: str, attempts: int = 3) -> None:
    """Stream `url` to `dest` atomically (dest.part + rename)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SLRUnity-build/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest + ".part", "wb") as out:
                shutil.copyfileobj(resp, out)
            os.replace(dest + ".part", dest)
            print(f"[dl] {url}\n     -> {dest} ({os.path.getsize(dest):,} bytes)", flush=True)
            return
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                break  # a missing file will not appear by retrying
            print(f"[dl] attempt {i + 1}/{attempts} failed: {e}", flush=True)
        except (urllib.error.URLError, OSError) as e:
            last = e
            print(f"[dl] attempt {i + 1}/{attempts} failed: {e}", flush=True)
    try:
        os.remove(dest + ".part")
    except OSError:
        pass
    raise RuntimeError(f"download failed for {url}: {last}")


def sh(cmd, **kw) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd), flush=True)
    kw.setdefault("cwd", None)
    return subprocess.run(cmd, **kw)


def is_executable(python_exe: str) -> bool:
    """True when the interpreter can actually run on THIS host (an ELF binary
    on Windows raises OSError from CreateProcess)."""
    try:
        r = subprocess.run([python_exe, "--version"], capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def verify_import(python_exe: str) -> bool:
    """Brief-mandated verification: `<python> -c "import UnityPy, PIL; print('runtime ok')"`."""
    try:
        r = subprocess.run([python_exe, "-c", "import UnityPy, PIL; print('runtime ok')"],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[verify] could not run {python_exe}: {e}", flush=True)
        return False
    if r.returncode != 0 or "runtime ok" not in r.stdout:
        print(f"[verify] FAILED for {python_exe} (exit {r.returncode})\n{r.stdout}\n{r.stderr}", flush=True)
        return False
    # fmod_toolkit must NOT be importable: its proprietary libfmod payload
    # never ships (strip_fmod). A stale runtime that still has it fails here
    # and is rebuilt.
    try:
        r2 = subprocess.run([python_exe, "-c", "import fmod_toolkit"],
                            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[verify] could not run {python_exe}: {e}", flush=True)
        return False
    if r2.returncode == 0:
        print(f"[verify] FAILED for {python_exe}: fmod_toolkit still importable (stale runtime)",
              file=sys.stderr, flush=True)
        return False
    print(f"[verify] {python_exe}: runtime ok (no FMOD payload)", flush=True)
    return True


# UnityPy pulls fmod_toolkit -> pyfmodex -> proprietary libfmod binaries. The
# backend never exports FMOD audio, so none of it ships: strip the packages
# (and any stray libfmod payload) from the target site-packages after pip
# install. Verified by verify_package.py's FORBIDDEN_ANYWHERE rules.
_FMOD_NAMES = ("fmod_toolkit", "pyfmodex")
_FMOD_PREFIXES = ("libfmod", "fmod.dll", "fmodL.dll")

def strip_fmod(site_packages: str) -> None:
    removed = []
    for entry in os.listdir(site_packages):
        full = os.path.join(site_packages, entry)
        low = entry.lower()
        is_dist = any(low.endswith(".dist-info") and (low.startswith(n + "-") or low.startswith(n + "_"))
                     for n in _FMOD_NAMES)
        if low in _FMOD_NAMES or is_dist:
            shutil.rmtree(full, ignore_errors=True)
            removed.append(entry)
        elif any(low.startswith(pfx.lower()) for pfx in _FMOD_PREFIXES):
            os.remove(full)
            removed.append(entry)
    if removed:
        print("[strip] removed FMOD payload from runtime: " + ", ".join(sorted(removed)), flush=True)


def cross_verify(site_packages: str) -> bool:
    """Structural stand-in for verify_import when the interpreter cannot run
    on this host: the wheels must at least be unpacked in place."""
    for pkg in ("UnityPy", "PIL"):
        if not os.path.isfile(os.path.join(site_packages, pkg, "__init__.py")):
            print(f"[verify] structural check FAILED: {pkg} missing from {site_packages}", flush=True)
            return False
    leftover = [e for e in os.listdir(site_packages)
                if e.lower() in ("fmod_toolkit", "pyfmodex")
                or e.lower().startswith(("fmod_toolkit.", "pyfmodex.", "libfmod"))
                or e.lower() in ("fmod.dll", "fmodl.dll")]
    if leftover:
        print(f"[verify] structural check FAILED: FMOD payload present: {leftover}", flush=True)
        return False
    print(f"[verify] structural (cross-install): UnityPy + PIL present, no FMOD payload", flush=True)
    return True


def fetch_win() -> str:
    py_dir = os.path.join(RUNTIME_DIR, "win", "python")
    py = os.path.join(py_dir, "python.exe")

    if os.path.isfile(py) and verify_import(py):
        print("[win] runtime already complete - nothing to do", flush=True)
        return "ok"

    print("[win] building embedded CPython 3.10.11 runtime", flush=True)
    if os.path.isdir(os.path.join(RUNTIME_DIR, "win")):
        shutil.rmtree(os.path.join(RUNTIME_DIR, "win"))
    os.makedirs(py_dir)

    with tempfile.TemporaryDirectory() as tmp:
        z = os.path.join(tmp, "python-embed.zip")
        download(WIN_PY_ZIP, z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(py_dir)

    # Overwrite the stock python310._pth with the brief-mandated entries
    # (+ the "import site" activation line, see WIN_PTH_CONTENT).
    with open(os.path.join(py_dir, WIN_PTH_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(WIN_PTH_CONTENT)

    with tempfile.TemporaryDirectory() as tmp:
        gp = os.path.join(tmp, "get-pip.py")
        download(GET_PIP, gp)
        r = sh([py, gp, "--no-warn-script-location"])
        if r.returncode != 0:
            print("[win] latest get-pip failed (pip may have dropped 3.10) - retrying the pinned 3.10 variant", flush=True)
            download(GET_PIP_310, gp)
            r = sh([py, gp, "--no-warn-script-location"])
            if r.returncode != 0:
                print("ERROR: get-pip failed for the win runtime", file=sys.stderr, flush=True)
                return "fail"
        r = sh([py, "-m", "pip", "install", *PIP_PINS, "--no-warn-script-location"])
        if r.returncode != 0:
            print("ERROR: pip install failed for the win runtime", file=sys.stderr, flush=True)
            return "fail"
        strip_fmod(os.path.join(py_dir, "Lib", "site-packages"))

    if not verify_import(py):
        print("ERROR: win runtime failed verification (import UnityPy, PIL)", file=sys.stderr, flush=True)
        return "fail"
    print("[win] runtime ok", flush=True)
    return "ok"


def fetch_linux() -> str:
    linux_root = os.path.join(RUNTIME_DIR, "linux")
    py = os.path.join(linux_root, "bin", "python3")
    site_packages = os.path.join(linux_root, *LINUX_SITE_PACKAGES.split("/"))

    already = False
    if os.path.isfile(py):
        if is_executable(py):
            if verify_import(py):
                print("[linux] runtime already complete - nothing to do", flush=True)
                already = True
        elif cross_verify(site_packages):
            print("[linux] runtime already cross-installed - nothing to do", flush=True)
            already = True
    if already:
        return "ok"

    print("[linux] building python-build-standalone CPython 3.10.13 runtime", flush=True)
    if os.path.isdir(linux_root):
        shutil.rmtree(linux_root)
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=RUNTIME_DIR)  # same volume -> cheap renames
    try:
        tgz = os.path.join(tmp, "cpython.tar.gz")
        try:
            download(LINUX_PY_TAR, tgz)
        except RuntimeError:
            print("[linux] primary URL failed - trying the astral-sh mirror", flush=True)
            download(LINUX_PY_TAR_MIRROR, tgz)
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(tmp)  # tar root is python/
        if not os.path.isdir(os.path.join(tmp, "python")):
            print("ERROR: linux tar did not contain a python/ root", file=sys.stderr, flush=True)
            return "fail"
        # Hoist python/* one level so runtime/linux/bin/python3 exists
        # (the layout lib/backend.js resolveRuntime expects in the package).
        os.rename(os.path.join(tmp, "python"), linux_root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.isfile(py):
        print(f"ERROR: {py} missing after extraction", file=sys.stderr, flush=True)
        return "fail"
    print(f"[linux] extracted -> {py}", flush=True)

    if is_executable(py):
        r = sh([py, "-m", "ensurepip"])
        if r.returncode != 0:
            print("ERROR: ensurepip failed for the linux runtime", file=sys.stderr, flush=True)
            return "fail"
        r = sh([py, "-m", "pip", "install", *PIP_PINS, "--no-warn-script-location"])
        strip_fmod(site_packages)
        if r.returncode != 0 or not verify_import(py):
            print("ERROR: linux runtime failed verification", file=sys.stderr, flush=True)
            return "fail"
        print("[linux] runtime ok", flush=True)
        return "ok"

    # Windows host: the ELF interpreter cannot run here. Cross-install the
    # pins with the HOST pip into the target's site-packages (manylinux
    # binary wheels only), then verify structurally.
    print("[linux] host cannot execute the linux interpreter - cross-installing dependencies", flush=True)
    r = sh([sys.executable, "-m", "pip", "install",
            "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_28_x86_64",
            "--implementation", "cp", "--python-version", "3.10",
            "--only-binary=:all:", "--target", site_packages,
            *PIP_PINS])
    strip_fmod(site_packages)
    if r.returncode == 0 and cross_verify(site_packages):
        print("[linux] runtime cross-installed (structural verification only - the interpreter cannot run on this host)", flush=True)
        return "structural"
    print("WARNING: linux deps could not be installed on this host - the runtime is "
          "STRUCTURAL-ONLY (no UnityPy/Pillow); build the linux package with --skip-runtime", file=sys.stderr, flush=True)
    return "structural"




def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--os", dest="target", choices=["win", "linux", "all"], default="all",
                    help="which runtime payload to fetch (default: all)")
    args = ap.parse_args()
    targets = ["win", "linux"] if args.target == "all" else [args.target]

    statuses: dict[str, str] = {}
    for t in targets:
        statuses[t] = fetch_win() if t == "win" else fetch_linux()

    failures = []
    for name in targets:
        status = statuses[name]
        print(f"[summary] {name}: {status}", flush=True)
        if status == "fail":
            failures.append(name)
    if failures:
        print("FAILED: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
