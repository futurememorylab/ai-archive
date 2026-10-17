#!/usr/bin/env python3
"""Structural-erosion gate for Python code.

Erosion = fraction of total complexity mass held by functions with CC > 10,
where mass(f) = CC(f) * sqrt(SLOC(f)). See SlopCodeBench (arXiv:2603.24755)
and docs/specs/2026-06-07-erosion-detection-ci-design.md.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from radon.complexity import cc_visit

CC_THRESHOLD = 10  # a function strictly above this counts as "high complexity"
# How far erosion may sit below the baseline before the baseline counts as
# stale and must be lowered (ADR 0129: gates only tighten).
DEFAULT_STALE_SLACK = 0.003
DEFAULT_EXCLUDES = {".venv", "venv", "build", "dist", "__pycache__", ".git", "migrations"}


@dataclass(frozen=True)
class CallableInfo:
    """One function/method/closure with its complexity and source length."""

    path: str
    name: str
    lineno: int
    classname: str | None
    complexity: int
    sloc: int


def mass(complexity: int, sloc: int) -> float:
    """Complexity mass: CC * sqrt(SLOC). SLOC floored at 1."""
    return complexity * math.sqrt(max(sloc, 1))


def _iter_blocks(blocks):
    """Flatten radon blocks into individual callables.

    radon.cc_visit returns every method TWICE: once as a top-level Function
    block (is_method=True) and again inside its Class block's `.methods`.
    Closures appear only inside `.closures`. This walk yields all of them;
    callers dedupe by (path, name, lineno).
    """
    for b in blocks:
        if hasattr(b, "methods"):  # a Class block
            yield from _iter_blocks(b.methods)
            yield from _iter_blocks(getattr(b, "inner_classes", []))
        else:  # a Function / method / closure
            yield b
            yield from _iter_blocks(getattr(b, "closures", []))


def callables_in_source(source: str, path: str) -> list[CallableInfo]:
    """Parse `source` and return each callable exactly once (deduped)."""
    seen: set[tuple[str, str, int]] = set()
    out: list[CallableInfo] = []
    for b in _iter_blocks(cc_visit(source)):
        key = (path, b.name, b.lineno)
        if key in seen:
            continue
        seen.add(key)
        sloc = max(b.endline - b.lineno + 1, 1)
        out.append(
            CallableInfo(path, b.name, b.lineno, getattr(b, "classname", None), b.complexity, sloc)
        )
    return out


def erosion_stats(callables: list[CallableInfo]) -> tuple[float, int]:
    """Return (erosion_ratio, max_cc) for a list of callables."""
    total = sum(mass(c.complexity, c.sloc) for c in callables)
    eroded = sum(mass(c.complexity, c.sloc) for c in callables if c.complexity > CC_THRESHOLD)
    max_cc = max((c.complexity for c in callables), default=0)
    erosion = (eroded / total) if total else 0.0
    return erosion, max_cc


def evaluate(
    erosion: float,
    max_cc: int,
    *,
    baseline: float | None,
    tolerance: float,
    max_cc_cap: int | None,
    baseline_max_cc: int | None = None,
    stale_slack: float | None = None,
) -> list[str]:
    """Return a list of failure messages (empty == pass).

    Besides the drift and cap checks, two ratchets keep the baseline honest:
    max CC may not climb past the baseline's ``max_cc``, and when erosion or
    max CC has dropped (erosion by more than ``stale_slack``) the baseline is
    stale and must be lowered so the improvement locks in.
    """
    failures: list[str] = []
    if baseline is not None:
        cap = baseline + tolerance
        if erosion > cap:
            failures.append(
                f"erosion {erosion:.3f} exceeds baseline {baseline:.3f} (+{tolerance}) = {cap:.3f}"
            )
        elif stale_slack is not None and baseline - erosion > stale_slack:
            failures.append(
                f"erosion {erosion:.3f} is more than {stale_slack} below baseline "
                f"{baseline:.3f}; the baseline is stale. {REBASELINE_HINT}"
            )
    if baseline_max_cc is not None:
        if max_cc > baseline_max_cc:
            failures.append(f"max function CC {max_cc} exceeds baseline max_cc {baseline_max_cc}")
        elif max_cc < baseline_max_cc:
            failures.append(
                f"max function CC fell to {max_cc} below baseline max_cc {baseline_max_cc}; "
                f"the baseline is stale. {REBASELINE_HINT}"
            )
    if max_cc_cap is not None and max_cc > max_cc_cap:
        failures.append(f"a function has CC {max_cc} > cap {max_cc_cap}")
    return failures


REBASELINE_HINT = (
    "Run `python tools/erosion_gate.py --path backend --baseline .erosion-baseline.json "
    "--write-baseline` and commit the lower values."
)


def _qualname(c: CallableInfo) -> str:
    return f"{c.classname}.{c.name}" if c.classname else c.name


def _keyed(callables: list[CallableInfo]) -> dict[tuple[str, int], CallableInfo]:
    """Key callables by (qualified name, occurrence) so same-named closures stay apart."""
    seen: dict[str, int] = {}
    out: dict[tuple[str, int], CallableInfo] = {}
    for c in sorted(callables, key=lambda c: c.lineno):
        q = _qualname(c)
        n = seen.get(q, 0)
        seen[q] = n + 1
        out[(q, n)] = c
    return out


def touched_regressions(before: list[CallableInfo], after: list[CallableInfo]) -> list[str]:
    """Functions present in both versions of a file whose CC went up."""
    old = _keyed(before)
    msgs: list[str] = []
    for key, now in _keyed(after).items():
        was = old.get(key)
        if was is not None and now.complexity > was.complexity:
            msgs.append(
                f"{now.path}::{key[0]} CC rose {was.complexity} -> {now.complexity} "
                f"(line {now.lineno}); a function this PR changes may not get more complex"
            )
    return msgs


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def touched_function_failures(root: Path, base_ref: str, extra_excludes: list[str]) -> list[str]:
    """Compare each changed .py file under root with its merge-base version.

    Raises ``subprocess.CalledProcessError`` when git can't resolve the ref,
    so a misconfigured CI run fails loudly instead of skipping the rule.
    """
    merge_base = _git("merge-base", base_ref, "HEAD").strip()
    top = Path(_git("rev-parse", "--show-toplevel").strip())
    changed = _git("diff", "--name-only", "--diff-filter=M", merge_base, "--", str(root))
    candidates = {p.resolve() for p in iter_python_files(root, extra_excludes)}
    failures: list[str] = []
    for rel in changed.splitlines():
        path = top / rel
        if path.resolve() not in candidates:
            continue
        try:
            before = callables_in_source(_git("show", f"{merge_base}:{rel}"), rel)
            after = callables_in_source(path.read_text(encoding="utf-8"), rel)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            print(f"WARN: skipped {rel}: {exc}", file=sys.stderr)
            continue
        failures.extend(touched_regressions(before, after))
    return failures


def iter_python_files(root: Path, extra_excludes: list[str]) -> list[Path]:
    """All *.py under root, skipping DEFAULT_EXCLUDES and any extra substrings."""
    files: list[Path] = []
    for p in root.rglob("*.py"):
        if any(part in DEFAULT_EXCLUDES for part in p.parts):
            continue
        rel = p.relative_to(root).as_posix()
        if any(ex in rel for ex in extra_excludes):
            continue
        files.append(p)
    return files


def analyze(files: list[Path]) -> tuple[float, int, list[CallableInfo]]:
    """Aggregate erosion over files. Returns (erosion, max_cc, offenders desc)."""
    callables: list[CallableInfo] = []
    for f in files:
        try:
            callables.extend(callables_in_source(f.read_text(encoding="utf-8"), str(f)))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            print(f"WARN: skipped {f}: {exc}", file=sys.stderr)
            continue
    erosion, max_cc = erosion_stats(callables)
    offenders = sorted(
        (c for c in callables if c.complexity > CC_THRESHOLD),
        key=lambda c: mass(c.complexity, c.sloc),
        reverse=True,
    )
    return erosion, max_cc, offenders


def _print_report(root: Path, n_files: int, erosion: float, max_cc: int, offenders, top: int):
    print(f"Scanned {n_files} files under {root}")
    print(f"Erosion:         {erosion:.3f}")
    print(f"Max function CC: {max_cc}")
    print(f"High-CC functions (CC>{CC_THRESHOLD}): {len(offenders)}")
    if offenders:
        print("\nTop offenders (by complexity mass):")
        print(f"{'mass':>8}  {'CC':>4}  {'SLOC':>5}  location")
        for c in offenders[:top]:
            print(
                f"{mass(c.complexity, c.sloc):8.1f}  {c.complexity:4d}  "
                f"{c.sloc:5d}  {c.path}:{c.lineno}  {_qualname(c)}()"
            )


def _read_baseline(path: str | None) -> dict | None:
    """Parsed baseline JSON, None when absent. Raises ValueError when unreadable."""
    if not path or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text())
        float(data["erosion"])
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ValueError(f"bad baseline file {path}: {exc}") from exc
    return data


def _write_baseline(path: str, erosion: float, max_cc: int, allow_raise: bool) -> int:
    """Write the baseline; refuse to raise either value unless allow_raise."""
    try:
        old = _read_baseline(path)
    except ValueError:
        old = None
    new = {"erosion": round(erosion, 4), "max_cc": max_cc}
    if old and not allow_raise:
        raised = [k for k in new if k in old and new[k] > old[k]]
        if raised:
            print(
                f"ERROR: refusing to raise {', '.join(raised)} in {path} "
                f"({old} -> {new}). Gates only tighten (ADR 0129); a human may "
                "pass --allow-raise.",
                file=sys.stderr,
            )
            return 1
    Path(path).write_text(json.dumps(new, indent=2) + "\n")
    print(f"\nBaseline written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    ap = argparse.ArgumentParser(description="Structural-erosion gate")
    ap.add_argument("--path", default="backend", help="Root dir to scan")
    ap.add_argument("--max-erosion", type=float, default=None, help="Absolute erosion ceiling")
    ap.add_argument("--max-cc", type=int, default=None, help="Hard cap on any single function's CC")
    ap.add_argument("--baseline", default=None, help="JSON baseline file to ratchet against")
    ap.add_argument(
        "--tolerance", type=float, default=0.005, help="Allowed erosion drift above baseline"
    )
    ap.add_argument(
        "--stale-slack",
        type=float,
        default=DEFAULT_STALE_SLACK,
        help="How far erosion may fall below the baseline before it must be lowered",
    )
    ap.add_argument(
        "--base-ref",
        default=None,
        help="Git ref (e.g. origin/main): fail if a function changed since the merge-base "
        "got more complex",
    )
    ap.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write current metrics to --baseline and exit (refuses to raise values)",
    )
    ap.add_argument(
        "--allow-raise",
        action="store_true",
        help="With --write-baseline: allow raising values (a human decision, ADR 0129)",
    )
    ap.add_argument("--exclude", action="append", default=[], help="Extra path substrings to skip")
    ap.add_argument("--top", type=int, default=15, help="How many offenders to print")
    args = ap.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"ERROR: path not found: {root}", file=sys.stderr)
        return 2

    files = iter_python_files(root, args.exclude)
    erosion, max_cc, offenders = analyze(files)
    _print_report(root, len(files), erosion, max_cc, offenders, args.top)

    if args.write_baseline:
        if not args.baseline:
            print("ERROR: --write-baseline needs --baseline PATH", file=sys.stderr)
            return 2
        return _write_baseline(args.baseline, erosion, max_cc, args.allow_raise)

    try:
        baseline = _read_baseline(args.baseline)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    baseline_val = float(baseline["erosion"]) if baseline else None
    baseline_max_cc = baseline.get("max_cc") if baseline else None

    failures = evaluate(
        erosion,
        max_cc,
        baseline=baseline_val,
        tolerance=args.tolerance,
        max_cc_cap=args.max_cc,
        baseline_max_cc=baseline_max_cc,
        stale_slack=args.stale_slack,
    )
    if args.max_erosion is not None and erosion > args.max_erosion:
        failures.append(f"erosion {erosion:.3f} exceeds ceiling {args.max_erosion}")
    if args.base_ref:
        try:
            failures.extend(touched_function_failures(root, args.base_ref, args.exclude))
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            detail = getattr(exc, "stderr", "") or exc
            print(
                f"ERROR: touched-function check needs git and {args.base_ref}: {detail}",
                file=sys.stderr,
            )
            return 2

    if failures:
        for msg in failures:
            print(f"\nFAIL: {msg}", file=sys.stderr)
        return 1
    if baseline_val is not None:
        print(f"\nOK: erosion {erosion:.3f} within baseline {baseline_val:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
