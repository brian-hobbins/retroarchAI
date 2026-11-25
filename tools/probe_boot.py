#!/usr/bin/env python3
"""Scripted-boot probe: tap START/A periodically and dump frames at intervals,
to find where the game becomes interactive (no LLM calls)."""
from __future__ import annotations
import glob, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from emulator.cores import find_snes_core
from emulator.libretro import LibretroCore
from emulator.pngwrite import write_png

def default_rom():
    h = sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))
    h += sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.sfc"), recursive=True))
    return h[0] if h else None

def main():
    rom = default_rom()
    out = os.path.join(ROOT, "logs", "probe")
    os.makedirs(out, exist_ok=True)
    emu = LibretroCore(find_snes_core("auto"),
                       os.path.join(ROOT, "saves", "system"),
                       os.path.join(ROOT, "saves"))
    emu.load_rom(rom)
    shots = [300, 900, 1800, 3000, 4200, 5400, 6600, 7800]
    maxf = max(shots)
    press = sys.argv[1] if len(sys.argv) > 1 else "START"
    for n in range(1, maxf + 1):
        # tap the button for 6 frames at the start of every 90-frame window
        phase = n % 90
        if phase < 6:
            emu.set_buttons(0, [press])
        else:
            emu.clear_input(0)
        emu.run(1)
        if n in shots:
            fr = emu.get_frame_rgb()
            if fr:
                w, h, rgb = fr
                p = os.path.join(out, f"boot_{n:05d}.png")
                write_png(p, w, h, rgb)
                print(f"frame {n}: {w}x{h} -> {p}")
    emu.close()

if __name__ == "__main__":
    main()
