"""Guard: every test or tool cited in an *Enforced by:* line exists.

`docs/architecture-invariants.md` names, per invariant, the test or tool
that fails when the invariant breaks. A renamed or deleted test leaves the
invariant silently unenforced while the page still claims otherwise; this
turns that drift into a red build. Fix it by updating the page to the
current path, or by restoring the guard. Quality-gates hardening spec,
section G.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "docs" / "architecture-invariants.md"
ENFORCED_BY = re.compile(r"\*Enforced by:\*(.*?)(?=\*ADRs:\*|\n\n)", re.S)
CITED_PATH = re.compile(r"`((?:tests|tools)/[\w./-]+\.py)(?:::\w+)?`")


def _cited() -> list[str]:
    text = PAGE.read_text(encoding="utf-8")
    return [p for m in ENFORCED_BY.finditer(text) for p in CITED_PATH.findall(m.group(1))]


def test_page_cites_tests_and_tools():
    assert len(_cited()) >= 10, "pattern found too few citations; update the regexes"


def test_every_cited_test_or_tool_exists():
    missing = sorted({p for p in _cited() if not (REPO_ROOT / p).is_file()})
    assert not missing, "cited in an *Enforced by:* line but missing:\n" + "\n".join(missing)
