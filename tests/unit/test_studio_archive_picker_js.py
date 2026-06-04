"""Guard: the studio archive picker fetches its rows from the shared
/batches/picker endpoint (one renderer for pickable clip lists) instead of
rendering its own.

Source-scan guard — the repo has no JS test runner; brace-matching shape
mirrors tests/unit/test_studio_setlayout_keeps_compare.py.
"""

from pathlib import Path

STUDIO_JS = Path("backend/app/static/studio.js")
CLIP_PICKER_JS = Path("backend/app/static/clipPicker.js")


def _component_body(text: str, marker: str) -> str:
    """Source of the component from `marker` to its balanced closing brace."""
    start = text.index(marker)
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
    raise AssertionError(f"{marker} body not terminated — no closing brace")


def test_archive_picker_spreads_shared_core():
    body = _component_body(
        STUDIO_JS.read_text(encoding="utf-8"), "Alpine.data('archivePicker'"
    )
    assert "clipPickerCore" in body, (
        "archivePicker must spread window.clipPickerCore() — the shared "
        "picker — not define its own list logic"
    )


def test_clip_picker_core_owns_the_shared_renderer_contract():
    core = CLIP_PICKER_JS.read_text(encoding="utf-8")
    assert "/batches/picker" in core, "core must fetch the shared picker rows"
    assert "htmxAlpine.reinit" in core, (
        "fetch-injected rows must go through the shared lifecycle helper"
    )
    assert "nb-list-meta" in core, "pager total comes from the shared meta div"
