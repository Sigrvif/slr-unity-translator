"""Scan every non-system Managed DLL for bubble-sizing candidate sites.

A site = a method containing a size/position API call (set_sizeDelta,
DOSizeDelta, set_anchoredPosition, ...) whose preceding 12-instruction
window contains a get_Length call or an (ldc K, mul) pair — the two shapes
games use to price a box from text length.
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
MANAGED = os.path.join(GAME, "KJ_Data", "Managed")
SIZE_APIS = ("set_sizeDelta", "dosizedelta", "set_anchoredposition",
             "doanchorposx", "doanchorpos", "setwindowposition",
             "set_localscale", "dosize", "set_preferredwidth")

targets = sorted(f for f in os.listdir(MANAGED)
                 if f.lower().endswith(".dll")
                 and not f.lower().startswith(mp._SKIP_DLL_PREFIXES))

for name in targets:
    path = os.path.join(MANAGED, name)
    try:
        dll_data = open(path, "rb").read()
        asm = mp.DotNetAssembly(dll_data)
        mrefs = mp._member_names(asm)
    except Exception:
        continue
    methods = list(asm.method_rows())
    defnames = {m[0]: m[2] for m in methods}
    owner = {}
    for ns, tn, ml, e in mp._type_method_spans(asm):
        ft = (ns + "." + tn) if ns else tn
        for mrow in range(ml, e + 1):
            owner[mrow] = (ft, defnames.get(mrow, f"m{mrow}"))

    hits = []
    for _mrow, rva, mname, _sig in methods:
        ft, parent_m = owner.get(_mrow, ("?", mname))
        body = method_body_info(asm, rva)
        if not body:
            continue
        _o, co, cs, _eh = body
        seq = []
        try:
            for _i, op, opnd in _iter_il(dll_data, co, cs):
                if op in (0x28, 0x6F) and opnd is not None:
                    tok = struct.unpack_from("<I", dll_data, opnd)[0]
                    tbl, rid = tok >> 24, tok & 0xFFFFFF
                    nm = defnames.get(rid) if tbl == 0x06 else mrefs.get(rid)
                    seq.append(("call", nm or ""))
                elif op == 0x1F and opnd is not None:
                    seq.append(("ldc", dll_data[opnd]))
                elif op == 0x5A:
                    seq.append(("mul", None))
        except (ValueError, IndexError):
            continue
        for idx, item in enumerate(seq):
            if item[0] != "call":
                continue
            if not any(a in item[1].lower() for a in SIZE_APIS):
                continue
            window = seq[max(0, idx - 12):idx]
            kinds = [w[0] + (":" + str(w[1]) if w[0] == "ldc" else "") for w in window]
            has_len = any(k == "call:get_Length" for k in kinds)
            pairs = sum(1 for i, k in enumerate(kinds[:-1])
                        if k.startswith("ldc:") and kinds[i + 1] == "mul")
            if has_len or pairs:
                hits.append((ft, mname, has_len, pairs,
                             [k for k in kinds if k.startswith("ldc")]))
    if hits:
        print(f"== {name}")
        for ft, mname, has_len, pairs, ldcs in hits[:8]:
            print(f"   {ft}.{mname}  get_Length={has_len} ldc-mul-pairs={pairs} ldc={ldcs}")
print("scan complete")
