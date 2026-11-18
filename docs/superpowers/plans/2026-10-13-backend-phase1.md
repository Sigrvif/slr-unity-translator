# SLR Unity Translator — Backend (Plan 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the self-contained Python backend at `<dev-root>/SLR Unity Translator\backend\` — the existing PoC scanner/patcher extended to all five in-scope string sources, behind a manifest/result JSON CLI.

**Architecture:** The proven PoC modules (`ut/`) move over unchanged in spirit and get three new modules: `ut/metadata.py` (IL2CPP literal read/rebuild), `ut/textfiles.py` (JSON/CSV/line extraction for TextAssets and StreamingAssets files), and `slr_cli.py` (manifest/result envelope). JS integration is Plan 2; this plan's user is a test suite and a shell.

**Tech Stack:** Python 3.10+, UnityPy 1.25.2, Pillow, pytest.

**Spec:** `<dev-root>/SLR Unity Translator\docs\superpowers\specs\2026-10-13-unity-addon-design.md` — §2.2 (CLI contract), §3.3 (five sources), §6 (error handling), §7 (testing). This plan argues from the spec; read both.

## Global Constraints

- **No git.** Never run `git init` or `git commit`. Every task's final step snapshots the `backend/` folder into `_snapshots/` as a zip (exact command given per task).
- Python 3.10+ (the embedded runtime targets 3.10). Pin `UnityPy==1.25.2`, `Pillow>=10.0`.
- CLI contract version is `1` (`CONTRACT_VERSION = 1` in `slr_cli.py`); scan/patch payloads carry the same number.
- Example games live under `<example-games-root>\` and may be absent — every game-dependent test must skip cleanly via the shared `conftest.py` helper (Task 1). Tests must never depend on games being present to pass overall.
- Dev shell is Git Bash on Windows: use raw strings for Windows paths in Python.
- The backend must remain importable and unit-testable with no game files present.
- Existing PoC source of truth: `<poc-source-root>\unity_translator\ut\` (rawstrings.py, jp.py, discovery.py, files.py, inject.py, textures.py, fonts.py, __init__.py). Copy, do not modify the PoC in place.

## File Structure (end state of this plan)

```
<dev-root>/SLR Unity Translator\
  backend\
    slr_cli.py          manifest/result envelope, command dispatch, progress, logging
    requirements.txt
    ut\
      __init__.py       (copied)
      jp.py             (copied)
      rawstrings.py     (copied)
      files.py          (copied, then extended: StreamingAssets text enumeration)
      discovery.py      (copied, then extended: sources 3/4/5, key field, report)
      inject.py         (copied, then extended: write-back routing for new sources)
      textures.py       (copied)
      fonts.py          (copied)
      metadata.py       NEW — IL2CPP global-metadata literal read/rebuild
      textfiles.py      NEW — JSON/CSV/line extraction+apply
    tests\
      conftest.py
      test_rawstrings.py
      test_metadata.py
      test_textfiles.py
      test_discovery_sources.py
      test_inject_roundtrip.py
      test_cli.py
      test_e2e_game.py
```

---

### Task 1: Scaffold the backend module

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_scaffold.py`
- Copy: `<poc-source-root>\unity_translator\ut\` → `backend\ut\` (8 files, unchanged)

**Interfaces:**
- Consumes: the PoC `ut` package as-is.
- Produces: importable `backend.ut` for all later tasks; `conftest.py` exposing `game_dir()` / `game2_dir()` / `has_game` fixtures used by every game-dependent test in this plan.

- [ ] **Step 1: Copy the PoC package and pin requirements**

```bash
mkdir -p "<dev-root>/SLR Unity Translator/backend/tests"
cp "<bugreport>/unity translation/unity_translator/ut"/*.py "<dev-root>/SLR Unity Translator/backend/ut/" 2>/dev/null || (mkdir -p "<dev-root>/SLR Unity Translator/backend/ut" && cp "<bugreport>/unity translation/unity_translator/ut"/*.py "<dev-root>/SLR Unity Translator/backend/ut/")
printf 'UnityPy==1.25.2\nPillow>=10.0\npytest>=7.0\n' > "<dev-root>/SLR Unity Translator/backend/requirements.txt"
python -m pip install -r "<dev-root>/SLR Unity Translator/backend/requirements.txt"
```

- [ ] **Step 2: Write conftest with game-path helpers**

`backend/tests/conftest.py`:

```python
import glob
import os

import pytest

_EXAMPLES = r"<example-games-root>"

def _find(pred):
    for p in sorted(glob.glob(os.path.join(_EXAMPLES, "*"))):
        if os.path.isdir(p) and pred(p):
            return p
    return None

STEALTH = _find(lambda p: "the example IL2CPP game" in os.path.basename(p))
MONO = _find(lambda p: "the example IL2CPP game" not in os.path.basename(p))

has_game = pytest.mark.skipif(STEALTH is None, reason="example game not present")
has_mono = pytest.mark.skipif(MONO is None, reason="mono example game not present")

@pytest.fixture
def game_dir():
    return STEALTH

@pytest.fixture
def game2_dir():
    return MONO
```

- [ ] **Step 3: Write the smoke test**

`backend/tests/test_scaffold.py`:

```python
def test_ut_imports():
    from ut import discovery, files, fonts, inject, jp, rawstrings, textures
    assert callable(discovery.discover)
    assert callable(rawstrings.scan_strings)
    assert callable(inject.patch_game)

def test_rawstrings_finds_known_record():
    # Locked-in PoC behavior: Unity strings are int32 byte length + UTF-8 + zero pad.
    from ut import rawstrings
    buf = (6).to_bytes(4, "little") + "はい".encode("utf-8")  # 6 bytes, pad 2
    buf += b"\x00\x00"
    hits = rawstrings.scan_strings(buf, only_japanese=True)
    assert [h.text for h in hits] == ["はい"]
    assert hits[0].offset == 0 and hits[0].length == 6
```

- [ ] **Step 4: Run from the backend folder and verify pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_scaffold.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && mkdir -p _snapshots && python -c "import shutil; shutil.make_archive(r'_snapshots/task01-scaffold', 'zip', '.', 'backend')"
```

---

### Task 2: `ut/metadata.py` — reader (IL2CPP literal extraction)

Design note (read this before implementing): `global-metadata.dat` starts with `int32 sanity = 0xFAB11BAF`, `int32 version`, then a run of `(int32 offset, int32 size)` pairs. We do NOT trust per-version field-name tables — the string-literal tables are located **empirically and validated**: find a pair `(o_entries, s_entries)` with `s_entries % 8 == 0` and `s_entries >= 80`, interpreted as `count = s_entries // 8` entries of `(u32 data_index, u32 byte_len)` pointing into a *later* pair `(o_blob, s_blob)`, such that ≥ 95% of entries are in-bounds and decode as non-empty UTF-8 with byte lengths 1–2048. The identifier-string blob pair alone can't satisfy this (it has no entry table); binary tables (events/methods/...) fail the decode rate. On the real game this must yield the literals including `研究施設へようこそ` — that assertion is the calibration gate.

**Files:**
- Create: `backend/ut/metadata.py`
- Test: `backend/tests/test_metadata.py`

**Interfaces:**
- Produces (used by Tasks 4, 6, 7):
  - `class MetadataError(RuntimeError)`
  - `@dataclass Literal: index: int; data_offset: int; byte_len: int; text: str`  (`data_offset` is relative to the literal blob start)
  - `read_literals(data: bytes) -> list[Literal]` — raises `MetadataError` on bad sanity / implausible version / no validated table.
  - `locate_tables(data: bytes) -> tuple[int, int]` — returns `(entries_pair_index, blob_pair_index)`; pair `i` occupies bytes `8 + i*8 .. 8 + i*8 + 8`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_metadata.py`:

```python
import pytest

from ut.metadata import MetadataError, read_literals, locate_tables

def _build(entries, texts):
    """Synthetic file: sanity, version, [entries pair, blob pair], entries, blob.
    Needs >= 12 literals so the locator's minimum-entry threshold passes."""
    import struct
    if len(texts) < 12:
        texts = list(texts) + [f"filler-{i}" for i in range(12 - len(texts))]
    blob = b"".join(t.encode("utf-8") for t in texts)
    ent = b"".join(
        struct.pack("<II", sum(len(t.encode()) for t in texts[:i]), len(t.encode()))
        for i, t in enumerate(texts)
    )
    e_off, b_off = 16 + 16, 16 + 16 + len(ent)
    header = struct.pack("<ii", 0xFAB11BAF, 29)
    header += struct.pack("<ii", e_off, len(ent))   # pair 0: entries table
    header += struct.pack("<ii", b_off, len(blob))   # pair 1: literal blob
    return header + ent + blob

def test_reader_extracts_synthetic():
    data = _build(None, ["start", "研究施設へようこそ", "end"])
    lits = read_literals(data)
    assert len(lits) >= 12                       # padded past the locator threshold
    assert [l.text for l in lits[:3]] == ["start", "研究施設へようこそ", "end"]
    assert lits[1].index == 1 and lits[1].byte_len == len("研究施設へようこそ".encode())

def test_reader_rejects_bad_sanity():
    data = bytearray(_build(None, ["x" * 20]))
    data[0:4] = (0x12345678).to_bytes(4, "little")
    with pytest.raises(MetadataError):
        read_literals(bytes(data))

def test_reader_rejects_no_table():
    import struct
    data = struct.pack("<ii", 0xFAB11BAF, 29) + b"\x00" * 64
    with pytest.raises(MetadataError):
        read_literals(data)

def test_reader_on_real_game(game_dir, has_game):
    import os
    path = os.path.join(game_dir, [d for d in os.listdir(game_dir) if d.endswith("_Data")][0],
                        "il2cpp_data", "Metadata", "global-metadata.dat")
    data = open(path, "rb").read()
    lits = read_literals(data)
    texts = {l.text for l in lits}
    assert "研究施設へようこそ" in texts          # calibration anchor
    assert sum(1 for t in texts if any(0x3040 <= ord(c) <= 0x9FFF for c in t)) > 100
```

Note: `_build` ignores its first parameter (kept for call-site compatibility) — implement exactly as shown.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_metadata.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'ut.metadata'`.

- [ ] **Step 3: Implement `ut/metadata.py`**

```python
"""IL2CPP global-metadata.dat string literal reader/rebuilder.

The string-literal tables are located empirically (validated entry-table
+ blob pair) rather than via per-version field-name tables, so any
metadata version with the standard pair header works. Calibration:
the example IL2CPP game (v39) yields the known literals.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

SANITY = 0xFAB11BAF
MIN_VERSION, MAX_VERSION = 16, 45


class MetadataError(RuntimeError):
    pass


@dataclass
class Literal:
    index: int
    data_offset: int   # relative to the literal blob start
    byte_len: int
    text: str


def _pairs(data: bytes):
    n = (len(data) - 8) // 8
    for i in range(n):
        off, size = struct.unpack_from("<ii", data, 8 + i * 8)
        yield i, off, size


def locate_tables(data: bytes) -> tuple[int, int]:
    pair_list = list(_pairs(data))
    for ei, e_off, e_size in pair_list:
        if e_size < 80 or e_size % 8:
            continue
        count = e_size // 8
        if count < 10 or e_off < 0 or e_off + e_size > len(data):
            continue
        for bi, b_off, b_size in pair_list:
            if bi <= ei or b_size < count:
                continue
            ok = 0
            for k in range(count):
                d_index, blen = struct.unpack_from("<II", data, e_off + k * 8)
                if 1 <= blen <= 2048 and d_index + blen <= b_size:
                    try:
                        data[b_off + d_index : b_off + d_index + blen].decode("utf-8")
                        ok += 1
                    except UnicodeDecodeError:
                        pass
            if ok / count >= 0.95:
                return ei, bi
    raise MetadataError("no validated string-literal table found")


def read_literals(data: bytes) -> list[Literal]:
    sanity, version = struct.unpack_from("<ii", data, 0)
    if sanity != SANITY:
        raise MetadataError(f"bad sanity {sanity:#x}")
    if not (MIN_VERSION <= version <= MAX_VERSION):
        raise MetadataError(f"unsupported metadata version {version}")
    ei, bi = locate_tables(data)
    e_off, e_size = struct.unpack_from("<ii", data, 8 + ei * 8)
    b_off, _b_size = struct.unpack_from("<ii", data, 8 + bi * 8)
    out = []
    for k in range(e_size // 8):
        d_index, blen = struct.unpack_from("<II", data, e_off + k * 8)
        raw = data[b_off + d_index : b_off + d_index + blen]
        out.append(Literal(k, d_index, blen, raw.decode("utf-8", "replace")))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_metadata.py -v
```
Expected: 4 passed (game test skips if the example game is absent; if it RUNS and fails the calibration anchor, the empirical locator picked a wrong pair — tighten the validator: raise the pass rate to 0.98 and require `blen >= 2`, then re-run; do not proceed with a failing anchor).

- [ ] **Step 5: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task02-metadata-reader', 'zip', '.', 'backend')"
```

---

### Task 3: `ut/metadata.py` — writer (literal blob rebuild)

Rebuild strategy (spec §3.3 source 3), **unified for both reader shapes** (Task 2
found the real v39 game uses shape B — u32 offsets table, no blob pair):

- Build the new entries table and new blob from `replacements` (same entry
  count as the original — untouched literals keep their original bytes).
- Append `[new entries table][pad to 4][new blob]` at EOF, entries first
  and blob immediately after — this preserves shape B's contiguity
  invariant (blob = region right after the entries table) with **zero
  reader changes**, and works identically for shape A.
- Update the entries header pair: offset → new entries offset, size
  unchanged (count fixed). Shape A only: also update the blob pair
  (offset → new blob offset, size → new blob size).
- Old tables/blob become dead bytes at their original locations; the file
  grows; nothing shifts; no other table is touched. Code references
  literals by index, so nothing else changes.

**Files:**
- Modify: `backend/ut/metadata.py` (add `patch_literals`)
- Test: `backend/tests/test_metadata.py` (extend)

**Interfaces:**
- Consumes: `locate_tables`, `read_literals` from Task 2.
- Produces: `patch_literals(data: bytes, replacements: dict[int, str]) -> bytes` — maps literal **index** → new text; raises `MetadataError` for an index out of range. Untouched literals must be byte-identical after a no-op patch.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_metadata.py`:

```python
from ut.metadata import patch_literals

def test_patch_roundtrip_synthetic():
    data = _build(None, ["start", "研究施設へようこそ", "end"])
    out = patch_literals(data, {1: "Welcome to the research facility."})
    lits = read_literals(out)
    assert lits[0].text == "start"
    assert lits[1].text == "Welcome to the research facility."
    assert lits[2].text == "end"

def test_patch_noop_is_stable():
    data = _build(None, ["start", "研究施設へようこそ", "end"])
    out = patch_literals(data, {})
    assert read_literals(out) == read_literals(data)

def test_patch_shorter_and_longer():
    data = _build(None, ["あ" * 10, "い" * 3])
    out = patch_literals(data, {0: "x", 1: "a much longer english replacement string"})
    texts = [l.text for l in read_literals(out)]
    assert texts == ["x", "a much longer english replacement string"]

def test_patch_rejects_bad_index():
    data = _build(None, ["start"])
    try:
        patch_literals(data, {99: "no"})
        assert False, "expected MetadataError"
    except MetadataError:
        pass

def test_patch_on_real_game(game_dir, has_game):
    import os
    path = os.path.join(game_dir, [d for d in os.listdir(game_dir) if d.endswith("_Data")][0],
                        "il2cpp_data", "Metadata", "global-metadata.dat")
    data = open(path, "rb").read()
    lits = read_literals(data)
    target = next(l for l in lits if "研究施設へようこそ" in l.text)
    out = patch_literals(data, {target.index: "Welcome to the research facility."})
    re = read_literals(out)
    assert re[target.index].text == "Welcome to the research facility."
    same = sum(1 for a, b in zip(lits, re) if a.index == b.index and a.text == b.text)
    assert same >= len(lits) - 1          # every other literal untouched
    assert len(out) > len(data)           # blob was appended
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_metadata.py -v -k patch
```
Expected: FAIL — `ImportError: cannot import name 'patch_literals'`.

- [ ] **Step 3: Implement `patch_literals`**

Append to `backend/ut/metadata.py`:

```python
def patch_literals(data: bytes, replacements: dict[int, str]) -> bytes:
    sanity, version = struct.unpack_from("<Ii", data, 0)
    if sanity != SANITY:
        raise MetadataError(f"bad sanity {sanity:#x}")
    ei, bi = locate_tables(data)
    e_off, e_size = _pair(data, ei)
    count = e_size // 8 if bi >= 0 else e_size // 4
    for idx in replacements:
        if not (0 <= idx < count):
            raise MetadataError(f"literal index {idx} out of range (count={count})")

    lits = read_literals(data)
    new_entries = bytearray()
    new_blob = bytearray()
    for k, lit in enumerate(lits):
        payload = (replacements[k] if k in replacements else lit.text).encode("utf-8")
        if bi >= 0:   # shape A: (u32 offset-into-blob, u32 length) records
            new_entries += struct.pack("<II", len(new_blob), len(payload))
        else:         # shape B: u32 offsets-into-blob (lengths = differences)
            new_entries += struct.pack("<I", len(new_blob))
        new_blob += payload
    while len(new_blob) % 4:
        new_blob += b"\x00"

    # Relocate entries+blob to EOF as one contiguous block; repoint the
    # entries pair (shape A: and the blob pair). Old data becomes dead bytes.
    out = bytearray(data)
    pad = (-len(out)) % 4
    out += b"\x00" * pad
    new_e_off = len(out)
    out += new_entries
    new_b_off = len(out)
    out += new_blob
    struct.pack_into("<ii", out, 8 + ei * 8, new_e_off, len(new_entries))
    if bi >= 0:
        struct.pack_into("<ii", out, 8 + bi * 8, new_b_off, len(new_blob))
    return bytes(out)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_metadata.py -v
```
Expected: all passed (game tests skip when absent).

- [ ] **Step 5: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task03-metadata-writer', 'zip', '.', 'backend')"
```

---

### Task 4: `ut/textfiles.py` — JSON / CSV / line extraction and apply

**Files:**
- Create: `backend/ut/textfiles.py`
- Test: `backend/tests/test_textfiles.py`

**Interfaces:**
- Produces (used by Tasks 5, 6):
  - `@dataclass TextItem: key: str; text: str`
  - `@dataclass Extraction: format: str; items: list[TextItem]`  (`format` ∈ `"json" | "csv" | "lines"`)
  - `extract(content: bytes) -> Extraction` — never raises for text input; falls back to `"lines"` when content is not valid UTF-8 (items empty) or JSON/CSV parsing fails. Filtering by Japanese is the caller's job; `extract` returns every leaf string / cell / line candidate as-is.
  - `apply(content: bytes, translations: dict[str, str]) -> tuple[bytes, bool, bool]` — `(new_content, any_changed, formatting_changed)`. `translations` maps the `TextItem.key` of the original extraction to replacement text. JSON keys are JSON-pointer-style paths (`$.dialogues[3].text`), CSV keys are `r<row>c<col>`, line keys are `L<lineno>` (0-based).
  - JSON policy: parse with `json.loads`; reserialize with `json.dumps(..., ensure_ascii=False)` preserving key order (dicts preserve insertion order); compact output when the original contains no `", "`/`": "` sequences. `formatting_changed` is always `True` for JSON when `any_changed` (spec §8 item 8: every formatting change is reported).
  - CSV policy: dialect sniffed via `csv.Sniffer` on the first 4 KB (fallback `excel`); round-trip through `csv.reader`/`csv.writer`; `formatting_changed=False` unless quoting style changed.
  - Lines policy: replace the exact original line content (whole-line match after `rstrip`-preserving replacement — the newline bytes are preserved); `formatting_changed=False`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_textfiles.py`:

```python
from ut.textfiles import extract, apply

def test_json_extract_and_apply():
    content = b'{"dialogues":[{"name":"\xe7\x9c\x9f\xe7\x90\x86","text":"\xe3\x82\x88\xe3\x81\x86\xe3\x81\x93\xe3\x81\x9d"}]}'
    ex = extract(content)
    assert ex.format == "json"
    texts = {i.key: i.text for i in ex.items}
    assert texts["$.dialogues[0].name"] == "真理"
    new, changed, fmt = apply(content, {"$.dialogues[0].text": "Welcome"})
    assert changed and fmt
    assert '"Welcome"' in new.decode("utf-8")

def test_json_preserves_key_order():
    content = '{"b":"1","a":"\xe3\x81\x82"}'.encode()
    ex = extract(content)
    assert {i.key for i in ex.items} == {"$.b", "$.a"}
    new, _, _ = apply(content, {"$.a": "A"})
    s = new.decode("utf-8")
    assert s.index('"b"') < s.index('"a"')      # order kept

def test_jsonc_falls_back_to_lines():
    content = b'// comment\n{"a":"\xe3\x81\x82"}'
    ex = extract(content)
    assert ex.format == "lines"

def test_csv_extract_and_apply():
    content = "name,line\n真理,こんにちは\n".encode("utf-8")
    ex = extract(content)
    assert ex.format == "csv"
    texts = {i.key: i.text for i in ex.items}
    assert texts["r1c0"] == "真理" and texts["r1c1"] == "こんにちは"
    new, changed, fmt = apply(content, {"r1c1": "Hello"})
    assert changed and not fmt
    assert "Hello" in new.decode("utf-8")

def test_lines_extract_and_apply_preserves_newlines():
    content = "first\nこんにちは\r\nlast\n".encode("utf-8")
    ex = extract(content)
    assert ex.format == "lines"
    keys = {i.key: i.text for i in ex.items}
    assert keys["L1"] == "こんにちは"
    new, changed, fmt = apply(content, {"L1": "Hello"})
    assert changed and not fmt
    assert new == "first\nHello\r\nlast\n".encode("utf-8")

def test_binary_content_yields_empty_lines():
    ex = extract(b"\x00\x01\x02\xff\xfe")
    assert ex.format == "lines" and ex.items == []

def test_apply_untouched_returns_unchanged():
    content = '{"a":"\xe3\x81\x82"}'.encode()
    new, changed, fmt = apply(content, {})
    assert new == content and not changed and not fmt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_textfiles.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'ut.textfiles'`.

- [ ] **Step 3: Implement `ut/textfiles.py`**

```python
"""Text extraction/rewrite for TextAsset payloads and plain text files.

Format handlers: JSON (parse + reserialize, key order preserved), CSV
(dialect-sniffed round-trip), line-based fallback (JSONC and anything
else). Japanese filtering is the caller's job; these functions are pure.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field


@dataclass
class TextItem:
    key: str
    text: str


@dataclass
class Extraction:
    format: str
    items: list[TextItem] = field(default_factory=list)


def _walk_json(node, path="$", out=None):
    if out is None:
        out = []
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_json(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_json(v, f"{path}[{i}]", out)
    elif isinstance(node, str):
        out.append(TextItem(path, node))
    return out


def _set_json_path(node, path: str, value: str):
    import re
    tokens = re.findall(r"\.([^.\[]+)|\[(\d+)\]", path[2:])
    cur = node
    for name, idx in tokens[:-1]:
        cur = cur[int(idx)] if idx else cur[name]
    name, idx = tokens[-1]
    if idx:
        cur[int(idx)] = value
    else:
        cur[name] = value


def extract(content: bytes) -> Extraction:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return Extraction("lines", [])
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            obj = json.loads(text)
            return Extraction("json", _walk_json(obj))
        except ValueError:
            pass  # JSONC or malformed -> lines
    first_lines = text[:4096]
    if first_lines.count("\n") >= 1 and "," in first_lines:
        try:
            dialect = csv.Sniffer().sniff(first_lines)
            rows = list(csv.reader(io.StringIO(text), dialect))
            if rows and all(isinstance(c, str) for c in rows[0]):
                items = [TextItem(f"r{r}c{c}", cell)
                         for r, row in enumerate(rows) for c, cell in enumerate(row) if cell]
                return Extraction("csv", items)
        except csv.Error:
            pass
    items = [TextItem(f"L{i}", line) for i, line in enumerate(text.splitlines()) if line]
    return Extraction("lines", items)


def apply(content: bytes, translations: dict[str, str]) -> tuple[bytes, bool, bool]:
    ex = extract(content)
    if not translations:
        return content, False, False
    if ex.format == "json":
        text = content.decode("utf-8")
        obj = json.loads(text)
        changed = False
        for key, new_text in translations.items():
            try:
                _set_json_path(obj, key, new_text)
                changed = True
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        if not changed:
            return content, False, False
        compact = b'": ' not in content and b'", "' not in content
        dumps = json.dumps(obj, ensure_ascii=False,
                           separators=(",", ":") if compact else (",", ": "))
        return dumps.encode("utf-8"), True, True
    if ex.format == "csv":
        text = content.decode("utf-8")
        try:
            dialect = csv.Sniffer().sniff(text[:4096])
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        changed_any = False
        for key, new_text in translations.items():
            if not (key.startswith("r") and "c" in key):
                continue
            try:
                r_s, c_s = key[1:].split("c")
                rows[int(r_s)][int(c_s)] = new_text
                changed_any = True
            except (ValueError, IndexError):
                pass
        if not changed_any:
            return content, False, False
        buf = io.StringIO()
        csv.writer(buf, dialect).writerows(rows)
        return buf.getvalue().encode("utf-8"), True, False
    # lines
    text = content.decode("utf-8")
    parts = text.splitlines(keepends=True)
    changed = False
    for key, new_text in translations.items():
        if not key.startswith("L"):
            continue
        try:
            i = int(key[1:])
        except ValueError:
            continue
        if 0 <= i < len(parts):
            ending = parts[i][len(parts[i].rstrip("\r\n")):]
            parts[i] = new_text + ending
            changed = True
    if not changed:
        return content, False, False
    return "".join(parts).encode("utf-8"), True, False
```

(The CSV branch above is the clean version — the naive `r1c1` key parse in one loop, no dead variables.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_textfiles.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task04-textfiles', 'zip', '.', 'backend')"
```

---

### Task 5: Discovery extension — wire in sources 3, 4, 5

**Files:**
- Modify: `backend/ut/files.py` (add `find_text_files(data_dir)`)
- Modify: `backend/ut/discovery.py` (add metadata/TextAsset/plain-file scanning, `key` field, suspect report)
- Test: `backend/tests/test_discovery_sources.py`

**Interfaces:**
- Consumes: `read_literals` (Task 2), `extract`/`Extraction` (Task 4), existing `discover`/`StringHit`.
- Produces:
  - `StringHit` gains `key: str = ""` — for `source="textasset"`/`"textfile"` hits: the `TextItem.key`; unused for other sources.
  - `StringHit.source` may now be `"metadata"` (path_id = literal index, offset = `data_offset`, length = `byte_len`, context = `literal #<index>`), `"textasset"` (path_id = object's path_id, context = `<asset name> <key>`, e.g. `IntroDialog $.scenes[2].text`), `"textfile"` (path_id = 0, context = `<relpath> <key>`).
  - `discover(game_dir, ...)` unchanged signature; `DiscoveryReport` gains `suspect: list[str]` — plain files / TextAssets that contain Japanese characters but yielded zero passing items (spec §3.3 source 5's safety report).
  - `find_text_files(data_dir: str) -> list[str]` — files under `StreamingAssets/` with extensions `{.json, .jsonc, .txt, .csv, .bytes}`, size < 4 MB.
  - `export_report` occurrences now include `"key": h.key`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_discovery_sources.py`:

```python
import json
import os

import pytest

from ut import discovery


def test_metadata_source_on_real_game(game_dir, has_game):
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)
    meta_hits = [h for h in rep.strings if h.source == "metadata"]
    assert meta_hits, "expected IL2CPP literal hits"
    texts = {h.text for h in meta_hits}
    assert any("研究施設" in t for t in texts)
    sample = meta_hits[0]
    assert sample.context.startswith("literal #") and sample.length > 0


def test_textfile_source_on_real_game(game_dir, has_game):
    # The example game ships save.jsonc; even if it yields no strings the
    # scan must not crash on it and must enumerate it.
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)
    files = {os.path.relpath(h.file, game_dir) for h in rep.strings}
    # asset-based files must still be there
    assert any(f.endswith("level0") for f in files)


def test_textfile_extraction_synthetic(tmp_path):
    game = tmp_path / "Fake_Data"
    (game / "StreamingAssets").mkdir(parents=True)
    (game / "StreamingAssets" / "dialog.json").write_text(
        json.dumps({"lines": ["こんにちは", "さようなら"]}, ensure_ascii=False), encoding="utf-8"
    )
    rep = discovery._scan_text_sources(str(tmp_path))
    hits = [h for h in rep if h.source == "textfile"]
    assert {h.text for h in hits} == {"こんにちは", "さようなら"}
    assert hits[0].key == "$.lines[0]" or hits[0].key == "$.lines[1]"


def test_suspect_report_synthetic(tmp_path):
    game = tmp_path / "Fake_Data"
    (game / "StreamingAssets").mkdir(parents=True)
    (game / "StreamingAssets" / "weird.txt").write_bytes(
        "Привет 日本 \x01\x02".encode("utf-8")  # has JP, fails looks_like_text
    )
    suspects = discovery._suspect_files(str(tmp_path))
    assert any("weird.txt" in s for s in suspects)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_discovery_sources.py -v
```
Expected: FAIL — `AttributeError: module 'ut.discovery' has no attribute '_scan_text_sources'` (imports succeed; calls fail).

- [ ] **Step 3: Implement**

`backend/ut/files.py` — append:

```python
TEXT_EXTS = {".json", ".jsonc", ".txt", ".csv", ".bytes"}
MAX_TEXT_FILE = 4 * 1024 * 1024

def find_text_files(data_dir: str) -> list[str]:
    """Candidate plain-text files under StreamingAssets."""
    sa = os.path.join(data_dir, "StreamingAssets")
    out: list[str] = []
    if not os.path.isdir(sa):
        return out
    for root, _dirs, names in os.walk(sa):
        for n in names:
            p = os.path.join(root, n)
            if os.path.splitext(n)[1].lower() in TEXT_EXTS and os.path.getsize(p) <= MAX_TEXT_FILE:
                out.append(p)
    return out
```

`backend/ut/discovery.py` — changes:

1. Add `key: str = ""` as the last field of `StringHit`.
2. Add `suspect: list[str] = field(default_factory=list)` to `DiscoveryReport`.
3. New public function `_scan_text_sources(game_dir: str) -> list[StringHit]`:

```python
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
        ex = tf_extract(content)
        for item in ex.items:
            if item.text and _jp.contains_japanese(item.text) and _jp.looks_like_text(item.text):
                hits.append(StringHit(file=path, path_id=0, obj_type="TextFile",
                                      field=item.key, tokens=[], source="textfile",
                                      offset=0, length=0, text=item.text,
                                      context=f"{rel} {item.key}", key=item.key))
    return hits
```

4. New public function `_suspect_files(game_dir: str) -> list[str]` listing text files (and TextAssets during the main scan) that contain Japanese characters but produced zero passing items — iterate `find_text_files` + `find_asset_files` TextAssets, decode, check `contains_japanese` on the raw text, and record `f"{rel} (jp chars present, no translatable items)"` when no hit exists.
5. In `discover()`, after the per-file loop, append `_scan_text_sources(game_dir)` hits; if the game is IL2CPP (a `global-metadata.dat` exists under `il2cpp_data/Metadata`), append source-3 hits:

```python
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
        if _jp.contains_japanese(lit.text) and _jp.looks_like_text(lit.text):
            out.append(StringHit(file=path, path_id=lit.index, obj_type="Metadata",
                                  field=f"literal[{lit.index}]", tokens=[], source="metadata",
                                  offset=lit.data_offset, length=lit.byte_len, text=lit.text,
                                  context=f"literal #{lit.index}"))
    return out
```

6. Extend `_scan_file`'s existing TextAsset branch: after cataloguing, run `tf_extract` on `m_Script` and emit `source="textasset"` hits with `context=f"{name} {item.key}"`, `key=item.key`, `path_id=obj.path_id`.
7. In `discover()`, after the per-file loop, wire the new sources in exactly this shape:

```python
    try:
        report.strings.extend(_scan_text_sources(game_dir))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"text sources: {e!r}")
    meta_path = os.path.join(data_dir, "il2cpp_data", "Metadata", "global-metadata.dat")
    if os.path.isfile(meta_path):
        try:
            report.strings.extend(_scan_metadata_literals(game_dir, data_dir))
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"metadata literals: {e!r}")
    report.suspect = _suspect_files(game_dir)
```

8. `export_report`: add `"key": h.key` to the occurrence dict.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_discovery_sources.py tests/test_scaffold.py -v
```
Expected: all passed/skipped (game tests skip when absent). Also re-run the PoC-level check that nothing regressed:

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python - <<'EOF'
import sys; sys.path.insert(0, ".")
from ut import discovery
rep = discovery.discover(r"<example-games-root>\the example IL2CPP game", scan_textures=False, scan_fonts=False)
from collections import Counter
print(Counter(h.source for h in rep.strings))
EOF
```
Expected: `typetree` ~182, `raw` ~3265, `metadata` >100, `textfile`/`textasset` ≥ 0 (example game may have none — the synthetic tests cover those).

- [ ] **Step 5: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task05-discovery', 'zip', '.', 'backend')"
```

---

### Task 6: Injection extension — write-back for sources 3, 4, 5

**Files:**
- Modify: `backend/ut/inject.py` (`patch_game` routing)
- Test: `backend/tests/test_inject_roundtrip.py`

**Interfaces:**
- Consumes: index occurrences with `source` ∈ `{"typetree", "raw", "metadata", "textasset", "textfile"}` and `key` for the last two (Task 5's `export_report` schema).
- Produces: `patch_game(game_dir, out_dir, translations, index_path, *, overwrite=False, progress=None) -> dict` — same signature; stats gain `"strings_written"` across all sources and `"metadata_patched"`, `"textassets_patched"`, `"textfiles_patched"` counters. Routing:
  - `"metadata"` → group by literal file; `patch_literals(file_bytes, {path_id: en})`; write result to `out_dir/<relpath>`. The metadata file is NOT copied by the existing `shutil.copytree(game_dir, out_dir)`? It IS copied (it's inside game_dir) — overwrite the copy with the patched bytes.
  - `"textasset"` → load the asset file in UnityPy, `obj.read_typetree()`, `tf_extract(m_Script)`, `tf.apply(m_Script, {key: en})`, write new bytes back into the tree (`tree["m_Script"] = new_bytes` — TextAsset typetree exposes `m_Script` as a byte string), `save_typetree`, resave file.
  - `"textfile"` → `tf.apply(file_bytes, {key: en})` on the copied file in `out_dir`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_inject_roundtrip.py`:

```python
import json
import os
import shutil

import pytest

from ut import discovery, inject


def _translations_for(rep, source, n=2):
    texts = [h.text for h in rep.strings if h.source == source][:n]
    return {t: f"[EN]{t[:8]}" for t in texts}


def test_roundtrip_all_sources(game_dir, has_game, tmp_path):
    rep = discovery.discover(game_dir, scan_textures=False, scan_fonts=False)
    out = tmp_path / "out_game"
    # export index the way slr_cli will
    idx = tmp_path / "index.json"
    occ = {}
    for h in rep.strings:
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, game_dir), "abs": h.file,
            "path_id": h.path_id, "source": h.source, "offset": h.offset,
            "length": h.length, "key": h.key, "tokens": h.tokens})
    idx.write_text(json.dumps(occ, ensure_ascii=False), encoding="utf-8")

    trans = {}
    for src in ("typetree", "raw", "metadata"):
        trans.update(_translations_for(rep, src))
    stats = inject.patch_game(str(game_dir), str(out), trans, str(idx))
    assert stats["strings_written"] >= 1

    # verify: patched game scans back with the translations present
    rep2 = discovery.discover(str(out), scan_textures=False, scan_fonts=False)
    texts2 = {h.text for h in rep2.strings}
    for t in trans.values():
        assert t in texts2, f"missing {t!r}"
    # originals gone
    for t in trans:
        assert t not in texts2 or t in trans.values()
```

The final assertion allows a translated string to coincide with another original; the load-bearing check is the first loop.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_inject_roundtrip.py -v
```
Expected: FAIL — stats lack the new counters / metadata strings never appear in the re-scan (`strings_written` only covers typetree+raw).

- [ ] **Step 3: Implement routing in `patch_game`**

Inside `patch_game`, alongside the existing `tt_edits`/`raw_edits` grouping loop, add:

```python
meta_edits: dict[str, dict[int, str]] = defaultdict(dict)      # file -> {literal_index: en}
ta_edits: dict[str, dict[int, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))  # file -> path_id -> {key: en}
tf_edits: dict[str, dict[str, str]] = defaultdict(dict)        # file -> {key: en}
```

and route occurrences:

```python
elif o["source"] == "metadata":
    meta_edits[o["abs"]][o["path_id"]] = en
elif o["source"] == "textasset":
    ta_edits[o["abs"]][o["path_id"]][o.get("key", "")] = en
elif o["source"] == "textfile":
    tf_edits[o["abs"]][o.get("key", "")] = en
```

After the UnityPy file loop, add three blocks (the game copy already exists at `out_dir`):

```python
from .metadata import patch_literals
from .textfiles import apply as tf_apply

for abs_file, per_literal in meta_edits.items():
    rel = os.path.relpath(abs_file, game_dir)
    out_file = os.path.join(out_dir, rel)
    data = open(abs_file, "rb").read()
    open(out_file, "wb").write(patch_literals(data, per_literal))
    strings_written += len(per_literal)
    files_modified += 1

for abs_file, per_obj in ta_edits.items():
    rel = os.path.relpath(abs_file, game_dir)
    out_file = os.path.join(out_dir, rel)
    env = UnityPy.load(abs_file)
    changed = False
    for obj in env.objects:
        keys = per_obj.get(obj.path_id)
        if not keys:
            continue
        tree = obj.read_typetree()
        script = tree.get("m_Script", b"")
        new_script, ch, _fmt = tf_apply(script, keys)
        if ch:
            tree["m_Script"] = new_script
            obj.save_typetree(tree)
            strings_written += len(keys)
            changed = True
    if changed:
        _save_env_to_file(env, out_file)
        files_modified += 1

for abs_file, keys in tf_edits.items():
    rel = os.path.relpath(abs_file, game_dir)
    out_file = os.path.join(out_dir, rel)
    content = open(abs_file, "rb").read()
    new_content, ch, _fmt = tf_apply(content, keys)
    if ch:
        open(out_file, "wb").write(new_content)
        strings_written += len(keys)
        files_modified += 1
```

Add the counters to the returned stats dict (`"metadata_patched": len(meta_edits)`, etc.). Include the new files in the progress total by replacing the `all_files` computation with:

```python
    all_files = sorted(set(tt_edits) | set(raw_edits) | set(meta_edits)
                       | set(ta_edits) | set(tf_edits))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_inject_roundtrip.py -v
```
Expected: 1 passed (skip when game absent).

- [ ] **Step 5: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task06-inject', 'zip', '.', 'backend')"
```

---

### Task 7: `slr_cli.py` — manifest/result envelope

**Files:**
- Create: `backend/slr_cli.py`
- Test: `backend/tests/test_cli.py`

**Interfaces:**
- Produces (the Plan 2 JS addon's entire backend surface):
  - `python slr_cli.py --manifest job.json --result result.json`
  - `CONTRACT_VERSION = 1`
  - Manifest: `{"version": 1, "command": "scan"|"patch"|"extract_textures", ...}` — all paths inside the manifest, never argv. Scan: `{"game_dir"}`. Patch: `{"game_dir", "out_dir", "translations_path" (JSON {jp:en}), "index_path", "font_ttf"?, "textures_manifest_dir"? (extract folder with manifest.json), "textures_dir"? (SEP-render folder)}`. Extract: `{"game_dir", "out_dir"}`.
  - Result: `{"ok": true, "version": 1, ...payload}` or `{"ok": false, "version": 1, "error": str, "traceback": str}`. Scan payload: `{"strings": [...StringHit as dicts...], "index": {jp: [occ...]}, "fonts": [...], "textures": [...], "suspect": [...], "stats": {...}}`. Patch payload: the `patch_game` stats. Extract payload: the `textures.extract_textures` manifest summary.
  - Progress: one JSON per line on stdout, `{"type": "progress", "done": int, "total": int, "file": str}`; non-JSON stdout lines from libraries are swallowed into the log.
  - Log: `backend.log` written next to the manifest; on failure the result includes `traceback`.
  - Version mismatch in the manifest (`version != 1`) → `ok: false` with `error: "unsupported contract version <v>"`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_cli.py`:

```python
import json
import os
import subprocess
import sys

CLI = "slr_cli.py"


def _run(manifest, tmp_path):
    m = tmp_path / "job.json"; m.write_text(json.dumps(manifest), encoding="utf-8")
    r = tmp_path / "result.json"
    p = subprocess.run([sys.executable, CLI, "--manifest", str(m), "--result", str(r)],
                       capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert p.returncode == 0, p.stderr
    return json.loads(r.read_text(encoding="utf-8"))

def test_bad_version(tmp_path):
    res = _run({"version": 99, "command": "scan", "game_dir": str(tmp_path)}, tmp_path)
    assert res["ok"] is False and "contract version" in res["error"]

def test_unknown_command(tmp_path):
    res = _run({"version": 1, "command": "nope", "game_dir": str(tmp_path)}, tmp_path)
    assert res["ok"] is False

def test_scan_empty_dir(tmp_path):
    res = _run({"version": 1, "command": "scan", "game_dir": str(tmp_path)}, tmp_path)
    assert res["ok"] is True and res["stats"]["files_scanned"] >= 0
    assert (tmp_path / "backend.log").exists()

def test_scan_real_game(game_dir, has_game, tmp_path):
    res = _run({"version": 1, "command": "scan", "game_dir": game_dir}, tmp_path)
    assert res["ok"] is True
    sources = {s["source"] for s in res["strings"]}
    assert "typetree" in sources and "metadata" in sources

def test_patch_real_game(game_dir, has_game, tmp_path):
    scan = _run({"version": 1, "command": "scan", "game_dir": game_dir}, tmp_path)
    assert scan["ok"]
    jp = next(s["text"] for s in scan["strings"] if s["source"] == "metadata")
    (tmp_path / "trans.json").write_text(json.dumps({jp: "ENTEST"}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(scan["index"], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "gameout"
    res = _run({"version": 1, "command": "patch", "game_dir": game_dir, "out_dir": str(out),
                "translations_path": str(tmp_path / "trans.json"), "index_path": str(tmp_path / "index.json")}, tmp_path)
    assert res["ok"] is True and res["strings_written"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_cli.py -v
```
Expected: FAIL — `slr_cli.py` missing.

- [ ] **Step 3: Extend `replace_source_font` and `import_textures` for in-place staging**

Both PoC functions are standalone "copy game → modify copy" operations (each `rmtree`s `out_dir` first). The CLI needs them as *stages* on the already-patched output, so add an in-place mode to each. In `backend/ut/fonts.py`, change the signature and guard:

```python
def replace_source_font(
    game_dir: str,
    out_dir: str,
    new_ttf_path: str,
    *,
    target_names: list[str] | None = None,
    clear_atlas: bool = False,
    overwrite: bool = False,
    in_place: bool = False,
    progress=None,
) -> dict:
```

When `in_place=True`: require `game_dir == out_dir` (raise `ValueError` otherwise), skip the exists-check/`rmtree`/`copytree` entirely, and operate directly on the directory. In `backend/ut/textures.py`, add the same `in_place: bool = False` parameter with identical semantics, plus `png_dir: str | None = None` — when given, PNGs are looked up in `png_dir` instead of `manifest_dir` (the SEP-render dir contains rendered PNGs under the same filenames; the manifest still comes from `manifest_dir`), and force-import every entry whose PNG exists there regardless of mtime (`import_all` semantics for `png_dir` mode — SEP's rendered files may predate our manifest).

- [ ] **Step 4: Implement `backend/slr_cli.py`**

```python
"""SLR Unity Translator backend CLI — manifest/result JSON envelope.

All paths travel inside the manifest file (non-ASCII-safe); progress is
one JSON object per line on stdout; logs go to backend.log next to the
manifest. See spec §2.2.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from dataclasses import asdict

CONTRACT_VERSION = 1


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _progress(done: int, total: int, rel: str) -> None:
    _emit({"type": "progress", "done": done, "total": total, "file": rel})


def cmd_scan(job: dict) -> dict:
    from ut import discovery
    rep = discovery.discover(job["game_dir"], progress=_progress)
    occ = {}
    for h in rep.strings:
        occ.setdefault(h.text, []).append({
            "file": os.path.relpath(h.file, job["game_dir"]).replace("\\", "/"),
            "path_id": h.path_id, "source": h.source, "offset": h.offset,
            "length": h.length, "key": h.key, "tokens": h.tokens})
    return {
        "strings": [asdict(h) | {"file": ""} for h in rep.strings],  # file lives in the index; keep the list light
        "index": occ,
        "fonts": [asdict(f) for f in rep.fonts],
        "textures": [asdict(t) for t in rep.textures],
        "suspect": rep.suspect,
        "stats": {"files_scanned": rep.files_scanned, "hits": len(rep.strings),
                  "unique": len(occ), "errors": len(rep.errors)},
    }


def cmd_patch(job: dict) -> dict:
    from ut import fonts, inject, textures
    translations = json.load(open(job["translations_path"], encoding="utf-8"))
    stats = inject.patch_game(job["game_dir"], job["out_dir"], translations,
                              job["index_path"], overwrite=True, progress=_progress)
    if job.get("font_ttf"):
        rep = fonts.replace_source_font(job["out_dir"], job["out_dir"], job["font_ttf"],
                                        in_place=True)
        stats["fonts_swapped"] = rep.get("fonts_replaced", 0)
    if job.get("textures_dir") and job.get("textures_manifest_dir"):
        res = textures.import_textures(job["out_dir"], job["out_dir"],
                                       job["textures_manifest_dir"],
                                       png_dir=job["textures_dir"], in_place=True)
        stats["textures_imported"] = res.get("imported", 0)
    return stats


def cmd_extract_textures(job: dict) -> dict:
    from ut import textures
    res = textures.extract_textures(job["game_dir"], job["out_dir"], progress=_progress)
    # Spec §5.1 names the mapping unity_img_manifest.json; the extract step
    # already writes manifest.json with exactly that mapping (file, path_id,
    # png) — copy it under the spec name for the JS layer / SEP handoff.
    mj = os.path.join(job["out_dir"], "manifest.json")
    if os.path.isfile(mj):
        shutil.copy2(mj, os.path.join(job["out_dir"], "unity_img_manifest.json"))
    return res


COMMANDS = {"scan": cmd_scan, "patch": cmd_patch, "extract_textures": cmd_extract_textures}


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
            result["error"] = f"unsupported contract version {job.get('version')!r}"
        else:
            fn = COMMANDS.get(job.get("command"))
            if fn is None:
                result["error"] = f"unknown command {job.get('command')!r}"
            else:
                payload = fn(job)
                result = {"ok": True, "version": CONTRACT_VERSION, **payload}
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
```

Manifest additions this defines beyond Task 7's Interfaces block: `patch` accepts optional `"textures_manifest_dir"` (the extract folder holding `manifest.json`) alongside `"textures_dir"` (the SEP-render folder). `extract_textures` returns the extract summary dict.

- [ ] **Step 5: Verify the font in-place stage manually (no automated font test — no TTF fixture)**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python - <<'EOF'
import json, os, subprocess, sys, tempfile
game = r"<example-games-root>\the example IL2CPP game"
td = tempfile.mkdtemp()
# reuse any system TTF as the replacement payload
ttf = r"C:\Windows\Fonts\arial.ttf"
m = {"version": 1, "command": "patch", "game_dir": game, "out_dir": os.path.join(td, "o"),
     "translations_path": None, "index_path": None}
open(os.path.join(td, "t.json"), "w", encoding="utf-8").write(json.dumps({}))
open(os.path.join(td, "i.json"), "w", encoding="utf-8").write(json.dumps({}))
m["translations_path"] = os.path.join(td, "t.json"); m["index_path"] = os.path.join(td, "i.json")
m["font_ttf"] = ttf
open(os.path.join(td, "job.json"), "w", encoding="utf-8").write(json.dumps(m))
p = subprocess.run([sys.executable, "slr_cli.py", "--manifest", os.path.join(td, "job.json"),
                    "--result", os.path.join(td, "res.json")], capture_output=True, text=True)
res = json.load(open(os.path.join(td, "res.json"), encoding="utf-8"))
print(res.get("ok"), res.get("fonts_swapped"), res.get("error"))
EOF
```
Expected: `True`, `fonts_swapped >= 3` (the game has 3 embedded fonts), no error — and the patched strings (empty translations) intact because the font stage ran in-place on the output copy. Skippable when the game is absent; note the result in the task PR-equivalent (snapshot name stays honest).

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_cli.py -v
```
Expected: all passed/skipped. The real-game scan takes ~2–3 min.

- [ ] **Step 7: Snapshot**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task07-cli', 'zip', '.', 'backend')"
```

---

### Task 8: End-to-end verification against the example game + backend README

**Files:**
- Create: `backend/tests/test_e2e_game.py`
- Create: `backend/README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the Plan 2 fixture — run the scan command once and save a trimmed payload (`strings` for level0 + first 50 metadata literals + index slice, < 1 MB) to `docs/fixtures/stealth-scan-sample.json` for the JS addon's unit tests.

- [ ] **Step 1: Write the E2E test**

`backend/tests/test_e2e_game.py`:

```python
import json
import os

import pytest

from test_cli import _run


def test_full_pipeline(game_dir, has_game, tmp_path):
    # 1. scan
    scan = _run({"version": 1, "command": "scan", "game_dir": game_dir}, tmp_path)
    assert scan["ok"]
    # 2. pick one translation per source family that exists
    want = {}
    for src in ("raw", "typetree", "metadata"):
        for s in scan["strings"]:
            if s["source"] == src:
                want[s["text"]] = f"[E2E]{src}:en"
                break
    assert want, "no translatable strings found"
    (tmp_path / "trans.json").write_text(json.dumps(want, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(scan["index"], ensure_ascii=False), encoding="utf-8")
    # 3. patch
    out = tmp_path / "game_out"
    patch = _run({"version": 1, "command": "patch", "game_dir": game_dir, "out_dir": str(out),
                  "translations_path": str(tmp_path / "trans.json"),
                  "index_path": str(tmp_path / "index.json")}, tmp_path)
    assert patch["ok"] and patch["strings_written"] >= len(want)
    # 4. rescan the patched copy: every translation present, every original gone
    rescan = _run({"version": 1, "command": "scan", "game_dir": str(out)}, tmp_path)
    assert rescan["ok"]
    texts = {s["text"] for s in rescan["strings"]}
    for en in want.values():
        assert en in texts
    for jp in want:
        assert jp not in texts
```

- [ ] **Step 2: Run it**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests/test_e2e_game.py -v
```
Expected: 1 passed (skips without the game). This copies the 3.7 GB game twice (patch + rescan staging is in-place on the copy); budget ~10 min.

- [ ] **Step 3: Write `backend/README.md`**

Content: one paragraph on what the backend is (spec §1), the CLI invocation with the three commands and a minimal example manifest per command, the contract-version rule, how to run tests (`python -m pytest tests -v`, note that game-dependent tests skip), and a note that runtime packaging (embedded Python) happens in Plan 2.

- [ ] **Step 4: Full suite green**

```bash
cd "<dev-root>/SLR Unity Translator/backend" && python -m pytest tests -v
```
Expected: everything passed or skipped, zero failures.

- [ ] **Step 5: Emit the Plan 2 fixture**

```bash
cd "<dev-root>/SLR Unity Translator" && mkdir -p docs/fixtures && python - <<'EOF'
import json, sys, os
sys.path.insert(0, "backend")
from ut import discovery
rep = discovery.discover(r"<example-games-root>\the example IL2CPP game", scan_textures=False, scan_fonts=False)
strings = [dict(text=h.text, source=h.source, path_id=h.path_id, context=h.context, key=h.key,
                offset=h.offset, length=h.length, tokens=h.tokens,
                file=os.path.relpath(h.file, r"<example-games-root>\the example IL2CPP game").replace("\\", "/"))
           for h in rep.strings]
level0 = [s for s in strings if s["file"].endswith("level0")]
meta = [s for s in strings if s["source"] == "metadata"][:50]
json.dump({"version": 1, "strings": level0 + meta},
          open("docs/fixtures/stealth-scan-sample.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("fixture:", len(level0), "level0 +", len(meta), "metadata strings")
EOF
```

- [ ] **Step 6: Snapshot (backend + docs)**

```bash
cd "<dev-root>/SLR Unity Translator" && python -c "import shutil; shutil.make_archive(r'_snapshots/task08-e2e', 'zip', '.', 'backend') && shutil.make_archive(r'_snapshots/task08-docs', 'zip', '.', 'docs')"
```

(If the one-liner with two calls errors in your shell, run two separate `make_archive` one-liners.)

---

## Plan 2 preview (not part of this plan)

`docs/superpowers/plans/<date>-addon-js.md` will cover: addon package scaffold + engine registration/detector; `createProject` (spawn `slr_cli.py scan` via `common.aSpawn` + manifest writer, build project object, stage index); `UnitySceneGraph.js` (capture-stock-and-delegate, scene index from `options.unity`); texture extraction menu action + SEP handoff; `exportHandler` (patch + fonts + texture re-import, dir/zip); `fetch_runtime.py` + `build_tap.py` (embedded Python win/linux, .tap assembly); JS unit tests against `docs/fixtures/stealth-scan-sample.json`; SLR E2E checklist. Written after this plan lands, using the real scan payload shape.

> **As-built note (Task 2):** the real v39 game stores literals as a u32
> offsets table (shape B), not the (data_index, byte_len)-pair shape the
> code below assumes; the shipped module supports both shapes behind the
> same interfaces and its locator differs from the snippet here. See
> `.sdd/2026-10-13-backend-phase1/task-2-report.md` and the module
> docstring for the as-built design.
