# PR 4: WriteQueue + SyncEngine + ConnectionMonitor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the synchronous "Apply accepted" path off the request thread and behind a durable journal. Adds the `pending_operations` and `connection_events` tables, the canonical `WriteResult` / `ConflictDetail` types with JSON (de)serialisation for `ChangeOp`/`ChangeSet`, a `WriteQueue` repository that turns accepted review items into queued ops atomically, a `SyncEngine` background task that drains the queue per-clip respecting connection state, and a `ConnectionMonitor` that probes `provider.health()` and persists state transitions. The apply route now enqueues + notifies instead of writing live; the upstream PUT still happens (the engine drains immediately when online) but goes through the new typed boundary. No user-visible behaviour change — PR 4 is plumbing for the offline workflow PR 5 will expose. This is the fourth of seven PRs implementing the design in `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` (§7.1, §7.2, §7.3, §11, §13 PR 4).

**Architecture:** A single new migration `0004_pending_operations_and_connection_events.sql` creates the two new tables. `backend/app/archive/model.py` gains a `ConflictDetail` frozen dataclass plus JSON (de)serialisation helpers in a new `backend/app/archive/change_set_json.py` module (kept separate so `model.py` stays declarative); `WriteResult` is extended with `conflict_detail: ConflictDetail | None`. A new `backend/app/repositories/pending_operations.py` provides `PendingOperationsRepo` — atomic enqueue / list / status-transition methods over the new table; a new `backend/app/services/write_queue.py` wraps the repo with the higher-level `enqueue_apply(...)` that groups review items into `ChangeOp`s in one transaction with `review_items.mark_applied`. A new `backend/app/services/connection_monitor.py` exposes `ConnectionState` (StrEnum) and a `ConnectionMonitor` async service that probes `provider.health()` every `health_probe_interval_s` and persists state changes into `connection_events`. A new `backend/app/services/sync_engine.py` runs an async loop: on `notify()` or every `sync_tick_interval_s`, it pulls pending ops grouped per `(provider_id, provider_clip_id)`, builds a single `ChangeSet` per clip, calls `provider.apply_changes`, and updates the queue rows (`applied`, `conflict`, `retryable` with attempts+backoff, `fatal`). The `CatdvArchiveAdapter.apply_changes` is reworked to (a) capture `modifyDate` as a pseudo-etag, (b) compare against `change_set.expected_etag` and short-circuit with `WriteResult(status="conflict", conflict_detail=…)` on drift, (c) write `write_log` only on successful upstream PUT. The apply route refactors to call `ctx.write_queue.enqueue_apply(...)` then `ctx.sync_engine.notify()`. `AppContext` gains `pending_ops_repo`, `write_queue`, `sync_engine`, `connection_monitor` and `start()`/`stop()` of the latter two in `build`/`aclose`. On startup, `pending_operations` rows left in `status='in_flight'` are flipped back to `pending` (crash recovery). Settings adds five new `*_s` knobs.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dataclasses (frozen), `asyncio`, `aiosqlite`, `pytest` + `pytest-asyncio`. No new third-party deps. Migration is a plain SQL file applied by `migrations_runner.apply_migrations`.

**Scope guardrail:** This plan implements ONLY what spec §13 PR 4 lists, plus the §11 conflict-policy machinery needed for the WriteResult shape. NOT in this PR: workspaces / workspace_clips / WorkspaceManager (PR 5), the connection-pill / sync-drawer / "Work offline" UI toggle (PR 5), cache_actions_log / CacheInspector / CacheActions (PR 6), FS adapter (PR 7), conflict-resolution UI, removing `payload_builder` (already moved to `archive/providers/catdv/payload.py` in PR 1; PR 4 just wraps it behind the adapter). An SSE endpoint for connection state is allowed only if trivially small; no template/HTMX changes. No multi-provider runtime selection. The `connection_events` table is structurally created in this migration even though only the monitor writes to it — PR 5's UI is what reads it.

**Decisions to record in `docs/decisions.md`** (Task 15, if non-trivial):
1. **One migration covers both tables.** `pending_operations` and `connection_events` are conceptually one feature (the offline-write plumbing) and live behind the same code path; splitting them complicates rollback without buying anything.
2. **Manual `db_provider` callable for SyncEngine / WriteQueue.** The same pattern used in PR 3 for the cache adapters (`db_provider: Callable[[], aiosqlite.Connection]`). The single shared `aiosqlite.Connection` on `AppContext.db` is serial (sqlite is single-writer), so no pool / lock is needed beyond the connection's own.
3. **`expected_etag` policy for CatDV.** CatDV has no real etag. We use the `modifyDate` field from the upstream clip JSON as the pseudo-etag: captured at enqueue time (snapshot's modifyDate) and re-read at drain time. The adapter is responsible for the comparison; the queue just stores the string opaquely.
4. **`enqueue_apply` is atomic with `review_items.mark_applied`.** Both writes happen in one `conn.execute(...)` sequence followed by one `conn.commit()`. If `mark_applied` fails the queue insert rolls back via SAVEPOINT, so a double-click can never produce duplicate pending ops for the same review items. Two Apply clicks racing both see the same set of accepted items; the second sees `applied_at IS NOT NULL` and selects zero items.
5. **Per-clip ordering.** `pending_operations.enqueued_at` (ISO-8601 timestamp) plus the auto-increment `id` provide a total order; the engine processes per-clip ops in `enqueued_at, id` order. Because the engine collapses all of a clip's pending ops into one `ChangeSet`, the order matters only within that ChangeSet (ops are passed through to `build_put_payload` in queue order — that helper is already deterministic w.r.t. op order).
6. **Crash recovery: `in_flight → pending` on startup.** A simple `UPDATE pending_operations SET status='pending' WHERE status='in_flight'` runs in `AppContext.build` after migrations. The alternative — leaving them stuck and waiting for an operator — is worse because the engine would never retry.
7. **Conflict detection live only at the adapter.** The engine treats `WriteResult.status` opaquely. The CatDV adapter compares pre-drain `modifyDate` (from `change_set.expected_etag`) with freshly-fetched `modifyDate` and short-circuits with `status="conflict"` if they differ. Field/note value comparison (spec §11) is left as a future refinement — for PR 4, any drift is a conflict at the row level, which is the conservative position.
8. **SSE endpoint for connection state is in-scope but minimal.** `GET /api/connection/events` streams a stable stream of `{state, at, detail}` events from the monitor. No UI consumes it yet; spec §7.3 calls for it; the cost to add is ~20 lines and writing the SSE plumbing only once is cleaner than skipping it.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/migrations/0004_pending_operations_and_connection_events.sql` | Creates `pending_operations` (+ index) and `connection_events`. |
| `backend/app/archive/change_set_json.py` | JSON (de)serialisation of `ChangeOp` and `ChangeSet`. Round-trips through the `pending_operations.op_json` column. |
| `backend/app/repositories/pending_operations.py` | `PendingOperationsRepo` — `insert_many`, `list_pending_for_clip`, `list_pending`, `mark_in_flight`, `mark_applied`, `mark_conflict`, `mark_retryable`, `mark_failed`, `reset_in_flight_to_pending`. Atomic w.r.t. its own writes. |
| `backend/app/services/write_queue.py` | `WriteQueue.enqueue_apply(clip_key, accepted_items, template, annotation, fps) -> list[int]`. Groups items into ops in the same logic the route uses today; writes pending rows + marks review_items applied atomically. |
| `backend/app/services/connection_monitor.py` | `ConnectionState` StrEnum + `ConnectionMonitor` async service. Periodic `provider.health()` probe, persists transitions to `connection_events`, exposes `current_state()` + `set_manual_offline(bool)` + an async `subscribe()` for SSE. `start()` / `stop()`. |
| `backend/app/services/sync_engine.py` | `SyncEngine` async service. `tick()` drains pending ops per provider per clip; `notify()` wakes the loop; `start()` / `stop()`; `drain_once()` (test helper that runs one tick synchronously). Retry with exponential backoff between `sync_retry_base_s` and `sync_retry_max_s`. |
| `backend/app/routes/connection.py` | `GET /api/connection/state` returns the monitor's current state; `GET /api/connection/events` SSE stream. |
| `tests/integration/test_migration_0004.py` | Asserts `pending_operations` + `connection_events` exist with the right columns and index. |
| `tests/integration/test_pending_operations_repo.py` | DB-level: insert_many, list_pending, status transitions, attempts increment, reset_in_flight_to_pending. |
| `tests/integration/test_write_queue.py` | Service-level: enqueues correct ops for marker/field/note items; atomic with review_items.mark_applied; double-apply produces no duplicate rows. |
| `tests/integration/test_sync_engine.py` | Engine drains immediately when online; per-clip batching; retry on RetryableError; respects connection state. |
| `tests/integration/test_connection_monitor.py` | Monitor transitions online→offline→online based on a fake provider; persists `connection_events`; manual offline pins. |
| `tests/integration/test_routes_connection.py` | `GET /api/connection/state` returns current state. (SSE endpoint covered ad-hoc.) |
| `tests/unit/test_change_set_json.py` | Round-trip every ChangeOp kind + full ChangeSet through JSON. |
| `tests/unit/test_write_result_conflict_detail.py` | `WriteResult` carrying `ConflictDetail`. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/archive/model.py` | Add `ConflictDetail` frozen dataclass; extend `WriteResult` with `conflict_detail: ConflictDetail | None = None`. |
| `backend/app/archive/provider.py` | Add `health()` to the `ArchiveProvider` Protocol. (`apply_changes` is already there from PR 1.) |
| `backend/app/archive/providers/catdv/adapter.py` | (a) Add `health()` method (`GET /catdv/api/info` via a new `catdv_client.health()` thin wrapper); (b) `apply_changes` now compares `change_set.expected_etag` to live `modifyDate`, returns `WriteResult(status="conflict", conflict_detail=…)` on drift; on success returns `new_etag=modifyDate`. The old logic still PUTs and returns OK. |
| `backend/app/services/catdv_client.py` | Add `health()` (GET `/catdv/api/info`, returns `True` on OK envelope, raises on AUTH/ERROR/timeout). |
| `backend/app/routes/review.py` | Apply route now calls `ctx.write_queue.enqueue_apply(...)` then `ctx.sync_engine.notify()` and returns `{"queued": N}`. Helpers move to `write_queue.py`. |
| `backend/app/main.py` | Register `connection` router. Start the sync engine + connection monitor inside `lifespan` (after `ctx.build()`), stop them before `ctx.aclose()`. |
| `backend/app/context.py` | Add `pending_ops_repo`, `write_queue`, `sync_engine`, `connection_monitor` fields; wire them in `build`. Run the in_flight→pending reset post-migration. Wire `health()` callback into the monitor. |
| `backend/app/settings.py` | Add `health_probe_interval_s`, `health_probe_timeout_s`, `sync_retry_base_s`, `sync_retry_max_s`, `sync_tick_interval_s`. |
| `backend/app/repositories/write_log.py` | Add `provider_id`/`provider_clip_id` parameters to `record` (defaults `'catdv'` + str(catdv_clip_id) for back-compat). The engine writes the audit row with these populated. |
| `tests/integration/test_initial_schema.py` | `EXPECTED_TABLES` gains `"pending_operations"` and `"connection_events"`. |
| `tests/integration/test_routes_review.py` | The apply test now awaits `ctx.sync_engine.drain_once()` after POSTing and asserts the FakeArchive saw the ChangeSet. Response shape becomes `{"queued": N}` (old key kept as alias for one release). |

### Deleted files

None.

---

## Tasks

### Task 1: Migration 0004 — pending_operations + connection_events

**Files:**
- Create: `backend/migrations/0004_pending_operations_and_connection_events.sql`
- Create: `tests/integration/test_migration_0004.py`
- Modify: `tests/integration/test_initial_schema.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/integration/test_migration_0004.py`:

```python
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _columns(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_pending_operations_table_has_expected_columns(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "pending_operations")
    assert {
        "id", "provider_id", "provider_clip_id", "op_kind", "op_json",
        "origin_annotation_id", "origin_review_item_ids", "expected_etag",
        "status", "attempts", "last_error",
        "enqueued_at", "attempted_at", "applied_at",
    }.issubset(cols)


@pytest.mark.asyncio
async def test_pending_operations_index_exists(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='pending_operations'"
        )
        names = {r[0] for r in await cur.fetchall()}
    assert "idx_pending_ops_status" in names


@pytest.mark.asyncio
async def test_connection_events_table_has_expected_columns(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cols = await _columns(conn, "connection_events")
    assert {"id", "state", "detail", "at"}.issubset(cols)
```

Add `"pending_operations"` + `"connection_events"` to `EXPECTED_TABLES` in `tests/integration/test_initial_schema.py`.

- [ ] **Step 2: Run the test, verify it fails**

```bash
.venv/bin/pytest tests/integration/test_migration_0004.py tests/integration/test_initial_schema.py -v
```

- [ ] **Step 3: Create the migration file**

`backend/migrations/0004_pending_operations_and_connection_events.sql`:

```sql
-- PR 4: durable journal for upstream writes + connection-state audit.

CREATE TABLE pending_operations (
  id                     INTEGER PRIMARY KEY,
  provider_id            TEXT NOT NULL,
  provider_clip_id       TEXT NOT NULL,
  op_kind                TEXT NOT NULL,
  op_json                TEXT NOT NULL,
  origin_annotation_id   INTEGER REFERENCES annotations(id),
  origin_review_item_ids TEXT,
  expected_etag          TEXT,
  status                 TEXT NOT NULL,
  attempts               INTEGER NOT NULL DEFAULT 0,
  last_error             TEXT,
  enqueued_at            TEXT NOT NULL,
  attempted_at           TEXT,
  applied_at             TEXT
);
CREATE INDEX idx_pending_ops_status ON pending_operations(status, enqueued_at);

CREATE TABLE connection_events (
  id      INTEGER PRIMARY KEY,
  state   TEXT NOT NULL,
  detail  TEXT,
  at      TEXT NOT NULL
);
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0004_pending_operations_and_connection_events.sql \
        tests/integration/test_migration_0004.py \
        tests/integration/test_initial_schema.py
git commit -m "chore(migrations): 0004 pending_operations + connection_events"
```

---

### Task 2: ConflictDetail + WriteResult extension

**Files:**
- Modify: `backend/app/archive/model.py`
- Create: `tests/unit/test_write_result_conflict_detail.py`

- [ ] **Step 1: Write failing test**

```python
from backend.app.archive.model import ConflictDetail, WriteResult


def test_conflict_detail_carries_diff_payload():
    cd = ConflictDetail(
        kind="modified",
        expected_etag="v1",
        actual_etag="v2",
        fields={"pragafilm.theme": {"local": "x", "remote": "y"}},
    )
    assert cd.kind == "modified"
    assert cd.fields["pragafilm.theme"]["remote"] == "y"


def test_write_result_can_carry_conflict_detail():
    cd = ConflictDetail(kind="modified", expected_etag="v1", actual_etag="v2")
    wr = WriteResult(
        status="conflict",
        upstream_response={},
        new_etag=None,
        conflict_detail=cd,
    )
    assert wr.status == "conflict"
    assert wr.conflict_detail is cd
```

- [ ] **Step 2: Run, verify failure** (import errors).

- [ ] **Step 3: Add the types**

In `model.py`:

```python
@dataclass(frozen=True)
class ConflictDetail:
    kind: Literal["modified", "deleted", "marker-overlap"]
    expected_etag: str | None = None
    actual_etag: str | None = None
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
```

Extend `WriteResult` with `conflict_detail: ConflictDetail | None = None`. Drop the obsolete `detail: str | None` (replaced by `conflict_detail`) — search the repo for references first and migrate the two call-sites in the adapter (the no-op path becomes `WriteResult(status="ok", upstream_response={}, new_etag=None)`).

- [ ] **Step 4: Run tests, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(archive): ConflictDetail + WriteResult.conflict_detail"
```

---

### Task 3: ChangeSet JSON (de)serialisation

**Files:**
- Create: `backend/app/archive/change_set_json.py`
- Create: `tests/unit/test_change_set_json.py`

- [ ] **Step 1: Write failing test**

```python
import json

from backend.app.archive.change_set_json import (
    change_op_from_json,
    change_op_to_json,
    change_set_from_dict,
    change_set_to_dict,
)
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeSet,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)


def test_set_field_round_trip():
    op = SetField(identifier="pragafilm.theme", value=["a", "b"])
    raw = change_op_to_json(op)
    decoded = change_op_from_json(raw)
    assert decoded == op


def test_append_and_replace_note_round_trip():
    for op in (AppendNote(target="notes", text="x"),
               ReplaceNote(target="bigNotes", text="y")):
        assert change_op_from_json(change_op_to_json(op)) == op


def test_add_markers_round_trip():
    m = Marker(name="a",
               in_=Timecode(secs=0.0, fps=25.0, frm=0),
               out=Timecode(secs=1.0, fps=25.0, frm=25))
    op = AddMarkers(markers=(m,))
    decoded = change_op_from_json(change_op_to_json(op))
    assert decoded == op


def test_change_set_round_trip():
    cs = ChangeSet(
        clip_key=("catdv", "42"),
        ops=(
            SetField(identifier="x", value=1),
            AppendNote(target="notes", text="hi"),
        ),
        expected_etag="2026-05-19T00:00:00Z",
    )
    payload = change_set_to_dict(cs)
    assert json.loads(json.dumps(payload))
    decoded = change_set_from_dict(payload)
    assert decoded == cs
```

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement**

```python
# backend/app/archive/change_set_json.py
from __future__ import annotations

import json
from typing import Any

from backend.app.archive.model import (
    AddMarkers, AppendNote, ChangeOp, ChangeSet, Marker, ReplaceNote, SetField,
    Timecode,
)


def _tc_to_dict(tc: Timecode) -> dict[str, Any]:
    return {"secs": tc.secs, "fps": tc.fps, "frm": tc.frm, "txt": tc.txt}


def _tc_from_dict(d: dict[str, Any]) -> Timecode:
    return Timecode(secs=float(d["secs"]), fps=float(d["fps"]),
                    frm=d.get("frm"), txt=d.get("txt"))


def _marker_to_dict(m: Marker) -> dict[str, Any]:
    return {
        "name": m.name,
        "in_": _tc_to_dict(m.in_),
        "out": _tc_to_dict(m.out) if m.out else None,
        "description": m.description,
        "category": m.category,
        "color": m.color,
    }


def _marker_from_dict(d: dict[str, Any]) -> Marker:
    return Marker(
        name=d["name"],
        in_=_tc_from_dict(d["in_"]),
        out=_tc_from_dict(d["out"]) if d.get("out") else None,
        description=d.get("description"),
        category=d.get("category"),
        color=d.get("color"),
    )


def change_op_to_dict(op: ChangeOp) -> dict[str, Any]:
    if isinstance(op, AddMarkers):
        return {"kind": "AddMarkers", "markers": [_marker_to_dict(m) for m in op.markers]}
    if isinstance(op, SetField):
        return {"kind": "SetField", "identifier": op.identifier, "value": op.value}
    if isinstance(op, AppendNote):
        return {"kind": "AppendNote", "target": op.target, "text": op.text}
    if isinstance(op, ReplaceNote):
        return {"kind": "ReplaceNote", "target": op.target, "text": op.text}
    raise TypeError(f"unknown ChangeOp: {type(op).__name__}")


def change_op_from_dict(d: dict[str, Any]) -> ChangeOp:
    k = d.get("kind")
    if k == "AddMarkers":
        return AddMarkers(markers=tuple(_marker_from_dict(m) for m in d["markers"]))
    if k == "SetField":
        return SetField(identifier=d["identifier"], value=d["value"])
    if k == "AppendNote":
        return AppendNote(target=d["target"], text=d["text"])
    if k == "ReplaceNote":
        return ReplaceNote(target=d["target"], text=d["text"])
    raise ValueError(f"unknown ChangeOp kind: {k!r}")


def change_op_to_json(op: ChangeOp) -> str:
    return json.dumps(change_op_to_dict(op), ensure_ascii=False)


def change_op_from_json(raw: str) -> ChangeOp:
    return change_op_from_dict(json.loads(raw))


def change_set_to_dict(cs: ChangeSet) -> dict[str, Any]:
    return {
        "clip_key": list(cs.clip_key),
        "ops": [change_op_to_dict(o) for o in cs.ops],
        "expected_etag": cs.expected_etag,
    }


def change_set_from_dict(d: dict[str, Any]) -> ChangeSet:
    key = d["clip_key"]
    return ChangeSet(
        clip_key=(key[0], key[1]),
        ops=tuple(change_op_from_dict(o) for o in d["ops"]),
        expected_etag=d.get("expected_etag"),
    )
```

- [ ] **Step 4: Run tests, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(archive): JSON (de)serialisation for ChangeOp and ChangeSet"
```

---

### Task 4: PendingOperationsRepo

**Files:**
- Create: `backend/app/repositories/pending_operations.py`
- Create: `tests/integration/test_pending_operations_repo.py`

- [ ] **Step 1: Write failing tests**

Cover: `insert_many` writes rows with status=`pending`, attempts=0, enqueued_at set; `list_pending_for_clip` orders by enqueued_at then id; `list_pending` with `status='pending'` returns only pending; `mark_in_flight` flips status + sets attempted_at; `mark_applied` sets status='applied' + applied_at; `mark_conflict` stores conflict_detail JSON + sets attempted_at; `mark_retryable` increments attempts + sets last_error + leaves status='pending'; `mark_failed` sets status='failed' + last_error; `reset_in_flight_to_pending` flips all in_flight rows back.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement repo**

Schema-aware, raw SQL, same style as `write_log.py` and `clip_cache.py`. Insert via `executemany`. `mark_*` methods take `op_ids: list[int]`. `mark_conflict` stores `json.dumps(conflict_detail)` into `last_error` (no separate column — keep migration narrow). Each method ends with `await conn.commit()`.

A `PendingOpRow` Pydantic-or-dataclass helper deserialises rows for the engine; or return `dict[str, Any]` (matches `clip_cache.get_row`). Go with dict for consistency.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(repo): PendingOperationsRepo with atomic status transitions"
```

---

### Task 5: WriteQueue service

**Files:**
- Create: `backend/app/services/write_queue.py`
- Create: `tests/integration/test_write_queue.py`

- [ ] **Step 1: Write failing test**

`test_enqueue_apply_groups_markers_into_one_op` — feed three marker review items, expect one row with `op_kind='AddMarkers'`. `test_enqueue_apply_emits_one_set_field_per_identifier`. `test_enqueue_apply_emits_append_or_replace_note_based_on_target_map`. `test_enqueue_apply_marks_review_items_applied_atomically` — after enqueue, `review_items.applied_at` is set and a second call enqueues nothing (because `list_by_clip(decision='accepted')` excludes already-applied items, or we filter in WriteQueue). `test_enqueue_apply_captures_expected_etag_from_clip_snapshot` (using the snapshot's `modifyDate`).

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement**

```python
# backend/app/services/write_queue.py
class WriteQueue:
    def __init__(self, *, pending_ops_repo, review_items_repo,
                 clock: Callable[[], datetime] | None = None) -> None:
        ...

    async def enqueue_apply(
        self, conn, *, clip_key: ClipKey, items: list[ReviewItem],
        target_map: TargetMap, expected_etag: str | None,
        annotation_id: int | None, fps: float,
    ) -> list[int]:
        # 1. build ops via _items_to_change_ops (moved from review.py)
        # 2. for each op: write a pending_operations row (with origin_review_item_ids JSON)
        # 3. mark all items as applied in same txn
        # 4. commit
        # returns the op_ids inserted
```

Move the existing `_items_to_change_ops`, `_marker_from_review_value`, `_unwrap`, `_note_mode`, `_fps_from_snapshot` helpers from `routes/review.py` into a private section of this file (they're pure functions).

`review_items_repo` already exposes `mark_applied(conn, ids)`. To make the double-click safe: filter incoming `items` to those with `applied_at IS NULL` (requires the route to call `list_by_clip(decision='accepted')` which already excludes applied — but we add a defensive check). Actually since `mark_applied` sets `applied_at` and the route's `list_by_clip(decision='accepted')` filters only on `decision`, we add an `applied_at IS NULL` filter to that query (or filter inside WriteQueue before enqueueing).

Decision: filter inside WriteQueue (`items_to_enqueue = [it for it in items if it.applied_at is None]`). Requires exposing `applied_at` on the `ReviewItem` Pydantic model + selecting it in the repo. Adjust accordingly. Mark the un-applied subset only.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(svc): WriteQueue enqueue_apply with atomic mark-applied"
```

---

### Task 6: Connection monitor

**Files:**
- Create: `backend/app/services/connection_monitor.py`
- Create: `tests/integration/test_connection_monitor.py`

- [ ] **Step 1: Write failing tests**

- A `FakeProvider` with a `health()` that toggles between OK/raise. Monitor with short intervals (interval_s=0.05) transitions `online → offline → online`, persists 3 `connection_events` rows.
- `set_manual_offline(True)` pins state to `offline` regardless of probe outcome.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement**

```python
class ConnectionState(StrEnum):
    online = "online"
    degraded = "degraded"
    offline = "offline"
    syncing = "syncing"


class ConnectionMonitor:
    def __init__(self, *, provider, db_provider, interval_s, timeout_s,
                 clock=None, event_bus=None) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def current_state(self) -> ConnectionState: ...
    def set_manual_offline(self, enabled: bool) -> None: ...
    async def subscribe(self) -> AsyncIterator[dict]: ...   # for SSE
```

Use `asyncio.wait_for(provider.health(), timeout=timeout_s)`. Persist transitions via direct SQL (no repo for one INSERT). On transition, publish to `event_bus` (topic `"connection"`) and to any subscribers.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(svc): ConnectionMonitor + ConnectionState"
```

---

### Task 7: ArchiveProvider.health() + CatdvClient.health() + adapter.health()

**Files:**
- Modify: `backend/app/archive/provider.py`
- Modify: `backend/app/services/catdv_client.py`
- Modify: `backend/app/archive/providers/catdv/adapter.py`
- Modify: `tests/integration/test_catdv_adapter.py` (add health test using fake)

- [ ] **Step 1: Test**

```python
async def test_adapter_health_returns_ok_on_live_fake(...):
    # fake serves /catdv/api/info OK
    assert (await adapter.health()).ok
```

- [ ] **Step 2: Implement**

Define `ProviderHealth` dataclass (`ok: bool`, `latency_ms: float | None`, `detail: str | None`) in `model.py` (or in `provider.py`; choose `provider.py` since it's a result type, not a domain type).

`catdv_client.health()` → `GET /catdv/api/info`. Returns ok=True on 200+`OK` envelope; ok=False with detail on AUTH or ERROR; raises on transport timeout.

Add `/catdv/api/info` route to `fake_catdv.py` returning OK.

`adapter.health()` wraps `catdv_client.health()`. Adapter version maps to `ProviderHealth`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(archive): ProviderHealth + adapter.health() + CatdvClient.health()"
```

---

### Task 8: Adapter apply_changes — conflict detection

**Files:**
- Modify: `backend/app/archive/providers/catdv/adapter.py`
- Modify: `tests/integration/test_catdv_adapter.py` (or new `test_catdv_adapter_apply.py`)

- [ ] **Step 1: Tests**

- `test_apply_changes_returns_ok_and_new_etag_on_success` — `WriteResult.new_etag == modifyDate` from the upstream response.
- `test_apply_changes_returns_conflict_when_expected_etag_mismatches_current_modify_date`.
- `test_apply_changes_proceeds_when_expected_etag_is_none` (back-compat).

- [ ] **Step 2: Implement**

```python
async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
    ...
    current = await self._client.get_clip(int(clip_id_str))
    live_etag = self._etag_from_raw(current)
    if change_set.expected_etag and live_etag and live_etag != change_set.expected_etag:
        return WriteResult(
            status="conflict",
            upstream_response={},
            new_etag=live_etag,
            conflict_detail=ConflictDetail(
                kind="modified", expected_etag=change_set.expected_etag,
                actual_etag=live_etag,
            ),
        )
    payload = build_put_payload(...)
    if not payload:
        return WriteResult(status="ok", upstream_response={}, new_etag=live_etag)
    response = await self._client.put_clip(...)
    return WriteResult(
        status="ok",
        upstream_response=response,
        new_etag=self._etag_from_raw(response) or live_etag,
    )


def _etag_from_raw(self, raw: dict) -> str | None:
    v = raw.get("modifyDate")
    return str(v) if v is not None else None
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(archive/catdv): conflict detection via modifyDate pseudo-etag"
```

---

### Task 9: SyncEngine

**Files:**
- Create: `backend/app/services/sync_engine.py`
- Create: `tests/integration/test_sync_engine.py`

- [ ] **Step 1: Tests**

- `test_drain_once_applies_pending_op` — enqueue 1 op, call drain_once, assert provider.apply_changes was called and the row is `applied`.
- `test_drain_once_batches_per_clip` — enqueue 3 ops for the same clip, assert provider.apply_changes called once with 3 ops.
- `test_drain_once_skips_when_offline` — monitor returns offline, drain does nothing.
- `test_drain_once_marks_conflict` — provider returns WriteResult(status="conflict"), row goes to `conflict`.
- `test_drain_once_retries_on_retryable_error` — provider raises RetryableError, attempts=1, status stays pending, last_error set.
- `test_drain_once_writes_to_write_log_on_success`.

- [ ] **Step 2: Implement**

```python
class SyncEngine:
    def __init__(self, *, provider, pending_ops_repo, write_log_repo,
                 connection_monitor, db_provider, event_bus=None,
                 tick_interval_s=5, retry_base_s=2, retry_max_s=300,
                 clock=None) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def notify(self) -> None: ...           # sets an asyncio.Event

    async def drain_once(self) -> int:      # tick, sync wrapper for tests
        ...

    async def _tick_loop(self) -> None: ...
```

Implementation outline:
1. Bail if `connection_monitor.current_state() != online`.
2. `pending = list_pending(status='pending')` from repo.
3. Group by `(provider_id, provider_clip_id)`; for each group:
   - Build a single `ChangeSet` with the ops (deserialised via `change_op_from_json`), `expected_etag` = first row's `expected_etag`.
   - `mark_in_flight(group_ids)`.
   - `result = await provider.apply_changes(cs)` (catch ProviderError).
   - `ok`: `mark_applied`, write `write_log` row for the batch.
   - `conflict`: `mark_conflict(group_ids, conflict_detail)`.
   - retryable / RetryableError: `mark_retryable(group_ids, err)`; check attempts vs retry_max_s for backoff. The backoff just delays the next `tick` call by storing `attempted_at`; we re-process only after `now > attempted_at + backoff`. Implement that filter in `list_pending`.
   - fatal: `mark_failed`.
4. On any change, `event_bus.publish('sync', {...})` if event_bus.

Backoff: `delay = min(retry_max_s, retry_base_s * 2**(attempts-1))`. `list_pending` excludes pending rows whose `attempted_at + delay > now`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(svc): SyncEngine drains pending_operations per-clip with backoff"
```

---

### Task 10: Settings additions

**Files:**
- Modify: `backend/app/settings.py`
- Modify: `tests/unit/test_settings.py` (if it pins the field set)

- [ ] **Step 1: Add fields**

```python
health_probe_interval_s: int = 30
health_probe_timeout_s: int = 5
sync_retry_base_s: int = 2
sync_retry_max_s: int = 300
sync_tick_interval_s: int = 5
```

- [ ] **Step 2: Verify test_settings still passes.**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(settings): sync engine + connection monitor knobs"
```

---

### Task 11: AppContext wiring

**Files:**
- Modify: `backend/app/context.py`
- Modify: `backend/app/main.py`
- Modify: `tests/integration/test_context.py` (smoke)

- [ ] **Step 1: Wire up**

In `AppContext`:
- Add `pending_ops_repo: PendingOperationsRepo = field(default_factory=...)`.
- Add `write_queue: WriteQueue | None = None`.
- Add `sync_engine: SyncEngine | None = None`.
- Add `connection_monitor: ConnectionMonitor | None = None`.

In `AppContext.build`:
- After migrations, run `UPDATE pending_operations SET status='pending', attempted_at=NULL WHERE status='in_flight'` and commit (the crash-recovery step).
- After `ctx.archive` is built, construct:
  - `ctx.write_queue = WriteQueue(pending_ops_repo=..., review_items_repo=...)`
  - `ctx.connection_monitor = ConnectionMonitor(provider=ctx.archive, db_provider=lambda c=ctx: c.db, interval_s=settings.health_probe_interval_s, timeout_s=settings.health_probe_timeout_s, event_bus=ctx.event_bus)`
  - `ctx.sync_engine = SyncEngine(provider=ctx.archive, pending_ops_repo=..., write_log_repo=..., connection_monitor=ctx.connection_monitor, db_provider=lambda c=ctx: c.db, event_bus=ctx.event_bus, tick_interval_s=..., retry_base_s=..., retry_max_s=...)`
- Always construct `write_queue` (it doesn't need external services). For `sync_engine` and `connection_monitor`: also always construct them. Their `start()` is only called in lifespan if `init_external`.

In `aclose`:
- `if self.sync_engine: await self.sync_engine.stop()`
- `if self.connection_monitor: await self.connection_monitor.stop()`

In `main.lifespan`:
- After `ctx = await AppContext.build(...)`, if `init_external` start both background services.

- [ ] **Step 2: Verify existing context tests pass.**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(context): wire write_queue, sync_engine, connection_monitor"
```

---

### Task 12: Apply route refactor

**Files:**
- Modify: `backend/app/routes/review.py`
- Modify: `tests/integration/test_routes_review.py`

- [ ] **Step 1: Update the test**

```python
def test_apply_clip_enqueues_and_drains_to_catdv(monkeypatch, tmp_path):
    ...
    ctx.archive = FakeArchive()
    # rebuild sync_engine bound to FakeArchive (since AppContext built before)
    ctx.sync_engine = SyncEngine(provider=ctx.archive, ...)
    ...
    r = client.post("/api/review/clips/1/apply")
    assert r.status_code == 200
    assert r.json()["queued"] >= 1

    _run(ctx.sync_engine.drain_once())
    assert FakeArchive.last_change_set is not None
    ...
```

The FakeArchive needs `health()` that returns ok (so monitor stays online), and `ctx.connection_monitor.set_manual_offline(False)`; or simpler — bypass the monitor by injecting one that always returns `online`. Easiest: provide an `OnlineMonitor` test double, or set `ctx.connection_monitor` to a stub. The cleanest path is a tiny `AlwaysOnlineMonitor` test helper.

- [ ] **Step 2: Refactor route**

```python
@router.post("/clips/{clip_id}/apply")
async def apply_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return {"queued": 0, "applied": 0}
    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    template = await ctx.templates_repo.get(ctx.db, annotation.template_id)

    op_ids = await ctx.write_queue.enqueue_apply(
        ctx.db,
        clip_key=("catdv", str(clip_id)),
        items=accepted,
        target_map=template.target_map,
        expected_etag=_etag_from_snapshot(annotation.clip_snapshot),
        annotation_id=annotation.id,
        fps=_fps_from_snapshot(annotation.clip_snapshot),
    )
    if ctx.sync_engine is not None:
        ctx.sync_engine.notify()
    return {"queued": len(op_ids), "applied": len(op_ids)}
```

Old `applied` key kept as alias of `queued` so any older test that asserted on it (none today, but in clients) still works.

- [ ] **Step 3: Run, verify pass.**

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(routes/review): apply enqueues via WriteQueue + notifies SyncEngine"
```

---

### Task 13: SSE endpoint for connection state

**Files:**
- Create: `backend/app/routes/connection.py`
- Modify: `backend/app/main.py` (include router)
- Create: `tests/integration/test_routes_connection.py`

- [ ] **Step 1: Test**

```python
def test_get_connection_state_returns_current(monkeypatch, tmp_path):
    ...
    r = client.get("/api/connection/state")
    assert r.status_code == 200
    assert r.json()["state"] in {"online", "offline", "degraded", "syncing"}
```

(Skip an SSE-stream test for now; the endpoint exists and consumes EventBus; the loop itself is exercised by the monitor unit test.)

- [ ] **Step 2: Implement**

```python
@router.get("/api/connection/state")
async def state(request: Request):
    ctx = request.app.state.ctx
    if ctx.connection_monitor is None:
        return {"state": "online"}
    return {"state": ctx.connection_monitor.current_state().value}


@router.get("/api/connection/events")
async def events(request: Request):
    ctx = request.app.state.ctx
    bus = ctx.event_bus
    queue = bus.subscribe("connection")
    async def gen():
        try:
            while True:
                payload = await queue.get()
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            bus.unsubscribe("connection", queue)
    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(routes): /api/connection/state + /api/connection/events SSE"
```

---

### Task 14: write_log_repo provider columns

**Files:**
- Modify: `backend/app/repositories/write_log.py`

- [ ] **Step 1: Update `record` signature**

```python
async def record(self, conn, *, catdv_clip_id: int, annotation_id: int | None,
                 payload: dict, response: dict | str, status: Literal["ok","error"],
                 provider_id: str = "catdv",
                 provider_clip_id: str | None = None) -> None:
    if provider_clip_id is None:
        provider_clip_id = str(catdv_clip_id)
    ...
    INSERT INTO write_log
      (catdv_clip_id, annotation_id, payload, response, status, written_at,
       provider_id, provider_clip_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

The SyncEngine passes provider_id/provider_clip_id from the queued op group.

Update existing call sites (route's catch-all in review.py is gone after Task 12; no others).

- [ ] **Step 2: Verify `test_write_log_repo` still passes.**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(repo/write_log): provider_id + provider_clip_id columns populated"
```

---

### Task 15: Decisions log + full verification

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Run full suite**

```bash
.venv/bin/pytest -q --deselect tests/integration/test_proxy_resolver_fs.py::test_fs_resolver_raises_when_unreadable
```

Expect all green (192 + new tests).

- [ ] **Step 2: Ruff baseline check**

```bash
.venv/bin/ruff check backend tests
```

Expect 91 errors (the baseline). If higher: fix the new lints.

- [ ] **Step 3: Append decisions**

Append one or two of the non-trivial decisions (#4 enqueue atomicity, #7 conflict at adapter only) to `docs/decisions.md` in the same paragraph style as PR 3's entry.

- [ ] **Step 4: Commit**

```bash
git commit -m "docs(decisions): record PR 4 enqueue-atomicity and conflict-locus choices"
```

---

## Manual verification checklist

- [ ] `pytest -q --deselect …` all green
- [ ] `ruff check backend tests` ≤ 91 errors
- [ ] Apply flow end-to-end via `test_routes_review.py::test_apply_clip_...` passes
- [ ] Migration applies cleanly on a fresh db
- [ ] `connection_events` accumulates rows when the monitor transitions
- [ ] `pending_operations` rows reach `status='applied'` after a successful drain
