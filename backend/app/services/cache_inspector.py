"""CacheInspector: read-only view over the three cache layers.

The UI never queries `clip_cache` / `proxy_cache` / `ai_store_files`
directly — every per-clip badge, popover, and the `/cache` page goes
through this service. That lets PR 7 (FS adapter) and any future
adapters add layers without rewriting templates.

Layers, per spec §9.1:
  * metadata    — `clip_cache` row + `canonical_json` bytes.
  * media-local — `proxy_cache` row + on-disk proxy file under DATA_DIR.
  * media-ai    — `ai_store_files` rows for the active AIInputStore.

`last_used_at` for `metadata` is `clip_cache.fetched_at` (no per-row
access column exists today — see decisions doc). For the other two
layers the underlying table has its own `last_used_at`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import aiosqlite

from backend.app.archive.model import ClipKey

Layer = Literal["metadata", "media-local", "media-ai"]


@dataclass(frozen=True)
class LayerStatus:
    layer: Layer
    present: bool
    size_bytes: int | None
    location: str | None
    fetched_at: datetime | None
    last_used_at: datetime | None
    pinned_by_workspaces: tuple[int, ...]
    evictable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "present": self.present,
            "size_bytes": self.size_bytes,
            "location": self.location,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "last_used_at": (self.last_used_at.isoformat() if self.last_used_at else None),
            "pinned_by_workspaces": list(self.pinned_by_workspaces),
            "evictable": self.evictable,
        }


@dataclass(frozen=True)
class ClipCacheStatus:
    clip_key: ClipKey
    name: str
    layers: tuple[LayerStatus, ...]
    total_local_bytes: int
    total_ai_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_key": list(self.clip_key),
            "name": self.name,
            "layers": [layer.to_dict() for layer in self.layers],
            "total_local_bytes": self.total_local_bytes,
            "total_ai_bytes": self.total_ai_bytes,
        }


@dataclass(frozen=True)
class CacheSummary:
    total_local_bytes: int
    total_ai_bytes: int
    counts_by_store: dict[str, int] = field(default_factory=dict)
    counts_by_workspace: dict[int, int] = field(default_factory=dict)
    metadata_clip_count: int = 0
    media_local_clip_count: int = 0
    pending_ops_count: int = 0
    media_cache_cap_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_local_bytes": self.total_local_bytes,
            "total_ai_bytes": self.total_ai_bytes,
            "counts_by_store": dict(self.counts_by_store),
            "counts_by_workspace": {str(k): v for k, v in self.counts_by_workspace.items()},
            "metadata_clip_count": self.metadata_clip_count,
            "media_local_clip_count": self.media_local_clip_count,
            "pending_ops_count": self.pending_ops_count,
            "media_cache_cap_bytes": self.media_cache_cap_bytes,
        }


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


class CacheInspector:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        media_cache_cap_bytes: int = 0,
        provider: Any | None = None,
        host_local_proxies: bool = False,
    ) -> None:
        self._db_provider = db_provider
        self._cap = media_cache_cap_bytes
        self._provider = provider
        self._host_local = host_local_proxies

    # --- single + batch ----------------------------------------------

    async def status_for_clip(self, key: ClipKey) -> ClipCacheStatus:
        results = await self.status_for_clips([key])
        return results[0]

    async def status_for_clips(self, keys: Sequence[ClipKey]) -> list[ClipCacheStatus]:
        db = self._db_provider()
        if not keys:
            return []

        # Fetch per-layer rows in one batched pass each.
        metadata = await self._load_metadata(db, keys)
        media_local = {} if self._host_local else await self._load_media_local(db, keys)
        media_ai = await self._load_media_ai(db, keys)
        pins = await self._load_pins(db, keys)
        pending = await self._load_pending_counts(db, keys)

        out: list[ClipCacheStatus] = []
        for key in keys:
            md_row = metadata.get(key)
            ml_row = media_local.get(key)
            ai_rows = media_ai.get(key, [])
            ws_ids = tuple(pins.get(key, ()))
            pending_n = pending.get(key, 0)

            md_layer = LayerStatus(
                layer="metadata",
                present=md_row is not None,
                size_bytes=(len(md_row["canonical_json"]) if md_row is not None else None),
                location="clip_cache" if md_row is not None else None,
                fetched_at=_parse_iso(md_row["fetched_at"]) if md_row else None,
                last_used_at=_parse_iso(md_row["fetched_at"]) if md_row else None,
                pinned_by_workspaces=ws_ids,
                evictable=(md_row is not None and pending_n == 0),
            )

            if self._host_local:
                ml_layer = LayerStatus(
                    layer="media-local",
                    present=True,
                    size_bytes=None,
                    location="host:filesystem",
                    fetched_at=None,
                    last_used_at=None,
                    pinned_by_workspaces=ws_ids,
                    evictable=False,
                )
            else:
                ml_layer = LayerStatus(
                    layer="media-local",
                    present=ml_row is not None,
                    size_bytes=ml_row["size_bytes"] if ml_row else None,
                    location=ml_row["file_path"] if ml_row else None,
                    fetched_at=(_parse_iso(ml_row["downloaded_at"]) if ml_row else None),
                    last_used_at=(_parse_iso(ml_row["last_used_at"]) if ml_row else None),
                    pinned_by_workspaces=ws_ids,
                    evictable=(ml_row is not None and not ws_ids),
                )

            ai_size = sum(int(r["size_bytes"]) for r in ai_rows)
            ai_last = None
            ai_loc = None
            ai_fetched = None
            if ai_rows:
                ai_loc = ai_rows[0]["gcs_uri"]
                ai_fetched = _parse_iso(ai_rows[0]["uploaded_at"])
                ai_last = max(
                    (_parse_iso(r["last_used_at"]) for r in ai_rows if r["last_used_at"]),
                    default=None,
                )
            ai_layer = LayerStatus(
                layer="media-ai",
                present=bool(ai_rows),
                size_bytes=ai_size if ai_rows else None,
                location=ai_loc,
                fetched_at=ai_fetched,
                last_used_at=ai_last,
                pinned_by_workspaces=(),
                evictable=(bool(ai_rows) and pending_n == 0),
            )

            name = md_row["name"] if md_row else key[1]
            total_local = (md_layer.size_bytes or 0) + (ml_layer.size_bytes or 0)
            total_ai = ai_layer.size_bytes or 0

            out.append(
                ClipCacheStatus(
                    clip_key=key,
                    name=name,
                    layers=(md_layer, ml_layer, ai_layer),
                    total_local_bytes=total_local,
                    total_ai_bytes=total_ai,
                )
            )
        return out

    # --- summary + orphans -------------------------------------------

    async def summary(self) -> CacheSummary:
        db = self._db_provider()
        # metadata bytes ~ length of canonical_json column; cheaper to
        # sum length() in SQL than fetch JSON blobs.
        cur = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(canonical_json)), 0) FROM clip_cache"
        )
        md_count, md_bytes = await cur.fetchone()

        cur = await db.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM proxy_cache")
        ml_count, ml_bytes = await cur.fetchone()

        cur = await db.execute(
            "SELECT store_id, COUNT(*), COALESCE(SUM(size_bytes), 0) "
            "FROM ai_store_files GROUP BY store_id"
        )
        by_store: dict[str, int] = {}
        ai_total = 0
        for store_id, n, total in await cur.fetchall():
            by_store[store_id] = int(n)
            ai_total += int(total)

        cur = await db.execute(
            "SELECT workspace_id, COUNT(*) FROM workspace_clips GROUP BY workspace_id"
        )
        by_ws = {int(r[0]): int(r[1]) for r in await cur.fetchall()}

        cur = await db.execute(
            "SELECT COUNT(*) FROM pending_operations "
            "WHERE status IN ('pending', 'in_flight', 'conflict')"
        )
        pending = int((await cur.fetchone())[0])

        return CacheSummary(
            total_local_bytes=int(md_bytes) + int(ml_bytes),
            total_ai_bytes=ai_total,
            counts_by_store=by_store,
            counts_by_workspace=by_ws,
            metadata_clip_count=int(md_count),
            media_local_clip_count=int(ml_count),
            pending_ops_count=pending,
            media_cache_cap_bytes=self._cap,
        )

    async def list_orphans(self, *, deep: bool = False) -> list[ClipCacheStatus]:
        """Cached items without a live archive entry.

        Cheap leg: rows in `proxy_cache` or `ai_store_files` whose
        `clip_cache` row is absent. Deep leg: also call
        `provider.get_clip` on clips that DO have a `clip_cache` row but
        the upstream archive no longer knows about them (gated by `deep`
        so the route doesn't thunder the provider while offline).
        """
        db = self._db_provider()

        orphans: set[ClipKey] = set()

        cur = await db.execute(
            """
            SELECT pc.provider_id, pc.provider_clip_id
              FROM proxy_cache pc
              LEFT JOIN clip_cache cc
                ON cc.provider_id = pc.provider_id
               AND cc.provider_clip_id = pc.provider_clip_id
             WHERE cc.provider_id IS NULL
            """
        )
        for r in await cur.fetchall():
            orphans.add((r[0], r[1]))

        cur = await db.execute(
            """
            SELECT DISTINCT asf.provider_id, asf.provider_clip_id
              FROM ai_store_files asf
              LEFT JOIN clip_cache cc
                ON cc.provider_id = asf.provider_id
               AND cc.provider_clip_id = asf.provider_clip_id
             WHERE cc.provider_id IS NULL
            """
        )
        for r in await cur.fetchall():
            orphans.add((r[0], r[1]))

        if deep and self._provider is not None:
            cur = await db.execute("SELECT provider_id, provider_clip_id FROM clip_cache")
            for prov, pcid in await cur.fetchall():
                try:
                    await self._provider.get_clip(pcid)
                except Exception:  # noqa: BLE001
                    orphans.add((prov, pcid))

        return await self.status_for_clips(sorted(orphans))

    # --- internals ---------------------------------------------------

    async def _load_metadata(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, dict[str, Any]]:
        out: dict[ClipKey, dict[str, Any]] = {}
        for key in keys:
            cur = await db.execute(
                "SELECT name, canonical_json, fetched_at "
                "FROM clip_cache "
                "WHERE provider_id = ? AND provider_clip_id = ?",
                (key[0], key[1]),
            )
            row = await cur.fetchone()
            if row is not None:
                out[key] = {
                    "name": row[0],
                    "canonical_json": row[1],
                    "fetched_at": row[2],
                }
        return out

    async def _load_media_local(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, dict[str, Any]]:
        out: dict[ClipKey, dict[str, Any]] = {}
        for key in keys:
            cur = await db.execute(
                """
                SELECT file_path, size_bytes, downloaded_at, last_used_at
                  FROM proxy_cache
                 WHERE provider_id = ? AND provider_clip_id = ?
                """,
                (key[0], key[1]),
            )
            row = await cur.fetchone()
            if row is not None:
                out[key] = {
                    "file_path": row[0],
                    "size_bytes": row[1],
                    "downloaded_at": row[2],
                    "last_used_at": row[3],
                }
        return out

    async def _load_media_ai(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, list[dict[str, Any]]]:
        out: dict[ClipKey, list[dict[str, Any]]] = {}
        for key in keys:
            cur = await db.execute(
                """
                SELECT store_id, gcs_uri, mime_type, size_bytes,
                       uploaded_at, last_used_at
                  FROM ai_store_files
                 WHERE provider_id = ? AND provider_clip_id = ?
                """,
                (key[0], key[1]),
            )
            rows = await cur.fetchall()
            if rows:
                out[key] = [
                    {
                        "store_id": r[0],
                        "gcs_uri": r[1],
                        "mime_type": r[2],
                        "size_bytes": r[3],
                        "uploaded_at": r[4],
                        "last_used_at": r[5],
                    }
                    for r in rows
                ]
        return out

    async def _load_pins(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, list[int]]:
        out: dict[ClipKey, list[int]] = {}
        for key in keys:
            cur = await db.execute(
                """
                SELECT workspace_id FROM workspace_clips
                 WHERE provider_id = ? AND provider_clip_id = ?
                 ORDER BY workspace_id
                """,
                (key[0], key[1]),
            )
            ws_ids = [int(r[0]) for r in await cur.fetchall()]
            if ws_ids:
                out[key] = ws_ids
        return out

    async def _load_pending_counts(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, int]:
        out: dict[ClipKey, int] = {}
        for key in keys:
            cur = await db.execute(
                """
                SELECT COUNT(*) FROM pending_operations
                 WHERE provider_id = ? AND provider_clip_id = ?
                   AND status IN ('pending', 'in_flight', 'conflict')
                """,
                (key[0], key[1]),
            )
            n = int((await cur.fetchone())[0])
            if n:
                out[key] = n
        return out


# Unused import guard: `json` is reserved for future deep-orphan payloads.
_ = json
