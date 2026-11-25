#!/usr/bin/env python3
"""Capture ONE physical gamepad input and record it as the mapping for a named
SNES button. Writes/updates config/gamepad.json.

Usage: python3 tools/map_controller.py <SNES_BUTTON> [timeout_s]
       python3 tools/map_controller.py --show      # print current mapping
"""
from __future__ import annotations
import json, os, sys, time
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "gamepad.json")
AXIS_THRESH = 0.6


def load_cfg():
    if os.path.isfile(CFG):
        try:
            return json.load(open(CFG))
        except (OSError, json.JSONDecodeError):
            pass
    return {"device": None, "map": {}}


def save_cfg(c):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    json.dump(c, open(CFG, "w"), indent=1)


def descr_str(d):
    if d["type"] == "button":
        return f"button #{d['index']}"
    if d["type"] == "hat":
        return f"D-pad hat {tuple(d['value'])}"
    if d["type"] == "axis":
        return f"axis {d['axis']} {'+' if d['dir'] > 0 else '-'}"
    return str(d)


def main():
    if sys.argv[1] == "--show":
        c = load_cfg()
        print("device:", c.get("device"))
        for k, v in c.get("map", {}).items():
            print(f"  {k:7s} = {descr_str(v)}")
        return

    name = sys.argv[1].upper()
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0

    pygame.init(); pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print(json.dumps({"error": "no gamepad detected"})); return
    j = pygame.joystick.Joystick(0); j.init()
    cfg = load_cfg(); cfg["device"] = j.get_name()

    # settle / drain any pending events and record axis rest positions
    for _ in range(20):
        pygame.event.pump(); time.sleep(0.01)
    pygame.event.clear()
    rest = [j.get_axis(a) for a in range(j.get_numaxes())]

    captured = None
    t0 = time.time()
    while time.time() - t0 < timeout and captured is None:
        for e in pygame.event.get():
            if e.type == pygame.JOYBUTTONDOWN:
                captured = {"type": "button", "index": e.button}
            elif e.type == pygame.JOYHATMOTION and e.value != (0, 0):
                captured = {"type": "hat", "hat": e.hat, "value": list(e.value)}
            elif e.type == pygame.JOYAXISMOTION:
                if abs(e.value - rest[e.axis]) > AXIS_THRESH:
                    captured = {"type": "axis", "axis": e.axis,
                                "dir": 1 if e.value > 0 else -1}
            if captured:
                break
        time.sleep(0.01)

    if captured is None:
        print(json.dumps({"button": name, "error": "timeout — no press detected"}))
        return
    cfg["map"][name] = captured
    save_cfg(cfg)
    print(json.dumps({"button": name, "captured": captured,
                      "readable": descr_str(captured)}))


if __name__ == "__main__":
    main()
