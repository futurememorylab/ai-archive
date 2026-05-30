"""CacheActions: mutating operations against the three cache layers.

Each method:
  1. Consults the inspector (or raw queries) to evaluate the relevant
     invariant from spec §9.5.
  2. If the invariant blocks and `force=False`: append a
     `cache_actions_log` row with `result='skipped'` + reason, return
     without touching state.
  3. Otherwise perform the action and append `result='ok'` (or `error`
     on unexpected failure).

The on-disk proxy file lives at `proxy_cache.file_path`. We `unlink()`
it best-effort: a missing file is not an error (the user may have
deleted it manually), but the audit row reflects that case via
`detail`.

AI-store eviction goes through the active `AIInputStore` adapter's
`evict()` port from PR 2 — that's the only writer aware of bucket-side
state. PR 6 just calls it and prunes the `ai_store_files` row.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.archive.model import ClipKey
from backend.app.repositories.cache_actions_log import CacheActionsLogRepo
from backend.app.services.cache_inspector import CacheInspector

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvictOutcome:
    result: str  # "ok" | "skipped" | "error"
    detail: str | None
    bytes_freed: int
    log_id: int


async def _remove_local_bytes(
    actions: CacheActions,
    db: aiosqlite.Connection,
    key: ClipKey,
    rows: Sequence[Any],
) -> tuple[str | None, EvictOutcome | None]:
    """Unlink the on-disk proxy file (best-effort, async-safe).

    asyncio.to_thread keeps the event loop responsive when the file is
    on a slow network mount (e.g. /Volumes/ARECA* on the CatDV host
    deployment) — otherwise unlink() blocks every other request
    including the keepalive probe. A missing file is not an error
    (recorded via `file_missing`); an OSError on unlink is.
    """
    file_path = rows[0][0]
    try:
        if file_path:
            p = Path(file_path)
            exists = await asyncio.to_thread(p.exists)
            if exists:
                await asyncio.to_thread(os.unlink, p)
            else:
                return "file_missing", None
    except OSError as exc:
        detail = f"unlink_failed: {exc}"
        return detail, EvictOutcome("error", detail, 0, 0)
    return None, None


async def _remove_ai_bytes(
    actions: CacheActions,
    db: aiosqlite.Connection,
    key: ClipKey,
    rows: Sequence[Any],
) -> tuple[str | None, EvictOutcome | None]:
    """Delegate bucket-side cleanup to the active store, if present.

    When no store is configured (offline), the on-bucket bytes are left
    alone but the local index is still pruned by the caller — graceful
    offline pruning.
    """
    if actions._ai_store is not None:
        try:
            await actions._ai_store.evict(key)
        except Exception as exc:  # noqa: BLE001
            detail = f"ai_store_evict_failed: {exc}"
            return detail, EvictOutcome("error", detail, 0, 0)
    return None, None


async def _remove_no_bytes(
    actions: CacheActions,
    db: aiosqlite.Connection,
    key: ClipKey,
    rows: Sequence[Any],
) -> tuple[str | None, EvictOutcome | None]:
    return None, None


@dataclass(frozen=True)
class LayerPolicy:
    """Captures what differs between the three cache-layer evictions.

    The shared skeleton (read → absent-skip → invariant-skips → remove
    bytes → DELETE+commit → log ok) lives in `CacheActions._evict_impl`;
    everything below is the per-layer variance.
    """

    select_sql: str
    fetch_all: bool
    size_of: Callable[[Sequence[Any]], int]
    check_pins: bool
    check_pending: bool
    remove_bytes: Callable[
        [CacheActions, aiosqlite.Connection, ClipKey, Sequence[Any]],
        Awaitable[tuple[str | None, EvictOutcome | None]],
    ]
    delete_sql: str
    pins_sql: str | None = None


_PENDING_SQL = """
            SELECT COUNT(*) FROM pending_operations
             WHERE provider_id = ? AND provider_clip_id = ?
               AND status IN ('pending', 'in_flight', 'conflict')
            """

LOCAL_MEDIA = LayerPolicy(
    select_sql=(
        "SELECT file_path, size_bytes FROM proxy_cache "
        "WHERE provider_id = ? AND provider_clip_id = ?"
    ),
    fetch_all=False,
    size_of=lambda rows: int(rows[0][1] or 0),
    check_pins=True,
    check_pending=False,
    remove_bytes=_remove_local_bytes,
    delete_sql=(
        "DELETE FROM proxy_cache WHERE provider_id = ? AND provider_clip_id = ?"
    ),
    pins_sql=(
        "SELECT workspace_id FROM workspace_clips "
        "WHERE provider_id = ? AND provider_clip_id = ? "
        "ORDER BY workspace_id"
    ),
)

AI_MEDIA = LayerPolicy(
    select_sql=(
        "SELECT store_id, size_bytes FROM ai_store_files "
        "WHERE provider_id = ? AND provider_clip_id = ?"
    ),
    fetch_all=True,
    size_of=lambda rows: sum(int(r[1] or 0) for r in rows),
    check_pins=False,
    check_pending=True,
    remove_bytes=_remove_ai_bytes,
    delete_sql=(
        "DELETE FROM ai_store_files WHERE provider_id = ? AND provider_clip_id = ?"
    ),
)

METADATA = LayerPolicy(
    select_sql=(
        "SELECT length(canonical_json) FROM clip_cache "
        "WHERE provider_id = ? AND provider_clip_id = ?"
    ),
    fetch_all=False,
    size_of=lambda rows: int(rows[0][0] or 0),
    check_pins=True,
    check_pending=True,
    remove_bytes=_remove_no_bytes,
    delete_sql=(
        "DELETE FROM clip_cache WHERE provider_id = ? AND provider_clip_id = ?"
    ),
    pins_sql=(
        "SELECT workspace_id FROM workspace_clips "
        "WHERE provider_id = ? AND provider_clip_id = ?"
    ),
)


@dataclass(frozen=True)
class BulkEvictResult:
    ok: int = 0
    skipped: int = 0
    errors: int = 0
    bytes_freed: int = 0
    log_ids: tuple[int, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "errors": self.errors,
            "bytes_freed": self.bytes_freed,
            "log_ids": list(self.log_ids),
        }


class CacheActions:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        inspector: CacheInspector,
        log_repo: CacheActionsLogRepo,
        ai_store: Any | None = None,
        who_provider: Callable[[], str] | None = None,
    ) -> None:
        self._db_provider = db_provider
        self._inspector = inspector
        self._log_repo = log_repo
        self._ai_store = ai_store
        self._who_provider = who_provider or (lambda: "request")

    # --- single-layer evictions --------------------------------------

    async def evict_local_media(
        self,
        key: ClipKey,
        *,
        force: bool = False,
        who: str | None = None,
    ) -> EvictOutcome:
        return await self._evict_impl(
            LOCAL_MEDIA,
            key,
            force=force,
            who=who or self._who_provider(),
            action="evict_local_media",
        )

    async def evict_ai_media(
        self,
        key: ClipKey,
        *,
        force: bool = False,
        who: str | None = None,
    ) -> EvictOutcome:
        return await self._evict_impl(
            AI_MEDIA,
            key,
            force=force,
            who=who or self._who_provider(),
            action="evict_ai_media",
        )

    async def evict_metadata(
        self,
        key: ClipKey,
        *,
        force: bool = False,
        who: str | None = None,
    ) -> EvictOutcome:
        return await self._evict_impl(
            METADATA,
            key,
            force=force,
            who=who or self._who_provider(),
            action="evict_metadata",
        )

    async def evict_clip_everywhere(
        self,
        key: ClipKey,
        *,
        force: bool = False,
        who: str | None = None,
    ) -> BulkEvictResult:
        """media-ai → media-local → metadata.

        Without `force`, the chain short-circuits on the first invariant
        skip (so e.g. metadata is preserved if a pending op blocks an
        earlier layer). With `force=True`, all three layers are
        attempted regardless and the whole call is logged as a single
        prominent `evict_clip_everywhere_force` row in addition to the
        per-layer audit entries.
        """
        who = who or self._who_provider()
        outcomes: list[EvictOutcome] = []
        a = await self._evict_impl(
            AI_MEDIA,
            key,
            force=force,
            who=who,
            action="evict_clip_everywhere",
        )
        outcomes.append(a)
        if force or a.result != "skipped":
            b = await self._evict_impl(
                LOCAL_MEDIA,
                key,
                force=force,
                who=who,
                action="evict_clip_everywhere",
            )
            outcomes.append(b)
            if force or b.result != "skipped":
                c = await self._evict_impl(
                    METADATA,
                    key,
                    force=force,
                    who=who,
                    action="evict_clip_everywhere",
                )
                outcomes.append(c)

        result = _summarise(outcomes)
        if force:
            db = self._db_provider()
            await self._log_repo.append(
                db,
                who=who,
                action="evict_clip_everywhere_force",
                clip_keys=[key],
                result="ok" if result.errors == 0 else "partial",
                detail=f"layers_attempted={len(outcomes)}",
                bytes_freed=result.bytes_freed,
            )
        return result

    async def bulk_evict(
        self,
        keys: Sequence[ClipKey],
        layers: Sequence[str],
        *,
        force: bool = False,
        who: str | None = None,
    ) -> BulkEvictResult:
        who = who or self._who_provider()
        outcomes: list[EvictOutcome] = []
        for key in keys:
            for layer in layers:
                if layer == "media-local":
                    outcomes.append(
                        await self._evict_impl(
                            LOCAL_MEDIA,
                            key,
                            force=force,
                            who=who,
                            action="bulk_evict",
                        )
                    )
                elif layer == "media-ai":
                    outcomes.append(
                        await self._evict_impl(
                            AI_MEDIA,
                            key,
                            force=force,
                            who=who,
                            action="bulk_evict",
                        )
                    )
                elif layer == "metadata":
                    outcomes.append(
                        await self._evict_impl(
                            METADATA,
                            key,
                            force=force,
                            who=who,
                            action="bulk_evict",
                        )
                    )
                # unknown layer names silently ignored — the route
                # validates layer values, and a typo here shouldn't
                # crash a bulk-evict run.
        return _summarise(outcomes)

    async def evict_orphans(self, *, who: str | None = None) -> BulkEvictResult:
        who = who or self._who_provider()
        orphans = await self._inspector.list_orphans()
        outcomes: list[EvictOutcome] = []
        for status in orphans:
            for layer in status.layers:
                if not layer.present:
                    continue
                if layer.layer == "media-local":
                    outcomes.append(
                        await self._evict_impl(
                            LOCAL_MEDIA,
                            status.clip_key,
                            force=False,
                            who=who,
                            action="evict_orphans",
                        )
                    )
                elif layer.layer == "media-ai":
                    outcomes.append(
                        await self._evict_impl(
                            AI_MEDIA,
                            status.clip_key,
                            force=False,
                            who=who,
                            action="evict_orphans",
                        )
                    )
                # metadata orphans don't exist by construction:
                # list_orphans returns clips whose clip_cache row is
                # absent. Skip the metadata layer.
        return _summarise(outcomes)

    # --- internals ---------------------------------------------------

    async def _evict_impl(
        self,
        policy: LayerPolicy,
        key: ClipKey,
        *,
        force: bool,
        who: str,
        action: str,
    ) -> EvictOutcome:
        """Single LayerPolicy-driven eviction.

        Shared skeleton: read → absent-skip → (pins-skip) →
        (pending-skip) → remove bytes → DELETE + commit → log ok. The
        per-layer variance (which table, single vs many rows, how to
        size, which invariants, how to free bytes) lives in `policy`.

        Invariant ordering matters: for the metadata layer, pins are
        checked *before* pending_ops — preserved here by checking
        `check_pins` ahead of `check_pending`.
        """
        db = self._db_provider()
        cur = await db.execute(policy.select_sql, (key[0], key[1]))
        if policy.fetch_all:
            rows: Sequence[Any] = await cur.fetchall()
            absent = not rows
        else:
            single = await cur.fetchone()
            rows = [single] if single is not None else []
            absent = single is None
        if absent:
            log_id = await self._log_repo.append(
                db,
                who=who,
                action=action,
                clip_keys=[key],
                result="skipped",
                detail="absent",
            )
            return EvictOutcome("skipped", "absent", 0, log_id)

        bytes_freed = policy.size_of(rows)

        if policy.check_pins:
            assert policy.pins_sql is not None
            cur = await db.execute(policy.pins_sql, (key[0], key[1]))
            pins = [int(r[0]) for r in await cur.fetchall()]
            if pins and not force:
                detail = f"pinned_by_workspaces={pins}"
                log_id = await self._log_repo.append(
                    db,
                    who=who,
                    action=action,
                    clip_keys=[key],
                    result="skipped",
                    detail=detail,
                )
                return EvictOutcome("skipped", detail, 0, log_id)

        if policy.check_pending:
            cur = await db.execute(_PENDING_SQL, (key[0], key[1]))
            n_pending = int((await cur.fetchone())[0])
            if n_pending and not force:
                detail = f"pending_ops={n_pending}"
                log_id = await self._log_repo.append(
                    db,
                    who=who,
                    action=action,
                    clip_keys=[key],
                    result="skipped",
                    detail=detail,
                )
                return EvictOutcome("skipped", detail, 0, log_id)

        ok_detail, error_outcome = await policy.remove_bytes(self, db, key, rows)
        if error_outcome is not None:
            log_id = await self._log_repo.append(
                db,
                who=who,
                action=action,
                clip_keys=[key],
                result="error",
                detail=error_outcome.detail,
            )
            return EvictOutcome("error", error_outcome.detail, 0, log_id)

        await db.execute(policy.delete_sql, (key[0], key[1]))
        await db.commit()

        log_id = await self._log_repo.append(
            db,
            who=who,
            action=action,
            clip_keys=[key],
            result="ok",
            detail=ok_detail,
            bytes_freed=bytes_freed,
        )
        return EvictOutcome("ok", ok_detail, bytes_freed, log_id)


def _summarise(outcomes: Sequence[EvictOutcome]) -> BulkEvictResult:
    ok = sum(1 for o in outcomes if o.result == "ok")
    skipped = sum(1 for o in outcomes if o.result == "skipped")
    errors = sum(1 for o in outcomes if o.result == "error")
    bytes_freed = sum(o.bytes_freed for o in outcomes)
    log_ids = tuple(o.log_id for o in outcomes)
    return BulkEvictResult(
        ok=ok,
        skipped=skipped,
        errors=errors,
        bytes_freed=bytes_freed,
        log_ids=log_ids,
    )
