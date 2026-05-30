"""Guard against reaching into Alpine's private `_x_dataStack` internal.

`_x_dataStack` is an undocumented Alpine.js implementation detail that
changes across versions. Our own code must reach cross-component state
through public APIs (`Alpine.store(...)`, custom DOM events) instead.

The vendored `alpine.min.js` legitimately defines `_x_dataStack` as part
of its own internals — that bundle is excluded from the scan. T3-C2 later
widens this guard beyond the studio-relevant tree.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
TEMPLATES = Path("backend/app/templates")
NEEDLE = "_x_dataStack"


def _scan(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # alpine.min.js (and any other vendored bundle) defines the
        # internal itself — that is not a reach-in.
        if "vendor" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if NEEDLE in text:
            hits.append(str(path))
    return hits


def test_no_x_data_stack_reach_ins():
    hits = _scan(STATIC) + _scan(TEMPLATES)
    assert hits == [], (
        f"{NEEDLE} reach-ins found (use Alpine.store('studio') / DOM events "
        f"instead): {hits}"
    )
