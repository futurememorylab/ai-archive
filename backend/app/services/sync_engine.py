"""SyncEngine: drains pending_operations against the active ArchiveProvider.

Lifecycle:
- `start()` spawns a background loop that ticks every `tick_interval_s`
  and whenever `notify()` is called.
- `tick()` (alias `drain_once()`) is the single-shot drain: groups
  pending ops by clip, builds one ChangeSet per clip, calls
  `provider.apply_changes`, and updates the queue rows accordingly.

Connection awareness:
- Before draining, the engine consults the `ConnectionMonitor` (if wired);
  if state is not `online`, the tick returns immediately.

Retry policy:
- `RetryableError` from the adapter or `WriteResult(status="retryable")`
  bumps the row's attempts counter; the row stays at `status='pending'`.
- A pending row is skipped on later ticks until
  `attempted_at + min(retry_max_s, retry_base_s * 2**(attempts-1))` has
  elapsed.

Conflict / fatal:
- `WriteResult(status="conflict")` -> `mark_conflict` (terminal until
  user resolves).
- `WriteResult(status="fatal")` or `FatalProviderError` -> `mark_failed`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.change_set_json import change_op_from_json
from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    ProviderError,
    RetryableError,
)
from backend.app.archive.model import ChangeSet
from backend.app.services.connection_monitor import (
    ConnectionMonitor,
    ConnectionState,
)

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        ts = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


class SyncEngine:
    def __init__(
        self,
        *,
        provider: Any,
        pending_ops_repo: Any,
        write_log_repo: Any,
        connection_monitor: ConnectionMonitor | None,
        db_provider: Callable[[], aiosqlite.Connection],
        event_bus: Any = None,
        tick_interval_s: float = 5.0,
        retry_base_s: float = 2.0,
        retry_max_s: float = 300.0,
        max_attempts: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._pending = pending_ops_repo
        self._write_log = write_log_repo
        self._monitor = connection_monitor
        self._db_provider = db_provider
        self._event_bus = event_bus
        self._tick_interval_s = tick_interval_s
        self._retry_base_s = retry_base_s
        self._retry_max_s = retry_max_s
        self._max_attempts = max_attempts
        self._clock = clock or _now
        self._notify_evt: asyncio.Event = asyncio.Event()
        self._stop_evt: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- public API --------------------------------------------------

    def notify(self) -> None:
        self._notify_evt.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        self._notify_evt.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def drain_once(self) -> int:
        """Run one tick. Returns the number of clips processed."""
        return await self._tick()

    # --- internals ---------------------------------------------------

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 — engine loop must not die
                log.exception("sync_engine tick failed")
            try:
                await asyncio.wait_for(self._notify_evt.wait(), timeout=self._tick_interval_s)
            except TimeoutError:
                pass
            self._notify_evt.clear()

    async def _tick(self) -> int:
        if self._monitor is not None:
            if self._monitor.current_state() != ConnectionState.online:
                return 0

        db = self._db_provider()
        pending = await self._pending.list_pending(db)
        if not pending:
            return 0

        # filter out rows whose backoff hasn't elapsed
        now = self._clock()
        eligible: list[dict[str, Any]] = []
        for r in pending:
            attempts = int(r.get("attempts") or 0)
            if attempts == 0:
                eligible.append(r)
                continue
            attempted_at = _parse_iso(r.get("attempted_at"))
            if attempted_at is None:
                eligible.append(r)
                continue
            delay = min(self._retry_max_s, self._retry_base_s * (2 ** (attempts - 1)))
            if (now - attempted_at).total_seconds() >= delay:
                eligible.append(r)
        if not eligible:
            return 0

        # group by (provider_id, provider_clip_id) preserving order
        groups: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
        for r in eligible:
            key = (r["provider_id"], r["provider_clip_id"])
            groups.setdefault(key, []).append(r)

        processed = 0
        for (provider_id, clip_id), rows in groups.items():
            if provider_id != getattr(self._provider, "id", "catdv"):
                # Foreign provider; skip silently.
                continue
            op_ids = [r["id"] for r in rows]
            ops = [change_op_from_json(r["op_json"]) for r in rows]
            expected_etag = rows[0].get("expected_etag")
            change_set = ChangeSet(
                clip_key=(provider_id, clip_id),
                ops=tuple(ops),
                expected_etag=expected_etag,
            )

            await self._pending.mark_in_flight(db, op_ids)

            try:
                result = await self._provider.apply_changes(change_set)
            except RetryableError as exc:
                await self._pending.mark_retryable(db, op_ids, error=str(exc))
                continue
            except (AuthError, FatalProviderError) as exc:
                await self._pending.mark_failed(db, op_ids, error=str(exc))
                continue
            except ProviderError as exc:
                await self._pending.mark_failed(db, op_ids, error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — unknown adapter bug
                # Default to retryable: unknown exception is most often
                # transient (transport bug, adapter glitch). The
                # max-attempts ceiling prevents an infinitely-retried row
                # from blocking the queue. See ADR 0042 (added in this PR).
                #
                # Use min(attempts) across the group: ceiling fires only
                # when the YOUNGEST op in the batch has hit the cap. Using
                # max would kill younger ops early when sibling ops on the
                # same clip have divergent attempt counts.
                next_attempts = min(int(r.get("attempts") or 0) for r in rows) + 1
                err_msg = f"{type(exc).__name__}: {exc}"
                if next_attempts >= self._max_attempts:
                    # Atomic: status='failed' AND attempts+=1 in one SQL
                    # statement. A two-call sequence (mark_retryable then
                    # mark_failed) would leave a crash window where the
                    # row stays 'pending' at the ceiling and gets retried
                    # past it.
                    await self._pending.mark_failed(
                        db, op_ids,
                        error=f"{err_msg}; max_attempts={self._max_attempts} reached",
                        bump_attempts=True,
                    )
                else:
                    await self._pending.mark_retryable(db, op_ids, error=err_msg)
                continue

            await self._handle_result(
                db,
                provider_id=provider_id,
                clip_id=clip_id,
                op_ids=op_ids,
                rows=rows,
                result=result,
            )
            processed += 1
            if self._event_bus is not None:
                await self._event_bus.publish(
                    "sync",
                    {
                        "provider_id": provider_id,
                        "provider_clip_id": clip_id,
                        "status": result.status,
                    },
                )
        return processed

    async def _handle_result(
        self,
        db: aiosqlite.Connection,
        *,
        provider_id: str,
        clip_id: str,
        op_ids: list[int],
        rows: list[dict[str, Any]],
        result: Any,
    ) -> None:
        if result.status == "ok":
            await self._pending.mark_applied(db, op_ids)
            annotation_id = rows[0].get("origin_annotation_id")
            try:
                catdv_clip_id = int(clip_id)
            except (TypeError, ValueError):
                catdv_clip_id = 0
            await self._write_log.record(
                db,
                catdv_clip_id=catdv_clip_id,
                annotation_id=annotation_id,
                payload={"ops": [r["op_kind"] for r in rows]},
                response=result.upstream_response,
                status="ok",
                provider_id=provider_id,
                provider_clip_id=clip_id,
            )
        elif result.status == "conflict":
            detail = None
            cd = result.conflict_detail
            if cd is not None:
                detail = {
                    "kind": cd.kind,
                    "expected_etag": cd.expected_etag,
                    "actual_etag": cd.actual_etag,
                    "fields": cd.fields,
                }
            await self._pending.mark_conflict(db, op_ids, conflict_detail=detail)
        elif result.status == "retryable":
            await self._pending.mark_retryable(
                db,
                op_ids,
                error=json.dumps(result.upstream_response or {}),
            )
        else:  # fatal
            await self._pending.mark_failed(
                db,
                op_ids,
                error=json.dumps(result.upstream_response or {}),
            )
