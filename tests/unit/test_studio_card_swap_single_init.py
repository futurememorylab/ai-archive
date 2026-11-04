"""Guard: a prompt card swapped in by HTMX is initialized ONCE.

After a version switch the card comes back via hx-swap="outerHTML". It has
its own x-data, so Alpine's MutationObserver initializes it. The afterSwap
handler in htmxAlpine.js used to call Alpine.initTree(card) on top of that,
so every @click in the card fired twice: "+ Compare" opened the compare card
twice and the compare card's Diff toggle flipped on and straight back off,
so compare looked broken until a reload. Same rule as the connection chip
(test_connection_chip_no_double_init.py) and wireHtmx().
"""

from pathlib import Path

STATIC = Path("backend/app/static")


def test_after_swap_does_not_init_the_swapped_card_again():
    src = (STATIC / "htmxAlpine.js").read_text(encoding="utf-8")
    body = src[src.index("addEventListener('htmx:afterSwap'") :]
    code = "\n".join(line.split("//")[0] for line in body.splitlines())
    assert "initTree(" not in code, (
        "afterSwap must not call Alpine.initTree(): the swapped prompt card has "
        "its own x-data, so the observer already initializes it and a second "
        "init double-binds every click"
    )
