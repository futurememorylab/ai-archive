"""Guard against the connection-chip double-init regression.

The #connection-chip container is hosted-mode: the swapped-in pill trigger and
dropdown panel carry only directives (@click="toggle()", x-show="open") bound to
the stable container's popover() scope — they have no x-data of their own.
Alpine's MutationObserver already re-binds those directives when htmx swaps the
innerHTML. An explicit `Alpine.initTree(evt.target)` in the htmx:afterSwap
handler bound them a SECOND time, so every click fired toggle() twice
(open→close) and the dropdown could never be opened. The fix was to delete that
branch and rely on the observer.

This guard fails if someone re-adds an explicit re-init of the swap target.
"""

from pathlib import Path

HTMX_ALPINE = Path("backend/app/static/htmxAlpine.js")


def test_afterswap_does_not_reinit_the_swap_target():
    src = HTMX_ALPINE.read_text()
    # `initTree(evt.target)` was the exact buggy call (studio uses
    # initTree(card); reinit() uses initTree(el)). Re-adding it double-binds
    # the connection-chip directives.
    assert "initTree(evt.target)" not in src, (
        "htmx:afterSwap must not Alpine.initTree() the swapped chip subtree — "
        "Alpine's MutationObserver already binds it; an explicit initTree "
        "double-binds @click and breaks the connection dropdown toggle."
    )
