"""AIController: runs an LLM-driven player on one controller port.

Synchronous by design: the emulator only advances when we call run(), so it is
naturally paused while the provider thinks. That is exactly what we want for
turn-based / menu-driven games.
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from emulator.libretro import LibretroCore
from providers.base import Action, Observation, Provider


class AIController:
    def __init__(
        self,
        emu: LibretroCore,
        provider: Provider,
        port: int,
        goal: str,
        trace_dir: str,
        release_frames: int = 6,
        save_frames: bool = True,
    ):
        self.emu = emu
        self.provider = provider
        self.port = port
        self.goal = goal
        self.release_frames = release_frames
        self.save_frames = save_frames

        self.trace_dir = trace_dir
        self.frame_dir = os.path.join(trace_dir, "frames")
        os.makedirs(self.frame_dir, exist_ok=True)
        self.trace_path = os.path.join(trace_dir, "trace.jsonl")

        self.history: List[str] = []
        self.step = 0

    def _write_observation(self, step: int) -> Optional[Observation]:
        from emulator.pngwrite import write_png

        frame = self.emu.get_frame_rgb()
        if frame is None:
            return None
        w, h, rgb = frame
        path = os.path.join(self.frame_dir, f"obs_{step:05d}.png")
        write_png(path, w, h, rgb)
        return Observation(
            image_path=path, width=w, height=h,
            goal=self.goal, step=step, history=list(self.history),
        )

    def _apply(self, action: Action) -> None:
        self.emu.set_buttons(self.port, action.buttons)
        self.emu.run(action.hold_frames)
        self.emu.clear_input(self.port)
        self.emu.run(self.release_frames)

    def tick(self) -> Action:
        """One observe -> decide -> act cycle. Returns the action taken."""
        self.step += 1
        obs = self._write_observation(self.step)
        if obs is None:
            # No frame yet; advance a little and wait.
            self.emu.run(6)
            return Action.noop("no frame yet")

        t0 = time.time()
        action = self.provider.decide(obs)
        latency = time.time() - t0
        self._apply(action)

        self.history.append(
            f"#{self.step}: {action.buttons or 'wait'} x{action.hold_frames} — {action.reason}"
        )
        self._log({
            "step": self.step,
            "port": self.port,
            "image": os.path.relpath(obs.image_path, self.trace_dir),
            "buttons": action.buttons,
            "hold_frames": action.hold_frames,
            "reason": action.reason,
            "latency_s": round(latency, 2),
        })
        return action

    def _log(self, record: dict) -> None:
        with open(self.trace_path, "a") as f:
            f.write(json.dumps(record) + "\n")
