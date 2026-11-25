"""Reflector agent: the learning step. Periodically reviews the recent play
trajectory + rewards and writes concise, persistent LESSONS (what worked, what
wasted turns, game-specific strategy) that are fed back into the Planner.

Text-only (no screenshot), so it is cheaper than a vision call; run rarely.
"""

from __future__ import annotations

from providers.cli_call import run_claude_json

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lessons"],
    "properties": {
        "lessons": {"type": "array", "items": {"type": "string", "maxLength": 200},
                    "maxItems": 5},
    },
}

_SYSTEM = (
    "You are the REFLECTOR for an agent learning to play a SNES game better over "
    "time. You are given the recent play trajectory with a reward per step (higher "
    "reward = more progress: new text, new area, facts learned; negative = stuck / "
    "wasted turns), the current LESSONS, and raw HUMAN GUIDANCE (demonstrations the "
    "human showed and hints they typed). Write a few concise, ACTIONABLE lessons that "
    "would make future play score higher: repeat what earned reward, stop what wasted "
    "turns, note game-specific strategy, and GENERALIZE the human guidance into "
    "durable rules (turn 'press A to use Heal Force now' into 'in a battle, when HP is "
    "low, open MagiTek and select Heal Force'). Ignore one-off, sarcastic, or "
    "situation-specific noise — distill only what transfers to future play. Each "
    "lesson is one short imperative sentence. Do NOT repeat existing lessons; leave "
    "the list empty if there is nothing new worth saving. Output ONLY the JSON object."
)


class Reflector:
    def __init__(self, model: str = "sonnet", timeout: int = 60):
        self.model = model
        self.timeout = timeout

    def reflect(self, trajectory_text: str, lessons_text: str,
                reward_total: float, demos_text: str = "") -> tuple[list, dict]:
        human = ""
        if demos_text:
            human = ("\nRECENT HUMAN GUIDANCE (demos shown + hints typed — GENERALIZE "
                     f"into durable lessons, ignore one-off noise):\n{demos_text}\n")
        prompt = (
            f"Cumulative reward so far: {reward_total:+.1f}\n\n"
            f"RECENT TRAJECTORY (step  directive/buttons  reward  (notes)):\n"
            f"{trajectory_text}\n"
            f"{human}\n"
            f"CURRENT LESSONS:\n{lessons_text or '(none yet)'}\n\n"
            "Write new lessons as JSON."
        )
        payload, usage = run_claude_json(
            prompt, _SYSTEM, _SCHEMA, self.model, image_path=None,
            timeout=self.timeout,
        )
        if not payload:
            return [], usage
        lessons = [l for l in payload.get("lessons", []) if isinstance(l, str)]
        return lessons, usage

    def condense(self, lessons_text: str, target: int = 20) -> tuple[list, dict]:
        """Rewrite the whole lessons list into a SHORTER, deduplicated set that
        preserves every distinct useful rule — run periodically to cap token growth.
        Returns the full replacement list (the caller overwrites lessons.json)."""
        payload, usage = run_claude_json(
            ("These LESSONS have grown long and repetitive. Rewrite them as a shorter, "
             "deduplicated set that PRESERVES every distinct, useful rule: merge "
             "near-duplicates, drop redundant or obsolete ones, keep the clearest "
             f"phrasing. Aim for about {target} lessons, ordered most-useful first. Do "
             "NOT invent new advice — only compress what's here.\n\nCURRENT LESSONS:\n"
             f"{lessons_text}\n\nOutput the condensed lessons as JSON."),
            ("You compress a game-playing agent's strategy notes without losing "
             "knowledge. Merge duplicates, keep every distinct actionable rule, drop "
             "noise. Each lesson is one short imperative sentence. Output ONLY JSON."),
            _CONDENSE_SCHEMA, self.model, image_path=None, timeout=self.timeout)
        if not payload:
            return [], usage
        return [l for l in payload.get("lessons", []) if isinstance(l, str)], usage


_CONDENSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lessons"],
    "properties": {
        "lessons": {"type": "array", "items": {"type": "string", "maxLength": 200},
                    "maxItems": 40},
    },
}
