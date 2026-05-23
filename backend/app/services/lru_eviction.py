"""LruEviction: periodic background eviction of non-pinned media.

Walks `proxy_cache` ordered by `last_used_at ASC`, skipping any row
whose `(provider_id, provider_clip_id)` appears in `workspace_clips`.
Evicts least-recently-used until total non-pinned local-media size is
below `settings.media_cache_cap_gb`. Each eviction is logged as
`cache_actions_log` `action='lru_evict'`, `who='system'`.

LRU never crosses a pin. If the sum of non-pinned rows is already below
the cap, the tick is a no-op. If pinned rows alone exceed the cap, the
tick logs a `partial` summary row and emits a warning — it does not
attempt to break a pin.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiosqlite

from backend.app.repositories.cache_actions_log import CacheActionsLogRepo
from backend.app.services.cache_actions import CacheActions

log = logging.getLogger(__name__)


class LruEviction:
    def __init__(
        self,
        *,
        actions: CacheActions,
        log_repo: CacheActionsLogRepo,
        db_provider: Callable[[], aiosqlite.Connection],
        media_cache_cap_bytes: int,
        tick_interval_s: float = 300.0,
    ) -> None:
        self._actions = actions
        self._log_repo = log_repo
        self._db_provider = db_provider
        self._cap = media_cache_cap_bytes
        self._tick_interval_s = tick_interval_s
        self._stop_evt: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001 — loop must not die
                log.exception("lru_eviction tick failed")
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=self._tick_interval_s)
            except TimeoutError:
                pass

    # --- single tick -------------------------------------------------

    async def tick_once(self) -> int:
        """Run one LRU sweep. Returns the number of rows evicted."""
        db = self._db_provider()
        rows = await self._candidates_oldest_first(db)
        non_pinned_total = sum(int(r["size_bytes"] or 0) for r in rows)
        if non_pinned_total <= self._cap:
            return 0

        evicted = 0
        to_free = non_pinned_total - self._cap
        for row in rows:
            key = (row["provider_id"], row["provider_clip_id"])
            out = await self._actions.evict_local_media(
                key,
                force=False,
                who="system",
            )
            if out.result == "ok":
                evicted += 1
                to_free -= out.bytes_freed
                # tag this audit row as an lru_evict by writing a
                # companion log row; the actions' default action name
                # is "evict_local_media" so add a sibling row that
                # marks the sweep.
                await self._log_repo.append(
                    db,
                    who="system",
                    action="lru_evict",
                    clip_keys=[key],
                    result="ok",
                    detail=f"sibling_log_id={out.log_id}",
                    bytes_freed=out.bytes_freed,
                )
                if to_free <= 0:
                    break
        if to_free > 0:
            # could not get below cap without crossing a pin
            await self._log_repo.append(
                db,
                who="system",
                action="lru_evict",
                clip_keys=[],
                result="partial",
                detail=(
                    f"still_over_cap_by={to_free} "
                    f"cap={self._cap} non_pinned_total={non_pinned_total}"
                ),
            )
            log.warning(
                "lru_eviction left cache over cap: %s bytes remain over %s",
                to_free,
                self._cap,
            )
        return evicted

    # --- internals ---------------------------------------------------

    async def _candidates_oldest_first(self, db: aiosqlite.Connection) -> list[dict[str, Any]]:
        """Return non-pinned proxy_cache rows ordered oldest-first.

        "Non-pinned" = `(provider_id, provider_clip_id)` does not appear
        in `workspace_clips`.
        """
        cur = await db.execute(
            """
            SELECT pc.provider_id, pc.provider_clip_id,
                   pc.file_path, pc.size_bytes, pc.last_used_at
              FROM proxy_cache pc
              LEFT JOIN workspace_clips wc
                ON wc.provider_id = pc.provider_id
               AND wc.provider_clip_id = pc.provider_clip_id
             WHERE wc.workspace_id IS NULL
             ORDER BY pc.last_used_at ASC
            """
        )
        rows = await cur.fetchall()
        return [
            {
                "provider_id": r[0],
                "provider_clip_id": r[1],
                "file_path": r[2],
                "size_bytes": r[3],
                "last_used_at": r[4],
            }
            for r in rows
        ]
