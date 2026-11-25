"""Low-level reusable `claude -p` call that returns parsed JSON + token usage.

Shared by the Planner and the Executor so each can use its own model, prompt,
and schema, and so the orchestrator can account tokens per role.

Image handling: the screenshot is passed INLINE via stream-json stdin (not the
Read tool). This avoids the Read-tool extra turn and tool schemas, cutting the
per-call context from ~44k to ~20k tokens (measured). No tools are enabled.
"""

from __future__ import annotations

import base64
import json
import subprocess
from typing import Optional, Tuple

Usage = dict


def _empty_usage(ok: bool, error: Optional[str]) -> Usage:
    return {"ok": ok, "error": error, "input_ctx": 0, "output": 0, "cost": 0.0}


def _parse_payload(result) -> Optional[dict]:
    payload = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def run_claude_json(
    prompt: str,
    system: str,
    schema: dict,
    model: str,
    image_path: Optional[str] = None,
    timeout: int = 60,
    claude_bin: str = "claude",
) -> Tuple[Optional[dict], Usage]:
    """Run one headless claude call. Returns (payload|None, usage).

    With image_path, the screenshot is attached inline via stream-json.
    """
    cmd = [
        claude_bin, "-p",
        "--output-format", "stream-json", "--verbose",
        "--json-schema", json.dumps(schema),
        "--model", model,
        "--append-system-prompt", system,
    ]
    stdin_data = None
    if image_path:
        cmd += ["--input-format", "stream-json"]
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        msg = {"type": "user", "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt},
        ]}}
        stdin_data = json.dumps(msg) + "\n"
    else:
        cmd[1:1] = [prompt]  # put prompt right after claude_bin for text mode

    try:
        proc = subprocess.run(cmd, input=stdin_data, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, _empty_usage(False, "timeout")
    if proc.returncode != 0:
        return None, _empty_usage(False, f"rc={proc.returncode}: {proc.stderr[:120]}")

    # stream-json: one JSON object per line; find the terminal "result" event.
    result_event = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result_event = ev
    if result_event is None:
        return None, _empty_usage(False, "no result event")

    u = result_event.get("usage", {}) or {}
    usage: Usage = {
        "ok": not result_event.get("is_error"),
        "error": None if not result_event.get("is_error") else str(result_event.get("result"))[:120],
        "input_ctx": (u.get("cache_creation_input_tokens", 0)
                      + u.get("cache_read_input_tokens", 0)
                      + u.get("input_tokens", 0)),
        "output": u.get("output_tokens", 0),
        "cost": result_event.get("total_cost_usd", 0.0),
    }
    return _parse_payload(result_event.get("result", "")), usage
