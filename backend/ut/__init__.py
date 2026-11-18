"""SLRUnity Toolkit (ut) - universal in-place translation of Unity games.

Submodules:
  jp          - Japanese/CJK detection helpers.
  files       - enumerate a game's real asset files (+ path-safety guards).
  rawstrings  - length-prefix-validated Unity string scanning within byte buffers.
  mono        - report-only detection of JP literals in Mono Managed DLLs.
  metadata    - IL2CPP global-metadata.dat literal reader/rebuilder.
  textfiles   - TextAsset / plain-text extraction and rewrite.
  textures    - Texture2D PNG export / re-import (image-baked text).
  fonts       - embedded TTF replacement.
  discovery   - find every translatable string + texture + font in a game.
  inject      - write translations / fonts / textures back into the assets.
"""

__version__ = "0.1.0"
