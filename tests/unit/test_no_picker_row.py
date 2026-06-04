"""Guard: the bare `picker-row` clip-list renderer is gone — pickable clip
lists render through the shared _video_list.html scaffold served by
/batches/picker (spec:
docs/specs/2026-06-04-studio-archive-picker-reuse-design.md).

Scan shape mirrors tests/unit/test_no_x_data_stack.py: every file under
static/ and templates/, vendor excluded.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
TEMPLATES = Path("backend/app/templates")
NEEDLE = "picker-row"


def _scan(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "vendor" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if NEEDLE in text:
            hits.append(str(path))
    return hits


def test_no_picker_row_renderer():
    hits = _scan(STATIC) + _scan(TEMPLATES)
    assert hits == [], (
        f"bare '{NEEDLE}' renderer found — render through the shared "
        f"/batches/picker rows (_video_list.html) instead: {hits}"
    )
