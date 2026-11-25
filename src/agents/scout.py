"""Scout agent: the eyes. The one agent that actually LOOKS at the screenshot.

It turns raw pixels into a structured, literal SCENE report — what is on screen,
where each thing is relative to the player, and crucially whether each thing is
INTERACTABLE (a person, an openable door, a pickup) versus scenery (a window, a
wall, decoration). It does NOT strategize; it only reports what it sees.

That report is shared with the rest of the team (Planner reasons over it text-only;
Executor's fallback can read it), which (a) centralizes vision into one good call
instead of every agent squinting at pixels, and (b) stops the Planner from trying
to "open" a window, because the Scout labels it scenery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from providers.cli_call import run_claude_json

ENTITY_KINDS = ["npc", "enemy", "item", "door", "container", "sign",
                "exit", "hazard", "scenery", "player", "other"]
DIRS = ["UP", "DOWN", "LEFT", "RIGHT", "UP-LEFT", "UP-RIGHT",
        "DOWN-LEFT", "DOWN-RIGHT", "HERE", "OFFSCREEN", "UNKNOWN"]

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "entities", "text", "exits"],
    "properties": {
        "summary": {"type": "string", "maxLength": 160},
        "entities": {
            "type": "array", "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "kind", "dir", "interactable"],
                "properties": {
                    "label": {"type": "string", "maxLength": 40},
                    "kind": {"type": "string", "enum": ENTITY_KINDS},
                    "dir": {"type": "string", "enum": DIRS},
                    "interactable": {"type": "boolean"},
                    "note": {"type": "string", "maxLength": 80},
                },
            },
        },
        "text": {"type": "string", "maxLength": 400},
        "exits": {"type": "string", "maxLength": 160},
    },
}

_SYSTEM = (
    "You are the SCOUT — the eyes of an agent playing a SNES game. Look at the "
    "screenshot and report ONLY what is literally visible. Do NOT strategize, do "
    "NOT guess intentions, do NOT invent anything not on screen.\n"
    "The player's character is usually at or near the CENTER of the screen. For "
    "each notable thing, give its 'dir' = which way it is FROM the player "
    "(UP/DOWN/LEFT/RIGHT/diagonals, or HERE if adjacent/overlapping).\n"
    "Classify each with 'kind' and set 'interactable' HONESTLY:\n"
    "- interactable=true ONLY for things you can act on: people/creatures (npc, "
    "enemy), an OPENABLE door, a pickup/item, a container (chest/barrel you can "
    "open), a readable sign, a usable exit.\n"
    "- interactable=false for scenery you CANNOT act on: windows, walls, floors, "
    "fences, plants, furniture you can't open, decoration. Be strict — a WINDOW is "
    "scenery, not a door.\n"
    "List people FIRST. Put any readable on-screen text (dialogue, menus, signs) "
    "VERBATIM in 'text' (empty string if none). In 'exits', note visible ways to "
    "leave or paths (e.g. 'door to the south, open path east'). Output ONLY JSON."
)


@dataclass
class Entity:
    label: str
    kind: str
    dir: str
    interactable: bool
    note: str = ""


@dataclass
class Scene:
    summary: str = ""
    entities: List[Entity] = field(default_factory=list)
    text: str = ""
    exits: str = ""
    ok: bool = True

    def interactables(self) -> List[Entity]:
        return [e for e in self.entities if e.interactable]

    def scenery(self) -> List[Entity]:
        return [e for e in self.entities if not e.interactable]

    def brief(self) -> str:
        """Compact report injected into the Planner/Executor prompt."""
        if not self.ok and not self.summary:
            return ""
        lines = [f"SCENE (scout report): {self.summary}"]
        inter = self.interactables()
        if inter:
            lines.append("  INTERACTABLE: " + "; ".join(
                f"{e.label} [{e.kind}] to the {e.dir}"
                + (f" — {e.note}" if e.note else "") for e in inter))
        scen = self.scenery()
        if scen:
            lines.append("  NOT interactable (scenery): " + "; ".join(
                f"{e.label} ({e.dir})" for e in scen))
        if self.exits:
            lines.append(f"  exits/paths: {self.exits}")
        if self.text:
            lines.append(f'  on-screen text: "{self.text}"')
        return "\n".join(lines)

    def display(self) -> List[str]:
        """Short lines for the live viewer's SCENE panel."""
        out = []
        if self.summary:
            out.append(self.summary)
        for e in self.entities:
            tag = "●" if e.interactable else "·"
            out.append(f"{tag} {e.label} [{e.kind}] {e.dir}")
        if self.exits:
            out.append(f"exits: {self.exits}")
        return out


class Scout:
    def __init__(self, model: str = "sonnet", timeout: int = 60):
        self.model = model
        self.timeout = timeout

    def look(self, obs) -> tuple[Scene, dict]:
        prompt = (
            f"Your goal (context only): {obs.goal}\n"
            f"The current {obs.width}x{obs.height} screenshot is attached. "
            "Report the scene as JSON."
        )
        payload, usage = run_claude_json(
            prompt, _SYSTEM, _SCHEMA, self.model,
            image_path=obs.image_path, timeout=self.timeout,
        )
        if not payload:
            return Scene(summary="(scout: no view)", ok=False), usage
        ents = []
        for e in payload.get("entities", []):
            if not isinstance(e, dict):
                continue
            ents.append(Entity(
                label=str(e.get("label", "?"))[:40],
                kind=str(e.get("kind", "other")),
                dir=str(e.get("dir", "UNKNOWN")),
                interactable=bool(e.get("interactable", False)),
                note=str(e.get("note", ""))[:80],
            ))
        return (Scene(summary=str(payload.get("summary", "")),
                      entities=ents, text=str(payload.get("text", "")),
                      exits=str(payload.get("exits", "")), ok=True),
                usage)
