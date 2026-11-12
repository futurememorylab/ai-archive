#!/usr/bin/env python3
"""ADR log lint: keep `docs/adr/`, the index and the canon consistent.

The ADR rules in `docs/decisions.md` only held while someone remembered
them. This makes them mechanical, so they hold whichever tool wrote the
change (Claude Code, Cowork, a claude.ai Project, or by hand):

- every `docs/adr/NNNN-*.md` has `Date`, `Status` and `Lifespan` headers
  (any of the three header styles in the log), and `Lifespan` is one of the
  documented classes;
- every ADR is linked from `docs/decisions.md`, and every `./adr/...` link
  there points at a file that exists;
- when ADR X's header says it supersedes NNNN, ADR NNNN is marked
  (`Lifespan: Superseded`, or "partly superseded" in its header) and its
  header points back at X;
- every ADR number and every "Invariant N" cited in
  `docs/architecture-invariants.md` resolves;
- the synthesis log in `docs/decisions.md` is no more than SYNTHESIS_LAG
  ADRs behind the highest ADR number (the "~20 new ADRs" cadence).

Violations that existed when the gate landed are grandfathered in
`.adr-lint-baseline.json`. The list only shrinks: a fixed violation that
is still listed fails the gate until `--write-baseline` drops it, the same
ratchet as the frontend size gate (ADR 0129).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = "."
DEFAULT_BASELINE = ".adr-lint-baseline.json"
ADR_DIR = Path("docs/adr")
INDEX = Path("docs/decisions.md")
INVARIANTS = Path("docs/architecture-invariants.md")
REQUIRED_HEADERS = ("Date", "Status", "Lifespan")
LIFESPANS = ("invariant", "feature", "lesson", "superseded", "partly superseded", "partially")
SYNTHESIS_LAG = 20
REBASELINE_HINT = "Run `python tools/adr_lint.py --write-baseline` and commit the shorter list."

_ADR_FILE = re.compile(r"^(\d{4})-.+\.md$")
_NUM = r"(?<![\d.])(\d{4})(?!\d)"
_SUPERSEDES = re.compile(r"(?i)\bsupersedes\b")
_SPAN_END = re.compile(r"(?i);|·|\n\s*\n|superseded by")
_PARTLY = re.compile(r"(?i)\bpart(?:ly|ially)\s+superseded\b")
_INDEX_LINK = re.compile(r"\]\(\./adr/([^)\s]+)\)")
_INVARIANT_HEAD = re.compile(r"(?m)^\*\*(\d+[a-z]?)\.\s")
_INVARIANT_REF = re.compile(r"(?i)\binvariants?\s+#?(\d+[a-z]?)\b")
_ADR_REF = re.compile(r"\bADR\s+(\d{4})\b")


@dataclass(frozen=True)
class Adr:
    """One ADR file: its number, file name, header region and parsed headers."""

    num: str
    name: str
    header: str
    fields: dict[str, str]


def header_region(text: str) -> str:
    """Everything before the first `## ` section: title, headers, banners."""
    m = re.search(r"(?m)^## ", text)
    return text[: m.start()] if m else text


def parse_fields(header: str) -> dict[str, str]:
    """`Date` / `Status` / `Lifespan` values in any of the log's header styles.

    Handles `- **Date:** x`, `**Date:** x` and `Date: x · Status: y · ...`.
    """
    fields: dict[str, str] = {}
    for name in REQUIRED_HEADERS:
        m = re.search(rf"\b{name}\b\**\s*:\**\s*([^·\n]*)", header)
        if m and m.group(1).strip(" *"):
            fields[name] = m.group(1).strip(" *")
    return fields


def load_adrs(root: Path) -> dict[str, Adr]:
    """Every `docs/adr/NNNN-*.md`, keyed by its four-digit number."""
    adrs: dict[str, Adr] = {}
    for path in sorted((root / ADR_DIR).glob("*.md")):
        m = _ADR_FILE.match(path.name)
        if not m:
            continue
        header = header_region(path.read_text(encoding="utf-8"))
        adrs[m.group(1)] = Adr(m.group(1), path.name, header, parse_fields(header))
    return adrs


def check_headers(adrs: dict[str, Adr]) -> dict[str, str]:
    """Missing or unknown `Date` / `Status` / `Lifespan` headers."""
    out: dict[str, str] = {}
    for adr in adrs.values():
        for name in REQUIRED_HEADERS:
            if name not in adr.fields:
                out[f"header:{adr.num}:{name}"] = f"ADR {adr.num} has no `{name}:` header"
        lifespan = adr.fields.get("Lifespan", "").lower()
        if lifespan and not lifespan.startswith(LIFESPANS):
            out[f"header:{adr.num}:Lifespan-value"] = (
                f"ADR {adr.num} has `Lifespan: {adr.fields['Lifespan']}`; "
                "use Invariant, Feature, Lesson or Superseded"
            )
    return out


def check_index(root: Path, adrs: dict[str, Adr]) -> dict[str, str]:
    """ADRs missing from `docs/decisions.md`, and index links to missing files."""
    linked = set(_INDEX_LINK.findall((root / INDEX).read_text(encoding="utf-8")))
    names = {adr.name for adr in adrs.values()}
    out: dict[str, str] = {}
    for adr in adrs.values():
        if adr.name not in linked:
            out[f"unindexed:{adr.num}"] = f"ADR {adr.num} ({adr.name}) is not in {INDEX}"
    for target in sorted(linked - names):
        out[f"index-dangling:{target}"] = f"{INDEX} links ./adr/{target}, which does not exist"
    return out


def superseded_by(adr: Adr) -> set[str]:
    """ADR numbers this ADR's header says it supersedes (wholly or in part)."""
    nums: set[str] = set()
    for m in _SUPERSEDES.finditer(adr.header):
        rest = adr.header[m.end() :]
        end = _SPAN_END.search(rest)
        nums.update(re.findall(_NUM, rest[: end.start()] if end else rest))
    nums.discard(adr.num)
    return nums


def _supersession_problems(newer: Adr, old: Adr) -> dict[str, str]:
    out: dict[str, str] = {}
    key = f"supersession:{newer.num}->{old.num}"
    marked = old.fields.get("Lifespan", "").lower().startswith("superseded") or bool(
        _PARTLY.search(old.header)
    )
    if not marked:
        out[f"{key}:lifespan"] = (
            f"ADR {newer.num} supersedes {old.num}, but {old.num} is neither "
            "`Lifespan: Superseded` nor marked partly superseded in its header"
        )
    if not re.search(rf"(?<!\d){newer.num}(?!\d)", old.header):
        out[f"{key}:pointer"] = (
            f"ADR {newer.num} supersedes {old.num}, but {old.num}'s header does not "
            f"point forward to {newer.num}"
        )
    return out


def check_supersessions(adrs: dict[str, Adr]) -> dict[str, str]:
    """Every "supersedes NNNN" has its target marked and pointing back."""
    out: dict[str, str] = {}
    for newer in adrs.values():
        for num in sorted(superseded_by(newer)):
            if num not in adrs:
                out[f"supersession:{newer.num}->{num}:missing"] = (
                    f"ADR {newer.num} supersedes {num}, which does not exist"
                )
                continue
            out.update(_supersession_problems(newer, adrs[num]))
    return out


def _cited_adrs(page: str) -> set[str]:
    """ADR numbers in `*ADRs:*` paragraphs and `ADR NNNN` mentions."""
    nums = set(_ADR_REF.findall(page))
    for m in re.finditer(r"\*ADRs:\*", page):
        rest = page[m.end() :]
        end = re.search(r"\n\s*\n", rest)
        nums.update(re.findall(_NUM, rest[: end.start()] if end else rest))
    return nums


def check_invariants_page(root: Path, adrs: dict[str, Adr]) -> dict[str, str]:
    """ADR and invariant numbers cited on the canon page all resolve."""
    page = (root / INVARIANTS).read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for num in sorted(_cited_adrs(page) - set(adrs)):
        out[f"invariants-ref:ADR {num}"] = f"{INVARIANTS} cites ADR {num}, which does not exist"
    heads = set(_INVARIANT_HEAD.findall(page))
    for ref in sorted(set(_INVARIANT_REF.findall(page)) - heads):
        out[f"invariants-ref:Invariant {ref}"] = (
            f"{INVARIANTS} cites invariant {ref}, which has no `**{ref}.` entry"
        )
    return out


def synthesis_through(index_text: str) -> int:
    """Highest "Through ADR" in the synthesis log table (0 if none)."""
    m = re.search(r"(?ms)^## Synthesis log\s*$(.*?)(?=^## |\Z)", index_text)
    if not m:
        return 0
    rows = re.findall(r"(?m)^\|\s*[\d-]+\s*\|\s*(\d{4})\s*\|", m.group(1))
    return max((int(r) for r in rows), default=0)


def check_synthesis(root: Path, adrs: dict[str, Adr]) -> dict[str, str]:
    """The synthesis log keeps up with the ADR log (within SYNTHESIS_LAG)."""
    if not adrs:
        return {}
    highest = max(int(n) for n in adrs)
    through = synthesis_through((root / INDEX).read_text(encoding="utf-8"))
    if highest - through <= SYNTHESIS_LAG:
        return {}
    return {
        f"synthesis:{highest:04d}": (
            f"the synthesis log in {INDEX} stops at {through:04d} but ADRs go up to "
            f"{highest:04d}; run a synthesis pass (more than {SYNTHESIS_LAG} behind)"
        )
    }


def violations(root: Path) -> dict[str, str]:
    """Every rule violation in the repo at `root`, keyed by a stable id."""
    adrs = load_adrs(root)
    out: dict[str, str] = {}
    out.update(check_headers(adrs))
    out.update(check_index(root, adrs))
    out.update(check_supersessions(adrs))
    out.update(check_invariants_page(root, adrs))
    out.update(check_synthesis(root, adrs))
    return out


def evaluate(found: dict[str, str], grandfathered: list[str]) -> list[str]:
    """Failure messages (empty == pass): new violations and stale grandfather entries."""
    failures = [f"{msg} [{key}]" for key, msg in sorted(found.items()) if key not in grandfathered]
    failures += [
        f"{key} is grandfathered but no longer happens; the list is stale. {REBASELINE_HINT}"
        for key in sorted(set(grandfathered) - set(found))
    ]
    return failures


def _write_baseline(path: Path, found: dict[str, str], old: list[str], allow_raise: bool) -> int:
    added = sorted(set(found) - set(old))
    if added and path.exists() and not allow_raise:
        print("ERROR: refusing to grandfather new violations:\n  " + "\n  ".join(added))
        return 1
    path.write_text(json.dumps({"grandfathered": sorted(found)}, indent=2) + "\n")
    print(f"Baseline written to {path} ({len(found)} grandfathered)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    ap = argparse.ArgumentParser(description="ADR log / index / canon consistency lint")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true", help="Refuses to add entries")
    ap.add_argument("--allow-raise", action="store_true", help="A human decision (ADR 0129)")
    args = ap.parse_args(argv)

    root = Path(args.root)
    base_path = root / args.baseline
    old = json.loads(base_path.read_text())["grandfathered"] if base_path.exists() else []
    found = violations(root)
    print(f"Checked {len(load_adrs(root))} ADRs: {len(found)} violations, {len(old)} grandfathered")
    if args.write_baseline:
        return _write_baseline(base_path, found, old, args.allow_raise)
    failures = evaluate(found, old)
    for msg in failures:
        print(f"FAIL: {msg}", file=sys.stderr)
    if not failures:
        print("OK: ADR log, index and invariants page are consistent")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
