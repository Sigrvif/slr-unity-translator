"""Pattern-scan Assembly-CSharp for per-character typewriter/width methods.

Candidate pattern: a method that (a) indexes a string character-by-character
(get_Chars callvirt), (b) carries float constants (units), and (c) calls a
UI size/position API (DOSizeDelta / set_sizeDelta / DO* / SetWindowPosition /
set_preferredWidth family) — the shape of bubble-typewriter sizing code.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
import ut.monopatch as mp
from ut.monopatch import DotNetAssembly, method_body_info, _iter_il

# game root via env (local test game; never named in the repository)
GAME = os.environ.get("SLRUNITY_KJ_GAME", "")
P = os.path.join(GAME, "KJ_Data", "Managed", "Assembly-CSharp.dll")
data = open(P, "rb").read()
asm = mp.DotNetAssembly(data)
t = asm.tables
cls_sz = t.coded_sz((1, 0x1B, 0x1A))

# MemberRef name resolution (walk to 0x0A)
off = t._row_start
for tid in range(0, 0x0A):
    if not t.present[tid]:
        continue
    n = t.rows.get(tid, 0)
    if tid in (0, 1, 2, 4, 6, 8):
        off += n * sum(t._schema()[tid])
    elif tid == 9:
        off += n * 2 * t.coded_sz((2, 0x1B, 0x1A))
mref = {}
n = t.rows[0x0A]
row_sz = cls_sz + t.str_sz + t.blob_sz
for r in range(n):
    ro = asm.t_base + off + r * row_sz
    name_i = struct.unpack_from("<H" if t.str_sz == 2 else "<I", asm.data, ro + cls_sz)[0]
    mref[r] = asm.s(name_i)

SIZE_APIS = ("DOSizeDelta", "set_sizeDelta", "DOSize", "DOTransform",
             "set_preferredWidth", "SetWindowPosition", "DOFontSize",
             "set_fontSize", "DOText")
all_methods = list(asm.method_rows())
defnames = {m[0]: m[2] for m in all_methods}

hits = []
for ns, tname, mlist, end in mp._type_method_spans(asm):
    for mrow, rva, mname, _sig in all_methods[mlist - 1:end]:
        body = method_body_info(asm, rva)
        if not body:
            continue
        _o, co, cs, _eh = body
        has_getchars = has_float = False
        size_calls = []
        float_consts = []
        try:
            for _i, op, opnd in _iter_il(data, co, cs):
                if op in (0x28, 0x6F) and opnd:
                    tok = struct.unpack_from("<I", data, opnd)[0]
                    nm = defnames.get(tok & 0xFFFFFF) if (tok >> 24) == 6 \
                        else mref.get(tok & 0xFFFFFF)
                    if nm == "get_Chars":
                        has_getchars = True
                    elif nm and any(s in nm for s in SIZE_APIS):
                        size_calls.append(nm)
                elif op == 0x22 and opnd:
                    has_float = True
                    float_consts.append(struct.unpack_from("<f", data, opnd)[0])
        except (ValueError, IndexError):
            continue
        if has_getchars and size_calls:
            full = (ns + "." + tname) if ns else tname
            hits.append((full, mname, sorted(set(size_calls)),
                         sorted(set(float_consts))[:6]))

print(f"typewriter-pattern candidates: {len(hits)}")
for full, mname, calls, floats in hits:
    print(f"  {full}.{mname}")
    print(f"    size APIs: {calls}  floats: {floats}")
