"""Tests for tools/adr_lint.py (ADR log / index / canon consistency, ADR 0129 ratchet)."""

from __future__ import annotations

import json
from pathlib import Path

from tools import adr_lint

REPO_ROOT = Path(__file__).resolve().parents[2]

GOOD_HEADER = "- **Date:** 2026-01-01\n- **Status:** Accepted\n- **Lifespan:** {life}\n"


def _adr(root: Path, num: str, header: str, slug: str = "x") -> str:
    name = f"{num}-{slug}.md"
    (root / "docs" / "adr" / name).write_text(f"# {num}. Title\n\n{header}\n## Context\n\nBody.\n")
    return name


def _repo(
    tmp_path: Path, adrs: dict[str, str], invariants: str = "", through: str = "0002"
) -> Path:
    root = tmp_path
    (root / "docs" / "adr").mkdir(parents=True)
    names = [_adr(root, num, header) for num, header in adrs.items()]
    rows = "\n".join(f"| {n[:4]} | 2026 | [t](./adr/{n}) |" for n in names)
    (root / "docs" / "decisions.md").write_text(
        "# Decisions\n\n## Synthesis log\n\n| Date | Through ADR | Notes |\n|---|---|---|\n"
        f"| 2026-01-01 | {through} | x |\n\n## Index\n\n{rows}\n"
    )
    (root / "docs" / "architecture-invariants.md").write_text(
        invariants or "**1. Rule.** Text.\n*ADRs:* 0001.\n"
    )
    return root


def _ok(life: str = "Invariant") -> str:
    return GOOD_HEADER.format(life=life)


def test_clean_repo_has_no_violations(tmp_path):
    root = _repo(tmp_path, {"0001": _ok(), "0002": _ok("Feature")})
    assert adr_lint.violations(root) == {}


def test_all_three_header_styles_parse():
    styles = [
        "- **Date:** 2026-01-01\n- **Status:** Accepted\n- **Lifespan:** Lesson\n",
        "**Date:** 2026-01-01\n**Status:** Accepted\n**Lifespan:** Lesson\n",
        "Date: 2026-01-01 · Status: accepted · Lifespan: Lesson ·\n",
    ]
    for header in styles:
        fields = adr_lint.parse_fields(header)
        assert fields["Date"] == "2026-01-01"
        assert fields["Status"].lower() == "accepted"
        assert fields["Lifespan"] == "Lesson"


def test_missing_and_unknown_headers_are_flagged(tmp_path):
    root = _repo(
        tmp_path,
        {"0001": "Date: 2026-01-01 · Status: accepted\n", "0002": _ok("Whatever")},
    )
    assert set(adr_lint.violations(root)) == {
        "header:0001:Lifespan",
        "header:0002:Lifespan-value",
    }


def test_unindexed_adr_and_dangling_index_link(tmp_path):
    root = _repo(tmp_path, {"0001": _ok(), "0002": _ok()})
    _adr(root, "0003", _ok(), slug="unlisted")
    index = root / "docs" / "decisions.md"
    index.write_text(index.read_text() + "| 0009 | 2026 | [gone](./adr/0009-gone.md) |\n")
    assert set(adr_lint.violations(root)) == {"unindexed:0003", "index-dangling:0009-gone.md"}


def test_supersession_needs_marked_target_with_pointer_back(tmp_path):
    root = _repo(
        tmp_path,
        {"0001": _ok(), "0002": _ok() + "Supersedes [0001](./0001-x.md) · Spec: y\n"},
    )
    assert set(adr_lint.violations(root)) == {
        "supersession:0002->0001:lifespan",
        "supersession:0002->0001:pointer",
    }


def test_supersession_satisfied_by_superseded_lifespan_or_partly_banner(tmp_path):
    full = "- **Date:** 2026\n- **Status:** Superseded by 0003\n- **Lifespan:** Superseded\n"
    partly = _ok() + "> Partly superseded by 0003 (the switching half).\n"
    newer = _ok() + "Supersedes the switching half of 0001 and 0002\n"
    root = _repo(tmp_path, {"0001": full, "0002": partly, "0003": newer}, through="0003")
    assert adr_lint.violations(root) == {}


def test_superseded_by_is_not_read_as_supersedes():
    adr = adr_lint.Adr(
        "0112",
        "0112-x.md",
        "**Status:** Accepted — supersedes [0043](./0043-a.md); Superseded by [0130](./0130-b.md)",
        {},
    )
    assert adr_lint.superseded_by(adr) == {"0043"}


def test_supersession_of_missing_adr(tmp_path):
    root = _repo(tmp_path, {"0001": _ok(), "0002": _ok() + "Supersedes 0007.\n"})
    assert set(adr_lint.violations(root)) == {"supersession:0002->0007:missing"}


def test_invariants_page_refs_must_resolve(tmp_path):
    page = (
        "**1. Rule.** See invariant 2 and Invariant 1.\n*ADRs:* 0001 (supersedes\n"
        "0005's part).\n\nAlso ADR 0006.\n"
    )
    root = _repo(tmp_path, {"0001": _ok(), "0002": _ok()}, invariants=page)
    assert set(adr_lint.violations(root)) == {
        "invariants-ref:ADR 0005",
        "invariants-ref:ADR 0006",
        "invariants-ref:Invariant 2",
    }


def test_synthesis_log_may_lag_by_the_cadence_only(tmp_path):
    root = _repo(tmp_path, {"0001": _ok(), "0030": _ok()}, through="0009")
    assert set(adr_lint.violations(root)) == {"synthesis:0030"}
    root2 = _repo(tmp_path / "b", {"0001": _ok(), "0029": _ok()}, through="0009")
    assert adr_lint.violations(root2) == {}


def test_grandfathered_entries_pass_and_stale_ones_fail():
    found = {"a": "msg a"}
    assert adr_lint.evaluate(found, ["a"]) == []
    assert adr_lint.evaluate(found, []) == ["msg a [a]"]
    (stale,) = adr_lint.evaluate({}, ["a"])
    assert "stale" in stale


def test_write_baseline_refuses_to_add_entries(tmp_path):
    root = _repo(tmp_path, {"0001": _ok(), "0002": _ok()})
    base = root / ".adr-lint-baseline.json"
    base.write_text(json.dumps({"grandfathered": []}))
    _adr(root, "0003", _ok(), slug="unlisted")
    assert adr_lint.main(["--root", str(root), "--write-baseline"]) == 1
    assert adr_lint.main(["--root", str(root)]) == 1
    assert adr_lint.main(["--root", str(root), "--write-baseline", "--allow-raise"]) == 0
    assert json.loads(base.read_text()) == {"grandfathered": ["unindexed:0003"]}
    assert adr_lint.main(["--root", str(root)]) == 0


def test_the_repo_passes_against_its_baseline():
    """The real ADR log is clean apart from the grandfathered list."""
    assert adr_lint.main(["--root", str(REPO_ROOT)]) == 0
