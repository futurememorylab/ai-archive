"""Guard: `setLayout()` must not close the compare card.

Spec requirement #3 (docs/specs/2026-06-02-studio-resizable-panes-design.md):
in the RIGHT layout the user sees Player | Prompt(cur) | Compare(cmp) as three
columns. The earlier `setLayout()` force-closed the compare card when switching
to `right` (`if (v === 'right' && this.compareVersionId) this.closeCompare();`),
which made the three-column arrangement impossible.

This is a source-scan guard (the repo has no JS test runner, mirroring
tests/unit/test_no_x_data_stack.py): isolate the `setLayout(` method body and
assert it does NOT call `closeCompare` — switching layout must not close the
compare card.
"""

from pathlib import Path

STORE = Path("backend/app/static/studioStore.js")


def _setlayout_body(text: str) -> str:
    """Return the source of the `setLayout(...)` method, up to its closing brace."""
    start = text.index("setLayout(")
    # Find the body open-brace after the parameter list.
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("setLayout( body not terminated — could not find closing brace")


def test_setlayout_does_not_close_compare():
    body = _setlayout_body(STORE.read_text(encoding="utf-8"))
    assert "closeCompare" not in body, (
        "setLayout() must not call closeCompare(): the right layout supports a "
        "three-column Player | cur | cmp arrangement, so compare must stay open "
        "when switching layouts (spec req #3, resizable-panes design)."
    )
