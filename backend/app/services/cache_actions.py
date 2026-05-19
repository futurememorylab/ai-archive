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

import logging
import os
from collections.abc import Callable, Sequence
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
    result: str            # "ok" | "skipped" | "error"
    detail: str | None
    bytes_freed: int
    log_id: int


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
        return await self._evict_local_media_impl(
            key, force=force, who=who or self._who_provider(),
            action="evict_local_media",
        )

    async def evict_ai_media(
        self,
        key: ClipKey,
        *,
        force: bool = False,
        who: str | None = None,
    ) -> EvictOutcome:
        return await self._evict_ai_media_impl(
            key, force=force, who=who or self._who_provider(),
            action="evict_ai_media",
        )

    async def evict_metadata(
        self,
        key: ClipKey,
        *,
        force: bool = False,
        who: str | None = None,
    ) -> EvictOutcome:
        return await self._evict_metadata_impl(
            key, force=force, who=who or self._who_provider(),
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
        a = await self._evict_ai_media_impl(
            key, force=force, who=who, action="evict_clip_everywhere",
        )
        outcomes.append(a)
        if force or a.result != "skipped":
            b = await self._evict_local_media_impl(
                key, force=force, who=who, action="evict_clip_everywhere",
            )
            outcomes.append(b)
            if force or b.result != "skipped":
                c = await self._evict_metadata_impl(
                    key, force=force, who=who, action="evict_clip_everywhere",
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
                        await self._evict_local_media_impl(
                            key, force=force, who=who,
                            action="bulk_evict",
                        )
                    )
                elif layer == "media-ai":
                    outcomes.append(
                        await self._evict_ai_media_impl(
                            key, force=force, who=who,
                            action="bulk_evict",
                        )
                    )
                elif layer == "metadata":
                    outcomes.append(
                        await self._evict_metadata_impl(
                            key, force=force, who=who,
                            action="bulk_evict",
                        )
                    )
                # unknown layer names silently ignored — the route
                # validates layer values, and a typo here shouldn't
                # crash a bulk-evict run.
        return _summarise(outcomes)

    async def evict_orphans(
        self, *, who: str | None = None
    ) -> BulkEvictResult:
        who = who or self._who_provider()
        orphans = await self._inspector.list_orphans()
        outcomes: list[EvictOutcome] = []
        for status in orphans:
            for layer in status.layers:
                if not layer.present:
                    continue
                if layer.layer == "media-local":
                    outcomes.append(
                        await self._evict_local_media_impl(
                            status.clip_key, force=False, who=who,
                            action="evict_orphans",
                        )
                    )
                elif layer.layer == "media-ai":
                    outcomes.append(
                        await self._evict_ai_media_impl(
                            status.clip_key, force=False, who=who,
                            action="evict_orphans",
                        )
                    )
                # metadata orphans don't exist by construction:
                # list_orphans returns clips whose clip_cache row is
                # absent. Skip the metadata layer.
        return _summarise(outcomes)

    # --- internals ---------------------------------------------------

    async def _evict_local_media_impl(
        self,
        key: ClipKey,
        *,
        force: bool,
        who: str,
        action: str,
    ) -> EvictOutcome:
        db = self._db_provider()
        cur = await db.execute(
            "SELECT file_path, size_bytes FROM proxy_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        row = await cur.fetchone()
        if row is None:
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail="absent",
            )
            return EvictOutcome("skipped", "absent", 0, log_id)

        file_path, size_bytes = row[0], int(row[1] or 0)

        cur = await db.execute(
            "SELECT workspace_id FROM workspace_clips "
            "WHERE provider_id = ? AND provider_clip_id = ? "
            "ORDER BY workspace_id",
            (key[0], key[1]),
        )
        pins = [int(r[0]) for r in await cur.fetchall()]
        if pins and not force:
            detail = f"pinned_by_workspaces={pins}"
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail=detail,
            )
            return EvictOutcome("skipped", detail, 0, log_id)

        # delete the on-disk file (best-effort) then the row.
        unlink_detail: str | None = None
        try:
            if file_path:
                p = Path(file_path)
                if p.exists():
                    os.unlink(p)
                else:
                    unlink_detail = "file_missing"
        except OSError as exc:
            unlink_detail = f"unlink_failed: {exc}"
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="error", detail=unlink_detail,
            )
            return EvictOutcome("error", unlink_detail, 0, log_id)

        await db.execute(
            "DELETE FROM proxy_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        await db.commit()

        log_id = await self._log_repo.append(
            db, who=who, action=action, clip_keys=[key],
            result="ok", detail=unlink_detail, bytes_freed=size_bytes,
        )
        return EvictOutcome("ok", unlink_detail, size_bytes, log_id)

    async def _evict_ai_media_impl(
        self,
        key: ClipKey,
        *,
        force: bool,
        who: str,
        action: str,
    ) -> EvictOutcome:
        db = self._db_provider()
        cur = await db.execute(
            "SELECT store_id, size_bytes FROM ai_store_files "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        rows = await cur.fetchall()
        if not rows:
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail="absent",
            )
            return EvictOutcome("skipped", "absent", 0, log_id)

        cur = await db.execute(
            """
            SELECT COUNT(*) FROM pending_operations
             WHERE provider_id = ? AND provider_clip_id = ?
               AND status IN ('pending', 'in_flight', 'conflict')
            """,
            (key[0], key[1]),
        )
        n_pending = int((await cur.fetchone())[0])
        if n_pending and not force:
            detail = f"pending_ops={n_pending}"
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail=detail,
            )
            return EvictOutcome("skipped", detail, 0, log_id)

        total_bytes = sum(int(r[1] or 0) for r in rows)
        detail: str | None = None
        # delegate bucket-side cleanup to the active store
        if self._ai_store is not None:
            try:
                await self._ai_store.evict(key)
            except Exception as exc:  # noqa: BLE001
                detail = f"ai_store_evict_failed: {exc}"
                log_id = await self._log_repo.append(
                    db, who=who, action=action, clip_keys=[key],
                    result="error", detail=detail,
                )
                return EvictOutcome("error", detail, 0, log_id)

        # also prune our own index (the adapter's evict typically does
        # this via its files_repo, but PR 6 doesn't assume — clear here
        # regardless so the inspector sees consistent state).
        await db.execute(
            "DELETE FROM ai_store_files "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        await db.commit()
        log_id = await self._log_repo.append(
            db, who=who, action=action, clip_keys=[key],
            result="ok", detail=detail, bytes_freed=total_bytes,
        )
        return EvictOutcome("ok", detail, total_bytes, log_id)

    async def _evict_metadata_impl(
        self,
        key: ClipKey,
        *,
        force: bool,
        who: str,
        action: str,
    ) -> EvictOutcome:
        db = self._db_provider()
        cur = await db.execute(
            "SELECT length(canonical_json) FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        row = await cur.fetchone()
        if row is None:
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail="absent",
            )
            return EvictOutcome("skipped", "absent", 0, log_id)
        bytes_freed = int(row[0] or 0)

        cur = await db.execute(
            "SELECT workspace_id FROM workspace_clips "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        pins = [int(r[0]) for r in await cur.fetchall()]
        if pins and not force:
            detail = f"pinned_by_workspaces={pins}"
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail=detail,
            )
            return EvictOutcome("skipped", detail, 0, log_id)

        cur = await db.execute(
            """
            SELECT COUNT(*) FROM pending_operations
             WHERE provider_id = ? AND provider_clip_id = ?
               AND status IN ('pending', 'in_flight', 'conflict')
            """,
            (key[0], key[1]),
        )
        n_pending = int((await cur.fetchone())[0])
        if n_pending and not force:
            detail = f"pending_ops={n_pending}"
            log_id = await self._log_repo.append(
                db, who=who, action=action, clip_keys=[key],
                result="skipped", detail=detail,
            )
            return EvictOutcome("skipped", detail, 0, log_id)

        await db.execute(
            "DELETE FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (key[0], key[1]),
        )
        await db.commit()
        log_id = await self._log_repo.append(
            db, who=who, action=action, clip_keys=[key],
            result="ok", bytes_freed=bytes_freed,
        )
        return EvictOutcome("ok", None, bytes_freed, log_id)


def _summarise(outcomes: Sequence[EvictOutcome]) -> BulkEvictResult:
    ok = sum(1 for o in outcomes if o.result == "ok")
    skipped = sum(1 for o in outcomes if o.result == "skipped")
    errors = sum(1 for o in outcomes if o.result == "error")
    bytes_freed = sum(o.bytes_freed for o in outcomes)
    log_ids = tuple(o.log_id for o in outcomes)
    return BulkEvictResult(
        ok=ok, skipped=skipped, errors=errors,
        bytes_freed=bytes_freed, log_ids=log_ids,
    )
