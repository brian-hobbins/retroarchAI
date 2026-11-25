# retroarchAI

An environment where any SNES ROM can be loaded and **any player can be switched between human and AI control**, with the AI driven by an LLM (Claude or any LLM API).

> Status: early scaffolding. See [`docs/PROJECT_LOG.md`](docs/PROJECT_LOG.md) for the running build log and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit together.

## What this is (and isn't)

- **Is:** a thin libretro frontend (Python + `ctypes`) that loads a SNES core, exposes the framebuffer + per-port input injection + savestates, and lets an LLM act as a player on either controller port. Works with any ROM you own.
- **Isn't:** the `MatPoliquin/RetroArchAI` fork. That project overrides input with *trained PyTorch models* per game (60 fps, hand-coded RAM maps, no SNES example). We borrowed its UX idea — "switch a player to AI in-menu" — but not its architecture. See [`docs/DECISIONS.md`](docs/DECISIONS.md).

## The honest latency caveat

An LLM takes ~0.5–5 s per decision; SNES runs at 60 fps. So the AI does **not** play frame-by-frame. It observes, decides, and holds an action for a window of frames (optionally pausing the emulator while it thinks). This plays well for **turn-based / slower games** (RPGs, puzzles) and poorly for twitch games (fighters). See the recommended test games in `docs/ARCHITECTURE.md`.

## LLM credentials note

Your **Claude Code subscription powers the *building*** of this project (the assistant). **Runtime gameplay needs an API key** — Anthropic (Claude) or any OpenAI-compatible provider — configured in `config/config.yaml`. The provider layer is pluggable.

## Layout

```
retroarchAI/
  init.md               # original project brief
  config/config.yaml    # players, cadence, core, LLM provider
  roms/                 # your SNES ROMs (gitignored)
  saves/                # savestates (gitignored)
  logs/                 # per-session action traces (gitignored)
  src/
    emulator/           # ctypes libretro frontend: core load, framebuffer, input, state
    agents/             # LLM agent loop + action parsing
    providers/          # pluggable LLM backends (claude, openai-compatible)
    router/             # per-port human/AI routing + live toggle
    app.py              # entrypoint
  docs/                 # PROJECT_LOG, ARCHITECTURE, DECISIONS
  tests/
```

## Quick start

Not runnable yet — scaffolding in progress. The first milestone is the libretro frontend loading snes9x and stepping frames. Follow `docs/PROJECT_LOG.md`.
