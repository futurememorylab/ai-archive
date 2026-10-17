"""Guard: code the docs hold up as canonical must exist and stay simple.

`AGENTS.md` and `docs/architecture-invariants.md` point agents at a
`path::function` as "the canonical example" to copy. Agents copy what they
are pointed at, so the target must exist and have cyclomatic complexity
<= 10 (the erosion gate's high-complexity threshold). When this fails,
either simplify the function or point the docs at a better example; don't
point them at eroded code. Quality-gates hardening spec, section F.
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.erosion_gate import CC_THRESHOLD, callables_in_source

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = (REPO_ROOT / "AGENTS.md", REPO_ROOT / "docs" / "architecture-invariants.md")
# "canonical ..." followed (within a sentence or two) by `some/path.py::name`.
POINTER = re.compile(r"[Cc]anonical[^`]{0,80}`([\w/]+\.py)::(\w+(?:\.\w+)?)`")
SEARCH_ROOTS = (REPO_ROOT / "backend" / "app", REPO_ROOT)


def _pointers() -> list[tuple[Path, str, str]]:
    found = []
    for doc in DOCS:
        for m in POINTER.finditer(doc.read_text(encoding="utf-8")):
            found.append((doc, m.group(1), m.group(2)))
    return found


def _resolve(rel: str) -> Path | None:
    for root in SEARCH_ROOTS:
        if (root / rel).is_file():
            return root / rel
    return None


def test_docs_name_at_least_one_canonical_example():
    assert _pointers(), "pattern found no canonical pointers; update POINTER"


def test_canonical_examples_exist_and_are_simple():
    problems = []
    for doc, rel, qualname in _pointers():
        path = _resolve(rel)
        if path is None:
            problems.append(f"{doc.name}: {rel} does not exist")
            continue
        cls, _, name = qualname.rpartition(".")
        matches = [
            c
            for c in callables_in_source(path.read_text(encoding="utf-8"), rel)
            if c.name == name and (c.classname or "") == cls
        ]
        if not matches:
            problems.append(f"{doc.name}: {rel}::{qualname} not found")
        for c in matches:
            if c.complexity > CC_THRESHOLD:
                problems.append(
                    f"{doc.name}: {rel}::{qualname} has CC {c.complexity} > {CC_THRESHOLD}"
                )
    assert not problems, "\n".join(problems)
