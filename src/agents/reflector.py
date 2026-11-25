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
    "wasted turns) and the current LESSONS. Write a few concise, ACTIONABLE lessons "
    "that would make future play score higher: repeat what earned reward, stop what "
    "wasted turns, and note game-specific strategy. Each lesson is one short "
    "imperative sentence. Do NOT repeat existing lessons; leave the list empty if "
    "there is nothing new worth saving. Output ONLY the JSON object."
)


class Reflector:
    def __init__(self, model: str = "sonnet", timeout: int = 60):
        self.model = model
        self.timeout = timeout

    def reflect(self, trajectory_text: str, lessons_text: str,
                reward_total: float) -> tuple[list, dict]:
        prompt = (
            f"Cumulative reward so far: {reward_total:+.1f}\n\n"
            f"RECENT TRAJECTORY (step  directive/buttons  reward  (notes)):\n"
            f"{trajectory_text}\n\n"
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
