"""Studio runs — worker, resolver chain, lifecycle.

The worker (`StudioRunsService.run`) is a serial loop over testbench items
that calls into the shared `services/annotator.process_item` pipeline and
persists results into `studio_run_items`. The resolver chain
(`resolve_clip_input`) handles upload-vs-CatDV and the offline / cache
fallback path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)


@dataclass
class ResolvedInput:
    local_path: Path | None       # set for uploads + cached CatDV
    file_ref: Any | None          # set when we skipped to ai_store fallback
    clip_snapshot: dict[str, Any]
    archive_lookup_arg: str | None  # for archive.get_clip; None for uploads / cache-only


@dataclass
class Unacceptable:
    reason: str


async def resolve_clip_input(
    item,
    *,
    mode: str,                    # "online" | "offline" | "forced_offline"
    proxy_resolver,               # main resolver (may be None if CatDV never logged in)
    archive,                      # ArchiveProvider | None
    cache_only_resolver,          # LocalCacheOnlyResolver
    clip_cache_repo,
    ai_store,
    db: aiosqlite.Connection,
    uploads_root: Path,
) -> ResolvedInput | Unacceptable:
    if item.source_kind == "upload":
        assert item.upload_path is not None
        local = uploads_root / item.upload_path
        if not local.exists():
            return Unacceptable(reason=f"upload file missing: {item.upload_path}")
        return ResolvedInput(
            local_path=local,
            file_ref=None,
            clip_snapshot={"name": item.display_name},
            archive_lookup_arg=None,
        )

    # source_kind == 'catdv_clip'
    cid = item.catdv_provider_clip_id
    assert cid is not None  # CHECK constraint guarantees this

    if mode == "online" and archive is not None and proxy_resolver is not None:
        try:
            canonical = await archive.get_clip(cid)
            local = await proxy_resolver.path_for_clip_id(int(cid))
            return ResolvedInput(
                local_path=local, file_ref=None,
                clip_snapshot=dict(canonical.provider_data),
                archive_lookup_arg=cid,
            )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "studio resolver: live archive/path failed for %s: %s", cid, exc
            )

    try:
        local = await cache_only_resolver.path_for_clip_id(int(cid))
    except FileNotFoundError:
        local = None
    if local is not None:
        snapshot: dict[str, Any] = {"id": cid, "name": item.display_name}
        try:
            cached = await clip_cache_repo.get_by_key(
                db, provider_id="catdv", provider_clip_id=cid
            )
            if cached is not None:
                snapshot = dict(cached.provider_data)
        except Exception:  # noqa: BLE001
            pass
        return ResolvedInput(
            local_path=local, file_ref=None,
            clip_snapshot=snapshot, archive_lookup_arg=None,
        )

    uploaded = await ai_store.status(("catdv", cid))
    if uploaded is not None:
        file_ref = await ai_store.reference_for_gemini(uploaded)
        return ResolvedInput(
            local_path=None, file_ref=file_ref,
            clip_snapshot={"id": cid, "name": item.display_name},
            archive_lookup_arg=None,
        )

    return Unacceptable(
        reason=(
            f"catdv clip {cid}: archive unreachable; not in proxy_cache; "
            f"not in ai_store"
        )
    )


from backend.app.services.annotator import process_item  # noqa: E402


class _PreResolvedShim:
    """Adapts a pre-resolved Path to the proxy_resolver protocol so
    `process_item` doesn't need to know about the resolver chain."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def path_for_clip_id(self, _arg) -> Path:
        return self._path


class _PreResolvedStore:
    """Adapts a pre-resolved Gemini file_ref to the ai_store protocol."""

    def __init__(self, file_ref) -> None:
        self._ref = file_ref

    async def ensure_uploaded(self, *_args, **_kw):
        return self._ref

    async def reference_for_gemini(self, _upload):
        return self._ref


class StudioRunsService:
    def __init__(
        self,
        *,
        runs_repo,
        items_repo,
        prompts_repo,
        archive,
        proxy_resolver,
        cache_only_resolver,
        clip_cache_repo,
        ai_store,
        gemini,
        event_bus,
        uploads_root: Path,
        mode_getter,
    ) -> None:
        self.runs_repo = runs_repo
        self.items_repo = items_repo
        self.prompts_repo = prompts_repo
        self.archive = archive
        self.proxy_resolver = proxy_resolver
        self.cache_only_resolver = cache_only_resolver
        self.clip_cache_repo = clip_cache_repo
        self.ai_store = ai_store
        self.gemini = gemini
        self.event_bus = event_bus
        self.uploads_root = uploads_root
        self.mode_getter = mode_getter

    async def create_run(
        self, conn: aiosqlite.Connection,
        *, testbench_id: int, prompt_version_id: int,
    ) -> int:
        return await self.runs_repo.create(
            conn,
            testbench_id=testbench_id,
            prompt_version_id=prompt_version_id,
        )

    async def cancel(self, conn: aiosqlite.Connection, run_id: int) -> None:
        await self.runs_repo.update_status(conn, run_id, "cancelled", finished=True)
        await self.event_bus.publish(
            f"studio_run:{run_id}", {"run_status": "cancelled"}
        )

    async def run(self, conn: aiosqlite.Connection, run_id: int) -> None:
        run = await self.runs_repo.get(conn, run_id)
        if run.status == "cancelled":
            log.info("studio run %s already cancelled before start; skipping", run_id)
            return
        version = await self.prompts_repo.get_version(conn, run.prompt_version_id)
        await self.runs_repo.update_status(conn, run_id, "running", started=True)
        topic = f"studio_run:{run_id}"
        items = await self.items_repo.list_for_testbench(conn, run.testbench_id)

        had_error = False
        for tbi in items:
            current = await self.runs_repo.get(conn, run_id)
            if current.status == "cancelled":
                log.info("studio run %s cancelled mid-loop; stopping", run_id)
                return  # do not flip final status; it's already 'cancelled'

            ri_id = await self.runs_repo.upsert_item(
                conn, run_id=run_id, testbench_item_id=tbi.id,
            )
            try:
                resolved = await resolve_clip_input(
                    tbi,
                    mode=self.mode_getter(),
                    proxy_resolver=self.proxy_resolver,
                    archive=self.archive,
                    cache_only_resolver=self.cache_only_resolver,
                    clip_cache_repo=self.clip_cache_repo,
                    ai_store=self.ai_store,
                    db=conn,
                    uploads_root=self.uploads_root,
                )
                if isinstance(resolved, Unacceptable):
                    await self.runs_repo.update_item_status(
                        conn, ri_id, "unacceptable",
                        unacceptable_reason=resolved.reason,
                    )
                    await self.event_bus.publish(
                        topic,
                        {
                            "item_id": ri_id, "status": "unacceptable",
                            "reason": resolved.reason,
                        },
                    )
                    continue

                async def on_status(s: str, ri=ri_id) -> None:
                    await self.runs_repo.update_item_status(conn, ri, s)
                    await self.event_bus.publish(
                        topic, {"item_id": ri, "status": s}
                    )

                if resolved.local_path is not None:
                    shim_resolver = _PreResolvedShim(resolved.local_path)
                    shim_store = self.ai_store
                else:
                    shim_resolver = _PreResolvedShim(Path("/dev/null"))
                    shim_store = _PreResolvedStore(resolved.file_ref)

                clip_key_id = (
                    Path(str(resolved.local_path)).stem if resolved.local_path
                    else (tbi.catdv_provider_clip_id or f"upload-{tbi.id}")
                )
                clip_key = (
                    "studio_upload" if tbi.source_kind == "upload" else "catdv",
                    str(clip_key_id),
                )

                out = await process_item(
                    clip_resolver_arg=resolved.local_path,
                    archive_lookup_arg=resolved.archive_lookup_arg,
                    clip_key=clip_key,
                    version=version,
                    proxy_resolver=shim_resolver,
                    archive=self.archive if resolved.archive_lookup_arg else None,
                    ai_store=shim_store,
                    gemini=self.gemini,
                    on_status=on_status,
                )
                await self.runs_repo.attach_output(
                    conn, ri_id,
                    structured_json=(
                        json.dumps(out.structured, ensure_ascii=False)
                        if out.structured is not None else None
                    ),
                    raw_text=out.raw_text,
                    prompt_used=out.prompt_used,
                    model=out.model,
                    latency_ms=out.latency_ms,
                )
                await self.event_bus.publish(
                    topic, {"item_id": ri_id, "status": "done"}
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("studio run %s item %s failed", run_id, tbi.id)
                had_error = True
                await self.runs_repo.update_item_status(
                    conn, ri_id, "error", error=str(exc),
                )
                await self.event_bus.publish(
                    topic, {"item_id": ri_id, "status": "error", "error": str(exc)},
                )

        final = "failed" if had_error else "completed"
        await self.runs_repo.update_status(conn, run_id, final, finished=True)
        await self.event_bus.publish(topic, {"run_status": final})
