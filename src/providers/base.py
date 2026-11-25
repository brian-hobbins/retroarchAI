"""Provider interface and the shared Action / Observation types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

VALID_BUTTONS = {
    "A", "B", "X", "Y", "L", "R", "START", "SELECT",
    "UP", "DOWN", "LEFT", "RIGHT",
}


@dataclass
class Observation:
    """What the agent sees for one decision."""
    image_path: str           # PNG of the current frame
    width: int
    height: int
    goal: str                 # the player's objective, from config
    step: int                 # decision index
    history: List[str] = field(default_factory=list)  # recent action reasons
    ram_facts: dict = field(default_factory=dict)     # ground-truth state read from RAM
    manual: str = ""                                  # game manual text (controls/lore)
    scene: str = ""                                   # Scout's perception report (shared)
    lessons: str = ""                                 # hard-won lessons (shared w/ Executor)


@dataclass
class Action:
    """What the agent decides to do."""
    buttons: List[str]        # SNES buttons to hold, e.g. ["RIGHT", "B"]
    hold_frames: int          # how many frames to hold them
    reason: str               # short explanation (for logs)
    screen_text: str = ""     # verbatim transcription of on-screen game text/dialogue

    def sanitized(self) -> "Action":
        btns = [b.upper() for b in self.buttons if b.upper() in VALID_BUTTONS]
        hold = max(1, min(int(self.hold_frames), 120))
        return Action(buttons=btns, hold_frames=hold,
                      reason=self.reason[:600], screen_text=self.screen_text[:400])

    @staticmethod
    def noop(reason: str = "noop") -> "Action":
        return Action(buttons=[], hold_frames=6, reason=reason, screen_text="")


class Provider:
    """Base class: implement decide()."""

    name = "base"

    def decide(self, obs: Observation) -> Action:  # pragma: no cover - interface
        raise NotImplementedError
