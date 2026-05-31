"""Frontend UX-discipline guard (T3-B3).

CLAUDE.md "Frontend error handling" bans three patterns in our own
JS (everything under backend/app/static/ except vendored libs):

  * ``alert(`` — user-facing errors must go through the toast store.
  * ``location.reload()`` / ``window.location.reload()`` after a CRUD
    action — endpoints return HTMX partials; JS swaps them in place.
  * a *silent* ``.catch(() => {})`` that swallows a user-meaningful
    failure with no toast / surfacing.

There is no JS test runner in this Python-only stack, so this static
scan is the regression guard. It deliberately operates on source text,
not an AST, because the rules are about literal forbidden call-sites and
a small, explicitly-justified allowlist is clearer than a parser.

Each carve-out below is enumerated by ``file:line`` (or covered by a
``// silent-catch-ok`` pragma on the offending line) WITH a reason, so a
future contributor adding a new empty ``.catch`` is forced to justify it
here rather than silently widening the hole.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "app" / "static"


# ── empty-swallow .catch allowlist ──────────────────────────────────
# Empty ``.catch(() => {})`` is banned EXCEPT these call-sites, which do
# not swallow a user-meaningful failure. New entries must carry a reason.
# (A ``// silent-catch-ok`` trailing comment on the line is also accepted
# as an inline, self-documenting alternative to listing it here.)
ALLOWED_EMPTY_CATCH = {
    # video.play() can reject under the browser autoplay policy when the
    # user hasn't interacted yet — expected, not actionable, not a failure
    # the user can do anything about. Toasting it would be noise.
    ("player.js", "v.paused"),       # togglePlay()
    ("player.js", "if (v) v.play"),  # play()
    ("player.js", "v.play().catch"), # seek()
}

JS_FILES = sorted(
    p for p in STATIC_DIR.glob("*.js")
    # vendor/ is a subdir, glob("*.js") already excludes it; guard anyway.
    if "vendor" not in p.parts
)


def _code_lines(text: str):
    """Yield (1-based lineno, line) skipping whole-line // and * comments.

    Good enough for this guard: the forbidden tokens we scan for never
    legitimately appear inside string literals in our code, and the only
    real false-positive risk is documentation/comments (e.g. toast.js's
    docstring mentioning alert(), or promptEditor's comment explaining why
    it does NOT reload). Block-comment bodies in our files are ``*``-led.
    """
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        yield i, line


def test_js_files_present():
    # Guard against the scan silently passing because it found nothing.
    assert JS_FILES, f"no .js files found under {STATIC_DIR}"


def test_no_alert_calls():
    offenders = []
    for path in JS_FILES:
        for lineno, line in _code_lines(path.read_text(encoding="utf-8")):
            # `alert(` as a call: word-boundary before so we don't match
            # e.g. `myalert(`.
            if re.search(r"\balert\s*\(", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "alert() is banned — route user-facing errors through "
        "Alpine.store('toast').push(...). Offenders:\n" + "\n".join(offenders)
    )


def test_no_location_reload():
    offenders = []
    for path in JS_FILES:
        for lineno, line in _code_lines(path.read_text(encoding="utf-8")):
            if re.search(r"location\s*\.\s*reload\s*\(", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "location.reload() after a CRUD action is banned — return an HTMX "
        "partial and swap it in place. Offenders:\n" + "\n".join(offenders)
    )


def test_no_silent_empty_catch():
    # Matches `.catch(() => {})` / `.catch(()=>{})` (any inner whitespace),
    # i.e. an arrow fn with an empty body. A non-empty body or a
    # fallback-return like `.catch(() => ({...}))` is NOT matched.
    empty_catch = re.compile(r"\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)")
    offenders = []
    for path in JS_FILES:
        for lineno, line in _code_lines(path.read_text(encoding="utf-8")):
            if not empty_catch.search(line):
                continue
            if "// silent-catch-ok" in line:
                continue
            if any(
                path.name == fname and marker in line
                for (fname, marker) in ALLOWED_EMPTY_CATCH
            ):
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "empty `.catch(() => {})` swallows a failure silently — surface it "
        "via toast, or add a justified allowlist entry / `// silent-catch-ok` "
        "pragma if it is genuinely non-actionable. Offenders:\n"
        + "\n".join(offenders)
    )
