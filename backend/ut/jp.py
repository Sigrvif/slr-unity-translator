"""Japanese / CJK detection and run extraction.

Used both to decide which strings are worth translating and to filter noise.

Two notions:
  * contains_japanese / is_jp_char  - "does this look like it has Japanese?"
  * looks_like_text                 - strict gate that rejects binary garbage
    that decodes as exotic/rare CJK ranges (a common artefact of misaligned
    UTF-16LE reads of binary blobs).
"""
from __future__ import annotations

import re

# --- "common" Japanese ranges (what real in-game text is made of) -----------
HIRAGANA = (0x3040, 0x309F)
KATAKANA = (0x30A0, 0x30FF)
CJK_COMMON = (0x4E00, 0x9FFF)          # the bulk of jouyou / daily-use kanji
HALFW_KATA = (0xFF66, 0xFF9F)

# --- ranges that signal *garbage* (misaligned binary decoding) -------------
CJK_EXT_A = (0x3400, 0x4DBF)           # rare; almost never in shipped UI text
HANGUL_SYL = (0xAC00, 0xD7AF)
HANGUL_JAMO = (0x1100, 0x11FF)
HANGUL_COMPAT = (0x3130, 0x318F)
CJK_COMPAT = (0x3300, 0x33FF)          # ㌀㍿ etc.
PRIVATE_USE = (0xE000, 0xF8FF)
SURROGATE = (0xD800, 0xDFFF)


def _between(o, rng):
    return rng[0] <= o <= rng[1]


def is_jp_char(c: str) -> bool:
    """True for hiragana, katakana, common kanji, half-width katakana.

    Deliberately EXCLUDES CJK Extension ranges and Hangul so that binary
    garbage does not register as "Japanese".
    """
    o = ord(c)
    return (
        _between(o, HIRAGANA)
        or _between(o, KATAKANA)
        or _between(o, CJK_COMMON)
        or _between(o, HALFW_KATA)
    )


def is_kana(c: str) -> bool:
    o = ord(c)
    return _between(o, HIRAGANA) or _between(o, KATAKANA) or _between(o, HALFW_KATA)


def is_garbage_char(c: str) -> bool:
    """Characters that, if present, almost always mean a misaligned decode."""
    o = ord(c)
    return (
        _between(o, CJK_EXT_A)
        or _between(o, HANGUL_SYL)
        or _between(o, HANGUL_JAMO)
        or _between(o, HANGUL_COMPAT)
        or _between(o, CJK_COMPAT)
        or _between(o, PRIVATE_USE)
        or _between(o, SURROGATE)
    )


def contains_japanese(s: str) -> bool:
    return any(is_jp_char(c) for c in s)


def jp_ratio(s: str) -> float:
    if not s:
        return 0.0
    n = sum(1 for c in s if is_jp_char(c))
    return n / len(s)


# Characters that are "normal" in real text alongside Japanese.
# Font-glyph blocks JP games repurpose as text: box drawings carry the
# moan/emphasis marks of custom fonts (real case: あっ┻…んっ┻ — a bubble
# line the 85%-normal ratio used to reject because ┻ counted as exotic);
# arrows, misc symbols and dingbats are equally common on-screen decoration.
_GLYPH_EXTRA = "".join(chr(c) for c in list(range(0x2190, 0x2200))      # arrows
                       + list(range(0x2500, 0x2600))     # box drawing + block elements
                       + list(range(0x2600, 0x2800)))    # misc symbols + dingbats
_NORMAL_EXTRA = set(" \t\n\r　「」『』（）〈〉《》。、・‥…〜～＝！？・：；"
                    "※★☆♪♡◆◇○●◎■□▲△▼→←↑↓⇔々ゝ"
                    "／＼＿ー\-\"'#%&'()*+,./:;<=>?@[]^_`{|}~"
                    "0123456789０１２３４５６７８９"
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ" + _GLYPH_EXTRA)


def looks_like_text(s: str) -> bool:
    """Strict gate: is this plausibly real in-game Japanese text?

    Rules:
      * at least one kana, OR two or more common kanji, OR a single common
        kanji in a short string (single-kanji button labels like 開/閉/鍵);
      * no garbage-range characters (Ext-A, Hangul, PUA, surrogates, ...);
      * >= 85% of characters are "normal" (kana/kanji/common punct/ascii);
      * no control characters.
    """
    if not s or len(s) > 4096:
        return False
    kana = common_kanji = 0
    normal = 0
    for c in s:
        o = ord(c)
        if c in "\n\r\t" or c == "\u3000":
            normal += 1
            continue
        if o < 0x20 or o == 0x7F:
            return False
        if is_garbage_char(c):
            return False
        if is_kana(c):
            kana += 1
            normal += 1
        elif _between(o, CJK_COMMON):
            common_kanji += 1
            normal += 1
        elif c in _NORMAL_EXTRA:
            normal += 1
        # other exotic-but-not-garbage chars (e.g. rare symbols) count against ratio
    if kana == 0 and common_kanji < 2 and not (common_kanji == 1 and len(s) <= 8):
        return False
    if normal / len(s) < 0.85:
        return False
    return True



# --- display-text gate for strings WITHOUT Japanese (feature: collect all
# user-visible strings, not just Japanese ones) ------------------------------

_LATIN_LETTER = re.compile(r"[A-Za-z\u00C0-\u024F]")
_LOWER_CAMEL = re.compile(r"[a-z][A-Z]")
# Structure characters that mark a string as code rather than display text:
# paths, dotted identifiers, format placeholders, markup, hex blobs. Real UI
# text may still contain , ! ? ( ) - ' + and spaces — all allowed on purpose.
_IDENT_MARKS = set("._/\\:<>{[]}|=~`@$%^;\"'#&")
_UPPER_LATIN = ("A", "Z")
_UPPER_ACCENT = ("\u00C0", "\u024F")


def looks_like_visible_text(s: str) -> bool:
    """Strict gate for NON-Japanese display text ("Achievement", "New Game+").

    Rejects the shapes that dominate non-JP strings inside a Unity game:
    identifiers and keys (SetSeVolume-ish keys, messageId, m_Name values),
    paths and GUIDs, format placeholders, versions, numbers, single letters,
    code fragments. Calibrated against a full no-gate scan of the example
    game: typetree non-JP leaves were ~97% internal names and raw non-JP
    records mostly component-internal keys, so this gate is deliberately
    tighter than the Japanese one (a Japanese string is user-visible by
    construction; an English one usually is not).
    """
    if not s or len(s) < 2 or len(s) > 4096:
        return False
    if s.strip() != s:  # leading/trailing whitespace -> code fragment ('  at ')
        return False
    for c in s:
        o = ord(c)
        if c in "\n\r\t":
            continue
        if o < 0x20 or o == 0x7F:
            return False
        if is_garbage_char(c):
            return False
    if not _LATIN_LETTER.search(s):
        return False  # pure digits/punctuation ('2639', '123%', '---')
    if s[0].islower() and _LOWER_CAMEL.search(s):
        return False  # lowerCamel identifier ('messageId'), not UI text
    if any(c in _IDENT_MARKS for c in s):
        return False  # 'font.ttf', '<Gamepad>/x', '{0}', 'a=b', 'GUID:x'
    if " " not in s:
        if not (
            _UPPER_LATIN[0] <= s[0] <= _UPPER_LATIN[1]
            or _UPPER_ACCENT[0] <= s[0] <= _UPPER_ACCENT[1]
        ):
            return False  # lone lowercase tokens are keys ('blur'), not labels
        if any(c.isdigit() for c in s):
            return False  # lone PascalCase-with-digits are config values
            # ('StandaloneWindows64'), not UI labels
    return True
