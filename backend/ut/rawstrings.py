"""Length-prefixed scanning of Unity-serialized strings in raw bytes.

Unity serializes a string as:
    int32   byte_count      little endian; UTF-8 byte length (NOT char count)
    bytes   byte_count      UTF-8 payload, no null terminator
    bytes   padding         zero bytes up to the next 4-byte boundary

(An earlier revision assumed UTF-16LE with a char-count prefix and a null
terminator; that matched nothing on disk and silently found zero strings.)

Validating against this structure (rather than grepping for Japanese runs)
eliminates almost all false positives from binary blobs. Records always sit
on 4-byte boundaries inside object payloads, so the scanner walks the buffer
on a 4-byte grid, confirms the payload decodes as printable UTF-8 and that
the alignment padding is zero.

The scanner returns the byte offset of the *length prefix* and the payload
byte count so callers can rebuild records in place (a replacement may be any
length; the payload is re-emitted and everything after it shifts).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import jp as _jp


@dataclass
class RawString:
    offset: int   # byte offset of the int32 length prefix within the buffer
    length: int   # UTF-8 byte count of the payload (as stored)
    text: str


def record_size(byte_len: int) -> int:
    """Total bytes a record occupies: length prefix + payload + alignment padding."""
    return 4 + byte_len + (-byte_len % 4)


def encode_record(new_text: str) -> bytes:
    """Serialize `new_text` as a Unity string record (prefix + UTF-8 + padding)."""
    payload = new_text.encode("utf-8")
    return len(payload).to_bytes(4, "little", signed=True) + payload + b"\x00" * ((-len(payload)) % 4)


def _decode_utf8(buf: bytes, start: int, byte_len: int) -> str | None:
    if start + byte_len > len(buf):
        return None
    try:
        return buf[start : start + byte_len].decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_strings(
    buf: bytes,
    *,
    min_chars: int = 1,
    max_chars: int = 8192,
    only_japanese: bool = True,
    printable_only: bool = True,
) -> list[RawString]:
    """Return every length-prefix-validated Unity string found in `buf`.

    `min_chars`/`max_chars` bound the UTF-8 *byte* length of the payload.
    """
    results: list[RawString] = []
    n = len(buf)
    i = 0
    while i + 4 <= n:
        byte_len = int.from_bytes(buf[i : i + 4], "little", signed=True)
        if min_chars <= byte_len <= max_chars and i + 4 + byte_len <= n:
            s = _decode_utf8(buf, i + 4, byte_len)
            if s is not None:
                keep = True
                if printable_only and not _is_printable(s):
                    keep = False
                if keep:  # alignment padding must be zero; garbage never is
                    pad_off = i + 4 + byte_len
                    pad_len = -byte_len % 4
                    if pad_len and any(buf[pad_off : pad_off + pad_len]):
                        keep = False
                if keep and only_japanese and not _jp.contains_japanese(s):
                    keep = False
                if keep:
                    results.append(RawString(offset=i, length=byte_len, text=s))
                    i += record_size(byte_len)
                    continue
        i += 4
    return results


def _is_printable(s: str) -> bool:
    for c in s:
        o = ord(c)
        if c in "\n\r\t":
            continue
        if o < 0x20 or o == 0x7F:
            return False
        if 0xE000 <= o <= 0xF8FF:  # private use area
            return False
    return True
