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
    e_off, b_off = 8 + 16, 8 + 16 + len(ent)  # header: sanity+version (8) + two pairs (16)
    header = struct.pack("<Ii", 0xFAB11BAF, 29)  # sanity is unsigned on disk
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
    data = struct.pack("<Ii", 0xFAB11BAF, 29) + b"\x00" * 64  # sanity is unsigned on disk
    with pytest.raises(MetadataError):
        read_literals(data)

def test_shape_b_rejects_single_nonempty():
    # Regression: a shape-B table with only ONE non-empty literal used to pass
    # the pass-rate gate (1/1 >= 0.95), letting a mostly-zero monotonic u32
    # region false-positive as a literal table. Built directly with struct
    # (not via _build): a u32 offset table with 19 zero offsets (empty
    # literals) and one final offset whose slice is valid UTF-8 in-file.
    # 20 entries, not the usual 12, because locate_tables skips pairs smaller
    # than _MIN_TABLE_BYTES (80 bytes = 20 u32s) before the shape-B gate.
    import struct
    blob = b"payload"                                       # the single non-empty literal
    offs = struct.pack("<20I", *([0] * 19 + [len(blob)]))   # 19 empties + 1 final offset
    e_off = 8 + 8  # sanity+version (8) + one header pair (8)
    header = struct.pack("<Ii", 0xFAB11BAF, 29)             # sanity is unsigned on disk
    header += struct.pack("<ii", e_off, len(offs))           # pair 0: shape-B offset table
    data = header + offs + blob
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
    # _build pads to 12 literals (locator threshold): the two replacements
    # land first, the filler literals must survive untouched.
    assert texts[:2] == ["x", "a much longer english replacement string"]
    assert texts[2:] == [f"filler-{i}" for i in range(10)]

def test_patch_rejects_bad_index():
    data = _build(None, ["start"])
    try:
        patch_literals(data, {99: "no"})
        assert False, "expected MetadataError"
    except MetadataError:
        pass

def test_patch_preserves_invalid_utf8_literal_bytes():
    """I6: untouched literals keep their RAW bytes. A literal with invalid
    UTF-8 (decodes to U+FFFD via 'replace') used to be re-encoded from text,
    silently altering bytes the patch never touched. 24 entries so the one
    invalid literal still passes the locator's 95% validation gate."""
    import struct
    texts = [f"filler-{i}" for i in range(24)]
    texts[0] = "start"
    bad = b"ok\xff\xfe\x81part"  # invalid UTF-8
    entries, blob, off = b"", b"", 0
    raws = []
    for t in texts + [None]:
        raw = bad if t is None else t.encode("utf-8")
        raws.append(raw)
        entries += struct.pack("<II", off, len(raw))
        blob += raw
        off += len(raw)
    e_off, b_off = 8 + 16, 8 + 16 + len(entries)
    data = (struct.pack("<Ii", 0xFAB11BAF, 29)
            + struct.pack("<ii", e_off, len(entries))
            + struct.pack("<ii", b_off, len(blob))
            + entries + blob)

    lits = read_literals(data)
    bad_idx = len(raws) - 1
    assert lits[bad_idx].raw == bad
    assert "\ufffd" in lits[bad_idx].text  # reader surfaces U+FFFD for text

    out = patch_literals(data, {0: "ENGLISH"})
    relits = read_literals(out)
    assert relits[0].text == "ENGLISH"
    # the untouched invalid literal survived byte-identically
    assert relits[bad_idx].raw == bad
    assert relits[bad_idx].byte_len == len(bad)
    assert relits[bad_idx].text == lits[bad_idx].text

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

def test_patch_updates_runtime_data_pointer(game_dir, has_game):
    """The il2cpp runtime reads literal DATA through an explicit header pair —
    v39 stores it as (literalCount, dataOffset) with the offset in the SECOND
    int — NOT via contiguity like our reader. Regression for the in-app
    startup crash: after patch_literals, the runtime's own view (header pair
    interpretation) must decode every literal, with replacements present."""
    import os, struct
    path = os.path.join(game_dir, [d for d in os.listdir(game_dir) if d.endswith("_Data")][0],
                        "il2cpp_data", "Metadata", "global-metadata.dat")
    orig = open(path, "rb").read()

    def runtime_view(data):
        pairs = [struct.unpack_from("<ii", data, 8 + i * 8) for i in range(8)]
        a = pairs[0]                                  # (offsetsOff, offsetsSize)
        for p in pairs[1:8]:
            if p[1] == a[0] + a[1] and p[0] > 100:    # (count, dataOff)
                return a, p[0], p[1]
        raise AssertionError("runtime literal-data pair not found")

    ao, co, do = runtime_view(orig)
    lits = read_literals(orig)
    target = next(l for l in lits if "研究施設へようこそ" in l.text)
    out = patch_literals(orig, {target.index: "Welcome to the research facility."})

    ae, ce, de = runtime_view(out)
    assert (ce, de) != (co, do), "runtime data pointer was not repointed"
    assert de > len(orig), "dataOff should point into the appended region"
    offs = struct.unpack_from(f"<{ae[1] // 4}I", out, ae[0])
    def rt(i):
        start = de + offs[i]
        end = de + (offs[i + 1] if i + 1 < len(offs) else len(out))
        return out[start:end].decode("utf-8")
    assert rt(target.index) == "Welcome to the research facility."
    failures = 0
    for i in range(ce):
        try:
            rt(i)
        except Exception:
            failures += 1
    assert failures == 0, f"{failures} literals fail to decode under the runtime view"
    assert sum(1 for a, b in zip(lits, read_literals(out)) if a.text == b.text) == len(lits) - 1
