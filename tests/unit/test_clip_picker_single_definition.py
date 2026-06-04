"""Guard: the clip-picker logic is defined exactly once — in
static/clipPicker.js. If any of these method definitions appear in a
second non-vendor file under static/ or templates/, someone is growing
a parallel picker again (spec v2:
docs/specs/2026-06-04-studio-archive-picker-reuse-design.md).

Scan shape mirrors tests/unit/test_no_x_data_stack.py.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
TEMPLATES = Path("backend/app/templates")
CORE = "backend/app/static/clipPicker.js"
NEEDLES = (
    "async fetchPage(",
    "_syncFromCheckbox(",
    "_applyChecked(",
    "_renderSelected(",
)


def _files_containing(needle: str) -> list[str]:
    hits: list[str] = []
    for root in (STATIC, TEMPLATES):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "vendor" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if needle in text:
                hits.append(str(path))
    return hits


def test_picker_methods_defined_only_in_core():
    for needle in NEEDLES:
        hits = _files_containing(needle)
        assert hits == [CORE], (
            f"picker method '{needle}' found outside the shared core "
            f"(static/clipPicker.js) — reuse window.clipPickerCore() "
            f"instead of redefining it: {hits}"
        )
