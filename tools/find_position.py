#!/usr/bin/env python3
"""Find Avatar X/Y position addresses in WRAM: hold a direction for increasing
durations (with equal TOTAL frames so counters cancel) and report bytes whose
value changes monotonically with how long we moved. No LLM."""
from __future__ import annotations
import glob, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from emulator.cores import find_snes_core
from emulator.libretro import LibretroCore, RETRO_MEMORY_SYSTEM_RAM as SR

def rom():
    return sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))[0]

def main():
    e = LibretroCore(find_snes_core("auto"), os.path.join(ROOT, "saves", "system"),
                     os.path.join(ROOT, "saves"))
    e.load_rom(rom())
    st = open(os.path.join(ROOT, "saves", "ultima7_world.state"), "rb").read()
    TOTAL = 90

    def hold(btn, dur):
        # move `dur` frames, pad the rest with idle so every run is TOTAL frames
        e.unserialize(st)
        e.run(TOTAL - dur)
        if dur:
            e.set_buttons(0, [btn]); e.run(dur); e.clear_input(0)
        e.run(2)
        return e.ram_snapshot(SR)

    for btn in ["RIGHT", "LEFT", "UP", "DOWN"]:
        snaps = [hold(btn, d) for d in (0, 30, 60, 90)]
        mono = []
        for a in range(len(snaps[0])):
            vals = [s[a] for s in snaps]
            diffs = [vals[i+1]-vals[i] for i in range(3)]
            # strictly monotonic, each step changes, bounded (a coordinate, not RNG)
            if all(d > 0 for d in diffs) or all(d < 0 for d in diffs):
                if 1 <= max(abs(d) for d in diffs) <= 20:
                    mono.append((a, vals))
        print(f"{btn:5s}: {len(mono)} monotonic byte(s)")
        for a, vals in mono[:6]:
            print(f"    {hex(a)}: {vals}")
    e.close()

if __name__ == "__main__":
    main()
