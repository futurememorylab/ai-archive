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
import sys
from dataclasses import dataclass
from pathlib import Path

from radon.complexity import cc_visit

CC_THRESHOLD = 10  # a function strictly above this counts as "high complexity"
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
) -> list[str]:
    """Return a list of failure messages (empty == pass)."""
    failures: list[str] = []
    if baseline is not None:
        cap = baseline + tolerance
        if erosion > cap:
            failures.append(
                f"erosion {erosion:.3f} exceeds baseline {baseline:.3f} (+{tolerance}) = {cap:.3f}"
            )
    if max_cc_cap is not None and max_cc > max_cc_cap:
        failures.append(f"a function has CC {max_cc} > cap {max_cc_cap}")
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
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
    erosion, max_cc = erosion_stats(callables)
    offenders = sorted(
        (c for c in callables if c.complexity > CC_THRESHOLD),
        key=lambda c: mass(c.complexity, c.sloc),
        reverse=True,
    )
    return erosion, max_cc, offenders


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
        "--write-baseline",
        action="store_true",
        help="Write current metrics to --baseline and exit 0",
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

    print(f"Scanned {len(files)} files under {root}")
    print(f"Erosion:         {erosion:.3f}")
    print(f"Max function CC: {max_cc}")
    print(f"High-CC functions (CC>{CC_THRESHOLD}): {len(offenders)}")
    if offenders:
        print("\nTop offenders (by complexity mass):")
        print(f"{'mass':>8}  {'CC':>4}  {'SLOC':>5}  location")
        for c in offenders[: args.top]:
            name = f"{c.classname}.{c.name}" if c.classname else c.name
            print(
                f"{mass(c.complexity, c.sloc):8.1f}  {c.complexity:4d}  "
                f"{c.sloc:5d}  {c.path}:{c.lineno}  {name}()"
            )

    if args.write_baseline:
        if not args.baseline:
            print("ERROR: --write-baseline needs --baseline PATH", file=sys.stderr)
            return 2
        Path(args.baseline).write_text(
            json.dumps({"erosion": round(erosion, 4), "max_cc": max_cc}, indent=2) + "\n"
        )
        print(f"\nBaseline written to {args.baseline}")
        return 0

    baseline_val: float | None = None
    if args.baseline and Path(args.baseline).exists():
        baseline_val = json.loads(Path(args.baseline).read_text())["erosion"]

    failures = evaluate(
        erosion,
        max_cc,
        baseline=baseline_val,
        tolerance=args.tolerance,
        max_cc_cap=args.max_cc,
    )
    if args.max_erosion is not None and erosion > args.max_erosion:
        failures.append(f"erosion {erosion:.3f} exceeds ceiling {args.max_erosion}")

    if failures:
        for msg in failures:
            print(f"\nFAIL: {msg}", file=sys.stderr)
        return 1
    if baseline_val is not None:
        print(f"\nOK: erosion {erosion:.3f} within baseline {baseline_val:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
