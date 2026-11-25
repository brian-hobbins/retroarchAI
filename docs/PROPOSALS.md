# Proposals / design directions (not yet implemented)

Ideas under consideration. These are **proposed**, not decided — `DECISIONS.md` holds
what's committed, `PROJECT_LOG.md` holds what's built. Revisit before implementing.

## 1. Frontier-brain Orchestrator (ON HOLD — revisit)

**Idea:** promote the Orchestrator from cheap Python control logic to *the* decision-maker,
powered by a frontier high-reasoning model (Opus-class). Everyone else reports to it:
- Scout → fused perception (screenshot + RAM) as a text report.
- Memory (kb/lessons) → relevant knowledge.
- Reward/Reflector → how it's doing.
- The brain reasons over all of it and decides **whether and how to act** (including "do
  nothing / wait"), then sends a message/directive to the **Executor** (the hands).

**Why it's better:** replaces the brittle heuristic "when to re-plan" ladder (no-feedback
counters, stall timers) with actual reasoning. Those signals become *inputs the brain reads*,
not the decision itself. Merges today's Planner into the Orchestrator.

**The load-bearing constraint — cadence/cost.** Today's ~7–12%-of-naive token cost comes from
calling the strong model *rarely* + zero-token code on the common path. A frontier brain
reasoning every frame = an Opus call per decision = ~10–50× cost + much slower. **Not viable
as-is.** Fix: the brain controls its own cadence — its output is `{action/directive,
wake_conditions, horizon}` (e.g. "walk north ≤10 ticks; wake me if HP drops, a battle/menu
opens, the scene changes, or I'm blocked"). The cheap Executor runs deterministically between
brain calls and pings the brain only when a wake-condition fires. The old why-ladder doesn't
die — it becomes the *wake mechanism the brain configures*.

**Code delta:** merge Planner into Orchestrator-as-model; Scout stays a reporter (brain reasons
text-only = cheaper); brain emits action + wake-conditions + horizon; Executor becomes the
actuator that evaluates wake-conditions and reports back; reward/feedback/RAM become structured
reports *into* the brain.

**Open decisions:** (a) model — Opus on events vs. Sonnet in the hub role; (b) Executor — keep
it able to make cheap reflexive micro-moves (e.g. battle-menu timing) under the brain's standing
orders, or make it a pure actuator.

## 2. Reinforced / weighted lessons (pairs with #1)

Close the learning loop: **reward/penalty → credit assignment → weighted lessons → fed back to
the brain.** Turns lessons from a flat text dump into a self-reinforcing symbolic policy (RL over
a symbolic memory, no gradients).

- **Weighted lessons:** `{text, weight, uses, reward_sum}` — weight = "how much has following
  this actually paid off?"
- **Credit assignment:** attribute each tick's reward/penalty to what drove it. Cleanest in #1 —
  the brain *names* the lesson(s) it's acting on, giving exact credit for free; otherwise
  approximate via active directive + in-window lessons. Keep the horizon SHORT (last few ticks);
  don't attempt long-horizon RL.
- **Update:** `reward_sum += tick_reward; uses += 1; weight = running value with decay` (stale
  lessons fade; repeated winners rise; a cited lesson that keeps earning −1 decays fast).
- **Reflector becomes the credit judge:** promote high-weight ("proven"), prune/rewrite
  low-weight or penalty-correlated, and **mint "do-not" rules from penalties** (the missing piece
  in the Whelk fight: "attacking the shell earned a penalty → never attack the shell").
- **Inject by weight, not recency** — feed the brain the top-weighted lessons, not the last-20.
  Structurally fixes the window-crowding bug we hit.

**Caution — garbage-in:** reinforcement only optimizes what reward measures. Today's reward is
exploration-biased ("new text +2"), so reinforcing by it just makes a better *wanderer*. Reward
MUST include objective RAM progress (see #3).

## 3. Objective-progress reward (enables #2 and evaluation)

Today's reward (`reward.py`) is a hand-tuned exploration proxy (new text/area/fact, moved,
stuck). Add **objective, RAM-derived progress**: battle won, HP survived vs. lost, level up,
gil gained, story/area flags. We can now read these (FF6 reader: HP/MP/level, `in_battle`, etc.).
Does double duty: better reinforcement target (#2) AND the success criteria for evals (#4).

## 4. Scenario-based evaluation harness (to measure any of the above)

We're ~80% set up: reproducible **savestates** + an **RAM reader** of objective state. A
`tools/eval.py` would: load a savestate → run the orchestrator headless to a RAM-defined
success/fail → report **success, decisions-to-win, tokens, cost, damage taken**. Deterministic,
repeatable, and gives a real **before/after regression number** (today we can't prove Fix
#1/#2/#3 helped). First eval: **"win the Whelk fight"** from `whelk_battle.state` (kept in the
session scratchpad) — the thing we just spent three fixes on.
