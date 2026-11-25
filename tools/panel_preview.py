#!/usr/bin/env python3
"""Render the viewer's game+reasoning-panel composite to a PNG (headless), using
the latest observation frame and latest thought from a watch session. For
verification / sharing a still of what the live window shows."""
from __future__ import annotations
import glob, json, os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import pygame

def latest_dir():
    return sorted(glob.glob(os.path.join(ROOT, "logs", "watch", "*", "")), key=os.path.getmtime)[-1]

def latest_obs(d):
    fs = sorted(glob.glob(os.path.join(d, "obs_*.png")))
    return fs[-1] if fs else None

def latest_records(d, n=4):
    tf = os.path.join(d, "trace.jsonl")
    if not os.path.isfile(tf):
        return []
    recs = [json.loads(l) for l in open(tf) if l.strip()]
    return recs[-n:]

def wrap(text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if fnt.size(t)[0] <= max_w: cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def main():
    scale = 3
    W, H = 256, 224
    GW, GH = W*scale, H*scale
    PANEL_H = 262
    pygame.init()
    screen = pygame.Surface((GW, GH + PANEL_H))
    font = pygame.font.SysFont("monospace", 15)
    font_big = pygame.font.SysFont("monospace", 18, bold=True)
    font_lbl = pygame.font.SysFont("monospace", 13, bold=True)

    d = latest_dir()
    obs = latest_obs(d)
    img = pygame.image.load(obs)
    screen.blit(pygame.transform.scale(img, (GW, GH)), (0, 0))

    recs = latest_records(d, 4)
    cur = recs[-1] if recs else {"step": "?", "buttons": [], "reason": "", "screen_text": ""}
    step = cur.get("step", "?")
    btns = "+".join(cur.get("buttons") or []) or "wait"
    thought = cur.get("reason", "")
    screen_text = cur.get("screen_text", "")

    ram = cur.get("ram") or {}
    pygame.draw.rect(screen, (18,18,22), (0, GH, GW, PANEL_H))
    pygame.draw.line(screen, (60,60,70), (0,GH), (GW,GH))
    pad = 10; y = GH + 8
    screen.blit(font_lbl.render(f"Player 1: AI   decision #{step}", True, (120,200,255)), (pad, y)); y += 22
    screen.blit(font_big.render("▶ " + btns, True, (255,255,255)), (pad, y)); y += 26
    if ram:
        screen.blit(font_lbl.render("RAM (ground truth): " + str(ram), True, (235,180,120)), (pad, y)); y += 20
    screen.blit(font_lbl.render("On-screen text:", True, (130,220,140)), (pad, y)); y += 18
    if screen_text.strip():
        for ln in wrap('"' + screen_text.strip() + '"', font, GW-2*pad)[:3]:
            screen.blit(font.render(ln, True, (180,240,180)), (pad+6, y)); y += 18
    else:
        screen.blit(font.render("(none)", True, (90,110,90)), (pad+6, y)); y += 18
    y += 4
    screen.blit(font_lbl.render("Thinking:", True, (170,180,210)), (pad, y)); y += 18
    for ln in wrap(thought, font, GW-2*pad)[:3]:
        screen.blit(font.render(ln, True, (210,210,215)), (pad+6, y)); y += 18

    out = os.path.join(ROOT, "logs", "panel_preview.png")
    pygame.image.save(screen, out)
    print("saved", out, "| obs:", os.path.basename(obs), "| screen_text:", repr(screen_text[:60]))

if __name__ == "__main__":
    main()
