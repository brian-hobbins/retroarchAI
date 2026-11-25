"""Deterministic navigation primitive with spatial memory (zero tokens).

Walks toward a target (goto) or along a heading (walk). When a push produces NO
change in the RAM position (blocked), it records a WALL in the SpatialMap and
follows the obstacle's edge — committing to one perpendicular detour direction
until the direct heading opens up again — instead of bumping the same barrel
forever or escalating to the (expensive) Planner on every wall.

The SpatialMap persists across directives (a barrel does not move), so obstacles
learned under one plan are still avoided under the next; only the per-run detour
state resets between directives.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .spatial import SpatialMap

THRESH = 8  # within this many units of target on an axis = arrived on that axis

_OPP = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}


class Navigator:
    def __init__(self):
        self.map = SpatialMap()      # persistent spatial memory (obstacles/free)
        self.reset()

    def reset(self) -> None:
        """Reset per-directive state. Keeps the learned SpatialMap."""
        self.last_pos: Optional[Tuple[int, int]] = None
        self.last_try: List[str] = []
        self.detour_dir: Optional[str] = None   # committed while following a wall
        self.goal_block: Optional[str] = None   # the heading the detour is bypassing

    @staticmethod
    def toward(pos, tx, ty) -> List[str]:
        dirs = []
        if tx - pos[0] > THRESH:
            dirs.append("RIGHT")
        elif tx - pos[0] < -THRESH:
            dirs.append("LEFT")
        if ty - pos[1] > THRESH:
            dirs.append("DOWN")
        elif ty - pos[1] < -THRESH:
            dirs.append("UP")
        return dirs

    @staticmethod
    def _perp(primary: List[str]) -> List[str]:
        horiz = any(d in ("LEFT", "RIGHT") for d in primary)
        return ["DOWN", "UP"] if horiz else ["RIGHT", "LEFT"]

    def step(self, pos, primary: List[str]) -> List[str]:
        """Return the buttons to press this tick, routing around known walls."""
        # --- learn from last tick: did that push move us? ---
        if self.last_pos is not None and self.last_try:
            if pos == self.last_pos:
                for d in self.last_try:
                    self.map.mark_wall(self.last_pos, d)   # that push hit a wall
            else:
                self.map.mark_free(pos)                     # made progress

        # --- choose this tick's direction ---
        if self.detour_dir is not None:
            choice = self._follow_wall(pos, primary)
        else:
            choice = self._head_toward(pos, primary)

        self.last_try = choice
        self.last_pos = pos
        return choice

    def _head_toward(self, pos, primary: List[str]) -> List[str]:
        """Not currently detouring: go for the target, starting a detour if a
        desired direction is walled right here."""
        walled = [d for d in primary if self.map.is_wall(pos, d)]
        if not walled:
            # open_dirs drops any stray remembered wall so we never press a
            # direction diagonally into a known obstacle.
            return self.map.open_dirs(pos, primary) or primary
        # a desired direction is blocked: begin following the obstacle's edge.
        self.goal_block = walled[0]
        cand = self.map.open_dirs(pos, self._perp([self.goal_block]))
        if cand:
            self.detour_dir = cand[0]
            return [cand[0]]
        # perpendicular also walled (in a pocket): any other open heading, else shove
        return self.map.open_dirs(pos, primary) or primary[:1]

    def _follow_wall(self, pos, primary: List[str]) -> List[str]:
        """Committed to a detour: keep skirting the wall until the blocked
        heading GENUINELY reopens. The cross-axis pull toward the target must not
        undo the detour, or we oscillate against the barrel forever."""
        blk = self.goal_block
        if blk and self.map.is_wall(pos, blk):
            # still walled ahead: keep going the committed way around it
            if not self.map.is_wall(pos, self.detour_dir):
                return [self.detour_dir]
            # our detour direction is now walled too (turned a corner): re-pick a
            # perpendicular, preferring to keep the same side where possible.
            cand = self.map.open_dirs(pos, self._perp([blk]))
            if cand:
                self.detour_dir = cand[0]
                return [cand[0]]
            return self.map.open_dirs(pos, primary) or [blk]
        # the blocked heading looks clear here: stop detouring and re-probe it
        # ALONE (pure, not diagonal) to confirm we are actually past the obstacle.
        self.detour_dir = None
        probe, self.goal_block = blk, None
        if probe and probe in primary:
            return [probe]
        return self.map.open_dirs(pos, primary) or primary[:1]
