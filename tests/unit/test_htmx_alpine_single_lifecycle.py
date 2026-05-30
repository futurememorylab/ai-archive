"""Guard: HTMX↔Alpine lifecycle wiring lives in exactly ONE file.

`Alpine.initTree(` and `htmx.process(` re-scan a DOM subtree so Alpine
directives and HTMX attributes injected by a JS `fetch()`+innerHTML (or
swapped by HTMX) come alive. Spreading those calls across studio.js /
studioStore.js made the lifecycle wiring impossible to reason about as a
whole. T3-B2 consolidates them into a single helper module,
`htmxAlpine.js`, which owns `window.htmxAlpine.reinit(el)` and the one
global `htmx:afterSwap` listener.

This test asserts each literal appears in exactly one static file, and
that file is `htmxAlpine.js`. The vendored bundles legitimately define
these symbols themselves — they are excluded from the scan.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
# Match both the plain (`Alpine.initTree(`) and optional-chained
# (`Alpine?.initTree(`, `htmx?.process(`) call forms used in our code.
NEEDLES = ("initTree(", "htmx?.process(")


def _files_containing(needle: str) -> list[str]:
    hits: list[str] = []
    for path in sorted(STATIC.rglob("*")):
        if not path.is_file():
            continue
        if "vendor" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if needle in text:
            hits.append(path.name)
    return hits


def test_lifecycle_calls_live_in_single_helper():
    for needle in NEEDLES:
        hits = _files_containing(needle)
        assert hits == ["htmxAlpine.js"], (
            f"{needle!r} must appear in exactly one static file "
            f"(htmxAlpine.js); found in: {hits}"
        )
