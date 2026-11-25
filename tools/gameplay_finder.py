#!/usr/bin/env python3
"""Advance past intro/dialogue and find TRUE player control using a strong test
(DOWN vs UP must diverge), saving a trajectory montage so we can see each step.

Usage: python3 tools/gameplay_finder.py [start_frame] [iters]
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
    step = 53; t = n = 0
    for i in range(0, min(len(a), len(b)), step):
        t += abs(a[i] - b[i]); n += 1
    return t / max(n, 1)

def downscale(rgb, w, h, f):
    nw, nh = w // f, h // f
    out = bytearray(nw * nh * 3); oi = 0
    for y in range(nh):
        for x in range(nw):
            si = ((y*f) * w + (x*f)) * 3
            out[oi:oi+3] = rgb[si:si+3]; oi += 3
    return bytes(out), nw, nh

def montage(frames, path, cols=5, f=2, gap=3):
    tiles = [downscale(r, w, h, f) for (w, h, r) in frames]
    sw, sh = tiles[0][1], tiles[0][2]
    rows = (len(tiles) + cols - 1) // cols
    MW = cols*sw + (cols+1)*gap; MH = rows*sh + (rows+1)*gap
    canvas = bytearray(b"\x20" * (MW*MH*3))
    for i, (small, tw, th) in enumerate(tiles):
        r, c = divmod(i, cols)
        ox, oy = gap + c*(sw+gap), gap + r*(sh+gap)
        for y in range(th):
            d = ((oy+y)*MW + ox)*3; s = y*tw*3
            canvas[d:d+tw*3] = small[s:s+tw*3]
    write_png(path, MW, MH, bytes(canvas))

def strong_move_test(emu):
    st = emu.serialize()
    def ri(btns):
        emu.unserialize(st)
        emu.set_buttons(0, btns); emu.run(50); emu.clear_input(0); emu.run(6)
        return emu.get_frame_rgb()[2]
    idle = ri([]); down = ri(["DOWN"]); up = ri(["UP"])
    emu.unserialize(st)
    return max(mad(down, up), mad(down, idle), mad(up, idle))

def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 5600
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    emu = LibretroCore(find_snes_core("auto"),
                       os.path.join(ROOT, "saves", "system"),
                       os.path.join(ROOT, "saves"))
    emu.load_rom(rom())
    emu.clear_input(0); emu.run(start)

    frames = []
    for it in range(iters):
        score = strong_move_test(emu)
        w, h, rgb = emu.get_frame_rgb()
        frames.append((w, h, rgb))
        print(f"iter {it:2d}: move_score={score:6.2f}" + ("  <-- PLAYABLE" if score > 8 else ""))
        if score > 8:
            write_png(os.path.join(ROOT, "logs", "savestate_world.png"), w, h, rgb)
            with open(os.path.join(ROOT, "saves", "ultima7_world.state"), "wb") as f:
                f.write(emu.serialize())
            print("saved saves/ultima7_world.state + logs/savestate_world.png")
            break
        # advance the cutscene/dialogue
        emu.set_buttons(0, ["START"]); emu.run(6); emu.clear_input(0); emu.run(70)
        emu.set_buttons(0, ["A"]); emu.run(6); emu.clear_input(0); emu.run(70)
    montage(frames, os.path.join(ROOT, "logs", "trajectory.png"))
    print(f"trajectory montage -> logs/trajectory.png ({len(frames)} tiles)")
    emu.close()

if __name__ == "__main__":
    main()
