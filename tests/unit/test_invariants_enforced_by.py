"""Every invariant's *Enforced by:* points at something real; convention-only is a ratchet.

`docs/architecture-invariants.md` names, for each invariant, the test, lint
or file that fails when the rule breaks. A rename or deletion left those
pointers dangling with nothing to notice. This test resolves every
file-looking token in an *Enforced by:* line (`path`, `path::symbol` or a
glob) against the repo, `backend/app/` and `backend/`.

It also lists the invariants enforced only by convention (no test, tool or
lint named). The list may only shrink: when you add a mechanical check to
one, drop it here; a new convention-only invariant needs a reviewed edit to
this list (ADR 0129).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "docs" / "architecture-invariants.md"
SEARCH_ROOTS = (REPO_ROOT, REPO_ROOT / "backend" / "app", REPO_ROOT / "backend")
_FILEISH = re.compile(r"^[\w.*/-]+\.(py|json|md|toml|ya?ml|cfg|sh|sql|html|js|css)$|^\.[\w-]+$")
_MECHANICAL = re.compile(r"^(tests/|tools/|\.importlinter$|test_[\w*]+\.py$)")

# Invariants whose *Enforced by:* is convention alone. Only shrinks.
CONVENTION_ONLY = {"1", "3", "5", "6", "9", "10", "11", "12", "18", "21", "23", "24", "25"}


def invariants() -> dict[str, str]:
    """Invariant id (e.g. '9a') -> its *Enforced by:* text."""
    page = PAGE.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"(?ms)^\*\*(\d+[a-z]?)\.\s(.*?)(?=^\*\*\d+[a-z]?\.\s|^## |\Z)", page):
        enforced = re.search(r"(?s)\*Enforced by:\*(.*?)(?:\*ADRs:\*|\Z)", m.group(2))
        out[m.group(1)] = enforced.group(1) if enforced else ""
    return out


def file_tokens(text: str) -> list[str]:
    """Backticked tokens that name a file (`absence of ...` clauses excluded)."""
    text = re.sub(r"absence of[^.;]*", "", text)
    tokens = []
    for tok in re.findall(r"`([^`]+)`", text):
        path = tok.split("::")[0]
        if _FILEISH.match(path) and not path.startswith("/"):
            tokens.append(tok)
    return tokens


def resolves(token: str) -> bool:
    """The file (or glob) exists under a search root, and so does `::symbol` if given."""
    path, _, symbol = token.partition("::")
    for root in SEARCH_ROOTS:
        hits = sorted(root.glob(path)) if "*" in path else [root / path]
        hits = [h for h in hits if h.exists()]
        if not hits:
            continue
        if not symbol:
            return True
        rx = re.compile(rf"(?m)^\s*(?:async\s+)?(?:def|class)\s+{re.escape(symbol)}\b")
        return any(h.is_file() and rx.search(h.read_text(encoding="utf-8")) for h in hits)
    return False


def is_convention_only(enforced: str) -> bool:
    """Says "convention" and names no test, tool or lint config."""
    if not re.search(r"\bconvention\b", enforced):
        return False
    return not any(_MECHANICAL.match(tok.split("::")[0]) for tok in file_tokens(enforced))


def test_page_parses():
    found = invariants()
    assert len(found) >= 28
    assert all(text.strip() for text in found.values()), "an invariant has no *Enforced by:*"


def test_every_enforced_by_file_exists():
    dangling = [
        f"invariant {inv}: `{tok}`"
        for inv, text in invariants().items()
        for tok in file_tokens(text)
        if not resolves(tok)
    ]
    assert not dangling, "Enforced-by points at missing files:\n  " + "\n  ".join(dangling)


def test_convention_only_invariants_only_shrink():
    now = {inv for inv, text in invariants().items() if is_convention_only(text)}
    new = sorted(now - CONVENTION_ONLY)
    gone = sorted(CONVENTION_ONLY - now)
    assert not new, (
        f"invariant(s) {new} are enforced only by convention. Name the test or lint that "
        "enforces them; adding to CONVENTION_ONLY is a human decision (ADR 0129)."
    )
    assert not gone, (
        f"invariant(s) {gone} are now enforced mechanically; drop them from CONVENTION_ONLY."
    )


def test_helpers_classify_examples():
    assert file_tokens("convention + absence of `package.json`/bundler.") == []
    assert file_tokens("`tools/x.py`, `lint-imports`, `get_args(<Literal>)`, `/e2e`") == [
        "tools/x.py"
    ]
    assert is_convention_only("convention; `repositories/ai_store_files.py`.")
    assert not is_convention_only("`tests/unit/test_x.py`; otherwise convention.")
    assert not is_convention_only("`.importlinter` (`lint-imports`)")
    assert resolves("services/clip_media.py::ai_input_for_clip")
    assert not resolves("services/clip_media.py::no_such_function")
    assert not resolves("tests/unit/test_does_not_exist.py")
