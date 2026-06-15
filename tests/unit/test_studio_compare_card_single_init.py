"""Guard: the compare card injected by openCompare is initialized ONCE.

`openCompare()` (studioStore.js) injects the cmp prompt-card via
`fetch()` + `innerHTML`. That card carries its own `x-data`
(`studioPromptCard('cmp', …)`). Alpine's MutationObserver reliably
initializes a freshly-inserted x-data root on its own, so ALSO calling
`Alpine.initTree()` on the slot (via `htmxAlpine.reinit`) binds every
directive on the card twice. A double-bound `@click` on the Diff toggle
flips `$store.studio.compareDiff` twice per click — back to its original
value — so the Diff button looked dead when compare was reached by
clicking (it worked after a reload, where the card is server-rendered and
initialized once). See the diff-button double-bind investigation.

The fix: wire only HTMX on the injected card (`htmxAlpine.wireHtmx`), and
let Alpine's observer do the single Alpine init. This test pins that
contract so the `reinit` (initTree + process) double-init can't creep back
onto the compare-card injection path.
"""

import re
from pathlib import Path

STATIC = Path("backend/app/static")


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _open_compare_body() -> str:
    """The body of openCompare() in studioStore.js."""
    src = _read("studioStore.js")
    start = src.index("async openCompare(")
    # The next store method begins at "\n    closeCompare(" — slice up to it.
    end = src.index("closeCompare(", start)
    return src[start:end]


def test_htmx_alpine_exposes_htmx_only_wiring():
    """htmxAlpine must offer an HTMX-only wiring helper that does NOT run
    Alpine.initTree (so injected x-data roots aren't double-initialized)."""
    src = _read("htmxAlpine.js")
    assert "wireHtmx" in src, "htmxAlpine.js must define a wireHtmx() helper"
    # Slice the wireHtmx method body precisely: from its definition to the
    # closing "},".
    start = src.index("wireHtmx(el)")
    body = src[start:src.index("},", start)]
    assert ".process(" in body, "wireHtmx must call htmx.process()"
    assert "initTree(" not in body, (
        "wireHtmx must NOT call Alpine.initTree() — the whole point is to let "
        "Alpine's MutationObserver do the single init of the injected x-data root"
    )


def test_open_compare_wires_htmx_only_not_full_reinit():
    """openCompare must wire the injected cmp card with the HTMX-only helper,
    never the full reinit (initTree + process), which double-binds it."""
    body = _open_compare_body()
    assert "htmxAlpine.wireHtmx(" in body, (
        "openCompare must use htmxAlpine.wireHtmx() for the injected cmp card"
    )
    assert "htmxAlpine.reinit(" not in body, (
        "openCompare must NOT call htmxAlpine.reinit() on the cmp card — "
        "Alpine's observer already inits the injected x-data root, so reinit "
        "double-binds every directive (the dead Diff-button bug)"
    )
