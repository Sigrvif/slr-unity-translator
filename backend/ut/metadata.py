"""IL2CPP global-metadata.dat string literal reader/rebuilder.

The string-literal tables are located empirically (validated entry-table
+ blob pair) rather than via per-version field-name tables, so any
metadata version with the standard pair header works. Two record shapes
are validated against the file's own bytes (never against a version
table):

* shape A - the standard il2cpp layout: an entries table of
  (u32 data_index, u32 byte_len) records validated against a later
  blob pair; >= 95% of records must be in-bounds and decode as UTF-8
  with byte lengths 1-2048.
* shape B - a plain u32 offset table whose consecutive differences are
  the literal lengths, with the blob sitting immediately after the
  offset table. Empirically required: the example IL2CPP game (v39)
  ships a permuted/scrambled header where only the entries pair is
  reliable and the blob is not pair-addressable.

Calibration: the example IL2CPP game (v39) yields the known literals
(including 研究施設へようこそ, literal #18492 via its offset table);
synthetic fixtures cover shape A.

locate_tables() returns (entries_pair_index, blob_pair_index), where
pair i occupies bytes 8 + i*8 .. 8 + i*8 + 8. For shape B there is no
blob pair, so the second element is -1 and the blob is the region
directly following the entries table (ending at the next table's
offset or EOF).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

SANITY = 0xFAB11BAF
MIN_VERSION, MAX_VERSION = 16, 45

_MIN_ENTRIES = 10        # locator minimum-entry threshold
_MIN_TABLE_BYTES = 80    # a pair smaller than this cannot be a table
_PASS_RATE = 0.95        # fraction of records that must validate
_MAX_BLEN = 2048         # plausible single-literal byte length
_PRECHECK_SAMPLE = 2048  # records sampled by the shape-A entries pre-check


class MetadataError(RuntimeError):
    pass


@dataclass
class Literal:
    index: int
    data_offset: int   # relative to the literal blob start
    byte_len: int
    text: str
    raw: bytes = b""  # exact on-disk slice (may be invalid UTF-8; fix I6)


def _pair(data: bytes, i: int) -> tuple[int, int]:
    return struct.unpack_from("<ii", data, 8 + i * 8)


def _pair_count(data: bytes) -> int:
    return (len(data) - 8) // 8


def _shape_a_precheck(data: bytes, e_off: int, count: int) -> bool:
    """Cheap sanity filter on the entries table alone (no blob needed).

    Most sampled (data_index, byte_len) records must carry a plausible
    byte_len; the threshold is deliberately loose (0.75) so this only
    rejects tables that could not pass full validation anyway - it is a
    performance guard, not the validator.
    """
    limit = min(count, _PRECHECK_SAMPLE)
    ok = 0
    for k in range(limit):
        _d, blen = struct.unpack_from("<II", data, e_off + k * 8)
        if 1 <= blen <= _MAX_BLEN:
            ok += 1
    return ok >= limit * 0.75


def _shape_a_validate(data: bytes, e_off: int, count: int,
                      b_off: int, b_size: int) -> float:
    """Return the fraction of entries that are in-bounds UTF-8 slices.

    Aborts early once enough records have failed that the pass rate can
    no longer reach _PASS_RATE, so wrong blob candidates are cheap.
    """
    fail_max = count - int(count * _PASS_RATE)
    ok = fails = 0
    for k in range(count):
        d_index, blen = struct.unpack_from("<II", data, e_off + k * 8)
        if 1 <= blen <= _MAX_BLEN and d_index + blen <= b_size:
            try:
                data[b_off + d_index : b_off + d_index + blen].decode("utf-8")
                ok += 1
                continue
            except UnicodeDecodeError:
                pass
        fails += 1
        if fails > fail_max:
            return 0.0
    return ok / count


def _shape_b_validate(data: bytes, e_off: int, e_size: int) -> int | None:
    """Validate a u32 offset table whose blob follows it contiguously.

    Returns the blob start offset, or None if the table does not
    validate. Lengths come from consecutive offset differences; a small
    fraction of oversized literals is tolerated.
    """
    count = e_size // 4
    if count < _MIN_ENTRIES:
        return None
    offs = struct.unpack_from(f"<{count}I", data, e_off)
    prev = -1
    for o in offs:
        if o < prev:
            return None
        prev = o
    blob_start = e_off + e_size
    if blob_start + offs[-1] > len(data):
        return None
    oversized = sum(1 for k in range(count - 1) if offs[k + 1] - offs[k] > _MAX_BLEN)
    if oversized > 0.05 * (count - 1):
        return None
    ok = total = 0
    for k in range(count - 1):
        if offs[k] == offs[k + 1]:
            continue  # empty literal
        total += 1
        try:
            data[blob_start + offs[k] : blob_start + offs[k + 1]].decode("utf-8")
            ok += 1
        except UnicodeDecodeError:
            pass
    if total >= _MIN_ENTRIES and ok / total >= _PASS_RATE:
        return blob_start
    return None


def locate_tables(data: bytes) -> tuple[int, int]:
    """Locate the string-literal tables empirically.

    Returns (entries_pair_index, blob_pair_index); pair i occupies
    bytes 8 + i*8 .. 8 + i*8 + 8. A blob_pair_index of -1 means the
    blob is the region immediately following the entries table (shape
    B). Raises MetadataError when no combination validates.
    """
    n = _pair_count(data)
    for ei in range(n):
        e_off, e_size = _pair(data, ei)
        if e_size < _MIN_TABLE_BYTES or e_off < 0 or e_off + e_size > len(data):
            continue
        if e_size % 8 == 0:
            count = e_size // 8
            if count >= _MIN_ENTRIES and _shape_a_precheck(data, e_off, count):
                for bi in range(ei + 1, n):
                    b_off, b_size = _pair(data, bi)
                    if b_off < 0 or b_off + b_size > len(data) or b_size < count:
                        continue
                    if _shape_a_validate(data, e_off, count, b_off, b_size) >= _PASS_RATE:
                        return ei, bi
        if e_size % 4 == 0:
            if _shape_b_validate(data, e_off, e_size) is not None:
                return ei, -1
    raise MetadataError("no validated string-literal table found")


def _next_table_offset(data: bytes, after: int) -> int:
    """Smallest header-pair offset greater than `after` (else EOF)."""
    best = len(data)
    for i in range(_pair_count(data)):
        off, _size = struct.unpack_from("<ii", data, 8 + i * 8)
        if after < off < best:
            best = off
    return best


def read_literals(data: bytes) -> list[Literal]:
    sanity, version = struct.unpack_from("<Ii", data, 0)  # sanity magic is unsigned
    if sanity != SANITY:
        raise MetadataError(f"bad sanity {sanity:#x}")
    if not (MIN_VERSION <= version <= MAX_VERSION):
        raise MetadataError(f"unsupported metadata version {version}")
    ei, bi = locate_tables(data)
    e_off, e_size = _pair(data, ei)
    out: list[Literal] = []
    if bi >= 0:
        # shape A: 8-byte (data_index, byte_len) records into a blob pair
        b_off, _b_size = _pair(data, bi)
        for k in range(e_size // 8):
            d_index, blen = struct.unpack_from("<II", data, e_off + k * 8)
            raw = data[b_off + d_index : b_off + d_index + blen]
            out.append(Literal(k, d_index, blen, raw.decode("utf-8", "replace"), raw))
    else:
        # shape B: u32 offset table, blob contiguous after it
        count = e_size // 4
        offs = struct.unpack_from(f"<{count}I", data, e_off)
        blob_start = e_off + e_size
        blob_end = _next_table_offset(data, blob_start + offs[-1])
        for k in range(count):
            start = blob_start + offs[k]
            end = blob_start + offs[k + 1] if k + 1 < count else blob_end
            raw = data[start:end]
            out.append(Literal(k, offs[k], len(raw), raw.decode("utf-8", "replace"), raw))
    return out


def patch_literals(data: bytes, replacements: dict[int, str]) -> bytes:
    """Rewrite string literals in-place by relocating the literal tables.

    `replacements` maps literal index -> new text (any length, UTF-8).
    Strategy: rebuild the full entries table and blob, then append
    `[new entries][pad to 4][new blob]` as one contiguous block at EOF and
    repoint the entries pair (shape A: and the blob pair). This preserves
    shape B's contiguity invariant (blob sits directly after the entries
    table) with zero reader changes and works identically for shape A.
    Old tables/blob become dead bytes at their original offsets; nothing
    shifts and no other table is touched. Raises MetadataError for an
    index out of range or a bad sanity magic.
    """
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
    old_blob_start = _pair(data, bi)[0] if bi >= 0 else e_off + e_size
    old_last_off = lits[-1].data_offset if lits else 0
    new_entries = bytearray()
    new_blob = bytearray()
    new_last_off = 0
    for k, lit in enumerate(lits):
        # Fix I6: unreplaced literals keep their RAW bytes verbatim —
        # re-encoding lit.text would turn invalid-UTF-8 bytes into U+FFFD
        # and silently alter literals the patch never touched.
        payload = (
            replacements[k].encode("utf-8") if k in replacements else lit.raw
        )
        if bi >= 0:   # shape A: (u32 offset-into-blob, u32 length) records
            new_entries += struct.pack("<II", len(new_blob), len(payload))
        else:         # shape B: u32 offsets-into-blob (lengths = differences)
            new_entries += struct.pack("<I", len(new_blob))
        new_last_off = len(new_blob)
        new_blob += payload
    # No trailing pad after the blob: shape B's last literal ends at EOF
    # (or the next table), so trailing pad bytes would merge into its text
    # and change it. The blob START is already 4-aligned (aligned file pad
    # + entries size is a multiple of 4) and no reader needs the blob end
    # aligned, so the blob ends exactly at its last payload byte.

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

    # Runtime data-pointer repoint (crash fix, e2e user report): the il2cpp
    # runtime does NOT derive the literal-data location from contiguity like
    # our reader does — the header carries it explicitly, and v39 stores it
    # as (literalCount, dataOffset) with the offset in the SECOND int. After
    # relocation every header pair still referencing the OLD blob start must
    # follow. Hard-capped to the first 128 pair slots: real il2cpp headers
    # have well under 100 fields, and body bytes beyond the header contain
    # value matches (verified on the real game) that must never be touched.
    SCAN_PAIRS = 128
    for i in range(min(SCAN_PAIRS, max(0, (len(out) - 8) // 8))):
        if i == ei or i == bi:
            continue  # entries/blob pairs already repointed above
        a, b = struct.unpack_from("<ii", out, 8 + i * 8)
        if a == old_blob_start and old_blob_start > 0:
            struct.pack_into("<i", out, 8 + i * 8, new_b_off)
        elif b == old_blob_start and old_blob_start > 0:
            struct.pack_into("<i", out, 12 + i * 8, new_b_off)
        elif (bi < 0 and count > 0
              and a == old_last_off and b == count - 1):
            # v39's (lastLiteralOffset, lastIndex) companion pair — the
            # runtime (or validators) can use it to bound the last literal,
            # whose end is EOF rather than offs[count-1] + a stored length.
            struct.pack_into("<i", out, 8 + i * 8, new_last_off)
    return bytes(out)
