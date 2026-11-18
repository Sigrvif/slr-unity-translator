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
    # NOTE: path[1:] (not path[2:]) — the leading '.' must remain so the
    # regex can anchor the root key ("$.a.b[0]" -> ".a.b[0]").
    tokens = re.findall(r"\.([^.\[]+)|\[(\d+)\]", path[1:])
    cur = node
    for name, idx in tokens[:-1]:
        cur = cur[int(idx)] if idx else cur[name]
    name, idx = tokens[-1]
    if idx:
        cur[int(idx)] = value
    elif name in cur:
        cur[name] = value
    else:
        raise KeyError(name)  # ghost key: never create nonexistent dict keys


def extract(content: bytes) -> Extraction:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return Extraction("lines", [])
    if text.startswith("\ufeff"):
        text = text[1:]  # utf-8-sig: strip BOM so format detection sees the payload
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
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content, False, False  # non-UTF-8 payload: never a text rewrite
    if ex.format == "json":
        if text.startswith("\ufeff"):
            text = text[1:]
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
        if content.startswith(b"\xef\xbb\xbf"):
            dumps = "\ufeff" + dumps  # restore the utf-8-sig BOM we stripped
        return dumps.encode("utf-8"), True, True
    if ex.format == "csv":
        try:
            dialect = csv.Sniffer().sniff(text[:4096])
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        changed_any = False
        changed_rows = set()
        for key, new_text in translations.items():
            if not (key.startswith("r") and "c" in key):
                continue
            try:
                r_s, c_s = key[1:].split("c")
                rows[int(r_s)][int(c_s)] = new_text
                changed_any = True
                changed_rows.add(int(r_s))
            except (ValueError, IndexError):
                pass
        if not changed_any:
            return content, False, False
        # csv.writer defaults to \r\n; preserve the input's terminator instead.
        terminator = "\r\n" if "\r\n" in text else "\n"
        buf = io.StringIO()
        csv.writer(buf, dialect, lineterminator=terminator).writerows(rows)
        out_text = buf.getvalue()
        # Quoting-style drift: any untouched row whose re-serialized line
        # differs from its original input line means formatting changed.
        orig_lines = text.splitlines()
        new_lines = out_text.splitlines()
        fmt_changed = len(orig_lines) != len(new_lines)
        if not fmt_changed:
            for r, (orig, new) in enumerate(zip(orig_lines, new_lines)):
                if r not in changed_rows and orig != new:
                    fmt_changed = True
                    break
        return out_text.encode("utf-8"), True, fmt_changed
    # lines
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
