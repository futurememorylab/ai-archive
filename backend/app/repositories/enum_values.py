"""Repository for editable enum value edits (table: enum_values).

Leaf layer — no service imports. Operates only on editable enum keys; the
service enforces editability. `remove` is a soft delete so the boot-time
reconcile never re-adds a value the user deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from backend.app.enums.registry import EnumValueSpec

_COLS = "enum_key, value, label, enabled, is_default, sort_order, source, removed, metadata, created_at"


@dataclass(frozen=True)
class EnumValueRow:
    enum_key: str
    value: str
    label: str | None
    enabled: int
    is_default: int
    sort_order: int
    source: str
    removed: int
    metadata: str | None
    created_at: str


def _row(r: tuple) -> EnumValueRow:
    return EnumValueRow(*r)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EnumValuesRepo:
    async def live_values(self, conn: aiosqlite.Connection, enum_key: str) -> list[EnumValueRow]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM enum_values "
            "WHERE enum_key = ? AND removed = 0 ORDER BY sort_order, value",
            (enum_key,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def all_rows(self, conn: aiosqlite.Connection, enum_key: str) -> list[EnumValueRow]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM enum_values WHERE enum_key = ? ORDER BY sort_order, value",
            (enum_key,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def get(self, conn: aiosqlite.Connection, enum_key: str, value: str) -> EnumValueRow | None:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM enum_values WHERE enum_key = ? AND value = ?",
            (enum_key, value),
        )
        r = await cur.fetchone()
        return _row(r) if r else None

    async def upsert_seed(
        self,
        conn: aiosqlite.Connection,
        enum_key: str,
        spec: EnumValueSpec,
        *,
        sort_order: int,
        commit: bool,
    ) -> None:
        """Insert a seed value only when absent. Never touches an existing row
        (so it neither clobbers user edits nor resurrects a tombstone)."""
        await conn.execute(
            "INSERT OR IGNORE INTO enum_values "
            f"({_COLS}) VALUES (?, ?, ?, 1, ?, ?, 'seed', 0, ?, ?)",
            (
                enum_key,
                spec.value,
                spec.label,
                1 if spec.default else 0,
                sort_order,
                None,
                _now(),
            ),
        )
        if commit:
            await conn.commit()

    async def add_value(
        self,
        conn: aiosqlite.Connection,
        enum_key: str,
        value: str,
        *,
        label: str | None,
        commit: bool,
    ) -> None:
        """Add a user value. If a tombstoned row exists, revive it instead of
        raising (re-adding a previously removed value should succeed)."""
        existing = await self.get(conn, enum_key, value)
        if existing is not None and existing.removed == 1:
            await conn.execute(
                "UPDATE enum_values SET removed = 0, enabled = 1, label = ? "
                "WHERE enum_key = ? AND value = ?",
                (label, enum_key, value),
            )
        else:
            next_sort = await self._next_sort(conn, enum_key)
            await conn.execute(
                "INSERT INTO enum_values "
                f"({_COLS}) VALUES (?, ?, ?, 1, 0, ?, 'user', 0, ?, ?)",
                (enum_key, value, label, next_sort, None, _now()),
            )
        if commit:
            await conn.commit()

    async def _next_sort(self, conn: aiosqlite.Connection, enum_key: str) -> int:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM enum_values WHERE enum_key = ?",
            (enum_key,),
        )
        (n,) = await cur.fetchone()
        return int(n)

    async def set_enabled(
        self, conn: aiosqlite.Connection, enum_key: str, value: str, *, enabled: bool, commit: bool
    ) -> None:
        await conn.execute(
            "UPDATE enum_values SET enabled = ? WHERE enum_key = ? AND value = ? AND removed = 0",
            (1 if enabled else 0, enum_key, value),
        )
        if commit:
            await conn.commit()

    async def set_default(
        self, conn: aiosqlite.Connection, enum_key: str, value: str, *, commit: bool
    ) -> None:
        """Clear the prior default and set this one — atomic pair."""
        await conn.execute(
            "UPDATE enum_values SET is_default = 0 WHERE enum_key = ? AND is_default = 1",
            (enum_key,),
        )
        await conn.execute(
            "UPDATE enum_values SET is_default = 1 WHERE enum_key = ? AND value = ? AND removed = 0",
            (enum_key, value),
        )
        if commit:
            await conn.commit()

    async def soft_delete(
        self, conn: aiosqlite.Connection, enum_key: str, value: str, *, commit: bool
    ) -> None:
        await conn.execute(
            "UPDATE enum_values SET removed = 1, is_default = 0 WHERE enum_key = ? AND value = ?",
            (enum_key, value),
        )
        if commit:
            await conn.commit()

    async def count_enabled(self, conn: aiosqlite.Connection, enum_key: str) -> int:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM enum_values WHERE enum_key = ? AND enabled = 1 AND removed = 0",
            (enum_key,),
        )
        (n,) = await cur.fetchone()
        return int(n)
