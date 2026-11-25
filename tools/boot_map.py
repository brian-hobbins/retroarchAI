#!/usr/bin/env python3
"""Boot map: capture periodic frames during boot and tile them into ONE montage
PNG so the whole intro/title/menu timeline can be inspected at a glance.

Usage: python3 tools/boot_map.py [maxframes] [interval] [press]
  press: START | A | NONE  (button tapped every 90 frames during boot)
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

def downscale(rgb, w, h, factor):
    nw, nh = w // factor, h // factor
    out = bytearray(nw * nh * 3)
    oi = 0
    for y in range(nh):
        sy = y * factor
        for x in range(nw):
            sx = x * factor
            si = (sy * w + sx) * 3
            out[oi] = rgb[si]; out[oi+1] = rgb[si+1]; out[oi+2] = rgb[si+2]
            oi += 3
    return bytes(out), nw, nh

def main():
    maxf = int(sys.argv[1]) if len(sys.argv) > 1 else 12000
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    press = sys.argv[3] if len(sys.argv) > 3 else "START"
    cols = 4
    factor = 2
    gap = 4

    emu = LibretroCore(find_snes_core("auto"),
                       os.path.join(ROOT, "saves", "system"),
                       os.path.join(ROOT, "saves"))
    emu.load_rom(rom())

    tiles = []  # (frameno, rgb, w, h)
    for n in range(1, maxf + 1):
        if press != "NONE" and n % 90 < 6:
            emu.set_buttons(0, [press])
        else:
            emu.clear_input(0)
        emu.run(1)
        if n % interval == 0:
            fr = emu.get_frame_rgb()
            if fr:
                w, h, rgb = fr
                small, sw, sh = downscale(rgb, w, h, factor)
                tiles.append((n, small, sw, sh))
    emu.close()

    if not tiles:
        print("no tiles"); return
    sw, sh = tiles[0][2], tiles[0][3]
    rows = (len(tiles) + cols - 1) // cols
    MW = cols * sw + (cols + 1) * gap
    MH = rows * sh + (rows + 1) * gap
    canvas = bytearray(b"\x20" * (MW * MH * 3))  # dark grey bg
    order = []
    for idx, (fno, small, tw, th) in enumerate(tiles):
        r, c = divmod(idx, cols)
        ox = gap + c * (sw + gap)
        oy = gap + r * (sh + gap)
        for y in range(th):
            dst = ((oy + y) * MW + ox) * 3
            src = y * tw * 3
            canvas[dst:dst + tw * 3] = small[src:src + tw * 3]
        order.append((idx, fno))
    out = os.path.join(ROOT, "logs", "boot_map.png")
    write_png(out, MW, MH, bytes(canvas))
    print(f"press={press} interval={interval} tiles={len(tiles)}  -> {out}")
    print("tile order (row-major, 4 cols), frame numbers:")
    for i in range(0, len(order), cols):
        print("  " + "  ".join(f"[{o[0]:2d}]f{o[1]}" for o in order[i:i+cols]))

if __name__ == "__main__":
    main()
