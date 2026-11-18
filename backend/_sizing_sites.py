"""Find every char-count sizing site: get_Length -> constants -> Vector2 -> set_sizeDelta."""
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
off = t._row_start
for tid in range(0, 0x0A):
    if not t.present[tid]:
        continue
    n = t.rows.get(tid, 0)
    if tid in (0, 1, 2, 4, 6, 8):
        off += n * sum(t._schema()[tid])
    elif tid == 9:
        off += n * 2 * t.coded_sz((2, 0x1B, 0x1A))
n = t.rows[0x0A]
row_sz = cls_sz + t.str_sz + t.blob_sz


def member_name(r):
    ro = asm.t_base + off + (r - 1) * row_sz
    name_i = struct.unpack_from("<H" if t.str_sz == 2 else "<I", asm.data, ro + cls_sz)[0]
    return asm.s(name_i)


all_methods = list(asm.method_rows())
defnames = {m[0]: m[2] for m in all_methods}

sites = []
for ns, tname, mlist, end in mp._type_method_spans(asm):
    full = (ns + "." + tname) if ns else tname
    for mrow, rva, mname, _sig in all_methods[mlist - 1:end]:
        body = method_body_info(asm, rva)
        if not body:
            continue
        _o, co, cs, _eh = body
        seq = []
        try:
            for _i, op, opnd in _iter_il(data, co, cs):
                if op in (0x28, 0x6F) and opnd is not None:
                    tok = struct.unpack_from("<I", data, opnd)[0]
                    tbl, rid = tok >> 24, tok & 0xFFFFFF
                    nm = defnames.get(rid) if tbl == 0x06 else member_name(rid)
                    seq.append(("call", nm))
                elif op == 0x22 and opnd is not None:
                    seq.append(("f", struct.unpack_from("<f", data, opnd)[0]))
                elif op == 0x73 and opnd is not None:
                    seq.append(("newobj",))
                elif op == 0x1F and opnd is not None:
                    seq.append(("+i", data[opnd]))
        except (ValueError, IndexError):
            continue
        for i, s in enumerate(seq):
            if s == ("call", "get_Length"):
                win = seq[i:i + 9]
                if any(x == ("call", "set_sizeDelta") for x in win if isinstance(x, tuple)):
                    sites.append((full, mname, win))

for full, mname, win in sites:
    print(f"{full}.{mname}:")
    print("   ", " ".join(f"{x[1] if x[0] == 'call' else x}" if isinstance(x, tuple) and x[0] == "call"
                           else str(x) for x in win))
print(f"\nsizing sites: {len(sites)}")
