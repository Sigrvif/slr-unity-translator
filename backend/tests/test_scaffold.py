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
