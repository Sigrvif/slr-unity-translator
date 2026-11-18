import glob
import os

import pytest

_EXAMPLES = os.environ.get("SLRUNITY_EXAMPLE_GAMES", "")

def _find(pred):
    if not _EXAMPLES:
        return None
    for p in sorted(glob.glob(os.path.join(_EXAMPLES, "*"))):
        if os.path.isdir(p) and pred(p):
            return p
    return None

def _is_il2cpp(p):
    # IL2CPP games ship GameAssembly.dll next to the player exe; no game
    # names are hardcoded here — detection is structural.
    return os.path.isfile(os.path.join(p, "GameAssembly.dll"))

STEALTH = os.environ.get("SLRUNITY_IL2CPP_GAME") or _find(_is_il2cpp)
MONO = os.environ.get("SLRUNITY_MONO_GAME") or _find(lambda p: not _is_il2cpp(p))

# The Live2D demosaic regression game: censor material IS the organ mesh's
# only draw, and its display shader is derivable from the mosaic shader's
# name. Lives outside _EXAMPLES; skipped when absent
# (set SLRUNITY_KJ_GAME to enable).
KJ_GAME = os.environ.get("SLRUNITY_KJ_GAME") or None
KJ_GAME = KJ_GAME if KJ_GAME and os.path.isdir(KJ_GAME) else None

has_game = pytest.mark.skipif(STEALTH is None, reason="example game not present")
has_mono = pytest.mark.skipif(MONO is None, reason="mono example game not present")
has_kj = pytest.mark.skipif(KJ_GAME is None, reason="Live2D regression game not present (SLRUNITY_KJ_GAME)")

# The SLR Translator installation carries the two bake faces (e2e-fix19):
# Stint Ultra Condensed (Latin role) and the condensed CJK face (JP role).
_SLR_ROOTS = [r"<dev-root>/SLR Translator", r"C:\Program Files\SLR Translator"]


def _find_slr(rel):
    for root in _SLR_ROOTS:
        cand = os.path.join(root, *rel.split("/"))
        if os.path.isfile(cand):
            return cand
    return None


SUCR_TTF = _find_slr("fonts/sucr.ttf")
CJK_TTF = _find_slr("www/core/SEP/SLRCondensedCJK-Regular.ttf")
has_bake_fonts = pytest.mark.skipif(
    SUCR_TTF is None or CJK_TTF is None,
    reason="SLR Translator fonts (sucr.ttf / SLRCondensedCJK) not present",
)

@pytest.fixture
def game_dir():
    return STEALTH

@pytest.fixture(name="has_game")
def has_game_fixture():
    # Fixture twin of the has_game mark: lets tests declare (game_dir, has_game)
    # parameters; skips cleanly when the example game is absent.
    if STEALTH is None:
        pytest.skip("example game not present")
    return True

@pytest.fixture
def game2_dir():
    return MONO

@pytest.fixture
def kj_dir():
    return KJ_GAME

@pytest.fixture(name="has_kj")
def has_kj_fixture():
    # Fixture twin of the has_kj mark, like has_game.
    if KJ_GAME is None:
        pytest.skip("Live2D regression game not present (SLRUNITY_KJ_GAME)")
    return True
