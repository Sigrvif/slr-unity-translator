"""End-to-end test: scan -> patch -> rescan through the real CLI envelope.

Documented adaptation of the brief's assertions: the translation markers are
pure ASCII ("[E2E]{src}:en"), and discover() only emits strings that pass the
Japanese gate (contains_japanese), so a rescan of the patched copy can prove
the ORIGINALS are gone but can never re-find the ASCII markers. The spec
intent "every translation landed, every original gone" is therefore verified
in two complementary halves:

  * originals gone     -> rescan of the patched copy (discover() would still
                          re-emit any surviving Japanese original);
  * translations landed -> direct reads of the patched artifacts:
        metadata  -> ut.metadata.read_literals on the patched
                     global-metadata.dat (literal index == path_id)
        textasset -> UnityPy m_Script of the patched object
        typetree  -> read_typetree + token-path navigation
        raw       -> raw object payload contains the marker record
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                    # `from test_cli import _run`
sys.path.insert(0, os.path.dirname(_HERE))   # `import ut` for the deep checks

from test_cli import _run
from ut import jp as jp_det

# All four asset-level source families; this example game has hits in each.
FAMILIES = ("typetree", "raw", "metadata", "textasset")


def _get_by_tokens(tree, tokens):
    """Read the leaf `tokens` addresses in a typetree (mirror of
    inject._set_by_tokens' navigation); only called on paths a successful
    write just landed on, so plain indexing is safe."""
    cur = tree
    for t in tokens:
        cur = cur[t]
    return cur


def test_full_pipeline(game_dir, has_game, tmp_path):
    import UnityPy

    from ut import metadata as ut_metadata

    # 1. scan the pristine game
    scan = _run({"version": 2, "command": "scan", "game_dir": game_dir}, tmp_path)
    assert scan["ok"]

    # 2. pick one translation per source family that exists (v2.2: scan hits
    #    also include non-Japanese strings — this test verifies the JP
    #    round-trip with "[E2E]src:en" markers, which are bracketed ASCII and
    #    deliberately fail the new visible-text gate on rescan; EN write-back
    #    is covered by test_discovery_nonjp)
    want = {}    # jp text -> ascii marker
    picked = {}  # source  -> (jp text, full occurrence from the scan index)
    for src in FAMILIES:
        for s in scan["strings"]:
            if s["source"] == src and jp_det.contains_japanese(s["text"]):
                want[s["text"]] = f"[E2E]{src}:en"
                picked[src] = (s["text"], scan["index"][s["text"]][0])
                break
    assert want, "no translatable strings found"
    assert set(FAMILIES) <= set(picked), (
        f"example game should yield all four source families, got {sorted(picked)}"
    )
    (tmp_path / "trans.json").write_text(json.dumps(want, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(scan["index"], ensure_ascii=False), encoding="utf-8")

    # 3. patch a full copy of the game
    out = tmp_path / "game_out"
    patch = _run({"version": 2, "command": "patch", "game_dir": game_dir, "out_dir": str(out),
                  "translations_path": str(tmp_path / "trans.json"),
                  "index_path": str(tmp_path / "index.json")}, tmp_path)
    assert patch["ok"] and patch["strings_written"] >= len(want)

    # 4. rescan the patched copy: every ORIGINAL is gone. (The ASCII markers
    #    cannot re-appear here — discover() only emits Japanese strings; the
    #    translations are verified directly on the artifacts in step 5.)
    rescan = _run({"version": 2, "command": "scan", "game_dir": str(out)}, tmp_path)
    assert rescan["ok"]
    texts = {s["text"] for s in rescan["strings"]}
    for jp in want:
        assert jp not in texts, f"original survived patching: {jp!r}"

    # 5. every translation landed — read the patched artifacts directly.
    def patched(occ):
        return os.path.join(str(out), *occ["file"].split("/"))

    def object_at(occ):
        env = UnityPy.load(patched(occ))
        # contract v2.1: path_id crosses JSON as a decimal string
        return next(o for o in env.objects if o.path_id == int(occ["path_id"]))

    # metadata: the literal table was relocated and rebuilt; the literal at
    # the occurrence's index (== path_id for this source) must be the marker.
    jp, occ = picked["metadata"]
    with open(patched(occ), "rb") as f:
        lits = ut_metadata.read_literals(f.read())
    lit = next(l for l in lits if l.index == int(occ["path_id"]))
    assert lit.text == want[jp]

    # textasset: the marker replaced the JP line inside m_Script.
    jp, occ = picked["textasset"]
    d = object_at(occ).read()
    script = getattr(d, "m_Script", b"")
    if isinstance(script, str):
        script = script.encode("utf-8", "surrogateescape")
    text = bytes(script).decode("utf-8", "ignore")
    assert want[jp] in text and jp not in text

    # typetree: the token-addressed leaf now carries the marker.
    jp, occ = picked["typetree"]
    tree = object_at(occ).read_typetree()
    assert _get_by_tokens(tree, occ["tokens"]) == want[jp]

    # raw: the rebuilt payload holds a fresh length-prefixed marker record.
    jp, occ = picked["raw"]
    payload = object_at(occ).get_raw_data()
    assert want[jp].encode("utf-8") in payload
    assert jp.encode("utf-8") not in payload
