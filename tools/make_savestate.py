#!/usr/bin/env python3
"""Boot Ultima VII into the interactive overworld, verify the D-pad moves the
character, and save that state to disk for the AI/viewer to load.

Writes: saves/ultima7_world.state  and  logs/savestate_world.png
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

def stats(rgb):
    step = 97
    r = g = b = n = 0
    for i in range(0, len(rgb) - 2, step):
        r += rgb[i]; g += rgb[i+1]; b += rgb[i+2]; n += 1
    r, g, b = r/n, g/n, b/n
    green = g - (r + b) / 2.0          # grass overworld => clearly positive
    bright = (r + g + b) / 3.0
    return r, g, b, green, bright

def mad(a, b):
    step = 97; tot = n = 0
    for i in range(0, len(a), step):
        tot += abs(a[i] - b[i]); n += 1
    return tot / max(n, 1)

def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    emu = LibretroCore(find_snes_core("auto"),
                       os.path.join(ROOT, "saves", "system"),
                       os.path.join(ROOT, "saves"))
    emu.load_rom(rom())

    # Boot with NO input to the opening dialogue (intro auto-plays).
    emu.clear_input(0)
    emu.run(target)
    w, h, rgb = emu.get_frame_rgb()
    r, g, b, green, bright = stats(rgb)
    print(f"at frame {target}: rgb=({r:.0f},{g:.0f},{b:.0f}) green={green:.1f} bright={bright:.1f}")
    write_png(os.path.join(ROOT, "logs", "savestate_world.png"), w, h, rgb)

    # Verify the game responds to input (A advances dialogue; D-pad moves).
    st = emu.serialize()
    def run_input(btns, hold=30):
        emu.unserialize(st)
        emu.set_buttons(0, btns); emu.run(hold); emu.clear_input(0); emu.run(10)
        return emu.get_frame_rgb()[2]
    base = run_input([])
    a = run_input(["A"]); b_btn = run_input(["B"]); start = run_input(["START"])
    down = run_input(["DOWN"]); right = run_input(["RIGHT"])
    print(f"idle-vs-A    ={mad(base,a):6.2f}")
    print(f"idle-vs-B    ={mad(base,b_btn):6.2f}")
    print(f"idle-vs-START={mad(base,start):6.2f}")
    print(f"idle-vs-DOWN ={mad(base,down):6.2f}")
    print(f"idle-vs-RIGHT={mad(base,right):6.2f}")
    interactive = max(mad(base,a), mad(base,down), mad(base,right), mad(base,start)) > 2.0
    found = bright > 15

    emu.unserialize(st)
    state_path = os.path.join(ROOT, "saves", "ultima7_world.state")
    with open(state_path, "wb") as f:
        f.write(emu.serialize())
    print(f"found_world={found} interactive={interactive}")
    print(f"saved state -> {state_path} ({os.path.getsize(state_path)} bytes)")
    print(f"screenshot  -> logs/savestate_world.png")
    emu.close()

if __name__ == "__main__":
    main()
