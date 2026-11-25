"""Executor agent: the hands. Carries out a Directive each tick.

Hybrid: deterministic code primitives for things we have RAM for (goto by
position, dialogue by cursor) — near-zero tokens — and a cheap Haiku LLM only for
ambiguous directives (explore/freeform).
"""

from __future__ import annotations

from providers.base import Action
from providers.cli_call import run_claude_json
from .navigator import Navigator, THRESH as _POS_THRESHOLD

ZERO = {"ok": True, "error": None, "input_ctx": 0, "output": 0, "cost": 0.0}

_EXEC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["buttons", "hold_frames", "reason", "screen_text"],
    "properties": {
        "buttons": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "hold_frames": {"type": "integer", "minimum": 1, "maximum": 120},
        "reason": {"type": "string", "maxLength": 140},
        "screen_text": {"type": "string", "maxLength": 400},
    },
}

_EXEC_SYSTEM = (
    "You are the EXECUTOR for an agent playing a SNES game — you are the character, "
    "think in first person. You are given a short DIRECTIVE and the current screen; "
    "press the controller buttons that make progress on it, following the manual's "
    "controls. Keep 'reason' to one short first-person clause. Copy any on-screen "
    "text into 'screen_text' verbatim (or empty). Output ONLY the JSON object."
)


# Which SNES button performs each logical action. Defaults suit a standard SNES
# JRPG (A = confirm/talk/advance). Per-game overrides come from game.json
# "controls" (e.g. Ultima VII uses Y to interact and B to advance/speak).
DEFAULT_CONTROLS = {"interact": "A", "advance": "A", "cancel": "B", "menu": "X"}


class Executor:
    def __init__(self, model: str = "haiku", timeout: int = 60, controls: dict = None):
        self.model = model
        self.timeout = timeout
        self.controls = {**DEFAULT_CONTROLS, **(controls or {})}
        self.nav = Navigator()
        self._nav_for = None   # id of the directive the navigator is tracking
        self._interact_phase = 0

    def _nav_reset_if_new(self, directive):
        if id(directive) != self._nav_for:
            self.nav.reset()
            self._interact_phase = 0
            self._nav_for = id(directive)

    def act(self, directive, obs):
        """Return (Action, usage, agent_label). Only 'freeform' costs tokens."""
        ram = obs.ram_facts or {}
        k = directive.kind
        pos = (ram.get("avatar_x"), ram.get("avatar_y"))

        if k == "walk" and directive.direction:
            heading = [b for b in directive.direction.split("+")
                       if b in ("UP", "DOWN", "LEFT", "RIGHT")]
            if heading:
                self._nav_reset_if_new(directive)
                btns = self.nav.step(pos, heading) if pos[0] is not None else heading
                detour = " (around wall)" if self.nav.detour_dir else ""
                return (Action(buttons=btns, hold_frames=24,
                               reason=f"[code] walk {'+'.join(btns)}{detour} (heading {directive.direction})"),
                        dict(ZERO), "code:walk")

        if k == "interact":
            # You interact with what you FACE, and you face your last-moved
            # direction. So first step toward/face the target, THEN press Y.
            self._nav_reset_if_new(directive)
            face = [b for b in (directive.direction or "").split("+")
                    if b in ("UP", "DOWN", "LEFT", "RIGHT")]
            if face and self._interact_phase == 0:
                self._interact_phase = 1
                return (Action(buttons=face, hold_frames=6,
                               reason=f"[code] face {directive.direction} to talk"),
                        dict(ZERO), "code:interact")
            self._interact_phase = 0   # alternate back so repeated ticks retry facing
            btn = self.controls["interact"]
            return (Action(buttons=[btn], hold_frames=6, reason=f"[code] interact ({btn})"),
                    dict(ZERO), "code:interact")

        if k == "goto" and directive.target_x is not None \
                and pos[0] is not None:
            return self._goto(directive, pos)

        if k == "dialogue":
            return self._dialogue(directive, ram)

        # last resort: the expensive path (freeform, or goto without RAM position)
        return self._llm(directive, obs)

    # ---- primitives ----

    def _goto(self, directive, pos):
        self._nav_reset_if_new(directive)
        primary = self.nav.toward(pos, directive.target_x, directive.target_y)
        if not primary:
            return (Action(buttons=[], hold_frames=2,
                           reason=f"[code] arrived near ({directive.target_x},{directive.target_y})"),
                    dict(ZERO), "code:goto")
        btns = self.nav.step(pos, primary)
        detour = " (around wall)" if self.nav.detour_dir or (set(btns) - set(primary)) else ""
        return (Action(buttons=btns, hold_frames=20,
                       reason=f"[code] goto ({directive.target_x},{directive.target_y}) via {'+'.join(btns)}{detour}"),
                dict(ZERO), "code:goto")

    def _dialogue(self, directive, ram):
        cur = ram.get("dialogue_cursor_index")
        tgt = directive.target_keyword_index
        if cur is not None and tgt is not None and cur != tgt:
            return (Action(buttons=["DOWN"], hold_frames=4,
                           reason=f"[code] move cursor {cur}->{tgt}"),
                    dict(ZERO), "code:dialogue")
        # at target, or no cursor yet: press the advance button to speak/advance text
        adv = self.controls["advance"]
        return (Action(buttons=[adv], hold_frames=5,
                       reason=f"[code] advance/select ({adv}) (cursor={cur})"),
                dict(ZERO), "code:dialogue")

    # ---- LLM fallback ----

    def _llm(self, directive, obs):
        ram = ""
        if obs.ram_facts:
            vals = {k: v for k, v in obs.ram_facts.items() if not k.startswith("_")}
            ram = f"\nGROUND-TRUTH (memory): {vals}. {obs.ram_facts.get('_hint','')}"
        scene = f"\n{obs.scene}\n" if getattr(obs, "scene", "") else ""
        manual = ""
        if obs.manual:
            manual = ("\nMANUAL (controls):\n\"\"\"\n" + obs.manual[:2500] + "\n\"\"\"\n")
        prompt = (
            f"Directive: {directive.instruction}\n"
            f"{manual}"
            f"{scene}"
            f"The current {obs.width}x{obs.height} screenshot is attached."
            f"{ram}\n"
            "Press the buttons that advance the directive. JSON only."
        )
        payload, usage = run_claude_json(
            prompt, _EXEC_SYSTEM, _EXEC_SCHEMA, self.model,
            image_path=obs.image_path, timeout=self.timeout,
        )
        if not payload:
            return (Action.noop(f"exec: {usage.get('error') or 'no action'}"),
                    usage, f"llm:{self.model}")
        act = Action(
            buttons=list(payload.get("buttons", [])),
            hold_frames=int(payload.get("hold_frames", 6)),
            reason=str(payload.get("reason", "")),
            screen_text=str(payload.get("screen_text", "")),
        ).sanitized()
        return act, usage, f"llm:{self.model}"
