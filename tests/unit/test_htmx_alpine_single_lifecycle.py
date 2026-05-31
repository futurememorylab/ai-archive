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

import re
from pathlib import Path

STATIC = Path("backend/app/static")
# Each needle is a regex matching BOTH the plain and optional-chained
# call forms:
#   - `initTree(`  catches `Alpine.initTree(` and `Alpine?.initTree(`.
#   - `\.process(` catches `htmx.process(` AND `htmx?.process(` — the
#     earlier `htmx?.process(` literal let a plain `htmx.process(`
#     regression slip past the guard (B2 review finding). `\.process(`
#     anchors on the dot so it matches both `.process(` and `?.process(`.
NEEDLES = (re.compile(r"initTree\("), re.compile(r"\.process\("))


def _files_containing(needle: re.Pattern[str]) -> list[str]:
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
        if needle.search(text):
            hits.append(path.name)
    return hits


def test_lifecycle_calls_live_in_single_helper():
    for needle in NEEDLES:
        hits = _files_containing(needle)
        assert hits == ["htmxAlpine.js"], (
            f"{needle.pattern!r} must appear in exactly one static file "
            f"(htmxAlpine.js); found in: {hits}"
        )
