"""Spatial recognition: a persistent occupancy map in world coordinates.

The AI only gets the Avatar's (x, y) from RAM — it cannot "see" obstacles. This
builds spatial memory from experience: every time a push in some direction fails
to change position, the space just ahead in that direction is recorded as a WALL
for that locale; every position actually reached is recorded as FREE.

Walls are stored per coarse cell and direction — "from around here, going RIGHT
is blocked" — so the memory survives moving elsewhere (a barrel does not vanish
because we stepped around it) yet is forgotten once we are past it (a different
cell). This is what lets navigation route around a barrel instead of bumping it
forever.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import List, Tuple

CELL = 16        # world units per occupancy cell (quantization of x/y)
MAX_WALLS = 600  # cap on remembered directional walls (oldest evicted)

_VEC = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


class SpatialMap:
    def __init__(self, cell: int = CELL):
        self.cell = cell
        self.walls: "OrderedDict[Tuple[int, int, str], int]" = OrderedDict()
        self.inert: "OrderedDict[Tuple[int, int, str], int]" = OrderedDict()
        self.visited: set = set()
        self.step = 0

    def _cell(self, pos) -> Tuple[int, int]:
        return (pos[0] // self.cell, pos[1] // self.cell)

    def mark_free(self, pos) -> None:
        if pos[0] is None:
            return
        self.visited.add(self._cell(pos))

    def mark_wall(self, pos, direction: str) -> None:
        """Record that from `pos`'s cell, moving `direction` is blocked."""
        if pos[0] is None or direction not in _VEC:
            return
        self.step += 1
        key = (*self._cell(pos), direction)
        self.walls[key] = self.step
        self.walls.move_to_end(key)
        while len(self.walls) > MAX_WALLS:
            self.walls.popitem(last=False)

    def is_wall(self, pos, direction: str) -> bool:
        if pos[0] is None:
            return False
        return (*self._cell(pos), direction) in self.walls

    def open_dirs(self, pos, dirs: List[str]) -> List[str]:
        """Filter `dirs` to those not known to be walled from here."""
        return [d for d in dirs if not self.is_wall(pos, d)]

    def mark_inert(self, pos, direction) -> None:
        """Record that interacting toward `direction` from here did NOTHING — the
        thing faced is not interactable (a window mistaken for a door, scenery)."""
        if pos[0] is None:
            return
        self.step += 1
        d = direction if direction in _VEC else "?"
        key = (*self._cell(pos), d)
        self.inert[key] = self.step
        self.inert.move_to_end(key)
        while len(self.inert) > MAX_WALLS:
            self.inert.popitem(last=False)

    def is_inert(self, pos, direction) -> bool:
        if pos[0] is None:
            return False
        d = direction if direction in _VEC else "?"
        return (*self._cell(pos), d) in self.inert

    def nearby_walls(self, pos) -> List[str]:
        """Human-readable list of walls remembered around the current cell."""
        if pos[0] is None:
            return []
        cx, cy = self._cell(pos)
        return sorted(d for (wx, wy, d) in self.walls if wx == cx and wy == cy)

    def nearby_inert(self, pos) -> List[str]:
        """Directions from the current cell already found non-interactable."""
        if pos[0] is None:
            return []
        cx, cy = self._cell(pos)
        return sorted(d for (ix, iy, d) in self.inert if ix == cx and iy == cy)

    def summary(self, pos) -> str:
        n = self.nearby_walls(pos)
        return (f"walls here: {'+'.join(n)}" if n else "clear") + \
            f" | mapped {len(self.walls)}w/{len(self.visited)}c"
