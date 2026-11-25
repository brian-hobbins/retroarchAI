#!/usr/bin/env python3
"""Headless test of the Phase-A orchestrator: measures planner/executor/code
split and token usage over N ticks."""
from __future__ import annotations
import glob, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from emulator.cores import find_snes_core
from emulator.libretro import LibretroCore
from emulator.memory_map import get_reader
from emulator.manual import find_manual
from emulator.pngwrite import write_png
from providers.base import Observation
from agents.orchestrator import Orchestrator
from agents.knowledge import KnowledgeBase

def rom():
    return sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))[0]

def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "saves/ultima7_world.state"
    nticks = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    goal = ("Explore Britannia, talk to people (press Y next to them), and advance "
            "the story about the kidnapping.")
    e = LibretroCore(find_snes_core("auto"), os.path.join(ROOT, "saves", "system"),
                     os.path.join(ROOT, "saves"))
    e.load_rom(rom())
    e.unserialize(open(os.path.join(ROOT, state), "rb").read()); e.run(2)
    man, _ = find_manual(rom())
    reader = get_reader("ultima7", fair=True)
    kb = KnowledgeBase("ultima7_test", ROOT)
    lessons = KnowledgeBase("ultima7_test", ROOT, filename="lessons.json")
    orch = Orchestrator(planner_model="sonnet", executor_model="haiku",
                        knowledge=kb, lessons=lessons)
    out = os.path.join(ROOT, "logs", "orch"); os.makedirs(out, exist_ok=True)

    for i in range(1, nticks + 1):
        w, h, rgb = e.get_frame_rgb()
        p = os.path.join(out, f"f_{i:03d}.png"); write_png(p, w, h, rgb)
        obs = Observation(image_path=p, width=w, height=h, goal=goal, step=i,
                          ram_facts=reader(e), manual=man)
        t0 = time.time()
        a = orch.decide(obs)
        dt = time.time() - t0
        print(f"#{i:02d} [{orch.last_agent:13s}] {str(a.buttons):<16} x{a.hold_frames:<3} "
              f"{dt:4.1f}s | dir={orch.directive.short()[:50]}")
        # apply
        e.set_buttons(0, a.buttons); e.run(a.hold_frames); e.clear_input(0); e.run(6)

    print("\nSUMMARY:", orch.summary())
    print(f"\nREWARD: {orch.reward.summary()}")
    print(f"KNOWLEDGE BASE ({kb.count()} facts):")
    for f in kb.facts:
        print("  -", f["text"])
    print(f"LESSONS LEARNED ({lessons.count()}):")
    for f in lessons.facts:
        print("  *", f["text"])
    s = orch.stats
    tot = s["planner_tokens"] + s["executor_tokens"]
    print(f"total tokens: {tot}  (planner {s['planner_tokens']}, executor {s['executor_tokens']})")
    old = nticks * 44000
    print(f"single-agent Sonnet-every-tick estimate: {old} tokens  ->  orchestrator used {tot} ({100*tot//max(old,1)}%)")
    e.close()

if __name__ == "__main__":
    main()
