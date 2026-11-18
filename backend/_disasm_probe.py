"""Investigate the sliding bubble: disassemble DrawText/typewriter methods."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from ut.monopatch import DotNetAssembly, method_body_info, _iter_il

# game root via env (local test game; never named in the repository)
GAME = os.environ.get("SLRUNITY_KJ_GAME", "")
P = os.path.join(GAME, "KJ_Data", "Managed", "Assembly-CSharp.dll")
data = open(P, "rb").read()
asm = DotNetAssembly(data)
methods = list(asm.method_rows())
# memberref names (call targets outside the assembly)
def memberref_names(asm):
    """(row -> 'Type::name') from the MemberRef table."""
    t = asm.tables
    out = {}
    # schema: MemberRef(0x0A): Class coded(TypeDefOrRef: 2/4?), Name S, Sig B
    cls_sz = t.coded_sz((1, 0x1B, 0x1A))
    found = False
    # locate table 0x0A offset by walking present tables in id order
    off = t._row_start
    schema_known = {0: None, 1: None, 2: None, 3: None, 4: None, 5: None,
                    6: None, 7: None, 8: None}
    sizes = {
        0: sum(t._schema()[0]),
        1: sum([t.coded_sz((0, 0x1A, 0x23, 1)), t.str_sz, t.str_sz]),
        2: sum(t._schema()[2]),
        4: sum(t._schema()[4]),
        6: sum(t._schema()[6]),
        8: sum(t._schema()[8]),
    }
    for tid in range(0x0A + 1):
        if not t.present[tid]:
            continue
        if tid == 0x0A:
            n = t.rows[tid]
            for r in range(n):
                ro = asm.t_base + off + r * (cls_sz + t.str_sz + t.blob_sz)
                cls = struct.unpack_from("<H" if cls_sz == 2 else "<I",
                                         asm.data, ro)[0]
                name_i = struct.unpack_from(
                    "<H" if t.str_sz == 2 else "<I", asm.data, ro + cls_sz)[0]
                tag, rid = cls & 7, cls >> 3
                tname = ""
                if tag == 0 and rid:  # TypeDef
                    for row, ns, nm, _ml in asm.type_rows():
                        if row == rid:
                            tname = nm
                            break
                elif tag == 1 and rid:  # TypeRef
                    pass  # external type: leave blank, name alone suffices
                out[r] = (f"{tname}::" if tname else "") + asm.s(name_i)
            found = True
            break
        off += t.rows.get(tid, 0) * sizes.get(tid, 0)
    if not found:
        pass
    return out


mrefs = memberref_names(asm)
defdefs = {m[0]: m[2] for m in methods}   # MethodDef row -> name


def disasm(rva, label):
    body = method_body_info(asm, rva)
    if not body:
        print(f"  {label}: no body")
        return
    _o, co, cs, eh = body
    print(f"  {label}: {cs}B{' EH' if eh else ''}")
    for _i, op, opnd in _iter_il(data, co, cs):
        txt = f"{op:#04x}"
        if opnd is not None and op in (0x28, 0x6F, 0x72, 0x73, 0x20, 0x1F):
            if op in (0x28, 0x6F):
                tok = struct.unpack_from("<I", data, opnd)[0]
                if (tok >> 24) == 0x70:
                    txt += f" {tok:#x} STR:{'??'}"
                elif (tok >> 24) == 0x0A:
                    txt += f" call {mrefs.get(tok & 0xFFFFFF, hex(tok))}"
                elif (tok >> 24) == 0x06:
                    txt += f" call {defdefs.get(tok & 0xFFFFFF, hex(tok))}"
            elif op == 0x72:
                txt += " ldstr"
            else:
                txt += f" {struct.unpack_from('<I', data, opnd)[0]}"
        print(f"    {txt}")


# find interesting methods: names containing Draw/Text/Serif/Bubble/Message
from ut.monopatch import _type_method_spans
print("== interesting methods ==")
interesting = []
for ns, tname, mlist, end in _type_method_spans(asm):
    full = (ns + "." + tname) if ns else tname
    for mrow, rva, mname, _sig in methods[mlist - 1:end]:
        if any(k in mname for k in ("DrawText", "Serif", "Bubble", "Message")):
            interesting.append((full, mname, rva))
            print(f"  {full}.{mname} rva={rva:#x}")

for full, mname, rva in interesting:
    print(f"== {full}.{mname} ==")
    disasm(rva, mname)
