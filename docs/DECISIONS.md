# Decisions

Running record of the choices that shape the project, with the reasoning. Newest first.

## 2026-11-24 — Foundational decisions

### D1. AI brain = LLM-driven (not trained RL models)
The AI player is driven by an LLM (Claude or any API) in an observe→decide→act loop, **not** by the per-game trained PyTorch models that `MatPoliquin/RetroArchAI` uses.
- **Why:** goal is "any ROM, any player switchable to AI." RL models need per-game RAM maps + training and don't generalize to arbitrary ROMs. An LLM looking at the screen generalizes to any game.
- **Cost:** latency. LLM decisions take ~0.5–5 s, so no frame-perfect play. Mitigated by holding actions over a frame window and optionally pausing while thinking. Best for turn-based/slower games.

### D2. Stack = thin Python libretro frontend via `ctypes` (not stable-retro, not the fork)
We drive a libretro SNES core directly from Python using the stdlib `ctypes` module.
- **Why not `stable-retro`/Gym-Retro:** it's a compiled package not available for the installed **Python 3.14.4**; also tied to per-game integrations.
- **Why not compiling the MatPoliquin fork:** heavy (full RetroArch + libtorch + OpenCV), and it's the RL-model architecture we're not using.
- **Why `ctypes`:** stdlib (immune to Python-version wheel problems), loads the snes9x `.so` already on disk, and exposes exactly what we need — framebuffer (video_refresh callback), per-port input (input_state callback), savestates (retro_serialize), and pause (stop calling retro_run).

### D3. SNES core = snes9x (fallback snes9x2010)
- **Why:** best accuracy/speed/compatibility balance. `bsnes` (also on disk) is more accurate but heavier and unnecessary. The year-numbered cores (2002/2005/2010) target weak hardware; `snes9x2010` is a fine fast fallback and its `.so` is already present, while current `snes9x` may need a one-time core download.

### D4. Agents = one LLM agent per AI player (not a team)
Each AI-controlled port is a single agent with its own prompt/goal/context. A multi-agent (planner + executor) split is a later optional enhancement, not needed for the core environment.
- **Why:** playing one character is a single coherent control problem; a team adds latency and complexity with no v1 benefit.

### D5. Build vs runtime credentials
Claude Code subscription = building (the assistant). Runtime gameplay = a separate LLM **API key** (Anthropic or OpenAI-compatible), set in `config/config.yaml`. Provider layer is pluggable so "any LLM API" is a config change.
