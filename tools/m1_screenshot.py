#!/usr/bin/env python3
"""M1 smoke test: boot a SNES core, load a ROM, step frames, dump screenshots.

Usage:
    python3 tools/m1_screenshot.py [rom_path] [--core PATH] [--frames N]

With no rom_path it uses roms/ultima7/*.smc if present.
Screenshots are written to logs/m1/.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from emulator.cores import find_snes_core  # noqa: E402
from emulator.libretro import LibretroCore, PIXEL_RGB565, PIXEL_XRGB8888  # noqa: E402
from emulator.pngwrite import write_png  # noqa: E402

PIX_NAMES = {0: "0RGB1555", PIXEL_XRGB8888: "XRGB8888", PIXEL_RGB565: "RGB565"}


def default_rom() -> str | None:
    hits = sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))
    hits += sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.sfc"), recursive=True))
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", nargs="?", default=None)
    ap.add_argument("--core", default="auto")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--shots-at", default="60,180,360,600",
                    help="comma-separated frame numbers to screenshot")
    args = ap.parse_args()

    rom = args.rom or default_rom()
    if not rom or not os.path.isfile(rom):
        print("ERROR: no ROM found. Pass a path or drop a .smc/.sfc in roms/.",
              file=sys.stderr)
        return 2

    core = find_snes_core(args.core)
    shot_frames = {int(x) for x in args.shots_at.split(",") if x.strip()}
    out_dir = os.path.join(ROOT, "logs", "m1")
    os.makedirs(out_dir, exist_ok=True)

    print(f"core : {core}")
    print(f"rom  : {rom}")

    emu = LibretroCore(
        core_path=core,
        system_dir=os.path.join(ROOT, "saves", "system"),
        save_dir=os.path.join(ROOT, "saves"),
    )
    try:
        si = emu.system_info()
        print(f"library: {si.library_name.decode()} {si.library_version.decode()} "
              f"(need_fullpath={si.need_fullpath})")
        emu.load_rom(rom)
        av = emu.av_info()
        print(f"geometry: {av.geometry.base_width}x{av.geometry.base_height} "
              f"@ {av.timing.fps:.2f}fps  pixel_format={PIX_NAMES.get(emu.pixel_format, emu.pixel_format)}")

        shots = 0
        for n in range(1, args.frames + 1):
            emu.run(1)
            if n in shot_frames:
                frame = emu.get_frame_rgb()
                if frame is None:
                    print(f"frame {n}: no video yet")
                    continue
                w, h, rgb = frame
                path = os.path.join(out_dir, f"frame_{n:05d}.png")
                write_png(path, w, h, rgb)
                nonblack = any(rgb[i] for i in range(0, len(rgb), 97))
                print(f"frame {n}: {w}x{h} -> {path}  (non-black={nonblack})")
                shots += 1

        # Smoke check on savestate round-trip.
        try:
            st = emu.serialize()
            emu.unserialize(st)
            print(f"savestate: {len(st)} bytes, round-trip OK")
        except Exception as e:  # noqa: BLE001
            print(f"savestate: FAILED ({e})")

        print(f"done: {shots} screenshot(s) in {out_dir}")
        return 0
    finally:
        emu.close()


if __name__ == "__main__":
    raise SystemExit(main())
