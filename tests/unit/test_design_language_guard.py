"""Design-language enforcement guard (ratchet).

The shared UI library (`docs/design-language.md`, `components/_ui.html`,
`app.css`, `static/format.js`, `static/popover.js`) is only load-bearing if
bypassing it fails CI. Phase 1 (2026-05-27) shipped the library as
documentation only, and bespoke shapes re-grew (`studio-run-btn`,
`hdr-title-btn`, a hand-rolled timecode in `player.js`). This guard makes the
library enforceable.

It is a RATCHET, not a proof of purity. It freezes the known duplication axes
in place and forbids *new* growth on each:

- **Buttons / menus** — class tokens ending in ``btn`` / ``menu`` (outside the
  canonical bases ``btn`` / ``menu``). Reuse ``.btn`` / ``ui.menu``.
- **Modals** — ``modal`` / ``modal-*`` classes. (Candidate B will collapse the
  two modal vocabularies; until then they are frozen.)
- **Cards** — ``*-card`` classes. (Candidate D will unify the clip media card.)
- **Inline styles on form controls** — banned outright, except ``min-height``
  (the one inline style the library sanctions, via ``ui.textarea_field``).
- **JS formatters** — hand-rolled timecode / bytes; call ``fmtTimecode`` /
  ``fmtBytes`` (`static/format.js`).

Each allow-list shrinks as the migration PRs land (see
`docs/plans/2026-06-09-ui-consolidation-rollout-plan.md`). A coder who trips
this is routed to the reuse path by the assert message.

Scope/limits (intentional): only the named families are covered, and only
``class=`` / ``:class=`` attributes are scanned (so comments don't
false-positive). A bespoke ``*-dropdown`` would slip through — the guard
raises the cost of the common cases, it does not prove the absence of all
duplication.
"""

import re
from pathlib import Path

TEMPLATES = Path("backend/app/templates")
STATIC = Path("backend/app/static")

# Canonical bases the whole app is meant to use.
ALLOWED = {"btn", "menu"}

# Bespoke *-btn / *-menu names that exist today. The permanent block are
# intentional exceptions (status / chrome / nav, not action buttons). The
# "to migrate" block is deleted entry-by-entry as each menu/button moves
# onto the popover/menu module.
GRANDFATHERED = {
    # permanent, intentional (not action buttons)
    "shutdown-btn",
    "rail-btn",
    # bespoke *button* styling — a separate follow-up axis (frozen by the
    # guard; not part of the menu consolidation). All menus are migrated.
    "hdr-title-btn",
    "mp-fail-btn",
    "studio-run-btn",
}

# Modal classes — frozen pending Candidate B (one ui.modal vocabulary).
MODAL_GRANDFATHERED = {
    "modal",
    "modal-actions",
    "modal-backdrop",
    "modal-body",
    "modal-card",
    "modal-dialog",
    "modal-field",
    "modal-foot",
    "modal-h",
    "modal-hdr",
    "modal-label",
    "modal-overlay",
}

# Card classes — frozen pending Candidate D (one ui.clip_card). shutdown-card
# is a permanent chrome exception.
CARD_GRANDFATHERED = {
    "cmp-card",
    "modal-card",
    "nb-card",
    "ri-card",
    "shutdown-card",
    "studio-clip-card",
    "studio-prompt-card",
}

# player.js owns the canonical frame-accurate SMPTE formatter `tc()`
# (hh:mm:ss:ff, fps-aware) — a distinct, unique module, NOT a duplicate of
# fmtTimecode (which is m:ss). Permanent exception.
FORMATTER_GRANDFATHERED = {"player.js"}

# class-attr scanners: double- and single-quoted handled separately so an attr
# with inner quotes (class="x{% if s == 'cmp' %} cmp-card{% endif %}") is read
# whole.
_CLASS_DQ = re.compile(r'(?::class|class)\s*=\s*"([^"]*)"')
_CLASS_SQ = re.compile(r"(?::class|class)\s*=\s*'([^']*)'")
_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

# inline style on a form control, except a sole min-height (sanctioned).
_CONTROL_STYLE = re.compile(
    r'<(?:input|textarea|select)\b[^>]*\bstyle\s*=\s*"([^"]*)"', re.IGNORECASE
)
_MIN_HEIGHT_ONLY = re.compile(r"^\s*min-height\s*:[^;]+;?\s*$", re.IGNORECASE)


def _class_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for value in _CLASS_DQ.findall(text) + _CLASS_SQ.findall(text):
        tokens.update(_TOKEN.findall(value))
    return tokens


def _scan_tokens(predicate) -> dict[str, list[str]]:
    """token -> sorted files, over all templates, for tokens matching predicate."""
    hits: dict[str, set[str]] = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        for tok in _class_tokens(path.read_text(encoding="utf-8")):
            if predicate(tok):
                hits.setdefault(tok, set()).add(path.name)
    return {tok: sorted(files) for tok, files in hits.items()}


def _is_menu_or_btn(tok: str) -> bool:
    return tok.endswith(("btn", "menu")) and tok not in ALLOWED


def _is_modal(tok: str) -> bool:
    return tok == "modal" or tok.startswith("modal-")


def _is_card(tok: str) -> bool:
    return tok.endswith("-card")


def _assert_within(family: str, hits: dict, allow: set[str], reuse: str):
    violations = {tok: files for tok, files in hits.items() if tok not in allow}
    assert not violations, (
        f"Bespoke {family} class(es) found — {reuse} "
        f"(see docs/design-language.md). Offenders: {violations}. If a new "
        "bespoke class is genuinely intentional, add it to the allow-list "
        "with a comment."
    )


def test_no_unlisted_menu_or_button_classes():
    _assert_within(
        "*-btn / *-menu",
        _scan_tokens(_is_menu_or_btn),
        GRANDFATHERED,
        "reuse the .btn system or the ui.menu / ui.menu_item macros",
    )


def test_no_unlisted_modal_classes():
    _assert_within(
        "modal",
        _scan_tokens(_is_modal),
        MODAL_GRANDFATHERED,
        "reuse the modal vocabulary (one ui.modal is coming — Candidate B)",
    )


def test_no_unlisted_card_classes():
    _assert_within(
        "*-card",
        _scan_tokens(_is_card),
        CARD_GRANDFATHERED,
        "reuse an existing card (ui.clip_card is coming — Candidate D)",
    )


def test_grandfather_lists_have_no_dead_entries():
    """Keep the allow-lists honest: every grandfathered token still exists."""
    checks = [
        ("menu/btn", GRANDFATHERED, set(_scan_tokens(_is_menu_or_btn))),
        ("modal", MODAL_GRANDFATHERED, set(_scan_tokens(_is_modal))),
        ("card", CARD_GRANDFATHERED, set(_scan_tokens(_is_card))),
    ]
    dead = {name: sorted(allow - live) for name, allow, live in checks if allow - live}
    assert not dead, (
        f"Allow-list(s) name class(es) no longer present: {dead}. "
        "Delete them — the ratchet only tightens."
    )


def test_no_inline_style_on_form_controls():
    """No inline style= on input/textarea/select, except a sole min-height."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        bad = [
            s
            for s in _CONTROL_STYLE.findall(path.read_text(encoding="utf-8"))
            if not _MIN_HEIGHT_ONLY.match(s)
        ]
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "Inline style= on a form control — use .field / .txt / .txt-area or "
        f"ui.field / ui.textarea_field (min-height is the only exception): {offenders}"
    )


def test_no_hand_rolled_formatters_in_js():
    """Timecode/byte formatting must call format.js, not be re-implemented."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(STATIC.glob("*.js")):
        if path.name in {"format.js", "popover.js"} or path.name in FORMATTER_GRANDFATHERED:
            continue
        text = path.read_text(encoding="utf-8")
        flags = []
        if "padStart" in text and "% 60" in text:
            flags.append("hand-rolled timecode -> window.fmtTimecode")
        if re.search(r"\b1024\b", text):
            flags.append("byte-scaling loop -> window.fmtBytes")
        if flags:
            offenders[path.name] = flags
    assert not offenders, (
        f"Hand-rolled formatter(s) found — call format.js helpers: {offenders}"
    )


def test_guard_detects_fresh_bespoke_shapes():
    """Self-test: the scanners flag newly-introduced bespoke names."""
    assert _is_menu_or_btn("foo-menu") and _is_menu_or_btn("foo-btn")
    assert not _is_menu_or_btn("btn") and not _is_menu_or_btn("menu")
    assert _is_modal("modal") and _is_modal("modal-x") and not _is_modal("modeless")
    assert _is_card("foo-card") and not _is_card("cardamom")
    assert _class_tokens("<a class=\"x{% if s == 'cmp' %} cmp-card{% endif %}\">") >= {"cmp-card"}
