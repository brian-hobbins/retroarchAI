#!/usr/bin/env python3
"""M3 demo: let the LLM (via the claude CLI / subscription) play a SNES game.

Boots the ROM for a warmup, then hands control of a port to the AI for N
decisions. Writes a JSONL trace and the observed frames to logs/m3/<timestamp>/.

Usage:
    python3 tools/m3_play.py [rom] [--port 0] [--steps 8] [--model sonnet]
                             [--warmup 600] [--goal "..."]
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agents.controller import AIController  # noqa: E402
from emulator.cores import find_snes_core  # noqa: E402
from emulator.libretro import LibretroCore  # noqa: E402
from providers.claude_cli import ClaudeCLIProvider  # noqa: E402

DEFAULT_GOAL = (
    "You are playing Ultima VII: The Black Gate on SNES, a top-down action-RPG. "
    "You control the Avatar (the character near the center of the screen). Use the "
    "D-pad (UP/DOWN/LEFT/RIGHT) to walk around and explore the world. Use A to "
    "interact/confirm and B to cancel/back out of menus. If a text box or menu is "
    "open, read it and respond. Your objective right now: explore your surroundings "
    "by walking in open directions (grass/paths, not into walls), and interact with "
    "anything interesting (people, doors, signs). Avoid walking into walls repeatedly."
)


def default_rom():
    hits = sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))
    hits += sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.sfc"), recursive=True))
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", nargs="?", default=None)
    ap.add_argument("--core", default="auto")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--warmup", type=int, default=5400)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--goal", default=DEFAULT_GOAL)
    args = ap.parse_args()

    rom = args.rom or default_rom()
    if not rom or not os.path.isfile(rom):
        print("ERROR: no ROM found.", file=sys.stderr)
        return 2

    trace_dir = os.path.join(ROOT, "logs", "m3", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(trace_dir, exist_ok=True)

    core = find_snes_core(args.core)
    print(f"core : {core}")
    print(f"rom  : {rom}")
    print(f"trace: {trace_dir}")

    emu = LibretroCore(
        core_path=core,
        system_dir=os.path.join(ROOT, "saves", "system"),
        save_dir=os.path.join(ROOT, "saves"),
    )
    try:
        emu.load_rom(rom)
        print(f"warmup: running {args.warmup} frames to boot (tapping START)...")
        # Tap START periodically during warmup to advance title/intro screens.
        for n in range(1, args.warmup + 1):
            if n % 90 < 6:
                emu.set_buttons(0, ["START"])
            else:
                emu.clear_input(0)
            emu.run(1)
        emu.clear_input(0)

        provider = ClaudeCLIProvider(model=args.model)
        ctrl = AIController(
            emu=emu, provider=provider, port=args.port,
            goal=args.goal, trace_dir=trace_dir, save_frames=True,
        )

        print(f"Handing port {args.port} to the AI for {args.steps} decisions "
              f"(model={args.model})...\n")
        for _ in range(args.steps):
            action = ctrl.tick()
            print(f"  #{ctrl.step:02d} {str(action.buttons) or '[]':<22} "
                  f"x{action.hold_frames:<3} | {action.reason}")

        print(f"\ndone. trace: {os.path.join(trace_dir, 'trace.jsonl')}")
        print(f"frames: {os.path.join(trace_dir, 'frames')}")
        return 0
    finally:
        emu.close()


if __name__ == "__main__":
    raise SystemExit(main())
