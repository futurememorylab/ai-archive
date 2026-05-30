"""Source-grep regression: no sync filesystem I/O inside `async def`
blocks in services/cache_actions.py. The list expands in tier 3 to
cover the whole services/ tree.

Catches reintroduction of os.unlink, Path.exists, Path.read_text, raw
open() calls inside async def. CI-friendly: dumber than runtime
detection but always-on."""

import ast
import re
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[2] / "backend" / "app" / "services"

# Tier 2 scope: cache_actions only. Tier 3 expands to the whole tree.
_FILES_TO_CHECK = [SERVICES / "cache_actions.py"]

# Patterns banned inside async def. Each matches a direct call expression
# (requires the opening parenthesis so that passing the function as a
# callable argument to asyncio.to_thread doesn't trigger a false positive).
_BANNED = re.compile(
    r"\b(os\.unlink\s*\(|Path\.unlink\s*\(|Path\.exists\s*\(|"
    r"Path\.read_text\s*\(|Path\.write_text\s*\(|"
    r"Path\.read_bytes\s*\(|Path\.write_bytes\s*\(|"
    r"open\s*\()"
)


def _async_def_lines(path: Path) -> set[int]:
    """Return the set of line numbers inside any `async def` in `path`."""
    tree = ast.parse(path.read_text())
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for line in range(node.lineno, node.end_lineno + 1):
                inside.add(line)
    return inside


def test_no_sync_fs_inside_async_def_in_cache_actions():
    bad: list[tuple[Path, int, str]] = []
    for path in _FILES_TO_CHECK:
        text = path.read_text()
        async_lines = _async_def_lines(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno not in async_lines:
                continue
            if _BANNED.search(line):
                bad.append((path, lineno, line.strip()))
    assert not bad, (
        "sync filesystem I/O found inside async def — wrap in "
        "asyncio.to_thread:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in bad)
    )
