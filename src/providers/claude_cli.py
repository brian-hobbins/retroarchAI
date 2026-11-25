"""Provider that drives the AI player via the local `claude` CLI (subscription auth).

No API key required: it shells out to `claude -p ... --output-format json`, which
uses the logged-in Claude Code subscription. The screenshot is handed over by
letting the CLI's Read tool open the PNG we just wrote.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from .base import Action, Observation, Provider, VALID_BUTTONS

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["buttons", "hold_frames", "reason", "screen_text"],
    "properties": {
        "buttons": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(VALID_BUTTONS)},
            "maxItems": 4,
        },
        "hold_frames": {"type": "integer", "minimum": 1, "maximum": 120},
        "reason": {"type": "string", "maxLength": 160},
        "screen_text": {"type": "string", "maxLength": 400},
    },
}

_SYSTEM = (
    "You ARE the player character in this Super Nintendo (SNES) game — you control "
    "it directly, so think and write in the FIRST PERSON ('a wall blocks me to the "
    "south, so I'll go east'). Never write in the third person and never say 'I see "
    "the Avatar/the character' — that character is you. "
    "SNES pad: A B X Y, L R, START, SELECT, D-pad. "
    "Each turn you get one screenshot; reply with JSON only: buttons (pad buttons to "
    "hold together, empty = wait), hold_frames (3-6 to tap a menu/confirm, 20-40 to "
    "walk), reason, screen_text. "
    "Keep 'reason' to ONE short first-person clause — just what I'll do and why. "
    "Don't describe the whole screen or restate the obvious. "
    "In 'screen_text', copy any readable in-game text (dialogue, menus, signs, "
    "numbers) VERBATIM, or empty string if there is none. "
    "Output ONLY the JSON object."
)


class ClaudeCLIProvider(Provider):
    name = "claude_cli"

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 60,
        claude_bin: str = "claude",
    ):
        self.model = model
        self.timeout = timeout
        self.claude_bin = claude_bin
        # --json-schema takes the schema JSON inline (a string), not a path.
        self.schema_arg = json.dumps(_SCHEMA)

    def _build_prompt(self, obs: Observation) -> str:
        hist = ""
        if obs.history:
            hist = "\nRecent actions:\n" + "\n".join(
                f"  - {h}" for h in obs.history[-6:]
            )
        ram = ""
        if obs.ram_facts:
            hint = obs.ram_facts.get("_hint", "")
            vals = {k: v for k, v in obs.ram_facts.items() if not k.startswith("_")}
            ram = ("\nGROUND-TRUTH STATE read from the game's memory (trust this over "
                   f"your reading of the pixels): {vals}. {hint}")
        manual = ""
        if obs.manual:
            manual = ("\nGAME MANUAL (authoritative reference for controls, buttons, "
                      "and mechanics — follow it over any assumption):\n"
                      f"\"\"\"\n{obs.manual[:4000]}\n\"\"\"\n")
        return (
            f"Goal: {obs.goal}\n"
            f"{manual}"
            f"This is decision #{obs.step}. "
            f"Read the image file at {obs.image_path} "
            f"(a {obs.width}x{obs.height} SNES screenshot of the current frame)."
            f"{ram}"
            f"{hist}\n"
            "Decide the next controller input and output ONLY the JSON object."
        )

    def decide(self, obs: Observation) -> Action:
        cmd = [
            self.claude_bin, "-p", self._build_prompt(obs),
            "--allowedTools", "Read",
            "--output-format", "json",
            "--json-schema", self.schema_arg,
            "--model", self.model,
            "--append-system-prompt", _SYSTEM,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return Action.noop("timeout waiting for claude CLI")

        if proc.returncode != 0:
            return Action.noop(f"claude CLI error rc={proc.returncode}: {proc.stderr[:120]}")

        return self._parse(proc.stdout)

    @staticmethod
    def _parse(stdout: str) -> Action:
        try:
            env = json.loads(stdout)
        except json.JSONDecodeError:
            return Action.noop("unparseable CLI envelope")
        result = env.get("result", "")
        if env.get("is_error"):
            return Action.noop(f"CLI is_error: {str(result)[:120]}")
        # result should be the JSON object (string) per --json-schema
        payload = result
        if isinstance(result, str):
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                return Action.noop(f"non-JSON result: {result[:120]}")
        if not isinstance(payload, dict):
            return Action.noop("result not an object")
        try:
            action = Action(
                buttons=list(payload.get("buttons", [])),
                hold_frames=int(payload.get("hold_frames", 6)),
                reason=str(payload.get("reason", "")),
                screen_text=str(payload.get("screen_text", "")),
            )
        except (TypeError, ValueError):
            return Action.noop("bad action fields")
        return action.sanitized()
