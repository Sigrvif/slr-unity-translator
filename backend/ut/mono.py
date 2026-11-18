"""Report-only detection of Japanese strings inside Mono Managed DLLs.

Spec §3.3: Mono builds keep their string literals in the #US (user-string)
heap of <Data>/Managed/*.dll as UTF-16LE, NOT in global-metadata.dat (that
is the IL2CPP store). Those literals cannot be patched by the metadata
writer; the spec only requires them to be DETECTED AND REPORTED so the
translator knows the game also carries baked-in C# strings (typically a
DLL-pack/MonoMod job — out of scope for this backend).

Detection, two paths:
  * REAL assemblies (the whole game's Managed folder on a Mono build) are
    parsed as ECMA-335 and scanned record-by-record through their #US heap
    — exact and fast (a 4.6 MB mscorlib costs ~2 ms; the old whole-file
    brute force needed MINUTES per big DLL on JP games, which read as a
    dead hang in the scan UI).
  * Anything that does not parse as an assembly (synthetic test blobs,
    exotic packers) falls back to the legacy raw scan: UTF-16LE records of
    the shape (u8 length prefix + payload) holding a Japanese run.

A string counts only when it decodes and passes the strict looks_like_text
gate plus the kana-or->=4-JP rule (binary noise decodes short kanji-only
runs constantly). This is a report, never a translation target.
"""
from __future__ import annotations

import os
import re

from . import jp as _jp
from .files import find_data_dir

# .NET user strings are short; longer "records" are random binary noise.
_MAX_CHARS = 512
# Skip monster DLLs (native mixes); Mono managed assemblies stay well under.
_MAX_DLL_BYTES = 64 * 1024 * 1024

# A UTF-16LE unit is Japanese-looking when its HIGH byte (second byte, LE)
# falls in hiragana/katakana (0x3040-0x30FF -> 0x30), common kanji
# (0x4E00-0x9FFF -> 0x4E-0x9F) or half-width katakana (0xFF66-0xFF9F -> 0xFF).
# A RUN of >= 2 such units at one alignment is vanishingly rare in binary
# noise but ubiquitous in real Japanese text.
_JP_RUN = re.compile(rb"(?:[\x00-\xff][\x30\x4e-\x9f\xff]){2,}")


def _accept(s: str | None) -> bool:
    """The shared string gate: strict shape + kana or a real kanji run."""
    return bool(s) and _jp.looks_like_text(s) and (
        any(_jp.is_kana(c) for c in s)
        or sum(1 for c in s if _jp.is_jp_char(c)) >= 4
    )


def scan_us_heap(heap: bytes) -> list[str]:
    """Japanese user strings from a parsed #US heap — the fast, exact path.

    Record layout (verified byte-level against csc- AND Mono-produced
    assemblies, both parse to the exact heap end): every record is
      [compressed uint X][X-1 bytes of UTF-16LE string][1 flag byte 0x01]
    i.e. the prefix counts string bytes PLUS the flag byte. The heap opens
    with a single 0x00 (the empty entry) and empty strings are legal
    (X == 1 → no payload). The old raw byte-walker never understood this
    layout — on real games it only ever surfaced misaligned-binary mojibake
    (a real Mono game reported 10,727 "literals" of noise while its actual
    375 JP strings went unreported), and its every-offset regex probing
    cost MINUTES per big DLL, reading as a dead hang in the scan UI.
    """
    out: list[str] = []
    n = len(heap)
    i = 1  # heap[0] is the canonical empty entry
    while i < n:
        b0 = heap[i]
        if b0 == 0:  # empty entry (also inside: "" compiles to X=0? skip)
            i += 1
            continue
        if b0 & 0x80 == 0:
            val, hdr = b0, 1
        elif b0 & 0xC0 == 0x80:
            if i + 1 >= n:
                break
            val, hdr = ((b0 & 0x3F) << 8) | heap[i + 1], 2
        else:
            if i + 3 >= n:
                break
            val = (((b0 & 0x1F) << 24) | (heap[i + 1] << 16) |
                   (heap[i + 2] << 8) | heap[i + 3]) & 0x7FFFFFFF
            hdr = 4
        if val < 2:
            # X==0: an empty entry; X==1: the empty string (flag byte only).
            # Both are legal records — skip, never stop (real heaps carry
            # them between real strings).
            i += hdr + max(val, 0)
            continue
        if i + hdr + val > n:
            break  # corrupt/unaligned tail: keep what we have
        try:
            s = heap[i + hdr:i + hdr + val - 1].decode("utf-16-le")
        except UnicodeDecodeError:
            break
        if _accept(s):
            out.append(s)
        i += hdr + val
    return out


def scan_utf16_records(buf: bytes) -> list[str]:
    """Every (u8len-prefix + UTF-16LE payload) record holding Japanese.

    Semantically identical to the original every-offset walk, but probes are
    MATCH-DRIVEN: the C-speed regex first finds every Japanese-run position,
    and only the ≤1k record-start candidates that could cover each match are
    checked (parity + count-byte first, decode last). The naive version
    re-ran the regex at EVERY byte offset of the DLL — minutes per big
    assembly on JP games, which read as a dead hang in the scan UI; this
    one stays in the milliseconds.
    """
    if not _JP_RUN.search(buf):
        return []  # no Japanese-looking UTF-16LE run anywhere: not a JP DLL
    out: list[str] = []
    n = len(buf)
    consumed_to = 0  # records are consumed left-to-right, exactly like the
    # original walk: after an accepted record, nothing inside it re-fires
    for m in _JP_RUN.finditer(buf):
        p, q = m.start(), m.end()
        if p < consumed_to:
            continue  # this run sits inside an already-accepted record
        # candidates: every record start whose window [i+1, i+1+2*count)
        # could reach into this match (count ≤ 512 caps the lookback ~1k).
        # i == p is possible — the count byte itself can fuse into the first
        # matched pair. The ORIGINAL code probed these same windows with the
        # regex at every byte offset; the match-driven candidate set probes
        # exactly those windows, ascending, so acceptance order matches.
        lo = max(consumed_to, q - 1 - 2 * _MAX_CHARS)
        for i in range(lo, min(p, n - 2) + 1):
            count = buf[i]
            if not 1 <= count <= _MAX_CHARS:
                continue
            end = i + 1 + count * 2
            if end > n or not _JP_RUN.search(buf, i + 1, end):
                continue
            try:
                s = buf[i + 1 : end].decode("utf-16-le")
            except UnicodeDecodeError:
                continue
            # looks_like_text is the base gate (no control chars, no garbage
            # CJK ranges); the kana-or-4-JP rule additionally rejects the
            # short kanji-only decodes that random binary noise produces.
            if _accept(s):
                out.append(s)
                consumed_to = end
                break
    return out


def scan_managed_dir(data_dir: str) -> list[dict]:
    """Catalogue JP-bearing literals per Managed DLL (report-only).

    Returns one {dll, count, samples} per DLL that carries at least one
    Japanese user-string record; `samples` holds up to 50 strings.
    Assemblies are scanned via their #US heap; unparseable blobs fall back
    to the raw record scan.
    """
    managed = os.path.join(data_dir, "Managed")
    if not os.path.isdir(managed):
        return []
    from .monopatch import DotNetAssembly

    out: list[dict] = []
    for root, _dirs, names in os.walk(managed):
        for name in sorted(names):
            if not name.lower().endswith(".dll"):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) > _MAX_DLL_BYTES:
                    continue
                with open(path, "rb") as f:
                    buf = f.read()
            except OSError:
                continue
            try:
                strings = scan_us_heap(DotNetAssembly(buf).us_heap)
            except Exception:  # noqa: BLE001 — not a CLI image: raw fallback
                strings = scan_utf16_records(buf)
            if not strings:
                continue
            samples: list[str] = []
            for s in strings:
                if s not in samples:
                    samples.append(s)
                if len(samples) == 50:
                    break
            out.append(
                {
                    "dll": os.path.relpath(path, managed).replace("\\", "/"),
                    "count": len(strings),
                    "samples": samples,
                }
            )
    return out


def scan_game(game_dir: str) -> list[dict]:
    """Convenience wrapper: locate the *_Data dir, then scan its Managed tree."""
    data_dir = find_data_dir(game_dir) or game_dir
    return scan_managed_dir(data_dir)
