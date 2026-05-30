"""ClipListCacheRepo — persists / reads `clip_list_cache`; read-through
cache for paginated `list_clips` results. Called by the CatDV adapter."""

from __future__ import annotations

import json

import aiosqlite

from backend.app.archive.model import CanonicalClip
from backend.app.repositories.clip_cache import _clip_from_json, _clip_to_json


def _normalize_q(text: str | None) -> str:
    return "" if text is None else text


def _serialize_items(items: tuple[CanonicalClip, ...] | list[CanonicalClip]) -> str:
    # Each clip is serialized with the same shape as `clip_cache.canonical_json`
    # so the two caches can share parsing logic.
    return json.dumps([json.loads(_clip_to_json(c)) for c in items])


def _deserialize_items(raw: str) -> tuple[CanonicalClip, ...]:
    parsed = json.loads(raw)
    return tuple(_clip_from_json(json.dumps(entry)) for entry in parsed)


class ClipListCacheRepo:
    """Read-through cache for paginated list_clips responses."""

    async def get(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
        query_text: str | None,
        offset: int,
        limit: int,
    ) -> dict | None:
        cur = await conn.execute(
            "SELECT total, items_json, fetched_at FROM clip_list_cache "
            "WHERE provider_id = ? AND catalog_id = ? AND query_text = ? "
            "AND offset_ = ? AND limit_ = ?",
            (provider_id, catalog_id, _normalize_q(query_text), offset, limit),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        total, items_json, fetched_at = row
        return {
            "total": int(total),
            "items": _deserialize_items(items_json),
            "fetched_at": fetched_at,
        }

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
        query_text: str | None,
        offset: int,
        limit: int,
        total: int,
        items: tuple[CanonicalClip, ...],
        fetched_at_iso: str,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO clip_list_cache
              (provider_id, catalog_id, query_text, offset_, limit_,
               total, items_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, catalog_id, query_text, offset_, limit_)
            DO UPDATE SET
              total      = excluded.total,
              items_json = excluded.items_json,
              fetched_at = excluded.fetched_at
            """,
            (
                provider_id,
                catalog_id,
                _normalize_q(query_text),
                offset,
                limit,
                total,
                _serialize_items(items),
                fetched_at_iso,
            ),
        )
        await conn.commit()

    async def clips_for_catalog(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
    ) -> dict[str, CanonicalClip]:
        """Every clip we've cached in any list page for this catalog, keyed by
        provider_clip_id. Lets filtered views hydrate locally in one query
        instead of a per-clip CatDV round-trip."""
        cur = await conn.execute(
            "SELECT items_json FROM clip_list_cache "
            "WHERE provider_id = ? AND catalog_id = ?",
            (provider_id, catalog_id),
        )
        out: dict[str, CanonicalClip] = {}
        for (items_json,) in await cur.fetchall():
            for clip in _deserialize_items(items_json):
                out[str(clip.key[1])] = clip
        return out

    async def invalidate_catalog(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
    ) -> int:
        cur = await conn.execute(
            "DELETE FROM clip_list_cache WHERE provider_id = ? AND catalog_id = ?",
            (provider_id, catalog_id),
        )
        await conn.commit()
        return cur.rowcount or 0
