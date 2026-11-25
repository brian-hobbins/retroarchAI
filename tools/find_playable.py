#!/usr/bin/env python3
"""Advance through intro/cutscene (pressing START/A) until the D-pad actually
moves the character, i.e. the player has control. Save that state.

Writes saves/ultima7_world.state and logs/savestate_world.png on success.
"""
from __future__ import annotations
import glob, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from emulator.cores import find_snes_core
from emulator.libretro import LibretroCore
from emulator.pngwrite import write_png

def rom():
    h = sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))
    h += sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.sfc"), recursive=True))
    return h[0] if h else None

def mad(a, b):
    step = 53; tot = n = 0
    for i in range(0, min(len(a), len(b)), step):
        tot += abs(a[i] - b[i]); n += 1
    return tot / max(n, 1)

def interactivity(emu):
    """Return (change_down, change_right) of the character vs idle from current state."""
    st = emu.serialize()
    def ri(btns):
        emu.unserialize(st)
        emu.set_buttons(0, btns); emu.run(40); emu.clear_input(0); emu.run(6)
        f = emu.get_frame_rgb(); return f[2]
    base = ri([]); down = ri(["DOWN"]); right = ri(["RIGHT"])
    emu.unserialize(st)
    return mad(base, down), mad(base, right)

def main():
    emu = LibretroCore(find_snes_core("auto"),
                       os.path.join(ROOT, "saves", "system"),
                       os.path.join(ROOT, "saves"))
    emu.load_rom(rom())
    emu.clear_input(0); emu.run(1000)  # past the first logos

    frame = 1000
    for it in range(60):
        cd, cr = interactivity(emu)
        w, h, rgb = emu.get_frame_rgb()
        if max(cd, cr) > 3.0:
            print(f"iter {it:2d} (~f{frame}): D-pad RESPONDS (down={cd:.1f} right={cr:.1f}) -> PLAYABLE")
            write_png(os.path.join(ROOT, "logs", "savestate_world.png"), w, h, rgb)
            with open(os.path.join(ROOT, "saves", "ultima7_world.state"), "wb") as f:
                f.write(emu.serialize())
            print("saved saves/ultima7_world.state + logs/savestate_world.png")
            emu.close(); return
        # advance: try START then A, giving transitions time to settle
        emu.set_buttons(0, ["START"]); emu.run(8); emu.clear_input(0); emu.run(90)
        emu.set_buttons(0, ["A"]); emu.run(8); emu.clear_input(0); emu.run(90)
        frame += 196
        print(f"iter {it:2d} (~f{frame}): not playable yet (down={cd:.1f} right={cr:.1f})")
    print("no playable state found; inspect logs/savestate_world.png")
    w, h, rgb = emu.get_frame_rgb()
    write_png(os.path.join(ROOT, "logs", "savestate_world.png"), w, h, rgb)
    emu.close()

if __name__ == "__main__":
    main()
