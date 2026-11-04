"""Guard: a prompt card swapped in by HTMX is initialized ONCE.

After a version switch the card comes back via hx-swap="outerHTML". Alpine's
MutationObserver initializes it; the afterSwap handler in htmxAlpine.js used
to call Alpine.initTree(card) on top of that, so every @click in the card
fired twice. "+ Compare" then opened the compare card twice and the compare
card's Diff toggle flipped on and straight back off, so compare looked broken
until a reload. The handler now only initializes a card the observer skipped,
which it recognizes by the data-alpine-ready mark the card's init() sets.
"""

from pathlib import Path

STATIC = Path("backend/app/static")


def _after_swap_body() -> str:
    src = (STATIC / "htmxAlpine.js").read_text(encoding="utf-8")
    return src[src.index("addEventListener('htmx:afterSwap'") :]


def test_after_swap_inits_card_only_when_not_ready():
    body = _after_swap_body()
    i = body.index("initTree(card)")
    guard = body[max(0, i - 200) : i]
    assert "data-alpine-ready" in guard, (
        "afterSwap must only initTree() a card that has no data-alpine-ready "
        "mark; an unconditional initTree double-binds every click in the card"
    )


def test_prompt_card_marks_itself_ready_on_init():
    src = (STATIC / "studioPromptCard.js").read_text(encoding="utf-8")
    init = src[src.index("init() {") : src.index("pickModel(")]
    assert "data-alpine-ready" in init
