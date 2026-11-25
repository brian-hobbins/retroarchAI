"""Orchestrator: runs the Planner (rare, strong model) + Executor (frequent,
cheap/code) as a team, deciding WHEN to re-plan from cheap signals (RAM position
stall, scene change, directive completion, periodic). Accounts tokens per role.

Drop-in for a provider: `.decide(obs) -> Action`. Exposes `.directive`,
`.last_agent`, and `.stats` for display.
"""

from __future__ import annotations

from providers.base import Action
from .planner import Planner, Directive
from .executor import Executor, _POS_THRESHOLD
from .scout import Scout, Scene
from .knowledge import KnowledgeBase
from .reward import RewardTracker
from .reflector import Reflector

MAX_TICKS = 6        # re-plan after this many ticks with no MEANINGFUL progress
HARD_MAX = 14        # hard cap: re-plan at least this often regardless
STALL_TICKS = 4      # no position change this many ticks (nav gets time to detour)
DIALOGUE_REREAD = 3  # in a conversation, re-read + respond at least this often
NOFEEDBACK_TICKS = 3  # pressing buttons with NO observable effect this many ticks
                      # -> the action is doing nothing; abandon it and try else
REFLECT_EVERY = 15   # run the Reflector (learning step) this many ticks apart
CONDENSE_EVERY = 120  # condense lessons this many ticks apart (cap token growth)
CONDENSE_MIN = 30     # ...only once there are at least this many lessons
PROGRESS_REWARD = 1.0  # a tick scoring >= this counts as meaningful progress


class Orchestrator:
    def __init__(self, planner_model="sonnet", executor_model="haiku",
                 scout_model="sonnet", timeout=60,
                 knowledge: KnowledgeBase = None, lessons: KnowledgeBase = None,
                 reflect=True, controls: dict = None, demos: KnowledgeBase = None,
                 story=None):
        self.planner = Planner(model=planner_model, timeout=timeout)
        self.executor = Executor(model=executor_model, timeout=timeout, controls=controls)
        self.scout = Scout(model=scout_model, timeout=timeout)
        self.reflector = Reflector(model=planner_model, timeout=timeout)
        self.scene: Scene = None
        self.kb = knowledge
        self.lessons = lessons
        # Raw human input (demonstrations + coaching) is parked here, NOT in lessons:
        # it's distilled by the Reflector into concise lessons. This keeps lessons.json
        # from being flooded by verbose scene-dumps and one-shot/sarcastic coach lines.
        self.demos = demos
        # narrative/lore log (markdown); consulted only when the Planner asks
        self.story = story
        self._consult_story = False
        self.ticks_since_condense = 0
        self.reward = RewardTracker()
        self.reflect_enabled = reflect
        self.ticks_since_reflect = 0
        self.last_new_facts = []
        self.last_new_lessons = []
        self.total_ticks = 0
        self.directive = None
        self.ticks = 0
        self.no_progress = 0
        self.prev_xy = None
        self.stall = 0
        self.prev_in_dialogue = False
        self.prev_dsig = None
        # feedback detector: did the last action actually change anything?
        self.prev_sig = None
        self.prev_pressed = False
        self.no_feedback = 0
        self.last_agent = ""
        self.last_replan_why = ""
        self.last_screen_text = ""
        # live human coaching: typed hints that override the next decision and are
        # saved as lessons; and notes learned by watching a human play.
        self.coach_queue = []
        self.last_coach = ""
        self.stats = {
            "planner_calls": 0, "executor_calls": 0, "code_ticks": 0,
            "reflect_calls": 0, "scout_calls": 0,
            "planner_tokens": 0, "executor_tokens": 0, "reflect_tokens": 0,
            "scout_tokens": 0,
            "cost": 0.0,
        }

    def decide(self, obs) -> Action:
        ram = obs.ram_facts or {}
        in_dialogue = bool(ram.get("in_conversation")) or "dialogue_cursor_index" in ram
        xy = (ram.get("avatar_x"), ram.get("avatar_y"))
        dsig = ram.get("_dialogue_sig")

        # track position stall for movement directives
        moving = self.directive is not None and self.directive.kind in ("walk", "goto")
        if moving and xy[0] is not None and xy == self.prev_xy:
            self.stall += 1
        elif xy != self.prev_xy:
            self.stall = 0

        # feedback detector: compare an observable "state signature" to last tick.
        # For interact, position jitter from facing-nudges is NOT feedback — only a
        # dialogue/text box opening (or its text changing) counts; otherwise the
        # Avatar's position matters. If a tick pressed buttons but the signature did
        # not move, that action produced nothing.
        kind_now = self.directive.kind if self.directive else ""
        sig = self._feedback_sig(kind_now, xy, in_dialogue, dsig)
        if self.prev_sig is not None and self.prev_pressed and self.directive is not None:
            self.no_feedback = self.no_feedback + 1 if sig == self.prev_sig else 0

        # live coaching from the human watching: drain any typed hints. They
        # override the next decision AND are saved as lessons (reinforced learning).
        coach_note = ""
        if self.coach_queue:
            msgs, self.coach_queue = self.coach_queue, []
            coach_note = " ".join(msgs)
            self.last_coach = coach_note
            # Coaching steers THIS decision now (coach_note -> Planner override below),
            # and is parked as a demo for the Reflector to distill — not raw-persisted
            # as a permanent lesson (that let transient/sarcastic lines become a bias).
            if self.demos:
                self.demos.add_many([f"Human coached: {m}" for m in msgs], obs.step)

        why = ""
        if coach_note:
            why = "coach"             # a human just spoke -> replan and obey now
        elif self.directive is None:
            why = "init"
        elif self._done(ram):
            why = "done"
        elif in_dialogue != self.prev_in_dialogue:
            why = "scene-change"
        elif in_dialogue and self.ticks >= DIALOGUE_REREAD:
            why = "dialogue-reread"   # re-read the conversation and respond
        elif self.no_feedback >= NOFEEDBACK_TICKS:
            why = "no-feedback"       # action is doing nothing -> try something else
        elif self.no_progress >= MAX_TICKS or self.ticks >= HARD_MAX:
            why = "periodic"
        elif self.stall >= STALL_TICKS:
            why = "stuck"

        nf_flag = why == "no-feedback"

        new_facts_added = 0
        if why:
            # Perception first: the Scout (eyes) looks at the frame and produces a
            # fresh scene report the whole team shares. Then the Planner reasons over
            # that report text-only. Cached between replans via self.scene.
            s, su = self.scout.look(obs)
            if s.ok or self.scene is None:
                self.scene = s
            self.stats["scout_calls"] += 1
            self.stats["scout_tokens"] += su.get("input_ctx", 0) + su.get("output", 0)
            self.stats["cost"] += su.get("cost", 0.0)
            # the Scout is the ONLY agent that transcribes on-screen text (OCR);
            # take dialogue/menu text from its report, not from Planner/Executor.
            if self.scene is not None and self.scene.text.strip():
                self.last_screen_text = self.scene.text
            scene_brief = self.scene.brief() if self.scene else ""
            obs.scene = scene_brief
            kb_summary = self.kb.summary() if self.kb else ""
            lessons_summary = self.lessons.summary(limit=20) if self.lessons else ""
            # only inject the (token-heavy) story log when the Planner asked last turn
            story_text = (self.story.tail() if (self._consult_story and self.story)
                          else "")
            trouble = self._trouble_note(why, xy)
            self.directive, u, new_facts, self._consult_story = self.planner.plan(
                obs, kb_summary, lessons_summary, trouble=trouble, scene=scene_brief,
                coach=coach_note, story=story_text)
            # route tagged facts: clue/mechanics -> notebook; story/lore -> story log
            self.last_new_facts = []
            kb_facts = [f["text"] for f in new_facts if f.get("tag") in ("clue", "mechanics")]
            story_facts = [f for f in new_facts if f.get("tag") in ("story", "lore")]
            if self.kb and kb_facts:
                before = self.kb.count()
                self.kb.add_many(kb_facts, obs.step)
                new_facts_added = self.kb.count() - before
                if new_facts_added:
                    self.last_new_facts = kb_facts
            if self.story and story_facts:
                for f in story_facts:
                    self.story.add(f["text"], f["tag"], obs.step)
            self.stats["planner_calls"] += 1
            self.stats["planner_tokens"] += u.get("input_ctx", 0) + u.get("output", 0)
            self.stats["cost"] += u.get("cost", 0.0)
            self.ticks = 0
            self.stall = 0
            self.no_progress = 0
            self.no_feedback = 0
            self.last_replan_why = why

        if not obs.scene and self.scene:
            obs.scene = self.scene.brief()   # share latest perception with executor
        if self.lessons:
            # hand the Executor the hard-won lessons too, so the agent actually
            # pressing buttons (e.g. navigating a battle menu) knows them — not just
            # the Planner. Battle-relevant lessons float to the top via recency.
            obs.lessons = self.lessons.summary(limit=14)
        action, u, agent = self.executor.act(self.directive, obs)
        if agent.startswith("code"):
            self.stats["code_ticks"] += 1
        else:
            self.stats["executor_calls"] += 1
            self.stats["executor_tokens"] += u.get("input_ctx", 0) + u.get("output", 0)
            self.stats["cost"] += u.get("cost", 0.0)

        self.last_agent = agent
        self.ticks += 1
        self.total_ticks += 1
        self.prev_xy = xy
        self.prev_in_dialogue = in_dialogue
        self.prev_dsig = dsig
        # remember this tick's signature + whether we pressed, for next tick's
        # feedback check (sig reflects the state the LAST action produced).
        self.prev_sig = sig
        self.prev_pressed = bool(action.buttons)

        # --- reward (free, code) ---
        self.reward.observe(
            obs.step, self.directive.kind if self.directive else "-",
            action.buttons, xy, self.last_screen_text, new_facts_added, moving,
            no_feedback=nf_flag)
        # extend the leash while making meaningful progress; else count toward replan
        if self.reward.last >= PROGRESS_REWARD:
            self.no_progress = 0
        else:
            self.no_progress += 1

        # --- periodic reflection (the learning step) ---
        self.ticks_since_reflect += 1
        self.last_new_lessons = []
        if (self.reflect_enabled and self.lessons
                and self.ticks_since_reflect >= REFLECT_EVERY):
            self.ticks_since_reflect = 0
            # distill recent human demos/coaching into generalized lessons too
            demos_text = self.demos.summary(limit=25) if self.demos else ""
            new_lessons, ru = self.reflector.reflect(
                self.reward.trajectory_text(),
                self.lessons.summary(limit=20), self.reward.total,
                demos_text=demos_text)
            self.stats["reflect_calls"] += 1
            self.stats["reflect_tokens"] += ru.get("input_ctx", 0) + ru.get("output", 0)
            self.stats["cost"] += ru.get("cost", 0.0)
            if new_lessons:
                before = self.lessons.count()
                self.lessons.add_many(new_lessons, obs.step)
                if self.lessons.count() > before:
                    self.last_new_lessons = new_lessons

        # --- periodic condensation: merge/dedupe lessons to cap token growth ---
        self.ticks_since_condense += 1
        if (self.reflect_enabled and self.lessons
                and self.ticks_since_condense >= CONDENSE_EVERY
                and self.lessons.count() >= CONDENSE_MIN):
            self.ticks_since_condense = 0
            before = self.lessons.count()
            condensed, cu = self.reflector.condense(
                self.lessons.summary(limit=self.lessons.count()))
            self.stats["reflect_calls"] += 1
            self.stats["reflect_tokens"] += cu.get("input_ctx", 0) + cu.get("output", 0)
            self.stats["cost"] += cu.get("cost", 0.0)
            # only accept a real reduction that keeps a sensible amount of knowledge
            if condensed and len(condensed) < before and len(condensed) >= min(10, before):
                self.lessons.replace_all(condensed, obs.step)

        return action

    # ---- live human input: coaching + learning by watching ----

    def add_coach(self, msg: str) -> None:
        """Queue a hint typed by the human watching. It overrides the next decision
        (see decide()) and is parked as a demo the Reflector distills into a lesson —
        not raw-persisted, so one-shot/sarcastic lines don't become a forever-bias."""
        msg = (msg or "").strip()
        if msg:
            self.coach_queue.append(msg)

    def human_look(self, obs, buttons=None) -> None:
        """While a HUMAN plays: the Scout keeps looking so the SCENE and DIALOGUE
        panels stay live (and dialogue text, which only the Scout transcribes, keeps
        updating). If the human just made a deliberate move (`buttons`), also record a
        DEMO for the Reflector to distill into a lesson. Runs off the main thread."""
        try:
            scene, su = self.scout.look(obs)
        except Exception:
            return
        self.stats["scout_calls"] += 1
        self.stats["scout_tokens"] += su.get("input_ctx", 0) + su.get("output", 0)
        self.stats["cost"] += su.get("cost", 0.0)
        if scene is not None and scene.ok:
            self.scene = scene            # share the human-time perception with the team
            if scene.text.strip():
                self.last_screen_text = scene.text   # keep DIALOGUE live
        if buttons and self.demos and scene is not None:
            # compact situation tag (summary + key RAM), NOT the whole verbose report
            summ = (scene.summary or "").replace("\n", " ")[:120]
            ram = (scene.ram or "")[:80]
            situation = (summ + (f" [{ram}]" if ram else "")).strip()
            self.demos.add_many(      # add_many persists to disk (add alone doesn't)
                [f"Human demo: seeing {situation}, pressed {'+'.join(buttons)}"], obs.step)

    @staticmethod
    def _feedback_sig(kind, xy, in_dialogue, dsig):
        if kind == "interact":
            # facing-nudges jitter position; only a text/dialogue box is real feedback
            return ("interact", in_dialogue, dsig)
        return (xy, in_dialogue, dsig)

    def _trouble_note(self, why, xy) -> str:
        """Tell the Planner when its last approach produced no feedback, and warn
        about targets already found inert (non-interactable) at this spot."""
        parts = []
        d = self.directive
        if why == "no-feedback" and d is not None:
            parts.append(
                f'Your directive "{d.short()}" produced NO observable change after '
                f"{self.no_feedback} attempts (no dialogue/text opened, no progress). "
                "It did NOT work — what you targeted is likely not interactable or not "
                "reachable from here (e.g. a window, scenery, a wall). Do something "
                "DIFFERENT: a different target or direction, or move on. Do NOT repeat it."
            )
            if d.kind == "interact" and xy[0] is not None:
                self.executor.nav.map.mark_inert(xy, d.direction)
        inert = self.executor.nav.map.nearby_inert(xy) if xy[0] is not None else []
        if inert:
            parts.append(
                f"Already found non-interactable from here: facing {'/'.join(inert)} "
                "did nothing before — don't retry those directions to interact."
            )
        return " ".join(parts)

    def _done(self, ram) -> bool:
        d = self.directive
        if d is None:
            return False
        if d.kind == "goto" and d.target_x is not None and "avatar_x" in ram:
            # match the executor's per-axis arrival so we replan the instant we arrive
            return (abs(ram["avatar_x"] - d.target_x) <= _POS_THRESHOLD
                    and abs(ram["avatar_y"] - d.target_y) <= _POS_THRESHOLD)
        return False

    def summary(self) -> str:
        s = self.stats
        kb = f" notes:{self.kb.count()}" if self.kb else ""
        le = f" lessons:{self.lessons.count()}" if self.lessons else ""
        tok = (s["planner_tokens"] + s["executor_tokens"] + s["reflect_tokens"]
               + s["scout_tokens"]) // 1000
        return (f"scout:{s['scout_calls']} plan:{s['planner_calls']} "
                f"refl:{s['reflect_calls']} code:{s['code_ticks']} | "
                f"{self.reward.summary()} | {tok}k tok ${s['cost']:.2f}{kb}{le}")
