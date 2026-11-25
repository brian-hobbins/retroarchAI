"""Reward tracking — the learning signal. All computed in code (zero tokens)
from signals we already have: new areas explored, new dialogue/text seen, new
facts learned (progress); getting stuck / bonking walls (penalty).

The Reflector periodically reads the recent trajectory + rewards and distills
lessons that feed back into planning.
"""

from __future__ import annotations

import hashlib
from collections import deque
from typing import Optional, Tuple

# weights
R_NEW_TEXT = 2.0       # encountered a dialogue/menu/sign we hadn't seen
R_NEW_FACT = 1.0       # archived a new durable fact
R_NEW_AREA = 1.5       # position jumped far (area/screen transition)
R_MOVED = 0.1          # actually changed position (made progress moving)
P_STUCK = -0.5         # tried to move but position didn't change (blocked)
P_NO_FEEDBACK = -1.0   # pressed buttons but produced NO meaningful change (e.g.
                       # interacting with a non-interactable thing like a window);
                       # catches wasted effort that P_STUCK misses when facing-
                       # nudges jitter the position. Feeds the Reflector so it
                       # learns to stop repeating dead actions.
AREA_JUMP = 160        # manhattan distance treated as an area transition


def _hash(s: str) -> str:
    return hashlib.md5(s.strip().lower().encode()).hexdigest()[:12]


class RewardTracker:
    def __init__(self, buffer: int = 30):
        self.total = 0.0
        self.last = 0.0
        self.seen_text = set()
        self.last_pos: Optional[Tuple[int, int]] = None
        self.traj = deque(maxlen=buffer)

    def observe(self, step, directive_kind, buttons, pos, screen_text,
                new_facts_count, moving, no_feedback=False) -> float:
        r = 0.0
        notes = []

        if no_feedback:
            r += P_NO_FEEDBACK
            notes.append("no-feedback")

        if screen_text and screen_text.strip():
            h = _hash(screen_text)
            if h not in self.seen_text:
                self.seen_text.add(h)
                r += R_NEW_TEXT
                notes.append("new-text")

        if new_facts_count:
            r += R_NEW_FACT * new_facts_count
            notes.append(f"learned+{new_facts_count}")

        if pos[0] is not None and self.last_pos is not None:
            dist = abs(pos[0] - self.last_pos[0]) + abs(pos[1] - self.last_pos[1])
            if dist >= AREA_JUMP:
                r += R_NEW_AREA
                notes.append("new-area")
            elif dist > 0:
                r += R_MOVED
            elif moving:
                r += P_STUCK
                notes.append("stuck")
        if pos[0] is not None:
            self.last_pos = pos

        self.total += r
        self.last = r
        self.traj.append({
            "step": step, "kind": directive_kind,
            "buttons": "+".join(buttons) if buttons else "wait",
            "reward": round(r, 2), "notes": ",".join(notes) or "-",
        })
        return r

    def trajectory_text(self, n: int = 20) -> str:
        rows = list(self.traj)[-n:]
        return "\n".join(
            f"#{t['step']} {t['kind']}/{t['buttons']} r={t['reward']} ({t['notes']})"
            for t in rows
        )

    def summary(self) -> str:
        return f"reward {self.total:+.1f} (last {self.last:+.1f})"
