"""Per-game persistent knowledge base — the Archivist's memory.

Durable facts the agent learns (clues, NPCs, quest steps, locations, riddle
hints) are saved to disk per game and reloaded every run, so knowledge
accumulates across sessions and the agent can use it to solve puzzles and
advance the story. Notes are written by the Planner (it already sees the screen
on scene changes, so no extra LLM calls) and read back into its prompt.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import List


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


class KnowledgeBase:
    def __init__(self, game_dir: str, max_facts: int = 300,
                 filename: str = "kb.json", game_key: str = ""):
        """Per-game store. Files live in <game_dir>/knowledge/ so everything
        specific to one game (knowledge, lessons, journals, notes) stays inside
        that game's own folder next to its ROM."""
        self.game_key = game_key or os.path.basename(os.path.normpath(game_dir))
        self.dir = os.path.join(game_dir, "knowledge")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, filename)
        self.max_facts = max_facts
        self.facts: List[dict] = []
        self._norms = set()
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self.path):
            try:
                with open(self.path) as f:
                    data = json.load(f)
                self.facts = data.get("facts", [])
                self._norms = {_norm(f["text"]) for f in self.facts if f.get("text")}
            except (OSError, json.JSONDecodeError, KeyError):
                self.facts, self._norms = [], set()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"game": self.game_key, "facts": self.facts}, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, text: str, step: int = 0) -> bool:
        text = text.strip()
        if not text:
            return False
        n = _norm(text)
        if n in self._norms:
            return False
        # skip if it's a near-duplicate substring of an existing fact
        for existing in self._norms:
            if n in existing or existing in n:
                return False
        self.facts.append({"text": text[:300], "step": step, "ts": int(time.time())})
        self._norms.add(n)
        if len(self.facts) > self.max_facts:
            self.facts = self.facts[-self.max_facts:]
            self._norms = {_norm(f["text"]) for f in self.facts}
        return True

    def add_many(self, items, step: int = 0) -> int:
        added = sum(1 for t in items if isinstance(t, str) and self.add(t, step))
        if added:
            self._save()
        return added

    def summary(self, max_chars: int = 1600, limit: int = 40) -> str:
        """Compact bullet list of what's been learned, for the planner prompt."""
        if not self.facts:
            return "(nothing learned yet)"
        lines, total = [], 0
        for f in self.facts[-limit:]:
            line = f"- {f['text']}"
            total += len(line) + 1
            lines.append(line)
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def replace_all(self, texts, step: int = 0) -> None:
        """Replace the whole set (used when the Reflector condenses lessons)."""
        self.facts, self._norms = [], set()
        for t in texts:
            if isinstance(t, str):
                self.add(t, step)
        self._save()

    def count(self) -> int:
        return len(self.facts)


class StoryLog:
    """Narrative / lore log written as markdown in the kb dir (knowledge/story.md).

    Story and lore the Planner tags go here instead of the notebook, so the prompt
    isn't bloated with narration every turn. It is consulted only on request (when a
    puzzle or hidden object might need story context). Each line carries its tag.
    """

    def __init__(self, game_dir: str):
        self.dir = os.path.join(game_dir, "knowledge")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "story.md")
        self._seen = set()
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        self._seen.add(_norm(line))
            except OSError:
                pass

    def add(self, text: str, tag: str = "story", step: int = 0) -> bool:
        text = text.strip()
        if not text:
            return False
        key = _norm(text)
        if key in self._seen:
            return False
        self._seen.add(key)
        new_file = not os.path.isfile(self.path)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write("# Story & lore log\n\n"
                            "Narrative, lore and NPC dialogue the AI noted. Consulted "
                            "for clues to puzzles / hidden things.\n\n")
                f.write(f"- `[{tag}]` {text}\n")
        except OSError:
            return False
        return True

    def tail(self, n: int = 40, max_chars: int = 2000) -> str:
        """Recent entries, for injecting when the Planner asks to consult the story."""
        if not os.path.isfile(self.path):
            return ""
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                lines = [l.rstrip() for l in f if l.startswith("- ")]
        except OSError:
            return ""
        txt = "\n".join(lines[-n:])
        return txt[-max_chars:]

    def count(self) -> int:
        return len(self._seen)
