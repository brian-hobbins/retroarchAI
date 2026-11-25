# Architecture

## High-level flow

```
                 ┌─────────────────────────────────────────────┐
                 │                  app.py                       │
                 │  loads config, wires emulator + router        │
                 └───────────────┬───────────────────────────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                          ▼
  ┌───────────┐          ┌──────────────┐           ┌───────────────┐
  │ Emulator  │          │   Router      │           │   Agents       │
  │ (libretro │◀────────▶│ per-port mode │◀─────────▶│ LLM loop       │
  │  ctypes)  │  frames  │ HUMAN | AI    │  actions  │ (1 per AI port)│
  └─────┬─────┘  + input └──────┬────────┘           └──────┬─────────┘
        │                       │                           │
        │ video_refresh         │ keyboard (human)          │ observation
        │ input_state           │                           ▼
        │ serialize             │                     ┌───────────────┐
        ▼                       │                     │  Providers     │
  snes9x_libretro.so            │                     │ claude | openai│
  + ROM                         ▼                     │  -compatible   │
                           pygame window              └───────────────┘
```

## Components

### `src/emulator/` — libretro frontend (ctypes)
Wraps a libretro core `.so`:
- `retro_init`, `retro_load_game(ROM)`, `retro_run()` (one frame), `retro_deinit`.
- **video_refresh** callback → capture framebuffer into a numpy/bytes image (used for screenshots and the window).
- **input_state** callback → returns button state for `(port, device, id)`; we feed it from the Router so each port is driven by human or AI.
- **savestate**: `retro_serialize` / `retro_unserialize` for pause-and-think and rewind.
- Audio callback: stubbed/optional for v1.

### `src/router/` — per-port mode + live toggle
- Holds mode for port 0 (P1) and port 1 (P2): `HUMAN` or `AI`.
- HUMAN → reads keyboard (pygame) mapped to SNES buttons.
- AI → reads the latest action the agent produced (held across the decision window).
- Hotkeys to flip a port human↔AI at runtime (the "switch any player" feature).

### `src/agents/` — LLM control loop (one per AI port)
Loop cadence `decision_interval` (frames) OR event-driven:
1. Build **observation**: current framebuffer (downscaled PNG) + optional structured hints.
2. Prompt the provider with goal + controls + observation; optionally pause the emulator while waiting.
3. Parse the reply into an **action plan**: button combo + how many frames to hold.
4. Hand the action to the Router; repeat.
Action schema (JSON): `{"buttons": ["RIGHT","B"], "hold_frames": 20, "reason": "..."}`.

### `src/providers/` — pluggable LLM backends
- `claude.py` — Anthropic Messages API (vision-capable model).
- `openai_compat.py` — any OpenAI-compatible endpoint (local or hosted).
- Selected by `config/config.yaml`; both take an image + text, return text/JSON.

## Recommended test games (LLM-friendly first)

| Priority | Game | Why |
|---|---|---|
| 1 | Turn-based RPG battle (Final Fantasy VI, Chrono Trigger, Super Mario RPG) | LLM has time each turn; latency hidden; clear state. |
| 1 | Puzzle (Tetris, Tetris Attack / Panel de Pon) | Small grid state, forgiving timing, easy to reason about. |
| 2 | The Legend of Zelda: A Link to the Past | Action-RPG, pausable, exploration — good general test. |
| 3 | Super Mario World | Platformer; visually impressive but timing-sensitive — stretch goal. |
| avoid v1 | Street Fighter II, fast sports | Twitch timing; LLM latency makes play poor. |

ROMs are user-supplied (your own legal dumps) in `roms/`. No per-game code is required for the vision-based LLM path.

## Milestones

1. **M1 — Core boots:** ctypes frontend loads snes9x + a ROM, steps frames, dumps a PNG screenshot. *(no AI yet)*
2. **M2 — Window + human play:** pygame window renders frames, keyboard drives P1.
3. **M3 — LLM plays:** agent loop + Claude provider drives a port on a puzzle/RPG game.
4. **M4 — Switchable:** live hotkey toggle of each port human↔AI; AI-vs-AI by setting both to AI.
5. **M5 — Any ROM + polish:** drop-in ROM loading, provider config for any LLM API, logging/traces.
