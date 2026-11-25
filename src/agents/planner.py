"""Planner agent: the strategist. Called rarely (event-driven) on a strong model.

Given the current screen + RAM ground truth + manual + goal, it sets a DIRECTIVE
for the cheap Executor to carry out until it completes or something changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from providers.cli_call import run_claude_json

DIRECTIVE_KINDS = ["walk", "goto", "interact", "dialogue", "freeform"]

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "instruction", "done_when"],
    "properties": {
        "kind": {"type": "string", "enum": DIRECTIVE_KINDS},
        "direction": {"type": "string",
                      "enum": ["UP", "DOWN", "LEFT", "RIGHT",
                               "UP+LEFT", "UP+RIGHT", "DOWN+LEFT", "DOWN+RIGHT"]},
        "target_x": {"type": "integer"},
        "target_y": {"type": "integer"},
        "target_keyword_index": {"type": "integer"},
        "instruction": {"type": "string", "maxLength": 160},
        "done_when": {"type": "string", "maxLength": 120},
        "screen_text": {"type": "string", "maxLength": 400},
        "new_facts": {"type": "array", "items": {"type": "string", "maxLength": 200},
                      "maxItems": 5},
    },
}

_SYSTEM = (
    "You are the PLANNER for an agent playing a SNES game; you are the character. "
    "You set ONE directive that a FREE, code-driven executor carries out for "
    "several steps before consulting you again — so STRONGLY PREFER the "
    "code-executable kinds below and AVOID 'freeform' (it is expensive). Choose:\n"
    "- walk: travel/explore by holding a 'direction' (e.g. RIGHT or DOWN+LEFT). The "
    "executor walks that way until it is blocked or the scene changes. This is your "
    "DEFAULT for getting around.\n"
    "- goto: walk to a specific map position — set target_x,target_y (estimate from "
    "the ground-truth avatar_x/avatar_y plus which way the spot is on screen).\n"
    "- interact: talk to a person / open a door / pick up or examine something. You "
    "interact with whatever you FACE, so ALWAYS set 'direction' = which way the "
    "target is from you (UP/DOWN/LEFT/RIGHT); the executor faces it, then presses Y. "
    "TALKING TO PEOPLE IS A PRIORITY: whenever a humanoid person is next to you or "
    "one step away, use 'interact' with their direction — do not walk past them. If a "
    "person is a few tiles away, 'walk' toward them first, then 'interact'.\n"
    "- dialogue: in a conversation — set target_keyword_index (0 = leftmost) to ask "
    "a topic, or omit it to just advance the text.\n"
    "- freeform: ONLY as a last resort for something the above cannot express.\n"
    "'instruction' = one short first-person directive; 'done_when' = brief completion "
    "test. In 'screen_text', copy any readable in-game text (dialogue, menus, signs) "
    "VERBATIM, or empty string.\n"
    "You also keep a NOTEBOOK. You are given WHAT YOU'VE LEARNED SO FAR; use it to "
    "decide (e.g. go where a clue points). In 'new_facts', add any NEW durable facts "
    "worth remembering to solve puzzles or advance the story — NPC names & where they "
    "are, quest steps, clues, riddle hints, item/door/key locations, directions given. "
    "Keep each fact short and specific; do NOT repeat anything already in the notebook; "
    "leave empty if nothing new. Output ONLY the JSON object."
)


@dataclass
class Directive:
    kind: str
    instruction: str
    done_when: str = ""
    direction: Optional[str] = None
    target_x: Optional[int] = None
    target_y: Optional[int] = None
    target_keyword_index: Optional[int] = None

    def short(self) -> str:
        t = ""
        if self.kind == "walk" and self.direction:
            t = f"->{self.direction}"
        elif self.kind == "goto" and self.target_x is not None:
            t = f"->({self.target_x},{self.target_y})"
        elif self.kind == "dialogue" and self.target_keyword_index is not None:
            t = f"->kw{self.target_keyword_index}"
        return f"{self.kind}{t}: {self.instruction}"

    @staticmethod
    def fallback(reason: str = "walk") -> "Directive":
        return Directive(kind="walk", direction="DOWN",
                         instruction=f"walk to explore ({reason})",
                         done_when="blocked or something new appears")


class Planner:
    def __init__(self, model: str = "sonnet", timeout: int = 60):
        self.model = model
        self.timeout = timeout

    def plan(self, obs, knowledge: str = "", lessons: str = "",
             trouble: str = "", scene: str = "",
             coach: str = "") -> tuple[Directive, dict, str, list]:
        prompt = _build_prompt(obs, knowledge, lessons, trouble, scene, coach)
        # The Scout is the team's eyes: when it provided a scene report, the Planner
        # reasons text-only over that report + RAM (no image) to save tokens. If the
        # Scout had no view, fall back to looking at the screenshot itself.
        image = None if scene else obs.image_path
        payload, usage = run_claude_json(
            prompt, _SYSTEM, _SCHEMA, self.model,
            image_path=image, timeout=self.timeout,
        )
        if not payload:
            return Directive.fallback(usage.get("error") or "no plan"), usage, "", []
        d = Directive(
            kind=str(payload.get("kind", "walk")),
            instruction=str(payload.get("instruction", "")),
            done_when=str(payload.get("done_when", "")),
            direction=payload.get("direction"),
            target_x=payload.get("target_x"),
            target_y=payload.get("target_y"),
            target_keyword_index=payload.get("target_keyword_index"),
        )
        facts = [f for f in payload.get("new_facts", []) if isinstance(f, str)]
        return d, usage, str(payload.get("screen_text", "")), facts


def _build_prompt(obs, knowledge: str = "", lessons: str = "",
                  trouble: str = "", scene: str = "", coach: str = "") -> str:
    ram = ""
    if obs.ram_facts:
        hint = obs.ram_facts.get("_hint", "")
        vals = {k: v for k, v in obs.ram_facts.items() if not k.startswith("_")}
        ram = (f"\nGROUND-TRUTH STATE (from memory, trust over pixels): {vals}. {hint}")
    manual = ""
    if obs.manual:
        manual = ("\nGAME MANUAL (authoritative controls/mechanics):\n"
                  f"\"\"\"\n{obs.manual[:3500]}\n\"\"\"\n")
    notes = ""
    if knowledge:
        notes = f"\nWHAT YOU'VE LEARNED SO FAR (your notebook):\n{knowledge}\n"
    strat = ""
    if lessons:
        strat = f"\nSTRATEGY LESSONS (follow these to play better):\n{lessons}\n"
    trbl = ""
    if trouble:
        trbl = f"\n⚠ FEEDBACK FROM YOUR LAST ATTEMPT: {trouble}\n"
    coaching = ""
    if coach:
        coaching = ("\n🧑 THE HUMAN WATCHING JUST TOLD YOU (this OVERRIDES your own "
                    f"plan — do exactly this now): \"{coach}\"\n")
    if scene:
        # text-only: reason over the Scout's report (the team's shared eyes)
        view = (f"\nWHAT YOU SEE (from your SCOUT — trust it for identifying things; "
                f"only try to 'interact' with things it marks INTERACTABLE):\n{scene}\n")
    else:
        view = f"The current {obs.width}x{obs.height} screenshot is attached."
    return (
        f"Overall goal: {obs.goal}\n"
        f"{manual}"
        f"{notes}"
        f"{strat}"
        f"{coaching}"
        f"{trbl}"
        f"{view}"
        f"{ram}\n"
        "Set the next directive as JSON."
    )
