"""Phantom-token fallbacks (e.g. var(--bg-3, #1f1f1f) where --bg-3 has
never been defined in :root) silently render the hex fallback forever
and lie about being design-system compliant. PR3 removes them; this
gate prevents regressions."""

import re
from pathlib import Path

PHANTOM_TOKENS = (
    "--bg-3",
    "--accent-fade",
    "--border",
    "--fg-muted",
)

BANNED_RGBA = (
    "rgba(74, 144, 226",
    "rgba(220, 140, 60",
    "rgba(220, 60, 60",
    "rgba(60, 180, 90",
)


def _root_defines(css: str, name: str) -> bool:
    root_block = css.split(":root", 1)[1].split("}", 1)[0]
    return re.search(rf"^\s*{re.escape(name)}\s*:", root_block, re.MULTILINE) is not None


def test_no_phantom_token_fallbacks():
    css = Path("backend/app/static/app.css").read_text()
    for tok in PHANTOM_TOKENS:
        if _root_defines(css, tok):
            continue
        pattern = re.compile(rf"var\(\s*{re.escape(tok)}\b")
        for m in pattern.finditer(css):
            line_no = css[: m.start()].count("\n") + 1
            raise AssertionError(
                f"phantom-token fallback for {tok} at app.css:{line_no} — "
                f"PR3 spec mandates replacement with a real token."
            )


def test_no_raw_studio_rgba_colors():
    css = Path("backend/app/static/app.css").read_text()
    for needle in BANNED_RGBA:
        if needle in css:
            line_no = css[: css.index(needle)].count("\n") + 1
            raise AssertionError(
                f"raw rgba color {needle!r} reintroduced at app.css:{line_no} — "
                f"PR3 spec replaced these with tokens / color-mix()."
            )
