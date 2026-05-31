"""Source-grep regression: no UN-PRAGMA'D sync filesystem I/O inside
`async def` blocks anywhere under `services/` and `routes/`.

Catches reintroduction of `os.unlink`, `Path.exists`, `Path.read_text`,
raw `open()`, `.stat()` etc. inside an `async def` — those block the
event loop and must be wrapped in `asyncio.to_thread`.

CI-friendly: dumber than runtime detection but always-on.

### Scope (T3-C2)

Tier 2 scanned only `cache_actions.py`. Tier 3 widens the scan to EVERY
`.py` under `backend/app/services/` and `backend/app/routes/`.

That widening surfaces a set of PRE-EXISTING sync-IO-in-async sites
(thumbnail_service, proxy_resolver, catdv_client, proxy_cache_reconciler,
media_prefetcher, routes/media). They are tracked for the tier-4 async-io
pass, NOT introduced by tier-3. To keep the guard's real value — failing
on NEW un-pragma'd sync-IO — each known site carries an inline escape
hatch: either a `# sync-io-ok` pragma (with a short justification) or an
existing `# noqa: ASYNC2*` async-lint suppression. A line carrying either
is allowed; anything else inside an `async def` that matches a banned
call is a failure.

The one site that should stay WRAPPED (not pragma'd) is
`cache_actions.py`, which routes its file ops through `asyncio.to_thread`
(tier 2). Function REFERENCES passed to `to_thread` (e.g.
`asyncio.to_thread(p.exists)`) are not call expressions and so never
match the banned regex — the regex anchors on the opening `(`.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "backend" / "app"
SCAN_DIRS = [APP / "services", APP / "routes"]

# Patterns banned inside async def. Each matches a direct call expression
# (requires the opening parenthesis so that passing the function as a
# callable argument to asyncio.to_thread doesn't trigger a false positive
# — handoff lesson 9).
_BANNED = re.compile(
    r"(os\.unlink\s*\(|os\.remove\s*\(|Path\.unlink\s*\(|\.unlink\s*\(|"
    r"\.exists\s*\(|\.read_text\s*\(|\.write_text\s*\(|"
    r"\.read_bytes\s*\(|\.write_bytes\s*\(|\bopen\s*\(|\.stat\s*\()"
)

# A line carrying either escape hatch is allowed: an explicit
# `# sync-io-ok` pragma, or an existing `# noqa: ASYNC2*` async-lint
# suppression (these mark the same pre-existing tech debt).
_ALLOW = re.compile(r"#\s*sync-io-ok|#\s*noqa:\s*ASYNC2")


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_DIRS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


def _async_def_lines(tree: ast.AST) -> set[int]:
    """Return the set of line numbers inside any `async def`."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                inside.add(line)
    return inside


def test_no_sync_fs_inside_async_def():
    bad: list[tuple[Path, int, str]] = []
    for path in _iter_py_files():
        text = path.read_text()
        async_lines = _async_def_lines(ast.parse(text))
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno not in async_lines:
                continue
            if _ALLOW.search(line):
                continue
            if _BANNED.search(line):
                bad.append((path, lineno, line.strip()))
    assert not bad, (
        "un-pragma'd sync filesystem I/O found inside async def — wrap in "
        "asyncio.to_thread, or (if pre-existing tech debt deferred to the "
        "tier-4 async-io pass) add a `# sync-io-ok: <why>` pragma:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in bad)
    )
