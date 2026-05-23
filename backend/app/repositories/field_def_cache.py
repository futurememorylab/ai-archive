"""FieldDefCacheRepo — persists / reads `field_def_cache`; per-provider
catalog FieldDef snapshots with TTL. Called by the CatDV adapter."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime

import aiosqlite

from backend.app.archive.model import FieldDef


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _field_def_to_json(fd: FieldDef) -> str:
    return json.dumps(
        {
            "identifier": fd.identifier,
            "name": fd.name,
            "type": fd.type,
            "is_multi": fd.is_multi,
            "is_editable": fd.is_editable,
            "picklist_values": list(fd.picklist_values) if fd.picklist_values is not None else None,
            "provider_data": fd.provider_data,
        }
    )


def _field_def_from_json(raw: str) -> FieldDef:
    p = json.loads(raw)
    pv = p.get("picklist_values")
    return FieldDef(
        identifier=p["identifier"],
        name=p["name"],
        type=p["type"],
        is_multi=bool(p["is_multi"]),
        is_editable=bool(p["is_editable"]),
        picklist_values=tuple(pv) if pv is not None else None,
        provider_data=p.get("provider_data") or {},
    )


class FieldDefCacheRepo:
    """DB-backed cache of provider field definitions."""

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        field_def: FieldDef,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO field_def_cache (provider_id, identifier, json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_id, identifier) DO UPDATE SET
              json       = excluded.json,
              fetched_at = excluded.fetched_at
            """,
            (provider_id, field_def.identifier, _field_def_to_json(field_def), _now_iso()),
        )
        await conn.commit()

    async def get_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        identifier: str,
    ) -> FieldDef | None:
        cur = await conn.execute(
            "SELECT json FROM field_def_cache WHERE provider_id = ? AND identifier = ?",
            (provider_id, identifier),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return _field_def_from_json(row[0])

    async def list_for_provider(
        self, conn: aiosqlite.Connection, *, provider_id: str
    ) -> list[FieldDef]:
        cur = await conn.execute(
            "SELECT json FROM field_def_cache WHERE provider_id = ? ORDER BY identifier",
            (provider_id,),
        )
        return [_field_def_from_json(row[0]) for row in await cur.fetchall()]

    async def replace_all_for_provider(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        field_defs: Iterable[FieldDef],
    ) -> None:
        await conn.execute("DELETE FROM field_def_cache WHERE provider_id = ?", (provider_id,))
        now = _now_iso()
        for fd in field_defs:
            await conn.execute(
                "INSERT INTO field_def_cache "
                "(provider_id, identifier, json, fetched_at) VALUES (?, ?, ?, ?)",
                (provider_id, fd.identifier, _field_def_to_json(fd), now),
            )
        await conn.commit()

    async def delete_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        identifier: str,
    ) -> None:
        await conn.execute(
            "DELETE FROM field_def_cache WHERE provider_id = ? AND identifier = ?",
            (provider_id, identifier),
        )
        await conn.commit()

    async def latest_fetched_at(
        self, conn: aiosqlite.Connection, *, provider_id: str
    ) -> str | None:
        cur = await conn.execute(
            "SELECT MAX(fetched_at) FROM field_def_cache WHERE provider_id = ?",
            (provider_id,),
        )
        row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return row[0]
