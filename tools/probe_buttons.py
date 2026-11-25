#!/usr/bin/env python3
"""Button sweep: from a boot state, try each SNES button in isolation (restoring
a savestate each time) and report how much the screen changed. No LLM calls.

Helps discover a game's controls (e.g. which button exits a menu)."""
from __future__ import annotations
import glob, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from emulator.cores import find_snes_core
from emulator.libretro import LibretroCore, SNES_BUTTONS
from emulator.pngwrite import write_png

def rom():
    h = sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))
    h += sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.sfc"), recursive=True))
    return h[0] if h else None

def mad(a: bytes, b: bytes) -> float:
    if a is None or b is None or len(a) != len(b):
        return -1.0
    step = 211  # sample sparsely for speed
    tot = n = 0
    for i in range(0, len(a), step):
        tot += abs(a[i] - b[i]); n += 1
    return tot / max(n, 1)

def main():
    warm = int(sys.argv[1]) if len(sys.argv) > 1 else 5400
    out = os.path.join(ROOT, "logs", "buttons"); os.makedirs(out, exist_ok=True)
    emu = LibretroCore(find_snes_core("auto"),
                       os.path.join(ROOT, "saves", "system"),
                       os.path.join(ROOT, "saves"))
    emu.load_rom(rom())
    for n in range(1, warm + 1):
        emu.set_buttons(0, ["START"]) if n % 90 < 6 else emu.clear_input(0)
        emu.run(1)
    emu.clear_input(0); emu.run(30)

    w, h, base = emu.get_frame_rgb()
    write_png(os.path.join(out, "baseline.png"), w, h, base)
    st = emu.serialize()
    print(f"baseline saved ({w}x{h}); state={len(st)}B. Sweeping buttons (hold 10f, +50f):\n")

    for name in ["UP","DOWN","LEFT","RIGHT","A","B","X","Y","L","R","START","SELECT"]:
        emu.unserialize(st)
        emu.set_buttons(0, [name]); emu.run(10)
        emu.clear_input(0); emu.run(50)
        _, _, fr = emu.get_frame_rgb()
        d = mad(base, fr)
        write_png(os.path.join(out, f"after_{name}.png"), w, h, fr)
        print(f"  {name:7s} screen-change={d:7.2f}")
    emu.close()
    print(f"\nPNGs in {out}/  (baseline.png + after_<BTN>.png)")

if __name__ == "__main__":
    main()
