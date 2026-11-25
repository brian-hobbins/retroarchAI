#!/usr/bin/env python3
"""Zoom into the conversation keyword row to see how the selection cursor moves.
Loads the dialogue savestate, taps a direction, and saves a 4x crop of the
bottom third (where topic keywords live). No LLM."""
from __future__ import annotations
import glob, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from emulator.cores import find_snes_core
from emulator.libretro import LibretroCore
from emulator.pngwrite import write_png

def rom():
    return sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))[0]

def crop_zoom(rgb, w, h, y0, y1, z=4):
    ch = y1 - y0
    out = bytearray(w * z * ch * z * 3)
    for y in range(ch * z):
        sy = y0 + y // z
        for x in range(w * z):
            sx = x // z
            si = (sy * w + sx) * 3
            di = (y * w * z + x) * 3
            out[di:di+3] = rgb[si:si+3]
    return bytes(out), w * z, ch * z

def main():
    e = LibretroCore(find_snes_core("auto"), os.path.join(ROOT, "saves", "system"),
                     os.path.join(ROOT, "saves"))
    e.load_rom(rom())
    st = open(os.path.join(ROOT, "saves", "ultima7_dialogue.state"), "rb").read()
    out = os.path.join(ROOT, "logs", "cursor"); os.makedirs(out, exist_ok=True)
    for label, btns in [("base", []), ("LEFT", ["LEFT"]), ("RIGHT", ["RIGHT"]),
                         ("RIGHT2", ["RIGHT"]), ("DOWN", ["DOWN"]), ("UP", ["UP"])]:
        e.unserialize(st)
        if btns:
            e.set_buttons(0, btns); e.run(5); e.clear_input(0); e.run(14)
        else:
            e.run(2)
        w, h, rgb = e.get_frame_rgb()
        # keyword row sits in the lower ~third
        cz, cw, ch = crop_zoom(rgb, w, h, 118, 170, 4)
        write_png(os.path.join(out, f"kw_{label}.png"), cw, ch, cz)
    print("saved logs/cursor/kw_base,LEFT,RIGHT,RIGHT2,DOWN,UP.png")
    e.close()

if __name__ == "__main__":
    main()
