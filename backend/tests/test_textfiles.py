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

def test_csv_preserves_line_terminator():
    content = "name,line\n真理,こんにちは\n".encode("utf-8")
    new, changed, fmt = apply(content, {"r1c1": "Hello"})
    assert changed and not fmt
    assert new == "name,line\n真理,Hello\n".encode("utf-8")

def test_csv_reports_quoting_change():
    content = '"name","line"\n"真理","こんにちは"\n'.encode("utf-8")
    ex = extract(content)
    assert ex.format == "csv"
    new, changed, fmt = apply(content, {"r1c1": "Hello"})
    assert changed and fmt

def test_json_ghost_key_ignored():
    content = '{"a":"x"}'.encode()
    new, changed, fmt = apply(content, {"$.ghost": "G"})
    assert new == content and not changed and not fmt

def test_apply_binary_content_noop():
    content = b"\x00\x01\x02\xff\xfe"
    new, changed, fmt = apply(content, {"L0": "x"})
    assert new == content and not changed and not fmt

def test_bom_json_roundtrip():
    content = b"\xef\xbb\xbf" + '{"a":"\xe3\x81\x82"}'.encode("utf-8")
    ex = extract(content)
    assert ex.format == "json"
    assert {i.key: i.text for i in ex.items} == {"$.a": "\xe3\x81\x82"}
    new, changed, fmt = apply(content, {"$.a": "A"})
    assert changed and fmt
    assert new.startswith(b"\xef\xbb\xbf")
    assert new.decode("utf-8-sig") == '{"a":"A"}'
