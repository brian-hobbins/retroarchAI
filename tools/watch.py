#!/usr/bin/env python3
"""Live viewer: watch the AI (or a human) play a SNES game in a window.

Renders the emulator in real time with pygame. The AI drives a controller port;
while it "thinks" (~a few seconds per decision via the claude CLI) the emulator
pauses and a THINKING overlay shows. Toggle a port between AI and human live.

Controls:
    TAB         toggle player 1 between AI and HUMAN
    + / =       grant the AI another batch of rounds (size = --max-steps)
    SPACE       pause / resume
    F5 / F9     save / load a savestate (saves/world.state); autosaves too
    ESC / close quit
  (from the shell: `kill -USR1 <pid>` writes a savestate without the keyboard)
  Human mode (player 1):
    Arrows      D-pad        Z=B  X=A  A=Y  S=X  Q=L  W=R
    Enter=START RShift=SELECT

Usage:
    python3 tools/watch.py [rom] [--port 0] [--model sonnet] [--warmup 5400]
                           [--scale 3] [--start-mode ai] [--max-steps 0]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pygame  # noqa: E402

from emulator.cores import find_snes_core  # noqa: E402
from emulator.libretro import LibretroCore, SNES_BUTTONS  # noqa: E402
from emulator.memory_map import get_reader  # noqa: E402
from emulator.manual import find_manual  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402
from agents.knowledge import KnowledgeBase  # noqa: E402
import re  # noqa: E402
from emulator.pngwrite import write_png  # noqa: E402
from providers.base import Action, Observation  # noqa: E402
from providers.claude_cli import ClaudeCLIProvider  # noqa: E402

DEFAULT_GOAL = (
    "You are playing a Super Nintendo (SNES) game. If a GAME MANUAL is provided "
    "above, it is the AUTHORITATIVE source for the controls (which button does what) "
    "and mechanics — follow it, and do not guess a button when the manual states it. "
    "Play to make progress: explore the world, walk up to and interact with people "
    "and objects, and read then respond to any on-screen text or menus using the "
    "manual's controls (for conversations, select the keyword/option the manual "
    "describes). Prefer talking to human characters over attacking harmless "
    "creatures. "
    "Use the RAM ground-truth facts given each turn: if a position value (e.g. "
    "avatar_x/avatar_y) did NOT change after you moved, you hit a wall — go a "
    "different way; a dialogue_cursor_index tells you which menu/keyword is "
    "highlighted. Don't repeat a button that clearly isn't doing anything."
)

KEYMAP = {
    pygame.K_UP: "UP", pygame.K_DOWN: "DOWN",
    pygame.K_LEFT: "LEFT", pygame.K_RIGHT: "RIGHT",
    pygame.K_z: "B", pygame.K_x: "A", pygame.K_a: "Y", pygame.K_s: "X",
    pygame.K_q: "L", pygame.K_w: "R",
    pygame.K_RETURN: "START", pygame.K_RSHIFT: "SELECT",
}

GAMEPAD_CFG = os.path.join(ROOT, "config", "gamepad.json")
AXIS_THRESH = 0.6
AI_OBS_SCALE = 2   # upscale the frame sent to the AI (nearest-neighbor) so the
                   # vision model can read small sprites (door vs window, items)


def frame_sig(rgb: bytes) -> int:
    """Cheap sampled checksum of a frame, to tell if the screen changed between
    two moments (used to detect a static 'waiting' dialogue box vs a moving
    cutscene). Not cryptographic — just sensitive to visible change."""
    return sum(rgb[7::997]) if rgb else 0


AUTOSAVE_EVERY = 25   # auto-snapshot a savestate every N AI decisions

# Set by SIGUSR1 so a savestate can be triggered from the shell with no keyboard
# (e.g. `kill -USR1 <viewer_pid>`); the main loop writes the state and clears it.
_SAVE_REQUEST = {"flag": False}


def _request_save(signum, frame):
    _SAVE_REQUEST["flag"] = True


def load_gamepad_map():
    """Load the SNES->physical mapping saved by tools/map_controller_gui.py."""
    if not os.path.isfile(GAMEPAD_CFG):
        return None
    try:
        return json.load(open(GAMEPAD_CFG)).get("map") or None
    except (OSError, json.JSONDecodeError):
        return None


def poll_gamepad(pad, pad_map):
    """Return the list of SNES buttons currently held on the physical gamepad."""
    if pad is None or not pad_map:
        return []
    out = []
    for name, d in pad_map.items():
        t = d.get("type")
        if t == "button":
            i = d["index"]
            if i < pad.get_numbuttons() and pad.get_button(i):
                out.append(name)
        elif t == "hat":
            h = d.get("hat", 0)
            if h < pad.get_numhats():
                hv, want = pad.get_hat(h), d["value"]
                # match on the non-zero axis so diagonals still register both dirs
                if (want[0] and hv[0] == want[0]) or (want[1] and hv[1] == want[1]):
                    out.append(name)
        elif t == "axis":
            a = d["axis"]
            if a < pad.get_numaxes():
                v = pad.get_axis(a)
                if (d["dir"] > 0 and v > AXIS_THRESH) or (d["dir"] < 0 and v < -AXIS_THRESH):
                    out.append(name)
    return out


def _find_rom_in(d):
    for ext in ("*.smc", "*.sfc"):
        hits = sorted(glob.glob(os.path.join(d, ext)))
        if hits:
            return hits[0]
    return None


def default_rom():
    # Prefer the game named in config/active.json; else the first ROM found.
    active = os.path.join(ROOT, "config", "active.json")
    if os.path.isfile(active):
        try:
            g = json.load(open(active)).get("game")
            if g:
                r = _find_rom_in(os.path.join(ROOT, "roms", g))
                if r:
                    return r
        except (OSError, json.JSONDecodeError):
            pass
    h = sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.smc"), recursive=True))
    h += sorted(glob.glob(os.path.join(ROOT, "roms", "**", "*.sfc"), recursive=True))
    return h[0] if h else None


def load_game_config(game_dir):
    """Per-game config from roms/<game>/game.json (or {} if absent/bad)."""
    path = os.path.join(game_dir, "game.json")
    if os.path.isfile(path):
        try:
            return json.load(open(path))
        except (OSError, json.JSONDecodeError) as e:
            print(f"game config: failed to read {path}: {e}", file=sys.stderr)
    return {}


def warmup(emu: LibretroCore, frames: int) -> None:
    """Boot past the title/attract screen into actual play. Press START to open
    the title menu and A to confirm 'New Game' (and to advance opening dialogue
    boxes). Mashing only START can open AND re-close the menu, leaving the game
    looping in attract mode — so we space START and A apart."""
    for n in range(1, frames + 1):
        if n % 150 < 5:
            emu.set_buttons(0, ["START"])       # open the title menu
        elif n % 50 < 5:
            emu.set_buttons(0, ["A"])           # confirm New Game / advance text
        else:
            emu.clear_input(0)
        emu.run(1)
    emu.clear_input(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", nargs="?", default=None)
    ap.add_argument("--core", default="auto")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--model", default="sonnet")
    # These fall back to the per-game config (roms/<game>/game.json), then a
    # built-in default. Left unset here (None) so an explicit CLI flag wins.
    ap.add_argument("--planner-model", default=None)
    ap.add_argument("--executor-model", default=None)
    ap.add_argument("--scout-model", default=None,
                    help="vision model for the Scout (the team's eyes)")
    ap.add_argument("--warmup", type=int, default=None)
    ap.add_argument("--state", default=None,
                    help="savestate to load into gameplay (skips warmup); relative "
                         "paths resolve inside the game folder. '' to disable")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--start-mode", choices=["ai", "human"], default="ai")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--ram-reader", default=None,
                    help="game RAM reader for ground-truth facts (ultima7|none)")
    ap.add_argument("--cheat-mode", action="store_true",
                    help="expose ALL RAM facts including hidden state a human could "
                         "not perceive (enemy HP, RNG, off-screen entities). Default "
                         "is fair mode: only human-perceivable facts.")
    ap.add_argument("--max-steps", type=int, default=0, help="0 = unlimited AI decisions")
    args = ap.parse_args()

    rom = args.rom or default_rom()
    if not rom or not os.path.isfile(rom):
        print("ERROR: no ROM found.", file=sys.stderr)
        return 2

    # Everything specific to one game lives in its folder (roms/<game>/): its
    # config (game.json), knowledge/lessons, savestates and manual.
    game_dir = os.path.dirname(os.path.abspath(rom))
    gdir = os.path.basename(game_dir)
    game_key = gdir if gdir and gdir != "roms" else re.sub(
        r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(rom))[0].lower()).strip("_")
    cfg = load_game_config(game_dir)

    def pick(cli_val, key, builtin):
        if cli_val is not None:
            return cli_val                       # explicit CLI flag wins
        if cfg.get(key) is not None:
            return cfg[key]                      # then per-game config
        return builtin                           # then built-in default

    planner_model = pick(args.planner_model, "planner_model", "sonnet")
    executor_model = pick(args.executor_model, "executor_model", "haiku")
    scout_model = pick(args.scout_model, "scout_model", "sonnet")
    warmup_frames = pick(args.warmup, "warmup", 5400)
    ram_reader_name = pick(args.ram_reader, "ram_reader", "none")
    goal = pick(args.goal, "goal", DEFAULT_GOAL)

    core = find_snes_core(args.core)
    saves_dir = os.path.join(game_dir, "saves")        # per-game savestates/SRAM
    os.makedirs(saves_dir, exist_ok=True)
    emu = LibretroCore(core, os.path.join(ROOT, "saves", "system"), saves_dir)
    emu.load_rom(rom)
    # Persist the game's own battery saves (in-game "Save" slots) across runs.
    sram_path = os.path.join(saves_dir, "battery.srm")
    state_quick = os.path.join(saves_dir, "world.state")   # F5 save / F9 load slot
    if os.path.isfile(sram_path) and emu.load_sram(sram_path):
        print(f"battery SRAM: loaded {sram_path} (in-game saves available via Continue)")
    try:                               # allow `kill -USR1 <pid>` to snapshot a state
        signal.signal(signal.SIGUSR1, _request_save)
        print(f"savestate: F5=save F9=load; or `kill -USR1 {os.getpid()}` to save; "
              f"autosave every {AUTOSAVE_EVERY} decisions -> {state_quick}")
    except (ValueError, OSError):
        pass
    av = emu.av_info()
    fps = av.timing.fps or 60.0

    # savestate: "" disables; relative paths resolve inside the game folder.
    state_arg = pick(args.state, "state", None)
    state_path = ""
    if state_arg:
        state_path = state_arg if os.path.isabs(state_arg) \
            else os.path.join(game_dir, state_arg)
    if state_path and os.path.isfile(state_path):
        print(f"core: {core}\nrom : {rom}\nloading savestate: {state_path}")
        emu.load_state(state_path)
    else:
        if state_arg and not os.path.isfile(state_path):
            print(f"savestate not found ({state_path}); booting instead")
        print(f"core: {core}\nrom : {rom}\nbooting ({warmup_frames} frames)...")
        warmup(emu, warmup_frames)

    kb = KnowledgeBase(game_dir, game_key=game_key)
    lessons_store = KnowledgeBase(game_dir, filename="lessons.json", game_key=game_key)
    orch = Orchestrator(planner_model=planner_model,
                        executor_model=executor_model,
                        scout_model=scout_model,
                        knowledge=kb, lessons=lessons_store,
                        controls=cfg.get("controls"))
    print(f"game: {cfg.get('name', game_key)}  (folder: roms/{gdir})")
    print(f"agents: scout={scout_model}  planner={planner_model}  "
          f"executor={executor_model} (hybrid code+LLM)")
    print(f"knowledge: {kb.count()} facts, {lessons_store.count()} lessons loaded for '{game_key}'")
    fair = not args.cheat_mode
    ram_reader = get_reader(ram_reader_name, fair=fair)
    print(f"RAM reader: {ram_reader_name}  (mode: {'FAIR (human-perceivable only)' if fair else 'CHEAT (full knowledge)'})")
    manual_text, manual_path = find_manual(rom)
    if manual_text:
        print(f"manual: loaded {manual_path} ({len(manual_text)} chars)")
    else:
        print("manual: none found next to ROM")
    obs_dir = os.path.join(ROOT, "logs", "watch", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(obs_dir, exist_ok=True)

    pygame.init()
    pygame.joystick.init()
    pad_map = load_gamepad_map()
    pad = None
    if pad_map and pygame.joystick.get_count() > 0:
        pad = pygame.joystick.Joystick(0)
        pad.init()
        print(f"gamepad: {pad.get_name()} connected ({len(pad_map)} buttons mapped)")
    elif pad_map:
        print("gamepad: mapping found but no device connected (keyboard only)")
    else:
        print("gamepad: not mapped (keyboard only); run tools/map_controller_gui.py")
    W, H = 256, 224
    scale = args.scale
    GW, GH = W * scale, H * scale           # game area (centered)
    LW, RW = 240, 360                       # left=ACTIONS, right=DIALOGUE/THINKING
    TOPBAR = 26                             # shortcuts strip across the very top
    WIN_W, WIN_H = LW + GW + RW, GH + TOPBAR
    window = pygame.display.set_mode((WIN_W, WIN_H))
    # Everything (game + panels) draws onto this content surface, which is then
    # composited BELOW the top shortcuts bar — so the existing panel code is
    # unchanged and only the final compositing step knows about the bar.
    screen = pygame.Surface((WIN_W, GH))
    pygame.display.set_caption("retroarchAI — watch")
    font = pygame.font.SysFont("monospace", 15)
    font_big = pygame.font.SysFont("monospace", 18, bold=True)
    font_lbl = pygame.font.SysFont("monospace", 13, bold=True)
    font_title = pygame.font.SysFont("monospace", 15, bold=True)
    clock = pygame.time.Clock()

    pool = ThreadPoolExecutor(max_workers=1)
    mode = args.start_mode           # "ai" | "human"
    future = None
    hold_left = 0
    cur_action = None
    act_start_sig = None       # frame checksum captured when the last action began
    prev_wait_static = False   # last WAIT left the screen unchanged (box waiting)
    status = ""
    # live coaching (type to the AI) + learning by watching a human play
    coach_active = False       # typing a message to the AI right now
    coach_text = ""
    overlay_active = False     # F1 settings/info overlay (pauses the game)
    SPEED_PRESETS = [1, 2, 4, 8, 16]   # emulator frames advanced per display tick
    speed_idx = 0
    speed = SPEED_PRESETS[speed_idx]   # fast-forward multiplier (F cycles it)
    human_future = None        # background Scout call noting what the human did
    prev_human_btns = []       # for rising-edge detection of deliberate presses
    last_demo_t = 0.0
    ACTION_BTNS = {"A", "B", "X", "Y", "L", "R", "START"}  # deliberate (non-move)
    HUMAN_DEMO_COOLDOWN = 1.5  # seconds between learning-from-human snapshots
    coach_inbox = os.path.join(ROOT, "logs", "coach_inbox.txt")  # shell can echo here
    coach_inbox_pos = os.path.getsize(coach_inbox) if os.path.isfile(coach_inbox) else 0
    SHORTCUTS = ("F1 settings   F fast-fwd   TAB AI/Human   SPACE pause   / coach   "
                 "F5 save   F9 load   ESC quit")
    steps = 0
    batch = args.max_steps or 30      # size of one "round" batch
    step_budget = args.max_steps      # 0 = unlimited; else stop at this many
    paused = False
    history: list[str] = []

    def blit_frame():
        fr = emu.get_frame_rgb()
        if fr is None:
            return
        w, h, rgb = fr
        surf = pygame.image.frombuffer(rgb, (w, h), "RGB")
        surf = pygame.transform.scale(surf, (GW, GH))
        screen.blit(surf, (LW, 0))

    def draw_topbar():
        # Horizontal strip of APP / emulator shortcuts (NOT in-game controls).
        pygame.draw.rect(window, (24, 26, 32), (0, 0, WIN_W, TOPBAR))
        pygame.draw.line(window, (70, 72, 85), (0, TOPBAR - 1), (WIN_W, TOPBAR - 1))
        if coach_active:
            txt = "coach the AI > " + coach_text + "_"
            window.blit(font_lbl.render(txt, True, (255, 230, 140)), (10, 6))
        else:
            window.blit(font_lbl.render("SHORTCUTS:", True, (150, 200, 255)), (10, 6))
            window.blit(font_lbl.render(SHORTCUTS, True, (190, 195, 210)), (108, 6))
            if speed > 1:                         # fast-forward indicator, right-aligned
                tag = f">> {speed}x"
                window.blit(font_lbl.render(tag, True, (255, 220, 120)),
                            (WIN_W - font_lbl.size(tag)[0] - 10, 6))

    def draw_overlay():
        # F1 "settings" overlay for THIS frontend (there is no RetroArch menu here).
        ctrls = cfg.get("controls") or "default (A=confirm/advance, B=cancel, X=menu)"
        lines = [
            ("SETTINGS / INFO", (150, 220, 255)),
            ("", (0, 0, 0)),
            (f"game      : {cfg.get('name', game_key)}", (220, 220, 230)),
            (f"core      : {os.path.basename(core)}", (200, 200, 210)),
            (f"RAM reader: {ram_reader_name}  ({'FAIR' if fair else 'CHEAT'})", (200, 200, 210)),
            (f"agents    : scout={scout_model}  planner={planner_model}  exec={executor_model}",
             (200, 200, 210)),
            (f"controls  : {ctrls}", (200, 200, 210)),
            (f"obs scale : {AI_OBS_SCALE}x     autosave: every {AUTOSAVE_EVERY} decisions",
             (200, 200, 210)),
            (f"speed     : {speed}x  (press F to cycle 1/2/4/8/16x)", (200, 200, 210)),
            (f"savestate : {os.path.relpath(state_quick, ROOT)}", (200, 200, 210)),
            ("", (0, 0, 0)),
            ("SHORTCUTS (app / emulator — not in-game):", (150, 220, 255)),
            ("  F1 settings     TAB AI<->Human    SPACE pause", (205, 205, 215)),
            ("  /  coach the AI  (type a hint, Enter sends)", (205, 205, 215)),
            ("  F5 save-state   F9 load-state     + more rounds", (205, 205, 215)),
            ("  ESC close / quit", (205, 205, 215)),
            ("", (0, 0, 0)),
            ("game is paused while this is open — F1 or ESC to close", (150, 160, 180)),
        ]
        bw, bh = min(WIN_W - 60, 720), 22 * len(lines) + 30
        box = pygame.Surface((bw, bh), pygame.SRCALPHA)
        box.fill((18, 20, 28, 238))
        pygame.draw.rect(box, (90, 150, 220), box.get_rect(), 2)
        yy = 16
        for txt, col in lines:
            if txt:
                box.blit(font_lbl.render(txt, True, col), (18, yy))
            yy += 22
        window.blit(box, ((WIN_W - bw) // 2, (WIN_H - bh) // 2))

    def wrap(text, fnt, max_w):
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if fnt.size(trial)[0] <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def draw_left():
        # ACTIONS panel (left of the game)
        pygame.draw.rect(screen, (16, 16, 20), (0, 0, LW, GH))
        pygame.draw.line(screen, (60, 60, 70), (LW - 1, 0), (LW - 1, GH))
        pad = 10
        y = 10
        screen.blit(font_title.render("ACTIONS", True, (235, 235, 245)), (pad, y)); y += 24
        mtag = "AI" if mode == "ai" else "HUMAN"
        mcol = (120, 200, 255) if mode == "ai" else (160, 255, 160)
        hdr = f"P{args.port+1}: {mtag}  #{steps}" + ("  PAUSED" if paused else "")
        screen.blit(font_lbl.render(hdr, True, mcol), (pad, y)); y += 20
        if mode == "ai" and think_start is not None:
            el = time.time() - think_start
            dots = "." * (1 + int(el * 2) % 3)
            screen.blit(font_lbl.render(f"thinking{dots} {el:4.1f}s", True, (255, 210, 120)), (pad, y))
        y += 20
        btn_txt = "+".join(last_buttons) if last_buttons else ("…" if think_start else "wait")
        screen.blit(font_big.render(f"▶ {btn_txt}", True, (255, 255, 255)), (pad, y)); y += 26
        if last_agent:
            acol = (150, 200, 150) if last_agent.startswith("code") else (255, 200, 130)
            screen.blit(font.render(f"via {last_agent}", True, acol), (pad, y)); y += 18
        if last_directive:
            for ln in wrap("plan: " + last_directive, font, LW - 2 * pad)[:3]:
                screen.blit(font.render(ln, True, (200, 180, 120)), (pad, y)); y += 16
        y += 4
        # RAM ground-truth state (telemetry)
        ram_vals = {k: v for k, v in last_ram.items() if not k.startswith("_")}
        if ram_vals:
            tag = "fair" if fair else "CHEAT"
            screen.blit(font_lbl.render(f"RAM [{tag}]", True, (235, 180, 120)), (pad, y)); y += 18
            for k, v in ram_vals.items():
                screen.blit(font.render(f"  {k}={v}", True, (210, 170, 120)), (pad, y)); y += 16
        y += 6
        pygame.draw.line(screen, (45, 45, 55), (pad, y), (LW - pad, y)); y += 8
        screen.blit(font_lbl.render("recent:", True, (120, 120, 140)), (pad, y)); y += 18
        # action lines only (reason lives on the right), newest last, fill space
        acts = [h.split(" — ")[0] for h in history]
        maxrows = max(0, (GH - 64 - y) // 16)
        for a in acts[-maxrows:]:
            screen.blit(font.render(a[:26], True, (160, 160, 175)), (pad, y)); y += 16
        if token_summary:
            for i, ln in enumerate(wrap(token_summary, font, LW - 2 * pad)[:2]):
                screen.blit(font.render(ln, True, (120, 140, 120)), (pad, GH - 58 + i * 15))
        screen.blit(font.render("+:more  TAB SPACE ESC", True, (90, 90, 100)), (pad, GH - 20))

    def draw_right():
        # DIALOGUE + THINKING panel (right of the game)
        x0 = LW + GW
        pygame.draw.rect(screen, (16, 16, 20), (x0, 0, RW, GH))
        pygame.draw.line(screen, (60, 60, 70), (x0, 0), (x0, GH))
        pad = 12
        xx = x0 + pad
        maxw = RW - 2 * pad
        y = 10
        screen.blit(font_title.render("DIALOGUE / ON-SCREEN TEXT", True, (130, 220, 140)), (xx, y)); y += 24
        if last_screen_text.strip():
            for ln in wrap('"' + last_screen_text.strip() + '"', font, maxw)[:8]:
                screen.blit(font.render(ln, True, (180, 240, 180)), (xx, y)); y += 18
        else:
            screen.blit(font.render("(none)", True, (90, 110, 90)), (xx, y)); y += 18
        y += 14
        pygame.draw.line(screen, (45, 45, 55), (xx, y), (x0 + RW - pad, y)); y += 10
        # SCENE (what the Scout sees) — ● = interactable, · = scenery
        screen.blit(font_title.render("SCENE (scout)", True, (150, 200, 230)), (xx, y)); y += 22
        if last_scene:
            for sln in last_scene[:7]:
                c = (150, 220, 235) if sln.startswith("●") else (150, 165, 185)
                screen.blit(font.render(sln[:46], True, c), (xx, y)); y += 16
        else:
            screen.blit(font.render("(not looked yet)", True, (90, 110, 120)), (xx, y)); y += 16
        y += 14
        pygame.draw.line(screen, (45, 45, 55), (xx, y), (x0 + RW - pad, y)); y += 10
        screen.blit(font_title.render("THINKING", True, (170, 180, 210)), (xx, y)); y += 24
        thought = last_thought or ("(thinking about the first move)" if think_start else "")
        for ln in wrap(thought, font, maxw)[:7]:
            screen.blit(font.render(ln, True, (210, 210, 215)), (xx, y)); y += 18
        # NOTEBOOK (accumulated knowledge)
        y += 14
        pygame.draw.line(screen, (45, 45, 55), (xx, y), (x0 + RW - pad, y)); y += 10
        screen.blit(font_title.render(f"NOTEBOOK ({kb.count()})", True, (220, 200, 140)), (xx, y)); y += 22
        for f in kb.facts[-4:]:
            isnew = f["text"] in last_new_facts
            col = (255, 235, 150) if isnew else (175, 165, 130)
            for ln in wrap(("* " if isnew else "- ") + f["text"], font, maxw)[:2]:
                if y < GH - 90:
                    screen.blit(font.render(ln, True, col), (xx, y)); y += 16
        # LESSONS (learned strategy)
        if y < GH - 70:
            y += 8
            screen.blit(font_title.render(f"LESSONS ({lessons_store.count()})",
                                          True, (200, 170, 220)), (xx, y)); y += 22
            for f in lessons_store.facts[-3:]:
                for ln in wrap("- " + f["text"], font, maxw)[:2]:
                    if y < GH - 8:
                        screen.blit(font.render(ln, True, (180, 160, 200)), (xx, y)); y += 16

    last_thought = ""
    last_screen_text = ""
    last_scene: list[str] = []
    last_buttons: list[str] = []
    last_ram: dict = {}
    last_directive = ""
    last_agent = ""
    token_summary = ""
    last_new_facts: list = []
    think_start = None
    running = True
    while running:
        if _SAVE_REQUEST["flag"]:          # shell-triggered save (kill -USR1 <pid>)
            _SAVE_REQUEST["flag"] = False
            try:
                emu.save_state(state_quick)
                emu.save_sram(sram_path)
                last_thought = f"[USR1] saved state -> {state_quick}"
                print(f"[USR1] savestate written: {state_quick}")
            except Exception as ex:
                print(f"[USR1] save failed: {ex}")
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if coach_active:                  # typing a message to the AI
                    if e.key == pygame.K_RETURN:
                        msg = coach_text.strip()
                        if msg:
                            orch.add_coach(msg)
                            last_thought = f"[coach] {msg}"
                            print(f"[coach] {msg}")
                        coach_text, coach_active = "", False
                    elif e.key == pygame.K_ESCAPE:
                        coach_text, coach_active = "", False
                    elif e.key == pygame.K_BACKSPACE:
                        coach_text = coach_text[:-1]
                    elif e.unicode and e.unicode.isprintable():
                        coach_text += e.unicode
                    continue                      # swallow the key; don't reach game
                if e.key == pygame.K_F1:          # settings/info overlay (pauses game)
                    overlay_active = not overlay_active
                elif e.key == pygame.K_f:         # fast-forward: cycle 1x/2x/4x/8x/16x
                    speed_idx = (speed_idx + 1) % len(SPEED_PRESETS)
                    speed = SPEED_PRESETS[speed_idx]
                    last_thought = f"[speed] {speed}x"
                    print(f"[speed] emulator {speed}x")
                elif e.key == pygame.K_SLASH:     # open the coach prompt (type to AI)
                    coach_active, coach_text = True, ""
                elif e.key == pygame.K_ESCAPE:
                    if overlay_active:
                        overlay_active = False    # ESC closes the overlay first
                    else:
                        running = False
                elif e.key == pygame.K_SPACE:
                    paused = not paused
                elif e.key == pygame.K_F5:        # snapshot a savestate (skip-intro)
                    try:
                        emu.save_state(state_quick)
                        emu.save_sram(sram_path)
                        last_thought = f"[F5] saved state -> {state_quick}"
                        print(f"[F5] savestate written: {state_quick}")
                    except Exception as ex:
                        print(f"[F5] save failed: {ex}")
                elif e.key == pygame.K_F9:        # load the savestate back
                    if os.path.isfile(state_quick):
                        emu.load_state(state_quick)
                        hold_left = 0
                        cur_action = None
                        future = None
                        last_thought = f"[F9] loaded state <- {state_quick}"
                        print(f"[F9] savestate loaded: {state_quick}")
                elif e.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                    if step_budget:               # grant another batch of rounds
                        step_budget += batch
                        print(f"[+{batch} rounds -> budget {step_budget}]")
                elif e.key == pygame.K_TAB:
                    new_mode = "human" if mode == "ai" else "ai"
                    mode = new_mode
                    emu.clear_input(args.port)
                    hold_left = 0
                    cur_action = None
                    future = None             # drop any in-flight AI decision
                    think_start = None

        # pick up coached hints echoed to the inbox file from a shell
        if os.path.isfile(coach_inbox):
            try:
                sz = os.path.getsize(coach_inbox)
                if sz > coach_inbox_pos:
                    with open(coach_inbox) as _cf:
                        _cf.seek(coach_inbox_pos)
                        for ln in _cf.read().splitlines():
                            if ln.strip():
                                orch.add_coach(ln.strip())
                                print(f"[coach:inbox] {ln.strip()}")
                    coach_inbox_pos = sz
                elif sz < coach_inbox_pos:
                    coach_inbox_pos = sz   # file was truncated/rotated
            except OSError:
                pass

        if not paused and not coach_active and not overlay_active:
            if mode == "human":
                pressed = pygame.key.get_pressed()
                btns = [name for key, name in KEYMAP.items() if pressed[key]]
                for b in poll_gamepad(pad, pad_map):
                    if b not in btns:
                        btns.append(b)
                emu.set_buttons(args.port, btns)
                emu.run(speed)                    # fast-forward runs N frames/tick
                last_buttons = btns
                src = "gamepad+keyboard" if pad else "keyboard"
                last_thought = f"(human control [{src}] — press TAB to hand back to the AI; the AI is watching & learning)"
                think_start = None
                # Learn by watching: when the human makes a DELIBERATE move (an
                # action button newly pressed), have the Scout note the situation
                # and save it as a lesson. Runs off-thread, rate-limited.
                new_press = [b for b in btns if b in ACTION_BTNS and b not in prev_human_btns]
                prev_human_btns = btns
                now = time.time()
                if (new_press and (now - last_demo_t) >= HUMAN_DEMO_COOLDOWN
                        and (human_future is None or human_future.done())):
                    fr = emu.get_frame_rgb()
                    if fr is not None:
                        w, h, rgb = fr
                        p = os.path.join(obs_dir, f"human_{int(now)}.png")
                        if AI_OBS_SCALE > 1:
                            _s = pygame.image.frombuffer(rgb, (w, h), "RGB")
                            ow, oh = w * AI_OBS_SCALE, h * AI_OBS_SCALE
                            _s = pygame.transform.scale(_s, (ow, oh))
                            write_png(p, ow, oh, pygame.image.tobytes(_s, "RGB"))
                        else:
                            ow, oh = w, h
                            write_png(p, w, h, rgb)
                        try:
                            _facts = ram_reader(emu)
                        except Exception:
                            _facts = {}
                        _obs = Observation(image_path=p, width=ow, height=oh,
                                           goal=goal, step=steps + 1,
                                           ram_facts=_facts, manual=manual_text)
                        human_future = pool.submit(orch.note_human, _obs, list(btns))
                        last_demo_t = now
                        last_thought = f"(human: {'+'.join(btns)} — AI noting this)"
            else:
                # AI state machine — the screen ALWAYS advances (never frozen); the
                # NEXT decision is computed in the background WHILE the current action
                # plays out (pipelined), so a slow think hides under the current move.
                budget_done = bool(step_budget and steps >= step_budget)

                # (a) advance one frame. Hold the action's buttons until its window
                #     ends; then run neutral frames so the world keeps moving (cut-
                #     scenes play, NPCs animate) while the next decision is computed.
                if hold_left > 0:
                    emu.run(speed)                            # fast-forward: N frames/tick
                    hold_left -= speed
                    if hold_left <= 0:
                        hold_left = 0
                        emu.clear_input(args.port)            # release at action end
                        _fe = emu.get_frame_rgb()
                        if cur_action is not None and act_start_sig is not None:
                            end_sig = frame_sig(_fe[2]) if _fe else act_start_sig
                            # a WAIT (no buttons) that changed nothing == a dialogue/
                            # message box sitting there waiting for input
                            if not cur_action.buttons and end_sig == act_start_sig:
                                prev_wait_static = True
                    status = f"AI acting: {cur_action.buttons} ({hold_left} left)"
                else:
                    emu.run(speed)                            # neutral: world plays on

                # (b) pipeline: start the next decision as soon as none is pending
                #     (this runs DURING the current action's hold -> full overlap).
                if future is None and not budget_done:
                    fr = emu.get_frame_rgb()
                    if fr is not None:
                        w, h, rgb = fr
                        p = os.path.join(obs_dir, f"obs_{steps+1:05d}.png")
                        if AI_OBS_SCALE > 1:
                            _s = pygame.image.frombuffer(rgb, (w, h), "RGB")
                            ow, oh = w * AI_OBS_SCALE, h * AI_OBS_SCALE
                            _s = pygame.transform.scale(_s, (ow, oh))
                            write_png(p, ow, oh, pygame.image.tobytes(_s, "RGB"))
                        else:
                            ow, oh = w, h
                            write_png(p, w, h, rgb)
                        try:
                            facts = ram_reader(emu)
                        except Exception:
                            facts = {}
                        # A WAIT that changed nothing -> a dialogue box is waiting.
                        if prev_wait_static:
                            facts["screen"] = "unchanged while waiting"
                            facts["_hint"] = (
                                facts.get("_hint", "") +
                                " The picture did NOT change while you waited — this is "
                                "not an auto-playing cutscene. If a dialogue/message box "
                                "is on screen it is WAITING for you: press A to advance it."
                            ).strip()
                            prev_wait_static = False          # consumed
                        last_ram = facts
                        obs = Observation(image_path=p, width=ow, height=oh,
                                          goal=goal, step=steps + 1,
                                          history=history[-6:], ram_facts=facts,
                                          manual=manual_text)
                        future = pool.submit(orch.decide, obs)
                        think_start = time.time()

                # (c) apply a finished decision, but only once the current action's
                #     hold window is done, so every action plays its full duration.
                if future is not None and future.done() and hold_left <= 0:
                    action = future.result()
                    future = None
                    cur_action = action
                    steps += 1
                    if AUTOSAVE_EVERY and steps % AUTOSAVE_EVERY == 0:
                        try:
                            emu.save_state(state_quick)
                            emu.save_sram(sram_path)
                        except Exception:
                            pass
                    _fr0 = emu.get_frame_rgb()   # frame as this action begins
                    act_start_sig = frame_sig(_fr0[2]) if _fr0 else None
                    emu.set_buttons(args.port, action.buttons)
                    hold_left = action.hold_frames
                    last_thought = action.reason
                    last_screen_text = orch.last_screen_text
                    last_buttons = action.buttons
                    last_directive = orch.directive.short() if orch.directive else ""
                    last_agent = orch.last_agent
                    last_scene = orch.scene.display() if orch.scene else last_scene
                    token_summary = orch.summary()
                    last_new_facts = orch.last_new_facts
                    think_start = None
                    history.append(f"#{steps}: {action.buttons or 'wait'} x{action.hold_frames} — {action.reason}")
                    print(f"#{steps:03d} [{orch.last_agent:13s}] {action.buttons or '[]'} x{action.hold_frames} | {action.reason}")
                    try:
                        import json as _json
                        with open(os.path.join(obs_dir, "trace.jsonl"), "a") as _tf:
                            _tf.write(_json.dumps({
                                "step": steps, "buttons": action.buttons,
                                "hold_frames": action.hold_frames,
                                "reason": action.reason,
                                "screen_text": action.screen_text,
                                "ram": {k: v for k, v in last_ram.items()
                                        if not k.startswith("_")},
                            }) + "\n")
                    except Exception:
                        pass

                if budget_done and future is None:
                    last_thought = (f"Done — {steps} decisions. Press + for {batch} "
                                    "more, TAB to play yourself, ESC to quit.")
                    think_start = None

        screen.fill((10, 10, 12))
        blit_frame()
        draw_left()
        draw_right()
        window.blit(screen, (0, TOPBAR))   # content below the shortcuts bar
        draw_topbar()
        if overlay_active:
            draw_overlay()
        pygame.display.flip()
        clock.tick(fps)

    pool.shutdown(wait=False, cancel_futures=True)
    try:                               # persist the game's battery saves on exit
        if emu.save_sram(sram_path):
            print(f"battery SRAM: wrote {sram_path}")
    except Exception as ex:
        print(f"battery SRAM: save on exit failed: {ex}")
    pygame.quit()
    emu.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
