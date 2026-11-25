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
        "new_facts": {
            "type": "array", "maxItems": 5,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["text", "tag"],
                "properties": {
                    "text": {"type": "string", "maxLength": 200},
                    "tag": {"type": "string",
                            "enum": ["story", "lore", "mechanics", "clue"]},
                },
            },
        },
        "consult_story": {"type": "boolean"},
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
    "test. (Your SCOUT already transcribes on-screen text for you — don't re-type it.)\n"
    "You also keep a NOTEBOOK. You are given WHAT YOU'VE LEARNED SO FAR; use it to "
    "decide (e.g. go where a clue points). In 'new_facts', add any NEW durable facts "
    "worth remembering, each TAGGED by kind:\n"
    "  - 'clue': a hint toward a puzzle / hidden item / quest step, a key/door/item "
    "location, directions given — actionable, kept in the notebook.\n"
    "  - 'mechanics': how the game works (a control, a system, what an item does).\n"
    "  - 'story': plot/narrative events, what happened, who did what.\n"
    "  - 'lore': world background, history, names — flavour, not immediately actionable.\n"
    "'story' and 'lore' are filed in a separate STORY LOG (not shown every turn, to "
    "save space); 'clue' and 'mechanics' go in the notebook. Keep each fact short and "
    "specific; do NOT repeat anything already known; leave empty if nothing new.\n"
    "Set 'consult_story'=true ONLY when you're stuck on a puzzle or looking for a "
    "hidden thing and think past story/lore might hold the clue — the STORY LOG will "
    "then be shown to you next turn. Output ONLY the JSON object."
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
             trouble: str = "", scene: str = "", coach: str = "",
             story: str = "") -> tuple[Directive, dict, list, bool]:
        prompt = _build_prompt(obs, knowledge, lessons, trouble, scene, coach, story)
        # The Scout is the team's eyes: when it provided a scene report, the Planner
        # reasons text-only over that report + RAM (no image) to save tokens. If the
        # Scout had no view, fall back to looking at the screenshot itself.
        image = None if scene else obs.image_path
        payload, usage = run_claude_json(
            prompt, _SYSTEM, _SCHEMA, self.model,
            image_path=image, timeout=self.timeout,
        )
        if not payload:
            return Directive.fallback(usage.get("error") or "no plan"), usage, [], False
        d = Directive(
            kind=str(payload.get("kind", "walk")),
            instruction=str(payload.get("instruction", "")),
            done_when=str(payload.get("done_when", "")),
            direction=payload.get("direction"),
            target_x=payload.get("target_x"),
            target_y=payload.get("target_y"),
            target_keyword_index=payload.get("target_keyword_index"),
        )
        # new_facts are now {text, tag}; accept legacy strings too (tag -> clue)
        facts = []
        for f in payload.get("new_facts", []):
            if isinstance(f, dict) and isinstance(f.get("text"), str):
                tag = str(f.get("tag", "clue"))
                facts.append({"text": f["text"],
                              "tag": tag if tag in ("story", "lore", "mechanics", "clue")
                              else "clue"})
            elif isinstance(f, str):
                facts.append({"text": f, "tag": "clue"})
        return d, usage, facts, bool(payload.get("consult_story", False))


def _build_prompt(obs, knowledge: str = "", lessons: str = "",
                  trouble: str = "", scene: str = "", coach: str = "",
                  story: str = "") -> str:
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
    storylog = ""
    if story:
        storylog = ("\nSTORY LOG (you asked to consult it — look here for a clue to "
                    f"your puzzle / hidden thing):\n{story}\n")
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
        f"{storylog}"
        f"{strat}"
        f"{coaching}"
        f"{trbl}"
        f"{view}"
        f"{ram}\n"
        "Set the next directive as JSON."
    )
