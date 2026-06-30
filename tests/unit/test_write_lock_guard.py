"""AST guard: every call passing ``commit=False`` to a repo must be inside
an ``async with …write_lock:`` block.

Catches the root cause of issue #115: a new multi-statement write sequence
added without the lock can be prematurely committed by another writer's
``commit()`` on the shared aiosqlite connection.

Escape hatch: a ``# write-lock-ok`` pragma on the same line (same shape as
the ``# sync-io-ok`` pragma in ``test_no_sync_fs_in_async.py``).
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "backend" / "app"

_ALLOW = "# write-lock-ok"


def _write_lock_async_with_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """Line ranges of ``async with …write_lock:`` blocks."""
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            expr = item.context_expr
            # Match ctx.write_lock, self._write_lock, self.write_lock, etc.
            if isinstance(expr, ast.Attribute) and expr.attr in ("write_lock", "_write_lock"):
                ranges.append((node.lineno, node.end_lineno or node.lineno))
                break
            # Match a bare `write_lock` name
            if isinstance(expr, ast.Name) and expr.id == "write_lock":
                ranges.append((node.lineno, node.end_lineno or node.lineno))
                break
    return ranges


def _commit_false_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Line numbers and source snippet of calls passing ``commit=False``."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "commit"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
            ):
                hits.append((node.lineno, ""))
                break
    return hits


def _in_any_range(lineno: int, ranges: list[tuple[int, int]]) -> bool:
    return any(lo <= lineno <= hi for lo, hi in ranges)


def test_commit_false_calls_inside_write_lock():
    bad: list[tuple[Path, int, str]] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lock_ranges = _write_lock_async_with_ranges(tree)
        lines = text.splitlines()
        for lineno, _ in _commit_false_calls(tree):
            if _in_any_range(lineno, lock_ranges):
                continue
            if _ALLOW in lines[lineno - 1]:
                continue
            bad.append((path, lineno, lines[lineno - 1].strip()))
    assert not bad, (
        "call passing commit=False found OUTSIDE an `async with write_lock:` "
        "block — wrap the multi-statement write in `async with ctx.write_lock:` "
        "(or `self._write_lock:`), or add a `# write-lock-ok` pragma if "
        "justified:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in bad)
    )


def test_guard_catches_unwrapped_commit_false(tmp_path):
    """The guard fails on a synthetic file with an unwrapped commit=False call."""
    fake = tmp_path / "fake.py"
    fake.write_text(
        "async def f(conn):\n"
        "    await repo.upsert_seed(conn, 'k', 'v', commit=False)\n"
    )
    tree = ast.parse(fake.read_text())
    lock_ranges = _write_lock_async_with_ranges(tree)
    hits = _commit_false_calls(tree)
    unwrapped = [ln for ln, _ in hits if not _in_any_range(ln, lock_ranges)]
    assert unwrapped, "guard should detect the unwrapped commit=False call"


def test_guard_allows_wrapped_commit_false(tmp_path):
    """The guard passes on a synthetic file with a wrapped commit=False call."""
    fake = tmp_path / "fake.py"
    fake.write_text(
        "async def f(conn, lock):\n"
        "    async with lock.write_lock:\n"
        "        await repo.upsert_seed(conn, 'k', 'v', commit=False)\n"
        "        await conn.commit()\n"
    )
    tree = ast.parse(fake.read_text())
    lock_ranges = _write_lock_async_with_ranges(tree)
    hits = _commit_false_calls(tree)
    unwrapped = [ln for ln, _ in hits if not _in_any_range(ln, lock_ranges)]
    assert not unwrapped, "guard should NOT flag a wrapped commit=False call"
