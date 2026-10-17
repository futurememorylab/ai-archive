#!/usr/bin/env python3
"""Frontend size ratchet for `static/*.js` (no Node tooling, invariant 1).

The Python gates (erosion, duplication) don't see the JavaScript. This one
keeps it from silently growing into a few huge files and functions:

- a file may grow up to FILE_LIMIT lines; one already above it may not grow
  past its baseline;
- a function may be up to FUNC_LIMIT lines of its *own* code (nested
  functions don't count toward their parent, so an Alpine component factory
  is measured by its methods, not as one giant function); one already above
  it may not grow past its baseline;
- when a baselined file or function shrinks by more than STALE_SLACK lines
  the baseline is stale and must be lowered, like the erosion gate (ADR 0129).

Functions are found with a small scanner (comments and strings stripped,
braces matched), not a real parser: good enough to measure size, and it
fails safe by skipping what it can't match.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

FILE_LIMIT = 500
FUNC_LIMIT = 80
STALE_SLACK = 10
DEFAULT_ROOT = "backend/app/static"
DEFAULT_BASELINE = ".frontend-size-baseline.json"
REBASELINE_HINT = (
    "Run `python tools/frontend_size_gate.py --write-baseline` and commit the lower values."
)

_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "with"}
_HEADERS = [
    re.compile(r"\bfunction\b\s*\*?\s*([\w$]*)\s*\([^()]*\)\s*\{"),
    re.compile(r"([\w$]+)\s*[:=]\s*(?:async\s*)?(?:\([^()]*\)|[\w$]+)\s*=>\s*\{"),
    re.compile(r"(?m)^[ \t]*(?:async\s+)?(?:get\s+|set\s+)?([\w$]+)\s*\([^()]*\)\s*\{"),
    re.compile(r"()(?:\([^()]*\)|[\w$]+)\s*=>\s*\{"),
]


@dataclass(frozen=True)
class JsFunction:
    """One function: its name (or `<anonymous>`), first line and own length."""

    name: str
    line: int
    own_lines: int


def strip_comments_and_strings(src: str) -> str:
    """Blank out comments and string/template contents, keeping newlines and offsets."""
    out = list(src)
    i, n = 0, len(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
        elif c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _match_brace(text: str, open_idx: int) -> int | None:
    depth = 0
    for k in range(open_idx, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return None


_CALLBACK_OF = re.compile(r"([\w$]+)\(\s*(['\"`])([^'\"`\n]*)\2\s*,\s*(?:async\s*)?$")


def _anonymous_name(src: str, start: int) -> str:
    """Name a callback after its call, e.g. `addEventListener(alpine:init)`.

    Keeps baseline keys stable when other anonymous functions are added.
    """
    m = _CALLBACK_OF.search(src[max(0, start - 120) : start])
    return f"{m.group(1)}({m.group(3)})" if m else "<anonymous>"


def functions_in_source(src: str) -> list[JsFunction]:
    """Every function in the source with its own (non-nested) line count."""
    text = strip_comments_and_strings(src)
    spans: dict[int, tuple[str, int]] = {}  # open-brace index -> (name, close index)
    for rx in _HEADERS:
        for m in rx.finditer(text):
            name = m.group(1)
            if name in _KEYWORDS:
                continue
            open_idx = m.end() - 1
            if open_idx in spans:
                continue
            close = _match_brace(text, open_idx)
            if close is not None:
                spans[open_idx] = (name or _anonymous_name(src, m.start()), close)

    def line_of(idx: int) -> int:
        return text.count("\n", 0, idx) + 1

    ordered = sorted(spans.items())
    result = []
    for open_idx, (name, close) in ordered:
        first, last = line_of(open_idx), line_of(close)
        total = last - first + 1
        children = [
            (o, c)
            for o, (_, c) in ordered
            if open_idx < o < close
            and not any(open_idx < o2 < o and c <= c2 < close for o2, (_, c2) in ordered)
        ]
        nested = sum(line_of(c) - line_of(o) + 1 for o, c in children)
        result.append(JsFunction(name, first, max(total - nested, 1)))
    return result


def measure(root: Path) -> dict:
    """Current sizes: every file's line count, and functions over FUNC_LIMIT."""
    files: dict[str, int] = {}
    funcs: dict[str, int] = {}
    for path in sorted(root.glob("*.js")):
        rel = path.as_posix()
        src = path.read_text(encoding="utf-8")
        files[rel] = src.count("\n") + (0 if src.endswith("\n") else 1)
        seen: dict[str, int] = {}
        for f in functions_in_source(src):
            key = f"{rel}::{f.name}"
            n = seen.get(key, 0)
            seen[key] = n + 1
            if n:
                key = f"{key}#{n + 1}"
            if f.own_lines > FUNC_LIMIT:
                funcs[key] = f.own_lines
    return {"files": files, "functions": funcs}


def _check(kind: str, now: dict[str, int], base: dict[str, int], limit: int) -> list[str]:
    failures = []
    for key, size in sorted(now.items()):
        cap = max(limit, base.get(key, 0))
        if size > cap:
            was = f"baseline {base[key]}" if key in base else f"limit {limit}"
            failures.append(f"{kind} {key} has {size} lines, over its {was}")
    for key, size in sorted(base.items()):
        if size > limit and size - now.get(key, 0) > STALE_SLACK:
            failures.append(
                f"{kind} {key} shrank from {size} to {now.get(key, 0)} lines; the baseline is "
                f"stale. {REBASELINE_HINT}"
            )
    return failures


def evaluate(current: dict, baseline: dict) -> list[str]:
    """Failure messages (empty == pass)."""
    return _check("file", current["files"], baseline.get("files", {}), FILE_LIMIT) + _check(
        "function", current["functions"], baseline.get("functions", {}), FUNC_LIMIT
    )


def _baseline_view(current: dict) -> dict:
    """What gets stored: only files over FILE_LIMIT and functions over FUNC_LIMIT."""
    return {
        "files": {k: v for k, v in current["files"].items() if v > FILE_LIMIT},
        "functions": current["functions"],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    ap = argparse.ArgumentParser(description="Frontend (static/*.js) size ratchet")
    ap.add_argument("--path", default=DEFAULT_ROOT)
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true", help="Refuses to raise values")
    ap.add_argument("--allow-raise", action="store_true", help="A human decision (ADR 0129)")
    args = ap.parse_args(argv)

    root = Path(args.path)
    if not root.is_dir():
        print(f"ERROR: path not found: {root}", file=sys.stderr)
        return 2
    current = measure(root)
    base_path = Path(args.baseline)
    baseline = json.loads(base_path.read_text()) if base_path.exists() else {}
    print(
        f"Scanned {len(current['files'])} files under {root}: "
        f"{sum(v > FILE_LIMIT for v in current['files'].values())} over {FILE_LIMIT} lines, "
        f"{len(current['functions'])} functions over {FUNC_LIMIT} own lines"
    )

    if args.write_baseline:
        new = _baseline_view(current)
        # The first baseline records what exists; after that it only goes down.
        first = not base_path.exists()
        raised = [] if args.allow_raise or first else evaluate(current, baseline)
        raised = [m for m in raised if "stale" not in m]
        if raised:
            print(
                "ERROR: refusing to raise the baseline:\n  " + "\n  ".join(raised), file=sys.stderr
            )
            return 1
        base_path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
        print(f"Baseline written to {base_path}")
        return 0

    failures = evaluate(current, baseline)
    for msg in failures:
        print(f"FAIL: {msg}", file=sys.stderr)
    if not failures:
        print("OK: no file or function grew past its limit or baseline")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
