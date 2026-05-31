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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import aiosqlite

from backend.app.archive.errors import is_provider_not_found
from backend.app.archive.model import ClipKey
from backend.app.repositories._batch import chunked_in_clause

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

        # Fetch per-layer rows via chunked `WHERE (a, b) IN (...)` queries;
        # one statement per layer per chunk (default chunk_size=400 keys).
        # See backend/app/repositories/_batch.py for the helper. ADR 0046.
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

    async def list_for_inventory(
        self,
        *,
        tab: str = "all",
        store: str | None = None,
        workspace: int | None = None,
        orphans: bool = False,
        evictable: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ClipCacheStatus], int]:
        """Inventory rows for the cache page, filtered and paginated in SQL.

        Returns (page_rows, total_matching_count). The COUNT and the
        page SELECT use the same WHERE clause so the pager stays
        consistent. Statuses are hydrated only for the page-rows slice,
        so per-render cost is bounded by `limit` not by total clip count.
        """
        db = self._db_provider()

        # Build the WHERE clause + params common to count and page select.
        # The driving table depends on the filters:
        #   - orphans=True: rows in proxy_cache OR ai_store_files whose
        #     clip_cache entry is absent.
        #   - tab='local': rows in clip_cache that also have a proxy_cache row.
        #   - tab='ai':    rows in clip_cache that also have an ai_store_files row.
        #   - tab='all':   rows in clip_cache.
        #   - workspace=N: rows pinned by workspace N.
        #   - store=S:     ai_store_files where S is a substring of
        #     either store_id (e.g. 'gcs:catdav-proxies') or gcs_uri
        #     (e.g. 'gs://catdav-proxies/...'). Substring rather than
        #     exact match because the cache-page Store filter input
        #     historically accepted the bucket name on its own
        #     ('catdav-proxies'), which appears in both forms.
        #   - evictable=True: rows with no pending operations. This is
        #     a simplification of the pre-T2.4 behavior which checked
        #     `any(layer.evictable for layer in status.layers)` after
        #     hydration. The shift is bounded: 'evictable' here means
        #     "AT LEAST ONE layer could be evicted right now" — clips
        #     with pending ops are correctly excluded; the only
        #     divergence is for clips that exist *only* in proxy_cache
        #     (no clip_cache, no ai_store_files) AND are pinned — those
        #     would have been EXCLUDED by the old logic but are
        #     INCLUDED here. That state is essentially "orphaned and
        #     pinned" which the inventory already surfaces via the
        #     orphans filter.

        where_clauses: list[str] = []
        params: list = []

        if orphans:
            # Orphans: clip_keys in proxy_cache or ai_store_files where
            # clip_cache row is absent. Drive from a UNION.
            base_sql = """
                SELECT pc.provider_id, pc.provider_clip_id
                  FROM proxy_cache pc
                  LEFT JOIN clip_cache cc
                    ON cc.provider_id = pc.provider_id
                   AND cc.provider_clip_id = pc.provider_clip_id
                 WHERE cc.provider_id IS NULL
                UNION
                SELECT asf.provider_id, asf.provider_clip_id
                  FROM ai_store_files asf
                  LEFT JOIN clip_cache cc
                    ON cc.provider_id = asf.provider_id
                   AND cc.provider_clip_id = asf.provider_clip_id
                 WHERE cc.provider_id IS NULL
            """
        else:
            base_sql = (
                "SELECT provider_id, provider_clip_id FROM clip_cache"
            )

        # Wrap as subquery so the filters apply uniformly.
        from_sql = f"FROM ({base_sql}) AS k"

        if tab == "local":
            where_clauses.append(
                "EXISTS (SELECT 1 FROM proxy_cache pc "
                "WHERE pc.provider_id = k.provider_id "
                "AND pc.provider_clip_id = k.provider_clip_id)"
            )
        elif tab == "ai":
            where_clauses.append(
                "EXISTS (SELECT 1 FROM ai_store_files asf "
                "WHERE asf.provider_id = k.provider_id "
                "AND asf.provider_clip_id = k.provider_clip_id)"
            )
        if store:
            # Substring match on BOTH store_id and gcs_uri — preserves
            # the pre-T2.4 UI behavior where users typed the bucket name
            # alone ('catdav-proxies') and matched both
            # 'gcs:catdav-proxies' (store_id) and
            # 'gs://catdav-proxies/...' (gcs_uri).
            where_clauses.append(
                "EXISTS (SELECT 1 FROM ai_store_files asf "
                "WHERE asf.provider_id = k.provider_id "
                "AND asf.provider_clip_id = k.provider_clip_id "
                "AND (asf.store_id LIKE ? OR asf.gcs_uri LIKE ?))"
            )
            pattern = f"%{store}%"
            params.append(pattern)
            params.append(pattern)
        if workspace is not None:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM workspace_clips wc "
                "WHERE wc.provider_id = k.provider_id "
                "AND wc.provider_clip_id = k.provider_clip_id "
                "AND wc.workspace_id = ?)"
            )
            params.append(workspace)
        if evictable:
            where_clauses.append(
                "NOT EXISTS (SELECT 1 FROM pending_operations po "
                "WHERE po.provider_id = k.provider_id "
                "AND po.provider_clip_id = k.provider_clip_id "
                "AND po.status IN ('pending', 'in_flight', 'conflict'))"
            )

        where_sql = ""
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)

        # COUNT
        count_cur = await db.execute(
            f"SELECT COUNT(*) {from_sql}{where_sql}", params
        )
        total = int((await count_cur.fetchone())[0])

        # Page SELECT
        page_cur = await db.execute(
            f"SELECT provider_id, provider_clip_id {from_sql}{where_sql} "
            "ORDER BY provider_id, provider_clip_id "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        page_keys: list[ClipKey] = [
            (row[0], row[1]) for row in await page_cur.fetchall()
        ]

        statuses = await self.status_for_clips(page_keys)
        return statuses, total

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
                except Exception as exc:  # noqa: BLE001
                    # Only documented absence (NotFoundError / 404) is evidence
                    # of orphaning. Transient errors (transport, auth, retryable)
                    # MUST NOT mark the clip orphan — Evict orphans would wipe
                    # legitimately-cached data on a VPN flap. See ADR 0042
                    # (added in this PR). asyncio.CancelledError is a
                    # BaseException not Exception, so cancellation propagates.
                    if is_provider_not_found(exc):
                        orphans.add((prov, pcid))
                    # else: silently skip; next deep call will retry.

        return await self.status_for_clips(sorted(orphans))

    # --- internals ---------------------------------------------------

    async def _load_metadata(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, dict[str, Any]]:
        out: dict[ClipKey, dict[str, Any]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, name, canonical_json, fetched_at "
                "FROM clip_cache "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql})",
                params,
            )
            for row in await cur.fetchall():
                out[(row[0], row[1])] = {
                    "name": row[2],
                    "canonical_json": row[3],
                    "fetched_at": row[4],
                }
        return out

    async def _load_media_local(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, dict[str, Any]]:
        out: dict[ClipKey, dict[str, Any]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, "
                "file_path, size_bytes, downloaded_at, last_used_at "
                "FROM proxy_cache "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql})",
                params,
            )
            for row in await cur.fetchall():
                out[(row[0], row[1])] = {
                    "file_path": row[2],
                    "size_bytes": row[3],
                    "downloaded_at": row[4],
                    "last_used_at": row[5],
                }
        return out

    async def _load_media_ai(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, list[dict[str, Any]]]:
        out: dict[ClipKey, list[dict[str, Any]]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, store_id, gcs_uri, "
                "mime_type, size_bytes, uploaded_at, last_used_at "
                "FROM ai_store_files "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql})",
                params,
            )
            for row in await cur.fetchall():
                key = (row[0], row[1])
                out.setdefault(key, []).append({
                    "store_id": row[2],
                    "gcs_uri": row[3],
                    "mime_type": row[4],
                    "size_bytes": row[5],
                    "uploaded_at": row[6],
                    "last_used_at": row[7],
                })
        return out

    async def _load_pins(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, list[int]]:
        out: dict[ClipKey, list[int]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, workspace_id "
                "FROM workspace_clips "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql}) "
                "ORDER BY provider_id, provider_clip_id, workspace_id",
                params,
            )
            for row in await cur.fetchall():
                key = (row[0], row[1])
                out.setdefault(key, []).append(int(row[2]))
        return out

    async def _load_pending_counts(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, int]:
        out: dict[ClipKey, int] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, COUNT(*) "
                "FROM pending_operations "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql}) "
                "AND status IN ('pending', 'in_flight', 'conflict') "
                "GROUP BY provider_id, provider_clip_id",
                params,
            )
            for row in await cur.fetchall():
                n = int(row[2])
                if n:
                    out[(row[0], row[1])] = n
        return out
