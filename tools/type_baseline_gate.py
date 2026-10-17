#!/usr/bin/env python3
"""Type-baseline ratchet: the basedpyright baseline may shrink, never grow.

basedpyright already fails on errors that are not in
`.basedpyright/baseline.json`, but `--writebaseline` can grandfather new
ones. This gate compares the number of baseline entries with the same file
at the merge-base of `--base-ref` and fails when it went up (ADR 0129).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_BASELINE = ".basedpyright/baseline.json"


def count_entries(text: str) -> int:
    """Number of grandfathered diagnostics in a basedpyright baseline JSON."""
    files = json.loads(text).get("files", {})
    return sum(len(entries) for entries in files.values())


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def baseline_at(ref: str, path: str) -> str | None:
    """The baseline's text at `ref`, or None when it didn't exist there."""
    try:
        return _git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    ap = argparse.ArgumentParser(description="basedpyright baseline ratchet")
    ap.add_argument("--base-ref", required=True, help="Git ref to compare with, e.g. origin/main")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE, help="Baseline path, repo-relative")
    args = ap.parse_args(argv)

    try:
        merge_base = _git("merge-base", args.base_ref, "HEAD").strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = getattr(exc, "stderr", "") or exc
        print(f"ERROR: can't resolve merge-base with {args.base_ref}: {detail}", file=sys.stderr)
        return 2

    path = Path(args.baseline)
    now = count_entries(path.read_text(encoding="utf-8")) if path.exists() else 0
    old_text = baseline_at(merge_base, args.baseline)
    before = count_entries(old_text) if old_text is not None else 0

    print(f"Type baseline entries: {before} at merge-base, {now} now")
    if now > before:
        print(
            f"\nFAIL: {args.baseline} grew from {before} to {now} entries. Fix the new type "
            "errors instead of grandfathering them; only a human may raise the baseline "
            "(ADR 0129).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
