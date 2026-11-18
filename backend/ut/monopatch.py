"""Mono code-level demosaic: stub mosaic-named Behaviour types in the game's
managed DLLs — the static equivalent of UUD's DumbTypeDemosaic.

UUD's plugin reflects over all loaded types at runtime, finds Behaviour
subclasses whose name matches the mosaic dictionary, and sets every live
instance's `enabled = false` each frame. The static equivalent: a type in
Assembly-CSharp (or a plugin DLL) NAMED like a mosaic is, with near
certainty, the censoring component; Unity drives components through their
magic methods (Update, OnEnable, ...), so overwriting those method BODIES
with `return default` makes the censoring behavior never execute — the
component still exists, its fields deserialize normally, the game boots.

How the stubber stays safe:
  * PURE READ for detection: PE → CLI header → metadata tables (#~) →
    TypeDef/MethodDef rows → names. No CLR needed.
  * In-place, SAME-SIZE writes only: the method's IL body region is
    overwritten with (nop padding + `return default`) and NOTHING else moves
    — no header edit, no size change, no relocation. Fat bodies with
    exception-handler sections are SKIPPED (stale EH tables could upset the
    JIT); value-type returns are SKIPPED (no safe one-size default stub).
  * Only Unity magic method names of matched types are touched — not every
    method (callers keep working; the aggressive gut is a possible later
    tier, deliberately not shipped).
  * Originals are never touched: patch_mode writes changed DLLs into
    out_dir, full-copy mode edits the copied tree.

Parity note: UUD ships no IL2CPP variant of DumbTypeDemosaic (the code of an
IL2CPP game is native machine code in GameAssembly.dll) — Mono-only here is
full parity. IL2CPP games still get DETECTION (MonoScript stubs carry class
names even in IL2CPP builds; see demosaic.py), reported as not patchable.
"""
from __future__ import annotations

import os
import struct

from .demosaic import NAME_PARTS, name_is_mosaic

# Unity's component lifecycle methods — the ones Unity calls on a Behaviour.
MAGIC_METHODS = {
    "Awake", "Start", "OnEnable", "OnDisable", "OnDestroy",
    "Update", "LateUpdate", "FixedUpdate",
    "OnRenderImage", "OnPreRender", "OnPostRender", "OnWillRenderObject",
    "OnGUI", "OnValidate", "OnApplicationFocus", "OnApplicationPause",
    "OnApplicationQuit", "OnDrawGizmos", "OnDrawGizmosSelected",
}

# Managed DLLs never worth scanning (framework / engine; a censor type will
# never live there, and scanning them wastes time).
_SKIP_DLL_PREFIXES = (
    "system", "mscorlib", "netstandard", "microsoft.", "mono.",
    "unityengine", "unity.", "unitycompiler", "boo.lang", "i18n.",
)

# --------------------------------------------------------------------------- #
# PE / CLI / metadata parsing (read-only)
# --------------------------------------------------------------------------- #
class PEFile:
    """Minimal PE reader: data directories + sections (RVA -> file offset)."""

    def __init__(self, data: bytes):
        if data[:2] != b"MZ":
            raise ValueError("not a PE file (no MZ)")
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_off:pe_off + 4] != b"PE\x00\x00":
            raise ValueError("bad PE signature")
        opt_off = pe_off + 24
        magic = struct.unpack_from("<H", data, opt_off)[0]
        if magic == 0x20B:      # PE32+
            dd_off = opt_off + 112
        elif magic == 0x10B:    # PE32
            dd_off = opt_off + 96
        else:
            raise ValueError(f"unknown optional-header magic {magic:#x}")
        self.data = data
        cli_rva, cli_size = struct.unpack_from("<II", data, dd_off + 14 * 8)
        self.cli_rva, self.cli_size = cli_rva, cli_size
        # sections
        num_sections = struct.unpack_from("<H", data, pe_off + 6)[0]
        opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
        sec_off = opt_off + opt_size
        self.sections = []
        for i in range(num_sections):
            base = sec_off + i * 40
            vsize, vaddr, rsize, roff = struct.unpack_from("<IIII", data, base + 8)
            self.sections.append((vaddr, max(vsize, rsize), roff, rsize))

    def rva_to_offset(self, rva: int) -> int:
        for vaddr, size, roff, _rsize in self.sections:
            if vaddr <= rva < vaddr + size:
                return roff + (rva - vaddr)
        raise ValueError(f"rva {rva:#x} outside any section")

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.data, off)[0]


class MetaTables:
    """The #~ (or #-) metadata table stream: enough schema to walk TypeDef
    and MethodDef rows."""

    # (table id): column sizes as a function of heap sizes + row counts.
    # S = string heap idx, B = blob heap idx, G = guid heap idx,
    # I<n> = index into table <n>, C:name = coded index.
    def __init__(self, data: bytes):
        self.data = data
        heap_sizes = data[6]
        self.str_sz = 4 if heap_sizes & 0x01 else 2
        self.guid_sz = 4 if heap_sizes & 0x02 else 2
        self.blob_sz = 4 if heap_sizes & 0x04 else 2
        valid_lo, valid_hi = struct.unpack_from("<II", data, 8)
        self.present = [(valid_lo >> i) & 1 for i in range(32)] + \
                       [(valid_hi >> i) & 1 for i in range(32)]
        self.rows = {}
        off = 24
        for tid in range(64):
            if self.present[tid]:
                self.rows[tid] = struct.unpack_from("<I", data, off)[0]
                off += 4
        self._row_start = off

    def idx_sz(self, table: int) -> int:
        return 4 if self.rows.get(table, 0) >= 0x10000 else 2

    def coded_sz(self, tables: tuple) -> int:
        bits = max(1, (len(tables) - 1).bit_length())
        biggest = max(self.rows.get(t, 0) for t in tables if t is not None)
        return 4 if biggest >= (1 << (16 - bits)) else 2

    def _schema(self):
        """(table id) -> [column byte sizes], for tables up to MethodDef."""
        S, B, G = self.str_sz, self.blob_sz, self.guid_sz
        s = {}
        s[0] = [2, S, G, G, G]                                    # Module
        s[1] = [self.coded_sz((0, 0x1A, 0x23, 1)), S, S]          # TypeRef
        s[2] = [4, S, S, self.coded_sz((1, 2, 0x1B)), self.idx_sz(4),
                self.idx_sz(6)]                                   # TypeDef
        s[3] = [self.idx_sz(4)]                                   # FieldPtr
        s[4] = [2, S, B]                                          # Field
        s[5] = [self.idx_sz(6)]                                   # MethodPtr
        s[6] = [4, 2, 2, S, B, self.idx_sz(8)]                    # MethodDef
        s[7] = [self.idx_sz(8)]                                   # ParamPtr
        s[8] = [2, 2, S]                                          # Param
        return s

    def table_rows(self, tid: int):
        """Yield (row index [1-based], row offset) for a table id."""
        schema = self._schema()
        off = self._row_start
        for t in range(0, tid + 1):
            if not self.present[t]:
                continue
            n = self.rows[t]
            if t == tid:
                for r in range(n):
                    yield r + 1, off
                    off += sum(schema[t])
            else:
                off += n * sum(schema[t]) if t in schema else 0
                if t not in schema:
                    raise ValueError(f"table {t:#x} present but unsupported")


class DotNetAssembly:
    """Parsed managed DLL: string heap + TypeDef/MethodDef rows."""

    def __init__(self, data: bytes):
        self.pe = PEFile(data)
        cli_off = self.pe.rva_to_offset(self.pe.cli_rva)
        md_rva, _md_size = struct.unpack_from("<II", self.pe.data, cli_off + 8)
        md_off = self.pe.rva_to_offset(md_rva)
        if self.pe.data[md_off:md_off + 4] != b"BSJB":
            raise ValueError("bad metadata signature")
        # streams (root layout: sig(4) ver(8) reserved(4) verLen(4) version
        # verLen bytes, flags(2), streamCount(2), then stream headers)
        ver_len = struct.unpack_from("<I", self.pe.data, md_off + 12)[0]
        off = md_off + 16 + ver_len + 2   # +2 skips the Flags field
        n_streams = struct.unpack_from("<H", self.pe.data, off)[0]
        off += 2
        streams = {}
        self.stream_headers = {}  # name -> (filepos of off u32, off, size)
        for _ in range(n_streams):
            hdr_pos = off
            s_off, s_size = struct.unpack_from("<II", self.pe.data, off)
            off += 8
            end = self.pe.data.index(b"\x00", off)
            name = self.pe.data[off:end].decode("ascii", "replace")
            off = (end + 1 + 3) & ~3
            streams[name] = (md_off + s_off, s_size)
            # record for in-place edits (the #US grow repoints off/size)
            self.stream_headers[name] = (hdr_pos, s_off, s_size)
        if "#~" in streams:
            t_off, t_size = streams["#~"]
        elif "#-" in streams:  # uncompressed variant
            t_off, t_size = streams["#-"]
        else:
            raise ValueError("no table stream")
        self.md_off = md_off  # metadata root file offset (stream off fields
        # are relative to it)
        self.data = self.pe.data
        self.tables = MetaTables(self.data[t_off:t_off + t_size])
        self.t_base = t_off  # table-stream offsets are slice-relative; reads
        # against self.data must add this base
        s_off, s_size = streams.get("#Strings", (0, 0))
        self.strings = self.data[s_off:s_off + s_size]
        b_off, b_size = streams.get("#Blob", (0, 0))
        self.blob = self.data[b_off:b_off + b_size]
        # #US (user-string) heap: where C# string literals live — mono.py
        # scans exactly this slice instead of brute-forcing the whole DLL.
        u_off, u_size = streams.get("#US", (0, 0))
        self.us_heap = self.data[u_off:u_off + u_size] if u_size else b""

    def s(self, index: int) -> str:
        end = self.strings.index(b"\x00", index)
        return self.strings[index:end].decode("utf-8", "replace")

    def type_rows(self):
        """(row, namespace, name, method_list_idx) for every TypeDef."""
        for row, off in self.tables.table_rows(2):
            base = self.t_base + off
            name_i = struct.unpack_from("<H" if self.tables.str_sz == 2 else "<I",
                                        self.data, base + 4)[0]
            ns_i = struct.unpack_from("<H" if self.tables.str_sz == 2 else "<I",
                                      self.data, base + 4 + self.tables.str_sz)[0]
            mlist = struct.unpack_from("<H" if self.tables.idx_sz(6) == 2 else "<I",
                                       self.data, base + 4 + 2 * self.tables.str_sz +
                                       self.tables.coded_sz((1, 2, 0x1B)) +
                                       self.tables.idx_sz(4))[0]
            yield row, self.s(ns_i), self.s(name_i), mlist

    def method_rows(self):
        """(row, rva, name, sig_blob_off) for every MethodDef."""
        for row, off in self.tables.table_rows(6):
            base = self.t_base + off
            rva = struct.unpack_from("<I", self.data, base)[0]
            name_i = struct.unpack_from("<H" if self.tables.str_sz == 2 else "<I",
                                        self.data, base + 8)[0]
            sig_i = struct.unpack_from("<H" if self.tables.blob_sz == 2 else "<I",
                                       self.data, base + 8 + self.tables.str_sz)[0]
            yield row, rva, self.s(name_i), sig_i

    def blob_bytes(self, index: int) -> bytes:
        """Blob-heap entry at index (compressed length prefix + payload)."""
        b0 = self.blob[index]
        if b0 & 0x80 == 0:
            ln, hdr = b0, 1
        elif b0 & 0xC0 == 0x80:
            ln = ((b0 & 0x3F) << 8) | self.blob[index + 1]
            hdr = 2
        else:
            ln = ((b0 & 0x1F) << 24) | (self.blob[index + 1] << 16) | \
                 (self.blob[index + 2] << 8) | self.blob[index + 3]
            hdr = 4
        return self.blob[index + hdr:index + hdr + ln]


# --------------------------------------------------------------------------- #
# method body analysis + stub IL
# --------------------------------------------------------------------------- #
def method_body_info(asm: DotNetAssembly, rva: int):
    """(file_offset, code_off, code_size, has_eh) — or None (no body /
    unparseable)."""
    if rva == 0:
        return None
    off = asm.pe.rva_to_offset(rva)
    b0 = asm.data[off]
    if b0 & 0x3 == 2:            # tiny
        return (off, off + 1, b0 >> 2, False)
    if b0 & 0x3 == 3:            # fat
        flags = struct.unpack_from("<H", asm.data, off)[0]
        size = struct.unpack_from("<I", asm.data, off + 4)[0]
        return (off, off + 12, size, bool(flags & 0x8))
    return None


def return_default_il(sig: bytes) -> bytes | None:
    """IL that pushes the signature's return-type default and returns, or
    None when no safe stub exists (value types etc.)."""
    if not sig:
        return None
    i = 0
    conv = sig[i]; i += 1
    if conv & 0x10:              # generic: skip generic param count
        i += _skip_compressed(sig, i)
    # param count
    i += _skip_compressed(sig, i)
    if i >= len(sig):
        return None
    # return type (skip custom modifiers first)
    while i < len(sig) and sig[i] in (0x1F, 0x20):   # CMOD_REQD / CMOD_OPT
        i += 1
        i += _skip_compressed(sig, i)
    if i >= len(sig):
        return None
    byref = sig[i] == 0x10
    if byref:
        i += 1
    et = sig[i]
    if et == 0x01:               # void
        return b"\x2a"                       # ret
    if et in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09):  # <= i4/bool/char
        return b"\x16\x00\x2a"               # ldc.i4.0 ; ret
    if et in (0x0A, 0x0B):       # i8 / u8
        return b"\x17" + b"\x00" * 8 + b"\x2a"
    if et == 0x0C:               # r4
        return b"\x22" + b"\x00" * 4 + b"\x2a"
    if et == 0x0D:               # r8
        return b"\x23" + b"\x00" * 8 + b"\x2a"
    if et in (0x0E, 0x12, 0x1C, 0x14, 0x15, 0x1D, 0x1F):  # ref-ish
        return b"\x14\x2a"                   # ldnull ; ret
    if et in (0x18, 0x19):       # native int / unsigned
        return b"\x16\x00\xd0\x2a"           # ldc.i4.0 ; conv.i ; ret
    return None                  # valuetype (0x11), typedref (0x16), ...: skip


def _skip_compressed(buf: bytes, i: int) -> int:
    b0 = buf[i]
    if b0 & 0x80 == 0:
        return 1
    if b0 & 0xC0 == 0x80:
        return 2
    return 4


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def _type_method_spans(asm):
    """(namespace, name, first_method_row, end_method_row_exclusive) per type —
    a type's MethodDef rows run from its MethodList index to the next type's
    (or the table end for the last type)."""
    types = list(asm.type_rows())
    for i, (row, ns, name, mlist) in enumerate(types):
        end = types[i + 1][3] - 1 if i + 1 < len(types) else len(list(asm.method_rows()))
        yield ns, name, mlist, end


def scan_managed_code(data_dir: str, game_dir: str | None = None) -> list[dict]:
    """Mosaic-named types in every non-system managed DLL under <data_dir>/Managed.

    Returns [{dll (abs), rel, type, methods: [name, ...]}] — `rel` is the
    GAME-relative path (out_dir writes and staging both key on it).
    """
    managed = os.path.join(data_dir, "Managed")
    if not os.path.isdir(managed):
        return []
    root = os.path.abspath(game_dir) if game_dir else None
    out = []
    for name in sorted(os.listdir(managed)):
        low = name.lower()
        if not low.endswith(".dll") or low.startswith(_SKIP_DLL_PREFIXES):
            continue
        path = os.path.join(managed, name)
        rel = path
        if root:
            try:
                rel = os.path.relpath(path, root)
            except ValueError:
                rel = path
        try:
            asm = DotNetAssembly(open(path, "rb").read())
        except Exception:  # noqa: BLE001 — a non-CLI dll in Managed/ is skipped
            continue
        methods = list(asm.method_rows())
        for ns, tname, mlist, end in _type_method_spans(asm):
            if not name_is_mosaic(tname):
                continue
            names = [m[2] for m in methods[mlist - 1:end] if m[1]]
            out.append({"dll": path, "rel": rel.replace("\\", "/"),
                        "type": (ns + "." + tname) if ns else tname,
                        "methods": names})
    return out


def patch_managed_dll(data: bytes, *, aggressive: bool = False) -> tuple[bytes, list, list]:
    """Stub the magic methods of mosaic-named types in one DLL image.

    Returns (patched_bytes, actions [{type, method, rva}], skipped_notes).
    Same-size in-place writes; the input bytes are never mutated.
    """
    asm = DotNetAssembly(data)
    buf = bytearray(data)
    methods = list(asm.method_rows())
    actions, skipped = [], []
    for ns, tname, mlist, end in _type_method_spans(asm):
        if not name_is_mosaic(tname):
            continue
        full = (ns + "." + tname) if ns else tname
        for mrow, rva, mname, sig_i in methods[mlist - 1:end]:
            if not (aggressive or mname in MAGIC_METHODS):
                continue
            body = method_body_info(asm, rva)
            if body is None:
                skipped.append(f"{full}.{mname}: no IL body")
                continue
            _off, code_off, code_size, has_eh = body
            if has_eh:
                skipped.append(f"{full}.{mname}: exception-handler body (skipped)")
                continue
            stub = return_default_il(asm.blob_bytes(sig_i))
            if stub is None:
                skipped.append(f"{full}.{mname}: unsafe return type (skipped)")
                continue
            if len(stub) > code_size:
                skipped.append(f"{full}.{mname}: stub does not fit (skipped)")
                continue
            # nops (0x00) then the stub — every byte stays reachable & valid
            buf[code_off:code_off + code_size] = b"\x00" * (code_size - len(stub)) + stub
            actions.append({"type": full, "method": mname, "rva": rva})
    return bytes(buf), actions, skipped


def patch_managed_code(
    game_dir: str,
    out_dir: str,
    *,
    patch_mode: bool = False,
    in_place: bool = False,
    aggressive: bool = False,
    progress=None,
) -> dict:
    """Export stage: stub censor Behaviours in every managed DLL of the game.

    patch_mode reads Managed DLLs from the staged tree (or out_dir copies an
    earlier stage wrote) and writes only changed DLLs to out_dir/Managed/...;
    in_place edits the copied tree. Returns stats for the export summary.
    """
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    from .files import find_data_dir
    data_dir = find_data_dir(game_dir) or game_dir
    findings = scan_managed_code(data_dir, game_dir=game_dir)
    stats = {"code_types": len(findings), "code_methods": 0,
             "code_files": 0, "code_log": [], "code_skipped": [],
             "exported_files": [], "code_il2cpp": False}
    if not findings:
        # IL2CPP games keep code in GameAssembly.dll (native) — say so when
        # the game is IL2CPP but MonoScript detection found code rows.
        ga = os.path.join(os.path.dirname(data_dir), "GameAssembly.dll")
        if os.path.isfile(ga):
            stats["code_il2cpp"] = True
            stats["code_log"].append(
                "this game compiles code to native (IL2CPP / GameAssembly.dll) — "
                "code-level censorship cannot be patched statically (UUD has no "
                "IL2Cpp variant of this plugin either)")
        return stats
    from .files import assert_inside, assert_not_nested
    if not in_place:
        assert_not_nested(game_dir, out_dir, "out_dir")
    changed_any = False
    for i, f in enumerate(findings):
        src = f["dll"]
        if patch_mode:
            rel_from_out = os.path.join(out_dir, f["rel"])
            if os.path.isfile(rel_from_out):
                src = rel_from_out
        data = open(src, "rb").read()
        patched, actions, skipped = patch_managed_dll(data, aggressive=aggressive)
        stats["code_skipped"] += skipped[:40]
        if not actions:
            continue
        out_file = os.path.join(out_dir, f["rel"])
        assert_inside(out_dir, out_file, f"code output for {f['rel']!r}")
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "wb") as fh:
            fh.write(patched)
        changed_any = True
        stats["code_methods"] += len(actions)
        stats["code_files"] += 1
        stats["exported_files"].append(f["rel"].replace("\\", "/"))
        for a in actions[:40]:
            stats["code_log"].append(
                f"stubbed {f['type']}.{a['method']} ({os.path.basename(f['dll'])})")
        if progress:
            progress(i + 1, len(findings), f["rel"])
    if not changed_any and not stats["code_log"]:
        stats["code_log"].append("no stubbable censor methods found in managed DLLs")
    return stats


# --------------------------------------------------------------------------- #
# literal translation: grow the #US heap, repoint ldstr tokens (Mono games)
# --------------------------------------------------------------------------- #
# ECMA-335 Partition III opcode -> inline-operand byte count (0 = none).
# Built for instruction-BOUNDARY decoding so every ldstr token we repoint is
# provably an instruction operand, never a data byte. A method whose decode
# does not consume exactly its code size is left untouched.
_IL_OPERAND = {
    0x0E: 1, 0x0F: 1, 0x10: 1, 0x11: 1, 0x12: 1, 0x13: 1,            # .s args/locs
    0x1F: 1,                                                          # ldc.i4.s
    0x20: 4, 0x21: 8, 0x22: 4, 0x23: 8,                               # ldc.i4/i8/r4/r8
    0x27: 4, 0x28: 4, 0x29: 4,                                        # jmp/call/calli
    **{op: 1 for op in range(0x2B, 0x38)},                             # br.s..blt.un.s
    **{op: 4 for op in range(0x38, 0x45)},                             # br..blt.un
    0x45: "switch",                                                   # switch: 4+4n+default
    0x6F: 4, 0x70: 4, 0x71: 4, 0x72: 4, 0x73: 4, 0x74: 4, 0x75: 4,    # callvirt..isinst
    0x79: 4,                                                           # unbox
    0x7B: 4, 0x7C: 4, 0x7D: 4, 0x7E: 4, 0x7F: 4, 0x80: 4, 0x81: 4,    # ldfld..stobj
    0x8F: 4,                                                           # leave
    0xA3: 4, 0xA4: 4, 0xA5: 4,                                         # ldelem/stelem/ldelema
    0xC2: 4, 0xC6: 4, 0xD0: 4,                                         # refanyval/mkrefany/ldtoken
    0xDD: 1,                                                           # leave.s
}
_IL_FE_OPERAND = {
    0x06: 4, 0x07: 4,                              # ldftn / ldvirtftn
    0x09: 2, 0x0A: 2, 0x0B: 2, 0x0C: 2, 0x0D: 2, 0x0E: 2,   # ldarg..stloc
    0x12: 1, 0x19: 1,                              # unaligned. / no.
    0x15: 4, 0x16: 4, 0x1C: 4,                     # initobj/constrained/sizeof
}
_LDSTR = 0x72


def us_records(heap: bytes):
    """(prefix_offset, X, decoded_string) for every #US record.

    Layout verified byte-level on csc- AND Mono-produced assemblies: record
    = [compressed uint X][X-1 string bytes][1 flag byte]; the heap opens
    with a single 0x00 (empty entry); X == 1 is the empty string.
    prefix_offset IS the ldstr token's offset part.
    """
    n = len(heap)
    i = 1
    while i < n:
        b0 = heap[i]
        if b0 == 0:
            i += 1
            continue
        if b0 & 0x80 == 0:
            val, hdr = b0, 1
        elif b0 & 0xC0 == 0x80:
            if i + 1 >= n:
                return
            val, hdr = ((b0 & 0x3F) << 8) | heap[i + 1], 2
        else:
            if i + 3 >= n:
                return
            val = (((b0 & 0x1F) << 24) | (heap[i + 1] << 16) |
                   (heap[i + 2] << 8) | heap[i + 3]) & 0x7FFFFFFF
            hdr = 4
        if val < 2:
            i += hdr + max(val, 0)
            continue
        if i + hdr + val > n:
            return
        try:
            s = heap[i + hdr:i + hdr + val - 1].decode("utf-16-le")
        except UnicodeDecodeError:
            return
        yield i, val, s
        i += hdr + val


def _iter_il(data: bytes, code_off: int, code_size: int):
    """Yield (insn_offset, opcode, operand_file_offset_or_None) over one
    method body, walking real instruction boundaries. Raises ValueError on
    any decode inconsistency (unknown size, overrun, bad switch) so the
    caller can skip the whole method."""
    end = code_off + code_size
    i = code_off
    while i < end:
        insn_off = i
        op = data[i]
        i += 1
        if op == 0xFE:
            if i >= end:
                raise ValueError("truncated 0xFE prefix")
            op2 = data[i]
            i += 1
            size = _IL_FE_OPERAND.get(op2, 0)
            has_operand = size not in (0, "switch")
        else:
            size = _IL_OPERAND.get(op, 0)
            has_operand = size not in (0, "switch")
        operand_off = i
        if size == "switch":
            if i + 4 > end:
                raise ValueError("switch overrun")
            n_targets = struct.unpack_from("<I", data, i)[0]
            i += 4 + 4 * n_targets + 4   # targets + the default target u32
        elif size:
            i += size
        if i > end:
            raise ValueError(f"opcode {op:#x} overruns the body")
        yield insn_off, (0xFE00 | op2) if op == 0xFE else op, (
            operand_off if has_operand else None)


def translate_literals(dll_bytes: bytes, translations: dict) -> tuple:
    """Translate C# string literals in a managed DLL (Mono games).

    translations: {exact old string: new string}. The WHOLE #US heap is
    rebuilt compacted: translated records are re-emitted from the English
    payload, untranslated records are copied verbatim, records no ldstr
    token references are dropped, and EVERY ldstr instruction in the game
    (translated or not) is repointed to its record's new offset by real
    instruction-boundary IL decoding. If any method body fails to decode,
    the DLL is left untouched — a skipped method would keep stale offsets
    and read garbage strings after the compaction.

    Growth is UNLIMITED: whatever heap space the section slack cannot hold
    is placed in .text territory beyond the metadata blob, with .rsrc and
    .reloc shifted deeper into the grown file (their PointerToRawData,
    VirtualAddress and the Resources/BaseReloc data directories follow; VA
    order and every other structure are untouched). The naive variants of
    this (EOF appends, section extension WITHOUT moving the trailing
    sections) were tried first and are rejected by both the .NET and Mono
    loaders; the full shift loads and executes correctly on a real game's
    embedded Mono (oracle-tested).

    Returns (patched_bytes or None, stats); None means "DLL left
    unchanged". stats = {applied, texts_applied, dropped, grew,
    methods_scanned, methods_skipped, notes}.
    """
    empty = {"applied": 0, "texts_applied": 0, "dropped": 0, "grew": 0,
             "methods_scanned": 0, "methods_skipped": 0, "notes": []}
    try:
        asm = DotNetAssembly(dll_bytes)
    except Exception as e:  # noqa: BLE001
        empty["notes"] = [f"not a parseable assembly: {e!r}"]
        return None, empty
    heap = asm.us_heap
    if not heap:
        empty["notes"] = ["no #US heap"]
        return None, empty
    records = list(us_records(heap))          # (prefix_off, X, text) in order
    by_str = {}
    for off, _x, s in records:
        by_str.setdefault(s, []).append(off)
    translations = {
        k: v for k, v in translations.items()
        if isinstance(v, str) and v and k in by_str and v != k}

    # ---- pass 1: decode every method body; collect referenced records ----
    referenced = set()
    methods_scanned = methods_skipped = 0
    for _mrow, rva, _mname, _sig in asm.method_rows():
        body = method_body_info(asm, rva)
        if body is None:
            continue
        _off, code_off, code_size, _eh = body
        methods_scanned += 1
        try:
            for _insn, op, opnd in _iter_il(dll_bytes, code_off, code_size):
                if op == _LDSTR and opnd is not None:
                    tok = struct.unpack_from("<I", dll_bytes, opnd)[0]
                    if (tok >> 24) == 0x70:
                        referenced.add(tok & 0xFFFFFF)
        except (ValueError, IndexError):
            methods_skipped += 1
    if methods_skipped:
        empty["methods_scanned"] = methods_scanned
        empty["methods_skipped"] = methods_skipped
        empty["notes"] = [f"{methods_skipped} method body(ies) failed to "
                          f"decode - compaction would corrupt their strings, "
                          f"DLL left unchanged"]
        return None, empty

    def _enc(x: int) -> bytes:
        if x < 0x80:
            return bytes([x])
        if x < 0x4000:
            return bytes([0x80 | (x >> 8), x & 0xFF])
        return bytes([0xC0 | ((x >> 24) & 0x1F), (x >> 16) & 0xFF,
                      (x >> 8) & 0xFF, x & 0xFF])

    def _hdr(off: int) -> int:
        b0 = heap[off]
        return 1 if b0 & 0x80 == 0 else (2 if b0 & 0xC0 == 0x80 else 4)

    # ---- pass 2: build the compacted heap (no cap — growth is handled by
    # extending .text and shifting the trailing sections) ----
    def build(with_translations: dict):
        """Emit the compacted heap: drop dead records, translate what is
        asked, copy the rest verbatim. Returns (heap, {old_off: new_off}).
        The cursor is len(out) — the EMITTED size, which for translated
        records differs from the original record's size (getting this wrong
        desyncs every offset after the first translation)."""
        out = bytearray(b"\x00")     # heap[0]: the canonical empty entry
        new_off = {}
        for off, x, s in records:
            total = _hdr(off) + x
            if off not in referenced:
                continue             # dead record: dropped
            new_off[off] = len(out)
            if s in with_translations:
                payload = with_translations[s].encode("utf-16-le")
                out += _enc(len(payload) + 1) + payload + b"\x01"
            else:
                out += heap[off:off + total]
        return bytes(out), new_off

    new_heap, new_off = build(translations)
    delta = len(new_heap) - len(heap)

    # ---- pass 3: repoint every ldstr token to the new layout. The IL
    # (everything before the heap) is byte-identical in the final image, so
    # tokens are patched into a working copy that flows into the assembly.
    buf = bytearray(dll_bytes)
    repoint_text = {off: s for off, _x, s in records if off in referenced}
    applied = 0
    hit_texts = set()
    for _mrow, rva, _mname, _sig in asm.method_rows():
        body = method_body_info(asm, rva)
        if body is None:
            continue
        _off, code_off, code_size, _eh = body
        for _insn, op, opnd in _iter_il(dll_bytes, code_off, code_size):
            if op != _LDSTR or opnd is None:
                continue
            tok = struct.unpack_from("<I", dll_bytes, opnd)[0]
            if (tok >> 24) != 0x70:
                continue
            old_off = tok & 0xFFFFFF
            new = new_off.get(old_off)
            if new is None:
                continue
            struct.pack_into("<I", buf, opnd, 0x70000000 | new)
            applied += 1
            hit_texts.add(repoint_text.get(old_off))

    texts_applied = sum(1 for t in translations if t in hit_texts)

    # ---- assemble: heap replaced in place; streams after #US and the
    # sections after .text shift by delta; .text grows by delta ----
    cli_off = asm.pe.rva_to_offset(asm.pe.cli_rva)
    _meta_rva, meta_size = struct.unpack_from("<II", dll_bytes, cli_off + 8)
    us_stream_off = asm.stream_headers["#US"][1]
    us_start = asm.md_off + us_stream_off
    us_end = us_start + len(heap)
    blob_end = asm.md_off + meta_size
    text_end = None
    for _va0, _sz, _roff, _rsz in asm.pe.sections:
        if _roff <= us_start < _roff + _rsz:
            text_end = _roff + _rsz
            break
    if text_end is None:
        empty["notes"] = ["#US lives in no section - DLL left unchanged"]
        return None, empty
    in_slack = min(delta, text_end - blob_end)   # fits inside mapped .text
    tail = delta - in_slack                      # extends .text itself

    # the compacted heap REPLACES the old one wholesale: everything before
    # it (IL, patched tokens) comes from buf, everything after the old heap
    # (trailing streams, .rsrc/.reloc raw) shifts by delta as-is
    out = bytearray(bytes(buf[:us_start]))
    out += new_heap
    out += dll_bytes[us_end:blob_end]
    out += dll_bytes[blob_end:]

    u_hdr = asm.stream_headers["#US"][0]
    struct.pack_into("<I", out, u_hdr + 4, len(new_heap))
    struct.pack_into("<I", out, cli_off + 12, meta_size + delta)
    for name, (hdr_pos, off, _sz) in asm.stream_headers.items():
        if off >= us_stream_off and name != "#US":
            struct.pack_into("<I", out, hdr_pos, off + delta)

    # .text header: VirtualSize and SizeOfRawData grow by delta (the VA
    # extent must cover the new heap tail); sections stored after .text
    # shift: raw pointer by tail, VA by delta
    pe_off = struct.unpack_from("<I", out, 0x3C)[0]
    opt_size = struct.unpack_from("<H", out, pe_off + 20)[0]
    sec_base = pe_off + 24 + opt_size
    n_sections = struct.unpack_from("<H", out, pe_off + 6)[0]
    shifted_vas = []
    for si in range(n_sections):
        so = sec_base + si * 40
        vsz, va, rsz, pr = struct.unpack_from("<IIII", out, so + 8)
        if pr <= us_start < pr + rsz:                # the heap's own section
            struct.pack_into("<I", out, so + 8, vsz + delta)   # VirtualSize
            struct.pack_into("<I", out, so + 16, rsz + tail)   # SizeOfRawData
        elif pr >= text_end:                         # stored after .text
            # raw content shifts by delta (file grew), VA follows
            struct.pack_into("<I", out, so + 20, pr + delta)
            struct.pack_into("<I", out, so + 12, va + delta)
            shifted_vas.append((va, va + delta))
    # data directories pointing into shifted sections (Resources, BaseReloc)
    magic = struct.unpack_from("<H", dll_bytes, pe_off + 24)[0]
    dd_base = pe_off + 24 + (112 if magic == 0x20B else 96)
    for di in range(16):
        dva, _dsz = struct.unpack_from("<II", out, dd_base + di * 8)
        if not dva:
            continue
        for old_va, new_va in shifted_vas:
            if old_va <= dva:
                struct.pack_into("<I", out, dd_base + di * 8, dva + delta)
                break

    texts_dropped = len(translations) - texts_applied
    notes = list(empty["notes"])
    if texts_dropped:
        notes.append(f"{texts_dropped} translation(s) had no matching heap "
                     f"record and stay Japanese")
    notes.append(f"rebuilt the #US heap {len(heap)} -> {len(new_heap)} bytes "
                 f"(+{delta}; .text +{tail}); repointed {applied} ldstr "
                 f"token(s) across {methods_scanned} method(s)")
    return bytes(out), {"applied": applied, "texts_applied": texts_applied,
                        "dropped": texts_dropped, "grew": delta,
                        "methods_scanned": methods_scanned,
                        "methods_skipped": methods_skipped, "notes": notes}


# --------------------------------------------------------------------------- #
# text-fit calibration: find char-count window sizing in game code and
# recalibrate it for Latin text (opt-in, advanced)
# --------------------------------------------------------------------------- #
# Many eroge size their text boxes from the string's CHARACTER COUNT, priced
# as one full-width Japanese glyph per character (real case: MessageWindow/
# Narration coroutines computing `sizeDelta = Vector2(text.Length + 32 + 64,
# 96)` — one pixel per character). English carries ~3x the characters for the
# same words, so the box (and right-anchored text inside it) slides off
# screen while the typewriter runs. The recalibration scales the per-char
# constant down by a RATIO of the site's own original price (same-size
# in-place IL edit — one constant byte). The ratio is self-calibrating: a
# site pricing a character at 32 px becomes 16 px at ratio 0.5 — the Latin
# advance at that font size, so an English line lays out as wide as the
# Japanese line it replaces, the designer's intended bubble width. 0.5 is
# the default; lower tightens further, 1.0 leaves every site untouched.
# Worst case is a wrongly sized box — cosmetic and iterative — never a
# broken assembly.
_TEXTFIT_WINDOW = 12
# Latin advance ≈ half the full-width Japanese price (see above).
_TEXTFIT_DEFAULT_RATIO = 0.5


def _member_names(asm):
    """MemberRef row -> name (best-effort; resolution may be partial)."""
    t = asm.tables
    cls_sz = t.coded_sz((1, 0x1B, 0x1A))
    off = t._row_start
    for tid in range(0, 0x0A):
        if not t.present[tid]:
            continue
        n = t.rows.get(tid, 0)
        if tid in (0, 1, 2, 4, 6, 8):
            off += n * sum(t._schema()[tid])
        elif tid == 9:
            off += n * 2 * t.coded_sz((2, 0x1B, 0x1A))
        else:
            break
    out = {}
    if 0x0A not in t.rows:
        return out
    row_sz = cls_sz + t.str_sz + t.blob_sz
    for r in range(t.rows[0x0A]):
        ro = asm.t_base + off + r * row_sz
        name_i = struct.unpack_from("<H" if t.str_sz == 2 else "<I",
                                    asm.data, ro + cls_sz)[0]
        out[r + 1] = asm.s(name_i)   # MemberRef rows are 1-BASED in tokens
    return out


def calibrate_text_sizing_dll(dll_bytes: bytes, char_ratio: float = 0.5,
                              swing_scale: float = 0.25) -> tuple:
    """Recalibrate char-count text-box sizing for Latin (two site kinds).

    kind M (the speech-bubble slide): the box width is animated per typed
    character as `charIndex * K + padding` — K prices every character as a
    full-width Japanese glyph (real case: K=32; a 25-character English line
    then lays out as wide as the longest Japanese line ever shown). The
    rewrite sets K to `round(K * char_ratio)` — ratio 0.5 halves the price,
    which is the Latin advance at the site's own font size, so English
    lines lay out at the width the designer gave the Japanese ones. A
    ratio that would not shrink a site (>= 1 after rounding) leaves it
    byte-identical.
    kind L (narration boxes): `Length + padding` — one unit per character is
    already correct for Latin, so those sites are counted but never touched.

    Same-size single-byte edits; worst case is a wrongly sized box (cosmetic,
    iterative), never a broken assembly.

    Returns (patched_bytes or None, stats): {sites, rewritten, notes}.
    """
    asm = DotNetAssembly(dll_bytes)
    mrefs = _member_names(asm)
    all_methods = list(asm.method_rows())
    defnames = {m[0]: m[2] for m in all_methods}
    buf = bytearray(dll_bytes)
    sites = rewritten = swing_rewritten = 0
    notes = []
    patched_pos = set()

    for ns, tname, mlist, end in _type_method_spans(asm):
        full = (ns + "." + tname) if ns else tname
        for _mrow, rva, mname, _sig in all_methods[mlist - 1:end]:
            body = method_body_info(asm, rva)
            if body is None:
                continue
            _off, co, cs, _eh = body
            try:
                insns = list(_iter_il(dll_bytes, co, cs))
            except (ValueError, IndexError):
                continue
            seq = []
            for _i, op, opnd in insns:
                if op in (0x28, 0x6F) and opnd is not None:
                    tok = struct.unpack_from("<I", dll_bytes, opnd)[0]
                    tbl, rid = tok >> 24, tok & 0xFFFFFF
                    nm = defnames.get(rid) if tbl == 0x06 else mrefs.get(rid)
                    seq.append(("call", opnd, nm or ""))
                elif op == 0x1F and opnd is not None:
                    seq.append(("ldcs", opnd, dll_bytes[opnd]))
                elif op == 0x22 and opnd is not None:
                    seq.append(("f", opnd, struct.unpack_from(
                        "<f", dll_bytes, opnd)[0]))
                elif op == 0x5A:            # mul (empirical: csc emits 5A)
                    seq.append(("mul", _i, None))
            for idx, item in enumerate(seq):
                if item[0] != "call":
                    continue
                window = seq[max(0, idx - _TEXTFIT_WINDOW):idx]
                kinds = [w[0] + (f":{w[2]}" if w[0] == "call" else "")
                         for w in window]
                # kind M: char-count-priced box width — the width driver
                # (real case): `ldc K; mul` inside the window — the box
                # width is K units per CHARACTER (full-width Japanese
                # pricing) times the typed char index. A get_Length call in
                # the same window is the per-char Substring typing, NOT the
                # sizing driver — its presence does not exclude.
                if item[2] == "set_sizeDelta" and \
                        any(k == "call:get_Length" for k in kinds):
                    sites += 1
                    pair = None
                    for j in range(len(window) - 1, -1, -1):
                        if window[j][0] == "mul" and j and \
                                window[j - 1][0] == "ldcs" and \
                                window[j - 1][2] > 8:
                            pair = (window[j - 1], window[j])
                            break
                    if pair is None:
                        continue
                    ldc_pos, mul_pos = pair[0][1], pair[1][1]
                    if ldc_pos in patched_pos or mul_pos in patched_pos:
                        continue
                    old_k = pair[0][2]
                    new_k = max(2, round(old_k * char_ratio))
                    if new_k >= old_k:
                        continue  # ratio >= 1 (or a rounding tie): leave it
                    buf[ldc_pos] = new_k
                    patched_pos.update((ldc_pos, mul_pos))
                    rewritten += 1
                    notes.append(f"{full}.{mname}: per-char width price "
                                 f"{old_k} -> {new_k} (x{char_ratio:g})")
                # kind S: the swinging-bubble entrance — a time-based Sin
                # sweep whose AMPLITUDE constant carries the bubble off
                # screen (real case: 400*Sin(...) on anchoredPosition while
                # the message types). Scale the amplitude down; same-size
                # float edit, purely cosmetic.
                if item[2] in ("set_anchoredPosition", "set_localPosition") \
                        and any(w[0] == "call" and
                                w[2] in ("Sin", "Cos") for w in window):
                    for j in range(len(window) - 1, -1, -1):
                        w = window[j]
                        if w[0] != "f" or w[2] < 100:
                            continue
                        if j + 1 >= len(window) or window[j + 1][0] != "mul":
                            continue
                        fpos = w[1]
                        if fpos in patched_pos:
                            continue
                        old_a = w[2]
                        new_a = round(old_a * swing_scale, 2)
                        struct.pack_into("<f", buf, fpos, new_a)
                        patched_pos.add(fpos)
                        swing_rewritten += 1
                        notes.append(f"{full}.{mname}: swing amplitude "
                                     f"{old_a:g} -> {new_a:g}")
    return bytes(buf), {"sites": sites, "rewritten": rewritten,
                        "swing_rewritten": swing_rewritten, "notes": notes}


def calibrate_text_sizing(
    game_dir: str,
    out_dir: str,
    *,
    char_ratio: float = _TEXTFIT_DEFAULT_RATIO,
    swing_scale: float = 0.25,
    patch_mode: bool = False,
    in_place: bool = False,
    progress=None,
) -> dict:
    """Export stage: recalibrate char-count window sizing in every
    non-system managed DLL. Staging semantics mirror patch_managed_code:
    everything is addressed by its GAME-RELATIVE path
    (<Game>_Data/Managed/<dll>) — v2.6k wrote bare out_dir/Managed/...,
    which no stage or zip consumer ever reads back (the patch shipped
    uncalibrated while the log claimed success)."""
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    from .files import assert_inside, assert_not_nested, find_data_dir
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if not in_place:
        assert_not_nested(game_dir, out_dir, "out_dir")
    data_dir = find_data_dir(game_dir) or game_dir
    data_rel = (os.path.relpath(data_dir, game_dir).replace("\\", "/")
                if data_dir != game_dir else "")
    managed = os.path.join(data_dir, "Managed")
    stats = {"sizing_sites": 0, "sizing_rewritten": 0, "sizing_files": 0,
             "sizing_log": [], "exported_files": []}
    if not os.path.isdir(managed):
        stats["sizing_log"].append(
            "no Managed folder (IL2CPP game) - text-fit calibration not "
            "applicable")
        return stats
    from .monopatch import _SKIP_DLL_PREFIXES
    names = sorted(x for x in os.listdir(managed)
                   if x.lower().endswith(".dll")
                   and not x.lower().startswith(_SKIP_DLL_PREFIXES))
    for i, name in enumerate(names):
        rel = os.path.join(data_rel, "Managed", name)
        src = os.path.join(managed, name)
        if patch_mode:
            maybe = os.path.join(out_dir, rel)
            if os.path.isfile(maybe):
                src = maybe  # stage-order rule
        try:
            data = open(src, "rb").read()
            new_data, st = calibrate_text_sizing_dll(
                data, char_ratio=char_ratio, swing_scale=swing_scale)
        except Exception as e:  # noqa: BLE001
            stats["sizing_log"].append(f"! {name}: {e!r}")
            continue
        stats["sizing_sites"] += st["sites"]
        stats["sizing_rewritten"] += st["rewritten"]
        if st["rewritten"]:
            out_file = os.path.join(out_dir, rel)
            assert_inside(out_dir, out_file, f"textfit output for {rel!r}")
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with open(out_file, "wb") as fh:
                fh.write(new_data)
            stats["sizing_files"] += 1
            stats["exported_files"].append(rel.replace("\\", "/"))
        for note in st["notes"]:
            stats["sizing_log"].append(f"{name}: {note}")
        if progress:
            progress(i + 1, len(names), rel.replace("\\", "/"))
    if stats["sizing_rewritten"]:
        stats["sizing_log"].insert(0,
            f"recalibrated {stats['sizing_rewritten']} char-count sizing "
            f"site(s) in {stats['sizing_files']} DLL(s) - per-character "
            f"width price scaled by x{char_ratio:g}; check bubbles in game "
            f"and re-export with a different ratio if needed")
    return stats


# --------------------------------------------------------------------------- #
# text alignment normalization: centered runtime text grows both ways
# --------------------------------------------------------------------------- #
# The sliding-bubble class of bugs: a bubble/message Text component whose
# rect is a zero-size POINT anchor with a centered pivot relies on Unity's
# text ALIGNMENT to place the string — and the game's typewriter re-fills
# it per character, so as the line grows it extends BOTH ways from that
# point. With Japanese (short lines) that stays on screen; with English
# (2-3x the characters) the left half walks off screen while typing.
#
# The fix: flip the serialized uGUI Text alignment to a LEFT variant. The
# text then grows rightward from a fixed left edge — readable, no slide.
# uGUI Text components in stripped (typetree-less) scenes are located by
# their serialized layout, which is stable across Unity versions:
#   m_Font (PPtr: i32 fileID + i64 pathID) | m_FontSize i32 | m_FontStyle
#   i32 | m_BestFit b | m_MinSize i32 | m_MaxSize i32 | m_Alignment i32 | ...
#   m_Text (i32 len + utf8 + pad)
# Validation: the Font PPtr must resolve (same file, or one of the file's
# externals) to a known Font object; FontSize/Min/Max/Alignment plausible;
# the m_Text length prefix decodes to valid UTF-8. Only center/right
# alignments flip; left alignments are left as-is.
_TEXT_ALIGN = {"upperleft": 0, "uppercenter": 1, "upperright": 2,
               "middleleft": 3, "middlecenter": 4, "middleright": 5,
               "lowerleft": 6, "lowercenter": 7, "lowerright": 8}
_ALIGN_TO_LEFT = {1: 0, 2: 0, 4: 3, 5: 3, 7: 6, 8: 6}  # center/right -> left


def _uigu_text_sites(payload: bytes, valid_pptrs: set) -> list:
    """(alignment_value_pos, current_alignment) candidates in one payload.

    valid_pptrs: set of (fileID, pathID) PPtr pairs that resolve to a Font.
    The m_Font PPtr is located by VALUE match; the alignment field sits 20
    bytes after the FontData block starts; the m_Text length prefix 20 bytes
    after the alignment. The UTF-8 decodability of the text is the final
    validator.
    """
    out = []
    n = len(payload)
    for fpos in range(0, max(1, n - 64), 4):
        fid = int.from_bytes(payload[fpos:fpos + 4], "little", signed=True)
        if fid < 0:
            continue
        pid = int.from_bytes(payload[fpos + 4:fpos + 12], "little", signed=True)
        if (fid, pid) not in valid_pptrs:
            continue
        base = fpos + 12                     # FontData starts after the PPtr
        if base + 44 > n:
            continue
        fontsize = int.from_bytes(payload[base:base + 4], "little")
        style = int.from_bytes(payload[base + 4:base + 8], "little")
        bestfit = payload[base + 8]
        minsz = int.from_bytes(payload[base + 12:base + 16], "little")
        maxsz = int.from_bytes(payload[base + 16:base + 20], "little")
        align = int.from_bytes(payload[base + 20:base + 24], "little")
        if not (4 <= fontsize <= 256 and style <= 2 and bestfit in (0, 1)
                and 4 <= minsz <= fontsize and fontsize <= maxsz <= 512
                and align <= 8):
            continue
        # m_Text: i32 length + utf8, after align+alignGeo+rich+hOver+vOver+spacing
        tpos = base + 24 + 20
        if tpos + 4 > n:
            continue
        tlen = int.from_bytes(payload[tpos:tpos + 4], "little")
        if tlen < 0 or tpos + 4 + tlen > n:
            continue
        try:
            payload[tpos + 4:tpos + 4 + tlen].decode("utf-8")
        except UnicodeDecodeError:
            continue
        out.append((base + 20, align))
    return out


def normalize_alignments(data: bytes, valid_pptrs: set):
    """Flip center/right uGUI Text alignments to Left in one payload.

    Returns (new_bytes, {"aligned": n, "candidates": m}); new_bytes is the
    ORIGINAL object when nothing changed.
    """
    out = bytearray(data)
    aligned = candidates = 0
    for pos, align in _uigu_text_sites(data, valid_pptrs):
        candidates += 1
        new = _ALIGN_TO_LEFT.get(align)
        if new is None:
            continue  # already left-aligned
        out[pos:pos + 4] = int(new).to_bytes(4, "little")
        aligned += 1
    if not aligned:
        return data, {"aligned": 0, "candidates": candidates}
    return bytes(out), {"aligned": aligned, "candidates": candidates}


def _file_font_pptrs(env, fonts_by_basename):
    """(fileID, pathID) pairs whose PPtr resolves to a Font, for one
    serialized file: own fonts (fileID 0) plus any external file that
    carries fonts (fileID = 1-based external index)."""
    pairs = set()
    own = {o.path_id for o in env.objects if o.type.name == "Font"}
    pairs.update((0, pid) for pid in own)
    exts = getattr(env.file, "externals", None) or []
    for i, e in enumerate(exts):
        pname = (getattr(e, "name", None)
                 or os.path.basename(getattr(e, "path", "") or ""))
        for pid in fonts_by_basename.get(str(pname).lower(), ()):
            pairs.add((i + 1, pid))
    return pairs


def calibrate_text_alignment(game_dir: str, out_dir: str, *, patch_mode: bool = False,
                             in_place: bool = False, progress=None) -> dict:
    """Export stage: left-align centered runtime text (the sliding-bubble
    fix) in every scene/asset file that references the game's own fonts.
    Staging semantics mirror the other stages."""
    if in_place and patch_mode:
        raise ValueError("in_place and patch_mode are mutually exclusive")
    from .files import assert_inside, assert_not_nested, find_asset_files,         find_data_dir
    game_dir = os.path.abspath(game_dir)
    out_dir = os.path.abspath(out_dir)
    if in_place and game_dir != out_dir:
        raise ValueError("in_place=True requires game_dir == out_dir")
    if not in_place:
        assert_not_nested(game_dir, out_dir, "out_dir")
    from .inject import _save_env_to_file
    data_dir = find_data_dir(game_dir) or game_dir
    files = find_asset_files(data_dir)
    stats = {"aligned": 0, "aligned_files": 0, "candidates": 0,
             "alignment_log": [], "exported_files": []}

    import UnityPy

    # pass 1: which files carry Font objects (for PPtr matching)
    fonts_by_base: dict = {}
    file_base = {}
    for af in files:
        base = os.path.basename(af.path).lower()
        file_base[af.path] = base
        try:
            env = UnityPy.load(af.path)
        except Exception:
            continue
        pids = {o.path_id for o in env.objects if o.type.name == "Font"}
        if pids:
            fonts_by_base[base] = pids

    done = 0
    for af in files:
        done += 1
        rel = os.path.relpath(af.path, game_dir).replace("\\", "/")
        src = af.path
        if patch_mode:
            maybe = os.path.join(out_dir, rel)
            if os.path.isfile(maybe):
                src = maybe  # stage-order rule
        try:
            env = UnityPy.load(src)
        except Exception:
            if progress:
                progress(done, len(files), rel)
            continue
        pairs = _file_font_pptrs(env, fonts_by_base)
        if not pairs:
            if progress:
                progress(done, len(files), rel)
            continue
        aligned = candidates = 0
        dirty = False
        for o in env.objects:
            if o.type.name != "MonoBehaviour":
                continue
            try:
                raw = o.get_raw_data()
            except Exception:
                continue
            new_raw, st = normalize_alignments(raw, pairs)
            candidates += st["candidates"]
            if st["aligned"]:
                o.set_raw_data(new_raw)
                aligned += st["aligned"]
                dirty = True
        if dirty:
            out_file = os.path.join(out_dir, rel)
            assert_inside(out_dir, out_file, f"alignment output for {rel!r}")
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            _save_env_to_file(env, out_file)
            stats["aligned_files"] += 1
            stats["exported_files"].append(rel.replace("\\", "/"))
            stats["alignment_log"].append(
                f"{rel}: left-aligned {aligned} centered text component(s)")
            stats["aligned"] += aligned
        stats["candidates"] += candidates
        if progress:
            progress(done, len(files), rel)
    if stats["aligned"]:
        stats["alignment_log"].insert(0,
            f"left-aligned {stats['aligned']} centered text component(s) in "
            f"{stats['aligned_files']} file(s) - text now grows rightward")
    return stats
