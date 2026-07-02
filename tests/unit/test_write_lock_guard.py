"""Guard for the issue #115 invariant: every write on the app's shared
aiosqlite connection is serialized by ``WriteSerializedConnection``.

The serializer only protects writes that flow through it. If any module opens
its own ``aiosqlite.connect(...)`` for the app connection, or the wiring in
``context.py`` stops wrapping the connection, writes would once again share the
raw connection with no lock — the exact atomicity break #115 fixed. These
static checks fail CI if that regresses. See ADR 0126.

Escape hatch: a ``# raw-conn-ok`` pragma on the same line (for a genuinely
isolated, single-owner connection that must not be wrapped).
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "backend" / "app"
DB_MODULE = APP / "db.py"
CONTEXT_MODULE = APP / "context.py"

_ALLOW = "# raw-conn-ok"


def _aiosqlite_connect_calls(tree: ast.AST) -> list[int]:
    """Line numbers of ``aiosqlite.connect(...)`` calls."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
            and func.value.id == "aiosqlite"
        ):
            hits.append(node.lineno)
    return hits


def test_only_db_module_opens_the_shared_connection():
    """``aiosqlite.connect`` must live only in ``db.py`` (behind ``open_db``);
    anywhere else bypasses the write serializer."""
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts or path == DB_MODULE:
            continue
        text = path.read_text()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for lineno in _aiosqlite_connect_calls(tree):
            if _ALLOW in lines[lineno - 1]:
                continue
            offenders.append(f"  {path}:{lineno}: {lines[lineno - 1].strip()}")
    assert not offenders, (
        "aiosqlite.connect(...) found outside backend/app/db.py — the app's "
        "shared connection must be created by open_db() and wrapped in "
        "WriteSerializedConnection (ADR 0126). Route through open_db, or add a "
        "`# raw-conn-ok` pragma for a genuinely isolated single-owner "
        "connection:\n" + "\n".join(offenders)
    )


def test_context_wraps_the_connection_in_the_serializer():
    """``CoreCtx.create`` must wrap the raw connection in
    ``WriteSerializedConnection`` before handing it to services."""
    text = CONTEXT_MODULE.read_text()
    assert "WriteSerializedConnection(" in text, (
        "context.py no longer wraps the connection in WriteSerializedConnection "
        "— every service would share the raw, unserialized connection (#115)."
    )
