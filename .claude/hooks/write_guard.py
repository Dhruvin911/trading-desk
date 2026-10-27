#!/usr/bin/env python3
"""PreToolUse guard for file writes during desk cycles.

Only active when the runner sets DESK_AGENT=1, so it does not get in your way
when you open Claude Code in this folder yourself. During a cycle the agents
may write notes, run logs and caches, but never the config, the on/off state,
the journal, the hooks or the agent definitions.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()
WRITABLE = [ROOT / "desk" / "notes", ROOT / "desk" / "runs", ROOT / "desk" / "cache"]


def allowed(path):
    target = (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    return any(target == d or d in target.parents for d in WRITABLE)


def main():
    if os.environ.get("DESK_AGENT") != "1":
        return
    payload = json.load(sys.stdin)
    inp = payload.get("tool_input") or {}
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    if not path or not allowed(path):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "During desk cycles, writes are limited to desk/notes, desk/runs and desk/cache.",
        }}))


if __name__ == "__main__":
    main()
