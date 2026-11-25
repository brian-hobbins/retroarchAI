"""Load a game manual that sits next to the ROM.

Pattern: drop a text file beside each ROM (e.g. roms/<game>/<something>.txt) and
it is fed to the AI as the authoritative control/mechanics reference. This makes
controls work for ANY game without hand-coding them.
"""

from __future__ import annotations

import glob
import os


def find_manual(rom_path: str) -> tuple[str, str]:
    """Return (text, path) of a manual next to the ROM, or ("", "").

    Prefers a text/markdown file whose name contains 'manual'/'guide'/'readme'/
    'controls'; otherwise the first .txt/.md in the ROM's directory. Plain-text
    only — scanned PDFs are not read here.
    """
    d = os.path.dirname(os.path.abspath(rom_path))
    txts = sorted(glob.glob(os.path.join(d, "*.txt"))
                  + glob.glob(os.path.join(d, "*.md")))
    if not txts:
        return "", ""
    preferred = [t for t in txts
                 if any(k in os.path.basename(t).lower()
                        for k in ("manual", "guide", "readme", "instructions",
                                  "controls"))]
    chosen = (preferred or txts)[0]
    try:
        with open(chosen, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), chosen
    except OSError:
        return "", ""
