#!/usr/bin/env python3
"""Stop hook: a session can't finish while the fast gates are red.

When Python files changed relative to the merge-base with origin/main
(committed, staged, unstaged or untracked), run ruff check and ruff format
--check on them, lint-imports, the erosion gate, and pytest on the test
files that match the changed modules. On failure, exit 2 so the agent sees
the output and keeps working.

- Respects `stop_hook_active`, so a second stop attempt in a row ends the
  session instead of looping.
- Budget ~60 s: pytest gets whatever the static gates left, and is skipped
  (and said so) when that isn't enough.
- Never starts the dev server or touches CatDV; walkthrough tests are not
  selected.
- Does nothing when no Python changed.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BUDGET_S = 60
MIN_PYTEST_S = 10
BASE_REFS = ("origin/main", "main")


def _run(cmd: list[str], root: Path, timeout: float | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except FileNotFoundError as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout + p.stderr).strip()


def _git_lines(root: Path, *args: str) -> list[str]:
    code, out = _run(["git", *args], root)
    return out.splitlines() if code == 0 and out else []


def merge_base(root: Path) -> str | None:
    """The merge-base with the first base ref that resolves, or None."""
    for ref in BASE_REFS:
        lines = _git_lines(root, "merge-base", ref, "HEAD")
        if lines:
            return lines[0]
    return None


def changed_python(root: Path, base: str) -> list[str]:
    """Repo-relative .py files changed since `base` that still exist."""
    names = set(_git_lines(root, "diff", "--name-only", base, "--", "*.py"))
    names |= set(_git_lines(root, "ls-files", "--others", "--exclude-standard", "--", "*.py"))
    return sorted(n for n in names if (root / n).is_file())


def matching_tests(root: Path, changed: list[str]) -> list[str]:
    """Changed test files, plus tests/**/test_<module>*.py for changed modules."""
    tests: set[str] = set()
    for rel in changed:
        p = Path(rel)
        if p.parts[0] == "tests":
            if p.name.startswith("test_"):
                tests.add(rel)
            continue
        if p.stem.startswith("__"):
            continue
        for t in (root / "tests").rglob(f"test_{p.stem}*.py"):
            tests.add(t.relative_to(root).as_posix())
    return sorted(t for t in tests if not t.startswith("tests/walkthrough/"))


def _tool(root: Path, name: str) -> str:
    venv = root / ".venv" / "bin" / name
    return str(venv) if venv.exists() else name


def run_gates(root: Path, changed: list[str], base: str) -> tuple[list[str], list[str]]:
    """Run the fast gates. Returns (failure reports, notes)."""
    start = time.monotonic()
    py = _tool(root, "python")
    ruff = _tool(root, "ruff")
    checks = [
        ("ruff check", [ruff, "check", *changed]),
        ("ruff format --check", [ruff, "format", "--check", *changed]),
        ("lint-imports", [_tool(root, "lint-imports")]),
        (
            "erosion gate",
            [
                py,
                "tools/erosion_gate.py",
                "--path",
                "backend",
                "--baseline",
                ".erosion-baseline.json",
                "--max-cc",
                "30",
                "--base-ref",
                base,
            ],
        ),
    ]
    failures: list[str] = []
    notes: list[str] = []
    for label, cmd in checks:
        code, out = _run(cmd, root, timeout=BUDGET_S)
        if code == 127:
            notes.append(f"{label} skipped: {out}")
        elif code != 0:
            failures.append(f"## {label} (exit {code})\n{out[-4000:]}")

    tests = matching_tests(root, changed)
    left = BUDGET_S - (time.monotonic() - start)
    if not tests:
        notes.append("pytest: no matching test files")
    elif left < MIN_PYTEST_S:
        notes.append(f"pytest skipped: the static gates used the ~{BUDGET_S}s budget")
    else:
        code, out = _run([py, "-m", "pytest", "-q", "-x", *tests], root, timeout=left)
        if code == 124:
            notes.append(f"pytest skipped: {len(tests)} test files didn't finish within budget")
        elif code not in (0, 5):  # 5 = no tests collected
            failures.append(f"## pytest {' '.join(tests)} (exit {code})\n{out[-4000:]}")
    return failures, notes


def main() -> int:
    """Hook entry point. Exit 2 with the failures on stderr to keep the agent working."""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    if event.get("stop_hook_active"):
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".").resolve()
    base = merge_base(root)
    if base is None:
        return 0
    changed = changed_python(root, base)
    if not changed:
        return 0

    failures, notes = run_gates(root, changed, base)
    if not failures:
        if notes:
            print("Stop gate passed. " + "; ".join(notes))
        return 0
    print(
        "The fast quality gates are red for the Python files you changed. Fix them before "
        "finishing (don't loosen a gate or baseline; those are human-owned).\n\n"
        + "\n\n".join(failures)
        + ("\n\nNotes: " + "; ".join(notes) if notes else ""),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
