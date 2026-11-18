# Third-party notices — SLR Unity Translator

SLR Unity Translator is licensed under the GNU General Public License,
version 3 or later — see [LICENSE](LICENSE). The release packages bundle the
following third-party components:

## Runtime

| Component | License |
|---|---|
| CPython 3.10.11 embeddable (Windows payload, `runtime/win/python/`) | Python Software Foundation License (payload carries its own `LICENSE.txt`) |
| CPython 3.10.13, python-build-standalone (Linux payload, `runtime/linux/`) | Python Software Foundation License |

## Bundled Python packages (runtime site-packages, installed at build time)

No FMOD runtime is bundled: the backend never exports FMOD audio, so the
`fmod_toolkit`/`pyfmodex` packages (and their proprietary `libfmod`
binaries) that UnityPy optionally pulls in are stripped from the runtime
at build time (`build/fetch_runtime.py`, enforced by
`build/verify_package.py`).

| Package | License |
|---|---|
| archspec 0.2.6 | Apache-2.0 OR MIT |
| astc_encoder_py 0.1.12 | MIT |
| attrs 26.1.0 | MIT |
| brotli 1.2.0 | MIT |
| etcpak 0.9.15 | MIT |
| fonttools 4.65.0 | MIT |
| fsspec 2026.9.0 | BSD-3-Clause |
| lz4 4.4.5 | BSD-2-Clause |
| numpy 2.2.6 | BSD-3-Clause (c) NumPy Developers |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow 12.3.0 | MIT-CMU |
| pip 26.2.1 | MIT |
| setuptools 84.0.0 | MIT |
| texture2ddecoder 1.0.6 | MIT |
| unitypy 1.25.2 | MIT |
| wheel 0.48.0 | MIT |

No font is bundled: the export font stage uses the host SLR Translator's own
`SLRCondensedCJK-Regular.ttf`, resolved at runtime. The IL2CPP / Mono
patchers (`backend/ut/monopatch.py`, `mono.py`) are original implementations.

MIT license text (for the MIT rows above):

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions: The above copyright
> notice and this permission notice shall be included in all copies or
> substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
> WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
> TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT.
