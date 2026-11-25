"""Locate libretro SNES core shared objects already installed on the system."""

from __future__ import annotations

import glob
import os

# Preference order: accuracy/speed balance first.
_SNES_PREFERENCE = [
    "snes9x_libretro.so",
    "snes9x2010_libretro.so",
    "bsnes_libretro.so",
    "snes9x2005_plus_libretro.so",
    "snes9x2005_libretro.so",
    "snes9x2002_libretro.so",
]

_SEARCH_DIRS = [
    os.path.expanduser("~/snap/retroarch/common/.config/retroarch/cores"),
    os.path.expanduser("~/.config/retroarch/cores"),
    "/usr/lib/libretro",
    "/usr/local/lib/libretro",
]


def find_snes_core(explicit: str | None = None) -> str:
    """Return a path to a usable SNES core .so.

    If ``explicit`` is a real file path, return it. If it is "auto" or None,
    pick the best available core by preference.
    """
    if explicit and explicit != "auto":
        if os.path.isfile(explicit):
            return explicit
        raise FileNotFoundError(f"configured core not found: {explicit}")

    found: dict[str, str] = {}
    for d in _SEARCH_DIRS:
        for path in glob.glob(os.path.join(d, "*_libretro.so")):
            found.setdefault(os.path.basename(path), path)

    for name in _SNES_PREFERENCE:
        if name in found:
            return found[name]

    raise FileNotFoundError(
        "No SNES libretro core found. Install one via RetroArch's core "
        f"downloader or place a *_libretro.so in one of: {_SEARCH_DIRS}"
    )
