"""Reconcile the `proxy_cache` table against the on-disk cache directory.

`RestProxyResolver` writes a `proxy_cache` row after every successful
download, so steady-state operation keeps the table in sync. But files
can pre-exist this code path (older PRs, manual downloads, restored
backups) and rows can outlive their files (manual `rm`, broken eviction).
A startup reconciler closes both gaps so the cache view is the source of
truth for *what's actually on disk*, not just "what was downloaded by
this build".

The reconciler is idempotent — running it twice in a row produces no
writes the second time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import aiosqlite

from backend.app.repositories.proxy_cache import ProxyCacheRepo

log = logging.getLogger(__name__)


class ProxyCacheReconciler:
    def __init__(
        self,
        *,
        cache_dir: Path,
        proxy_cache_repo: ProxyCacheRepo,
        db_provider: Callable[[], aiosqlite.Connection],
        provider_id: str = "catdv",
    ) -> None:
        self._cache_dir = cache_dir
        self._repo = proxy_cache_repo
        self._db_provider = db_provider
        self._provider_id = provider_id

    async def reconcile(self) -> dict[str, int]:
        """Run both passes. Returns counters for logging/tests."""
        counters = {
            "files_seen": 0,
            "rows_inserted": 0,
            "rows_size_updated": 0,
            "rows_deleted": 0,
        }
        if not self._cache_dir.exists():
            log.debug(
                "reconciler: cache_dir does not exist yet (%s) — skipping",
                self._cache_dir,
            )
            return counters

        conn = self._db_provider()
        await self._files_to_rows(conn, counters)
        await self._rows_to_files(conn, counters)

        if counters["rows_inserted"] or counters["rows_size_updated"] or counters["rows_deleted"]:
            log.info(
                "proxy_cache reconciliation: +%d rows, ~%d size-fixes, -%d phantoms",
                counters["rows_inserted"],
                counters["rows_size_updated"],
                counters["rows_deleted"],
            )
        return counters

    async def _files_to_rows(
        self,
        conn: aiosqlite.Connection,
        counters: dict[str, int],
    ) -> None:
        for file_path in sorted(self._cache_dir.iterdir()):
            if not file_path.is_file():
                continue
            stem = file_path.stem
            if not stem.isdigit():
                # Ignore anything that isn't <clip_id>.<ext>; this lets
                # README files and partial downloads (`*.part`) sit
                # peacefully alongside the proxies.
                continue
            clip_id = int(stem)
            counters["files_seen"] += 1
            try:
                actual_size = file_path.stat().st_size
            except OSError as exc:
                log.warning("reconciler: cannot stat %s: %s", file_path, exc)
                continue
            if actual_size == 0:
                # Zero-byte file is a half-finished download; ignore
                # (the `_rows_to_files` pass will delete a row pointing
                # at it if one exists).
                continue
            existing = await self._repo.get(conn, clip_id)
            if existing is None:
                await self._repo.record(
                    conn,
                    clip_id=clip_id,
                    file_path=str(file_path),
                    size_bytes=actual_size,
                    etag=None,
                    provider_id=self._provider_id,
                    provider_clip_id=str(clip_id),
                )
                counters["rows_inserted"] += 1
                log.info(
                    "reconciler: backfilled proxy_cache for %s (%d bytes)",
                    file_path,
                    actual_size,
                )
            elif int(existing["size_bytes"]) != actual_size:
                await self._repo.record(
                    conn,
                    clip_id=clip_id,
                    file_path=str(file_path),
                    size_bytes=actual_size,
                    etag=existing.get("etag"),
                    provider_id=self._provider_id,
                    provider_clip_id=str(clip_id),
                )
                counters["rows_size_updated"] += 1
                log.info(
                    "reconciler: size drift for %s (db=%s, fs=%d) — aligned",
                    file_path,
                    existing["size_bytes"],
                    actual_size,
                )

    async def _rows_to_files(
        self,
        conn: aiosqlite.Connection,
        counters: dict[str, int],
    ) -> None:
        cur = await conn.execute("SELECT catdv_clip_id, file_path FROM proxy_cache")
        rows = await cur.fetchall()
        for clip_id, file_path in rows:
            p = Path(file_path)
            if p.exists() and p.stat().st_size > 0:
                continue
            await self._repo.delete(conn, int(clip_id))
            counters["rows_deleted"] += 1
            log.warning(
                "reconciler: removed phantom proxy_cache row %d → %s",
                clip_id,
                file_path,
            )
