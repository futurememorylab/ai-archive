"""WorkspaceManager: lifecycle of a named pinned working set.

Drives the four lifecycle verbs from spec §8.1:

  create_workspace → add_clips/remove_clips → prepare() → release()

`prepare()` is an async iterator that yields one `PrepEvent` per state
transition per clip, so the route can stream them via SSE without
holding the request thread. State machine per clip:

  pending → metadata → media → ready
                     ↘ media (skipped when capabilities.media_is_local) ↘
                     ↘ error (stays terminal until prep is re-run)      ↘

The clip's metadata row in `clip_cache` gets `pinned_to_workspace_id`
set to this workspace as soon as metadata is fetched; it stays pinned
until `release()` (or another workspace pinning the same clip).

`release()` is non-destructive (spec §9.5 rule 5). It removes the
workspace_clips rows and clears the primary pin (delegating to any
other workspace still pinning the same clip). It does NOT delete media
files or `clip_cache` rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import aiosqlite

from backend.app.archive.model import ClipKey
from backend.app.repositories.workspaces import WorkspacesRepo


@dataclass(frozen=True)
class PrepEvent:
    clip_key: ClipKey
    state: str             # "metadata" | "media" | "ready" | "error"
    error: str | None = None


class WorkspaceManager:
    def __init__(
        self,
        *,
        workspaces_repo: WorkspacesRepo,
        provider: Any,
        proxy_resolver: Any | None,
        db_provider: Callable[[], aiosqlite.Connection],
    ) -> None:
        self._repo = workspaces_repo
        self._provider = provider
        self._resolver = proxy_resolver
        self._db_provider = db_provider

    # --- CRUD --------------------------------------------------------

    async def create_workspace(
        self,
        *,
        name: str,
        provider_id: str,
        catalog_id: str,
        description: str | None = None,
        clip_keys: list[ClipKey] | None = None,
    ) -> int:
        db = self._db_provider()
        ws_id = await self._repo.create(
            db,
            name=name,
            provider_id=provider_id,
            catalog_id=catalog_id,
            description=description,
        )
        if clip_keys:
            await self._repo.add_clips(db, ws_id, clip_keys)
        return ws_id

    async def add_clips(self, ws_id: int, clip_keys: list[ClipKey]) -> None:
        await self._repo.add_clips(self._db_provider(), ws_id, clip_keys)

    async def remove_clips(self, ws_id: int, clip_keys: list[ClipKey]) -> None:
        db = self._db_provider()
        await self._repo.remove_clips(db, ws_id, clip_keys)
        # Re-point primary pin column for each clip: if any *other*
        # workspace still pins it, point there; otherwise clear.
        for key in clip_keys:
            pinning = await self._repo.workspaces_pinning(db, key)
            await self._repo.set_primary_pin(
                db, key, pinning[0] if pinning else None
            )

    async def list_workspaces(self) -> list[dict[str, Any]]:
        return await self._repo.list(self._db_provider())

    async def get(self, ws_id: int) -> dict[str, Any] | None:
        db = self._db_provider()
        ws = await self._repo.get(db, ws_id)
        if ws is None:
            return None
        ws["clips"] = await self._repo.list_clips(db, ws_id)
        return ws

    # --- prepare -----------------------------------------------------

    async def prepare(self, ws_id: int) -> AsyncIterator[PrepEvent]:
        """Walk workspace_clips and bring each to `ready`.

        Per-clip independence: an error on one clip does not stop the
        others. Resumable: rows already at `ready` are skipped.
        """
        db = self._db_provider()
        ws = await self._repo.get(db, ws_id)
        if ws is None:
            raise LookupError(f"workspace {ws_id} not found")

        rows = await self._repo.list_clips(db, ws_id)
        media_local = bool(
            getattr(self._provider, "capabilities", None)
            and self._provider.capabilities.media_is_local
        )

        async def _run() -> AsyncIterator[PrepEvent]:
            for row in rows:
                key: ClipKey = (row["provider_id"], row["provider_clip_id"])
                if row["cache_state"] == "ready":
                    continue
                # 1. metadata
                try:
                    await self._provider.get_clip(key[1])
                except Exception as exc:  # noqa: BLE001 — provider surface varies
                    await self._repo.set_cache_state(
                        db, ws_id, key, "error", error=f"metadata: {exc}"
                    )
                    yield PrepEvent(clip_key=key, state="error", error=str(exc))
                    continue
                await self._repo.set_primary_pin(db, key, ws_id)
                await self._repo.set_cache_state(db, ws_id, key, "metadata")
                yield PrepEvent(clip_key=key, state="metadata")

                # 2. media (skip when provider serves it locally)
                if not media_local:
                    if self._resolver is None:
                        await self._repo.set_cache_state(
                            db, ws_id, key, "error",
                            error="no proxy resolver wired",
                        )
                        yield PrepEvent(
                            clip_key=key,
                            state="error",
                            error="no proxy resolver wired",
                        )
                        continue
                    try:
                        await self._resolver.path_for_clip_id(int(key[1]))
                    except Exception as exc:  # noqa: BLE001
                        await self._repo.set_cache_state(
                            db, ws_id, key, "error", error=f"media: {exc}"
                        )
                        yield PrepEvent(
                            clip_key=key, state="error", error=str(exc)
                        )
                        continue
                    await self._repo.set_cache_state(db, ws_id, key, "media")
                    yield PrepEvent(clip_key=key, state="media")

                # 3. ready
                await self._repo.set_cache_state(db, ws_id, key, "ready")
                yield PrepEvent(clip_key=key, state="ready")

        async for ev in _run():
            yield ev

    # --- release -----------------------------------------------------

    async def release(
        self, ws_id: int, *, delete_workspace: bool = False
    ) -> None:
        """Drop pins. Does NOT evict media or clip_cache rows."""
        db = self._db_provider()
        keys = await self._repo.pinned_clip_keys(db, ws_id)
        # remove this workspace's membership
        if keys:
            await self._repo.remove_clips(db, ws_id, keys)
        # re-point or clear the primary pin per clip
        for key in keys:
            pinning = await self._repo.workspaces_pinning(db, key)
            await self._repo.set_primary_pin(
                db, key, pinning[0] if pinning else None
            )
        if delete_workspace:
            await self._repo.delete(db, ws_id)

    # --- helper for tests: drain prepare into a list -----------------

    async def prepare_all(self, ws_id: int) -> list[PrepEvent]:
        evs: list[PrepEvent] = []
        async for ev in self.prepare(ws_id):
            evs.append(ev)
            await asyncio.sleep(0)
        return evs
