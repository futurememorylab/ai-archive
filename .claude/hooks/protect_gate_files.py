#!/usr/bin/env python3
"""PreToolUse hook: block agent edits to human-owned gate files (ADR 0129).

The protected set is every path pattern in `.github/CODEOWNERS`, so there is
one source of truth. The one exception is `.erosion-baseline.json` when the
edit only lowers its values: the erosion gate asks for exactly that after an
improvement. Exit code 2 blocks the tool call and shows stderr to the agent.

This guards Edit / Write / MultiEdit / NotebookEdit only. CODEOWNERS review
on `main` is the backstop for anything else (a shell `sed`, for example).
Stdlib only: it runs before the venv exists.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

LOWER_ONLY = ".erosion-baseline.json"


def codeowners_patterns(root: Path) -> list[str]:
    """Path patterns from .github/CODEOWNERS (first token of each rule line)."""
    path = root / ".github" / "CODEOWNERS"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.split()[0])
    return out


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Translate a CODEOWNERS (gitignore-style) pattern to a regex on repo paths."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    rx = ""
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            rx += ".*"
            i += 2
        elif body[i] == "*":
            rx += "[^/]*"
            i += 1
        elif body[i] == "?":
            rx += "[^/]"
            i += 1
        else:
            rx += re.escape(body[i])
            i += 1
    prefix = "^" if anchored else "^(?:.*/)?"
    # A pattern matches the path itself or anything under it (a directory).
    return re.compile(prefix + rx + "(?:/.*)?$")


def owned(rel: str, patterns: list[str]) -> bool:
    """True when the repo-relative path matches any CODEOWNERS pattern."""
    return any(_pattern_regex(p).match(rel) for p in patterns)


def _new_content(tool: str, tool_input: dict, current: str) -> str | None:
    """The file's content after the edit, or None if it can't be worked out."""
    if tool == "Write":
        return tool_input.get("content")
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    text = current
    for e in edits or []:
        old, new = e.get("old_string", ""), e.get("new_string", "")
        if not old or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def only_lowers_baseline(tool: str, tool_input: dict, path: Path) -> bool:
    """True when an edit to the erosion baseline keeps its keys and lowers or keeps values."""
    try:
        current = path.read_text(encoding="utf-8")
        before = json.loads(current)
        after_text = _new_content(tool, tool_input, current)
        after = json.loads(after_text) if after_text is not None else None
    except (OSError, ValueError):
        return False
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    if set(before) != set(after):
        return False
    try:
        return all(float(after[k]) <= float(before[k]) for k in before)
    except (TypeError, ValueError):
        return False


def main() -> int:
    """Hook entry point: read the tool call from stdin, exit 2 to block it."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".").resolve()
    path = Path(target)
    if not path.is_absolute():
        path = Path(event.get("cwd") or root) / path
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return 0  # outside the repo

    if not owned(rel, codeowners_patterns(root)):
        return 0
    if rel == LOWER_ONLY and only_lowers_baseline(tool, tool_input, root / rel):
        return 0

    print(
        f"BLOCKED: {rel} is a human-owned quality-gate file (.github/CODEOWNERS, ADR 0129). "
        "Don't edit it or work around this. Stop and ask Peter: say which change you "
        "wanted to make to this file and why.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
