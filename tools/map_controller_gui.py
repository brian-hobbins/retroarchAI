#!/usr/bin/env python3
"""Self-paced gamepad mapping wizard (you drive it in the window).

Listens continuously for gamepad input and shows a live "input detected"
indicator so you can SEE the controller working. Prompts one SNES button at a
time; press the physical control you want for it, whenever you're ready. Saves
config/gamepad.json. No timing coordination needed.

Keys:  BACKSPACE = redo previous   TAB = skip   ESC = finish & save
"""
from __future__ import annotations
import json, os, time
import pygame

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "gamepad.json")
TARGETS = ["B", "A", "Y", "X", "L", "R", "SELECT", "START",
           "UP", "DOWN", "LEFT", "RIGHT"]
AXIS = 0.6


def descr(d):
    if d["type"] == "button": return f"button #{d['index']}"
    if d["type"] == "hat":     return f"D-pad hat {tuple(d['value'])}"
    if d["type"] == "axis":    return f"axis {d['axis']} {'+' if d['dir']>0 else '-'}"
    return "?"


def main():
    pygame.init(); pygame.joystick.init()
    W, H = 560, 560
    scr = pygame.display.set_mode((W, H))
    pygame.display.set_caption("retroarchAI — map controller")
    big = pygame.font.SysFont("monospace", 34, bold=True)
    mid = pygame.font.SysFont("monospace", 20, bold=True)
    sm = pygame.font.SysFont("monospace", 16)
    clock = pygame.time.Clock()

    if pygame.joystick.get_count() == 0:
        j = None
    else:
        j = pygame.joystick.Joystick(0); j.init()
        rest = [j.get_axis(a) for a in range(j.get_numaxes())]

    mapping = {}
    idx = 0
    last_input = "(none yet)"
    last_input_t = 0.0
    done = False

    def save():
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        json.dump({"device": j.get_name() if j else None, "map": mapping},
                  open(CFG, "w"), indent=1)

    running = True
    while running:
        captured = None
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key == pygame.K_BACKSPACE and idx > 0:
                    idx -= 1; mapping.pop(TARGETS[idx], None); done = False
                elif e.key == pygame.K_TAB and not done:
                    idx += 1
                    if idx >= len(TARGETS): done = True
            elif j is not None and not done:
                if e.type == pygame.JOYBUTTONDOWN:
                    captured = {"type": "button", "index": e.button}
                elif e.type == pygame.JOYHATMOTION and e.value != (0, 0):
                    captured = {"type": "hat", "hat": e.hat, "value": list(e.value)}
                elif e.type == pygame.JOYAXISMOTION and abs(e.value - rest[e.axis]) > AXIS:
                    captured = {"type": "axis", "axis": e.axis,
                                "dir": 1 if e.value > 0 else -1}
            if j is not None and e.type in (pygame.JOYBUTTONDOWN, pygame.JOYHATMOTION):
                last_input = pygame.event.event_name(e.type) + " " + (
                    str(e.button) if e.type == pygame.JOYBUTTONDOWN else str(e.value))
                last_input_t = time.time()

        if captured and not done:
            mapping[TARGETS[idx]] = captured
            save()
            idx += 1
            if idx >= len(TARGETS):
                done = True
            pygame.time.wait(180)  # debounce so one press doesn't skip two

        # --- draw ---
        scr.fill((18, 18, 24))
        scr.blit(big.render("Map your controller", True, (235, 235, 245)), (20, 16))
        if j is None:
            scr.blit(mid.render("NO GAMEPAD DETECTED", True, (255, 120, 120)), (20, 70))
        else:
            scr.blit(sm.render(j.get_name(), True, (150, 160, 180)), (20, 62))
            # live input indicator
            hot = time.time() - last_input_t < 0.4
            col = (120, 255, 140) if hot else (90, 90, 110)
            scr.blit(sm.render(f"input: {last_input}", True, col), (20, 86))

        if not done:
            scr.blit(mid.render(f"Press the control for:", True, (180, 190, 210)), (20, 128))
            scr.blit(big.render(TARGETS[idx], True, (255, 215, 120)), (20, 158))
            scr.blit(sm.render("(TAB=skip  BACKSPACE=redo last  ESC=finish)", True,
                               (120, 120, 140)), (20, 208))
        else:
            scr.blit(mid.render("DONE — mapping saved. ESC to close.", True,
                                (150, 230, 160)), (20, 150))

        y = 250
        scr.blit(sm.render("mapped:", True, (140, 140, 160)), (20, y)); y += 22
        for t in TARGETS:
            c = (200, 230, 200) if t in mapping else (110, 110, 120)
            txt = f"  {t:7s} = {descr(mapping[t]) if t in mapping else '—'}"
            scr.blit(sm.render(txt, True, c), (20, y)); y += 20

        pygame.display.flip()
        clock.tick(60)

    save()
    pygame.quit()
    print("saved", CFG, "with", len(mapping), "mappings")


if __name__ == "__main__":
    main()
