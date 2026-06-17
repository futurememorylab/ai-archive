# Clip Version History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every clip a durable, restorable history of *published* annotation states, with one unified publish-state headline across the UI — without disturbing the proven write-queue / SyncEngine path.

**Architecture:** A snapshot layer on top of the existing working draft (Approach A from the spec). `review_items` stays the working draft. A new immutable `clip_versions` table records one row per publish. Publish materializes the accepted draft into a full snapshot, inserts a `clip_versions` row in `publishing` state, and enqueues ops through the existing `WriteQueue`; the `SyncEngine` flips the row to `live` (superseding the prior live) when CatDV confirms. A single derivation function turns `(draft-exists?, newest version state)` into the headline enum.

**Tech Stack:** Python 3.12/3.13, FastAPI, aiosqlite (raw SQL migrations), Pydantic v2, Jinja2 + HTMX + Alpine, pytest. JS/CSS have no test runner (ADR 0001) — those tasks are guarded by template-string assertions, mirroring `tests/integration/test_routes_review.py`.

**Spec:** `docs/specs/2026-06-17-clip-version-history-design.md`

**Conventions for this repo (read once):**
- Migrations: raw `.sql` under `backend/migrations/`, applied in lexical order, keyed by filename (`backend/app/migrations_runner.py`). Latest is `0022`; this plan uses `0023`. If `0023` is taken by a parallel branch at merge time, rename to the next free number — the runner keys on filename, not the integer.
- Repos are leaves (no service imports); list-of-keys reads use `backend/app/repositories/_batch.py::chunked_in_clause`.
- Fixed enums: declare in `backend/app/enums/registry.py` (`editable=False`) + a `Literal` in `models/` + a guard test (`tests/unit/test_enum_registry.py` pattern).
- Test fixtures: `tests/integration/conftest.py` provides `db` (aiosqlite conn with migrations applied); routes use `tests/_helpers/live_ctx.py::install_live_ctx`.
- Run a single test: `python -m pytest tests/path::test_name -v` (use the project venv, Python 3.12/3.13 — 3.14 venvs are broken on this machine).
- Lint gate after code: `lint-imports` (import-linter contracts) and `ruff check`.

---

## Phase 1 — Backend spine (executable TDD)

Produces a fully tested backend: versions are created on publish, confirmed live by the engine, restorable, and the headline status is derivable — all exercisable over the API and in tests, independent of any UI.

### Task 1: Migration — `clip_versions` table + `pending_operations.origin_clip_version_id`

**Files:**
- Create: `backend/migrations/0023_clip_versions.sql`
- Test: `tests/integration/test_clip_versions_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_clip_versions_migration.py
import pytest


@pytest.mark.asyncio
async def test_clip_versions_table_exists(db):
    cur = await db.execute("PRAGMA table_info(clip_versions)")
    cols = {row[1] for row in await cur.fetchall()}
    assert {
        "id", "provider_id", "catdv_clip_id", "version_num", "parent_version_id",
        "snapshot", "diff", "origin", "model", "prompt_version_id", "annotation_id",
        "author", "publish_state", "expected_etag", "failed_reason", "synced_at",
        "created_at",
    } <= cols


@pytest.mark.asyncio
async def test_pending_operations_has_origin_clip_version_id(db):
    cur = await db.execute("PRAGMA table_info(pending_operations)")
    cols = {row[1] for row in await cur.fetchall()}
    assert "origin_clip_version_id" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_clip_versions_migration.py -v`
Expected: FAIL — `no such table: clip_versions`.

- [ ] **Step 3: Write the migration**

```sql
-- backend/migrations/0023_clip_versions.sql
-- 0023: clip_versions — one immutable row per PUBLISH of a clip's annotation
-- state (a commit). History is the list of these rows; review_items remains
-- the working draft. publish_state tracks the row's write to CatDV; exactly
-- one 'live' per clip is enforced in code (supersede-on-flip), not by a
-- partial index, to keep conflict/failed transitions simple. See spec
-- docs/specs/2026-06-17-clip-version-history-design.md.
CREATE TABLE clip_versions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id        TEXT    NOT NULL DEFAULT 'catdv',
  catdv_clip_id      INTEGER NOT NULL,
  version_num        INTEGER NOT NULL,
  parent_version_id  INTEGER REFERENCES clip_versions(id),
  snapshot           TEXT    NOT NULL,
  diff               TEXT,
  origin             TEXT    NOT NULL,
  model              TEXT,
  prompt_version_id  INTEGER,
  annotation_id      INTEGER REFERENCES annotations(id),
  author             TEXT,
  publish_state      TEXT    NOT NULL,
  expected_etag      TEXT,
  failed_reason      TEXT,
  synced_at          TEXT,
  created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_clip_versions_clip
  ON clip_versions(provider_id, catdv_clip_id, version_num DESC);

-- The write-queue hook: which clip_version a pending op publishes, so the
-- SyncEngine can flip that version live when the op lands. Mirrors the
-- existing origin_annotation_id / origin_review_item_ids columns.
ALTER TABLE pending_operations ADD COLUMN origin_clip_version_id INTEGER;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_clip_versions_migration.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0023_clip_versions.sql tests/integration/test_clip_versions_migration.py
git commit -m "feat(versions): add clip_versions table + pending_operations.origin_clip_version_id"
```

---

### Task 2: `ClipVersion` model + `ClipVersionsRepo`

**Files:**
- Modify: `backend/app/models/annotation.py` (add `ClipVersion`; it lives with the other annotation models)
- Create: `backend/app/repositories/clip_versions.py`
- Test: `tests/integration/test_clip_versions_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_clip_versions_repo.py
import json
import pytest

from backend.app.models.annotation import ClipVersion
from backend.app.repositories.clip_versions import ClipVersionsRepo


def _v(clip_id=1, num=1, state="publishing", origin="publish"):
    return ClipVersion(
        catdv_clip_id=clip_id, version_num=num, snapshot={"markers": [], "fields": {}, "notes": None},
        origin=origin, publish_state=state,
    )


@pytest.mark.asyncio
async def test_insert_and_get_roundtrip(db):
    repo = ClipVersionsRepo()
    vid = await repo.insert(db, _v())
    got = await repo.get(db, vid)
    assert got.id == vid
    assert got.catdv_clip_id == 1
    assert got.publish_state == "publishing"
    assert got.snapshot == {"markers": [], "fields": {}, "notes": None}


@pytest.mark.asyncio
async def test_next_version_num_is_per_clip_max_plus_one(db):
    repo = ClipVersionsRepo()
    assert await repo.next_version_num(db, 1) == 1
    await repo.insert(db, _v(clip_id=1, num=1))
    await repo.insert(db, _v(clip_id=1, num=2))
    await repo.insert(db, _v(clip_id=2, num=1))
    assert await repo.next_version_num(db, 1) == 3
    assert await repo.next_version_num(db, 2) == 2


@pytest.mark.asyncio
async def test_list_by_clip_newest_first(db):
    repo = ClipVersionsRepo()
    await repo.insert(db, _v(num=1))
    await repo.insert(db, _v(num=2))
    rows = await repo.list_by_clip(db, 1)
    assert [r.version_num for r in rows] == [2, 1]


@pytest.mark.asyncio
async def test_mark_live_supersedes_prior_live(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(num=1, state="live"))
    v2 = await repo.insert(db, _v(num=2, state="publishing"))
    await repo.mark_live(db, v2)
    assert (await repo.get(db, v2)).publish_state == "live"
    assert (await repo.get(db, v1)).publish_state == "superseded"
    assert (await repo.get(db, v2)).synced_at is not None


@pytest.mark.asyncio
async def test_mark_failed_leaves_prior_live(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(num=1, state="live"))
    v2 = await repo.insert(db, _v(num=2, state="publishing"))
    await repo.mark_failed(db, v2, reason="boom")
    assert (await repo.get(db, v2)).publish_state == "failed"
    assert (await repo.get(db, v1)).publish_state == "live"


@pytest.mark.asyncio
async def test_newest_state_by_clip_is_batched(db):
    repo = ClipVersionsRepo()
    await repo.insert(db, _v(clip_id=1, num=1, state="live"))
    await repo.insert(db, _v(clip_id=2, num=1, state="publishing"))
    out = await repo.newest_state_by_clip(db, [1, 2, 3])
    assert out[1] == ("live", 1)
    assert out[2] == ("publishing", 1)
    assert 3 not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_clip_versions_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app.repositories.clip_versions`.

- [ ] **Step 3a: Add the model**

In `backend/app/models/annotation.py`, after the `ReviewItem` class, add:

```python
ClipPublishState = Literal[
    "none", "draft", "publishing", "live", "failed", "conflict"
]


class ClipVersion(BaseModel):
    id: int | None = None
    provider_id: str = "catdv"
    catdv_clip_id: int
    version_num: int
    parent_version_id: int | None = None
    snapshot: dict[str, Any]
    diff: dict[str, Any] | None = None
    origin: Literal["publish", "restore"] = "publish"
    model: str | None = None
    prompt_version_id: int | None = None
    annotation_id: int | None = None
    author: str | None = None
    publish_state: Literal["publishing", "live", "superseded", "failed", "conflict"]
    expected_etag: str | None = None
    failed_reason: str | None = None
    synced_at: str | None = None
    created_at: str | None = None
```

(`Literal`, `Any`, `BaseModel` are already imported at the top of the file.)

- [ ] **Step 3b: Write the repo**

```python
# backend/app/repositories/clip_versions.py
"""ClipVersionsRepo — the publish (commit) history for a clip. One immutable
row per publish; only publish_state / synced_at / failed_reason transition
(performed by the SyncEngine). Leaf repository — no service imports."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.annotation import ClipVersion
from backend.app.repositories._batch import chunked_in_clause

_COLS = (
    "id", "provider_id", "catdv_clip_id", "version_num", "parent_version_id",
    "snapshot", "diff", "origin", "model", "prompt_version_id", "annotation_id",
    "author", "publish_state", "expected_etag", "failed_reason", "synced_at",
    "created_at",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ClipVersionsRepo:
    async def insert(self, conn: aiosqlite.Connection, v: ClipVersion) -> int:
        cur = await conn.execute(
            """
            INSERT INTO clip_versions
              (provider_id, catdv_clip_id, version_num, parent_version_id,
               snapshot, diff, origin, model, prompt_version_id, annotation_id,
               author, publish_state, expected_etag, failed_reason, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                v.provider_id, v.catdv_clip_id, v.version_num, v.parent_version_id,
                json.dumps(v.snapshot, ensure_ascii=False),
                json.dumps(v.diff, ensure_ascii=False) if v.diff is not None else None,
                v.origin, v.model, v.prompt_version_id, v.annotation_id,
                v.author, v.publish_state, v.expected_etag, v.failed_reason, v.synced_at,
            ),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, version_id: int) -> ClipVersion:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM clip_versions WHERE id = ?", (version_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"clip_version {version_id} not found")
        return self._row(row)

    async def next_version_num(
        self, conn: aiosqlite.Connection, clip_id: int, *, provider_id: str = "catdv"
    ) -> int:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(version_num), 0) + 1 FROM clip_versions "
            "WHERE provider_id = ? AND catdv_clip_id = ?",
            (provider_id, clip_id),
        )
        row = await cur.fetchone()
        return int(row[0])

    async def list_by_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, provider_id: str = "catdv"
    ) -> list[ClipVersion]:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM clip_versions "
            "WHERE provider_id = ? AND catdv_clip_id = ? ORDER BY version_num DESC",
            (provider_id, clip_id),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def live_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, provider_id: str = "catdv"
    ) -> ClipVersion | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM clip_versions "
            "WHERE provider_id = ? AND catdv_clip_id = ? AND publish_state = 'live' "
            "ORDER BY version_num DESC LIMIT 1",
            (provider_id, clip_id),
        )
        row = await cur.fetchone()
        return self._row(row) if row is not None else None

    async def mark_live(self, conn: aiosqlite.Connection, version_id: int) -> None:
        """Flip a version live and supersede the prior live for the same clip."""
        cur = await conn.execute(
            "SELECT provider_id, catdv_clip_id FROM clip_versions WHERE id = ?",
            (version_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return
        provider_id, clip_id = row[0], row[1]
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'superseded' "
            "WHERE provider_id = ? AND catdv_clip_id = ? AND publish_state = 'live' AND id != ?",
            (provider_id, clip_id, version_id),
        )
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'live', synced_at = ? WHERE id = ?",
            (_now_iso(), version_id),
        )
        await conn.commit()

    async def mark_failed(
        self, conn: aiosqlite.Connection, version_id: int, *, reason: str
    ) -> None:
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'failed', failed_reason = ? WHERE id = ?",
            (reason, version_id),
        )
        await conn.commit()

    async def mark_conflict(
        self, conn: aiosqlite.Connection, version_id: int, *, reason: str | None = None
    ) -> None:
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'conflict', failed_reason = ? WHERE id = ?",
            (reason, version_id),
        )
        await conn.commit()

    async def newest_state_by_clip(
        self, conn: aiosqlite.Connection, clip_ids: list[int], *, provider_id: str = "catdv"
    ) -> dict[int, tuple[str, int]]:
        """Batched: {clip_id: (publish_state, version_num)} for the NEWEST
        version per clip. Backs the clips-list status badge without N+1."""
        out: dict[int, tuple[str, int]] = {}
        if not clip_ids:
            return out
        for clause, params in chunked_in_clause(clip_ids):
            cur = await conn.execute(
                f"""
                SELECT cv.catdv_clip_id, cv.publish_state, cv.version_num
                  FROM clip_versions cv
                  JOIN (
                    SELECT catdv_clip_id, MAX(version_num) AS mx
                      FROM clip_versions
                     WHERE provider_id = ? AND catdv_clip_id IN ({clause})
                     GROUP BY catdv_clip_id
                  ) m ON m.catdv_clip_id = cv.catdv_clip_id AND m.mx = cv.version_num
                 WHERE cv.provider_id = ?
                """,
                (provider_id, *params, provider_id),
            )
            for clip_id, state, num in await cur.fetchall():
                out[int(clip_id)] = (state, int(num))
        return out

    @staticmethod
    def _row(row) -> ClipVersion:
        return ClipVersion(
            id=row[0], provider_id=row[1], catdv_clip_id=row[2], version_num=row[3],
            parent_version_id=row[4],
            snapshot=json.loads(row[5]),
            diff=json.loads(row[6]) if row[6] is not None else None,
            origin=row[7], model=row[8], prompt_version_id=row[9], annotation_id=row[10],
            author=row[11], publish_state=row[12], expected_etag=row[13],
            failed_reason=row[14], synced_at=row[15], created_at=row[16],
        )
```

> Note: confirm `chunked_in_clause` returns `(clause, params)` pairs by reading `backend/app/repositories/_batch.py` before relying on the exact unpacking above; adjust the loop to its real signature.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_clip_versions_repo.py -v`
Expected: PASS (all six).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/annotation.py backend/app/repositories/clip_versions.py tests/integration/test_clip_versions_repo.py
git commit -m "feat(versions): ClipVersion model + ClipVersionsRepo (insert/get/list/mark/batched state)"
```

---

### Task 3: Thread `origin_clip_version_id` + extra ops through the write queue

**Files:**
- Modify: `backend/app/repositories/pending_operations.py` (`_ROW_COLS`, `insert_many`)
- Modify: `backend/app/services/write_queue.py` (`enqueue_apply` / `enqueue_apply_for_clip`)
- Test: `tests/integration/test_write_queue_clip_version.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_write_queue_clip_version.py
import pytest

from backend.app.archive.model import SetField
from backend.app.models.annotation import ReviewItem
from backend.app.models.prompt import TargetMap
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.write_queue import WriteQueue


@pytest.mark.asyncio
async def test_enqueue_carries_clip_version_id_and_extra_ops(db):
    ri = ReviewItemsRepo()
    [item] = await ri.bulk_insert(db, [ReviewItem(
        annotation_id=None, studio_run_id=7, catdv_clip_id=1, kind="field",
        target_identifier="pragafilm.genre", proposed_value="thriller",
    )])
    await ri.set_decision(db, item.id, "accepted")
    item = await ri.get(db, item.id)

    wq = WriteQueue(pending_ops_repo=PendingOperationsRepo(), review_items_repo=ri)
    op_ids = await wq.enqueue_apply_for_clip(
        db, clip_id=1, accepted=[item], target_map=TargetMap(fields={}),
        expected_etag=None, annotation_id=None, fps=25.0,
        clip_version_id=42,
        extra_ops=[SetField(identifier="pragafilm.anno_version", value="#1 · you")],
    )
    assert len(op_ids) == 2  # the field op + the provenance op
    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert all(r["origin_clip_version_id"] == 42 for r in rows)
    assert any(r["op_kind"] == "SetField" and "anno_version" in r["op_json"] for r in rows)
```

> Confirm `TargetMap(fields={})` is constructible; if it requires other fields, read `backend/app/models/prompt.py` and adjust.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_write_queue_clip_version.py -v`
Expected: FAIL — `enqueue_apply_for_clip() got an unexpected keyword argument 'clip_version_id'`.

- [ ] **Step 3a: Pending-ops column wiring**

In `backend/app/repositories/pending_operations.py`, add `"origin_clip_version_id"` to `_ROW_COLS` immediately after `"expected_etag"`:

```python
_ROW_COLS = (
    "id",
    "provider_id",
    "provider_clip_id",
    "op_kind",
    "op_json",
    "origin_annotation_id",
    "origin_review_item_ids",
    "expected_etag",
    "origin_clip_version_id",
    "status",
    "attempts",
    "last_error",
    "enqueued_at",
    "attempted_at",
    "applied_at",
)
```

In `insert_many`, change the INSERT to include the new column:

```python
            cur = await conn.execute(
                """
                INSERT INTO pending_operations
                  (provider_id, provider_clip_id, op_kind, op_json,
                   origin_annotation_id, origin_review_item_ids, expected_etag,
                   origin_clip_version_id, status, attempts, enqueued_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (
                    r["provider_id"],
                    r["provider_clip_id"],
                    r["op_kind"],
                    r["op_json"],
                    r.get("origin_annotation_id"),
                    origin_ids_json,
                    r.get("expected_etag"),
                    r.get("origin_clip_version_id"),
                    now,
                ),
            )
```

> `_ROW_COLS` is used by the SELECT helpers via `', '.join(_ROW_COLS)`, and the table column was added in Task 1, so reads pick it up automatically. `_row_to_dict` uses `strict=False`, so ordering must match the SELECT column order — which it does because both derive from `_ROW_COLS`.

- [ ] **Step 3b: Write-queue parameters**

In `backend/app/services/write_queue.py`, extend both methods. `enqueue_apply_for_clip` gains `clip_version_id` and `extra_ops` and forwards them; `enqueue_apply` accepts them, stamps every row with `origin_clip_version_id`, and appends `extra_ops` (origin review-item ids = `[]`):

```python
    async def enqueue_apply_for_clip(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_id: int,
        accepted: list[ReviewItem],
        target_map: TargetMap,
        expected_etag: str | None,
        annotation_id: int | None,
        fps: float,
        clip_version_id: int | None = None,
        extra_ops: list[ChangeOp] | None = None,
    ) -> list[int]:
        """Thin wrapper over enqueue_apply keyed by a catdv clip id."""
        return await self.enqueue_apply(
            conn,
            clip_key=("catdv", str(clip_id)),
            items=accepted,
            target_map=target_map,
            expected_etag=expected_etag,
            annotation_id=annotation_id,
            fps=fps,
            clip_version_id=clip_version_id,
            extra_ops=extra_ops,
        )

    async def enqueue_apply(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_key: ClipKey,
        items: list[ReviewItem],
        target_map: TargetMap,
        expected_etag: str | None,
        annotation_id: int | None,
        fps: float,
        clip_version_id: int | None = None,
        extra_ops: list[ChangeOp] | None = None,
    ) -> list[int]:
        provider_id, provider_clip_id = clip_key
        fresh_items = [it for it in items if it.applied_at is None and it.id is not None]
        ops_with_origin = _items_to_change_ops(fresh_items, target_map, fps=fps)
        for op in extra_ops or []:
            ops_with_origin.append((op, []))
        if not ops_with_origin:
            return []

        rows = []
        for op, origin_ids in ops_with_origin:
            rows.append(
                {
                    "provider_id": provider_id,
                    "provider_clip_id": provider_clip_id,
                    "op_kind": type(op).__name__,
                    "op_json": change_op_to_json(op),
                    "origin_annotation_id": annotation_id,
                    "origin_review_item_ids": origin_ids,
                    "expected_etag": expected_etag,
                    "origin_clip_version_id": clip_version_id,
                }
            )

        op_ids = await self._pending.insert_many(conn, rows=rows, commit=False)
        item_ids = [it.id for it in fresh_items if it.id is not None]
        await self._review_items.mark_applied(conn, item_ids, commit=False)
        await conn.commit()
        return op_ids
```

> The early-return guard moves from "no fresh items" to "no ops" so a publish with *only* an `extra_ops` provenance write (e.g. a restore that changes nothing on the draft) still enqueues. `ChangeOp` is already imported in this file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_write_queue_clip_version.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing write-queue + sync tests to confirm no regression**

Run: `python -m pytest tests/integration -k "write_queue or sync or pending" -v`
Expected: PASS (existing rows simply carry `origin_clip_version_id = NULL`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/pending_operations.py backend/app/services/write_queue.py tests/integration/test_write_queue_clip_version.py
git commit -m "feat(versions): thread origin_clip_version_id + extra_ops through the write queue"
```

---

### Task 4: `clip_publish_state` fixed enum + `Literal` + guard test

**Files:**
- Modify: `backend/app/enums/registry.py` (add the spec)
- (`Literal` `ClipPublishState` already added in Task 3a — `models/annotation.py`)
- Test: `tests/unit/test_enum_registry.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_enum_registry.py`:

```python
def test_clip_publish_state_matches_literal():
    from typing import get_args
    from backend.app.enums.registry import ENUM_REGISTRY
    from backend.app.models.annotation import ClipPublishState

    spec = ENUM_REGISTRY["clip_publish_state"]
    assert spec.editable is False
    assert tuple(v.value for v in spec.values) == get_args(ClipPublishState)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_enum_registry.py::test_clip_publish_state_matches_literal -v`
Expected: FAIL — `KeyError: 'clip_publish_state'`.

- [ ] **Step 3: Add the enum spec**

In `backend/app/enums/registry.py`, add to `ENUM_REGISTRY` (after `toast_level`):

```python
    "clip_publish_state": EnumSpec(
        key="clip_publish_state",
        name="Clip publish state",
        description="Headline status of a clip's annotation work versus CatDV.",
        editable=False,
        values=(
            EnumValueSpec("none"),
            EnumValueSpec("draft"),
            EnumValueSpec("publishing"),
            EnumValueSpec("live"),
            EnumValueSpec("failed"),
            EnumValueSpec("conflict"),
        ),
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_enum_registry.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/enums/registry.py tests/unit/test_enum_registry.py
git commit -m "feat(versions): clip_publish_state fixed enum pinned to the Literal"
```

---

### Task 5: `clip_publish_status` derivation (single + batched)

**Files:**
- Create: `backend/app/services/publish_status.py`
- Test: `tests/unit/test_publish_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_publish_status.py
from backend.app.services.publish_status import resolve_publish_status


def test_failed_beats_everything():
    assert resolve_publish_status(has_draft=True, version_state="failed", version_num=2) == ("failed", 2)


def test_conflict_beats_publishing_and_draft():
    assert resolve_publish_status(has_draft=True, version_state="conflict", version_num=2) == ("conflict", 2)


def test_publishing_beats_draft():
    assert resolve_publish_status(has_draft=True, version_state="publishing", version_num=3) == ("publishing", 3)


def test_draft_when_no_active_version():
    assert resolve_publish_status(has_draft=True, version_state="live", version_num=1) == ("draft", 1)
    assert resolve_publish_status(has_draft=True, version_state=None, version_num=None) == ("draft", None)


def test_live_when_no_draft():
    assert resolve_publish_status(has_draft=False, version_state="live", version_num=4) == ("live", 4)
    assert resolve_publish_status(has_draft=False, version_state="superseded", version_num=4) == ("live", 4)


def test_none_when_nothing():
    assert resolve_publish_status(has_draft=False, version_state=None, version_num=None) == ("none", None)
```

> Precedence note encoded by the tests: `failed`/`conflict` (newest version is a *broken* publish) win even over an existing draft, because a broken publish needs attention most. `superseded` with no draft reads as `live` (the prior live is still what's on CatDV) — though in practice a `superseded` newest is rare; it means the live row exists with a higher num. Treat any non-broken, non-publishing state with no draft as `live` if a version exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_publish_status.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the derivation**

```python
# backend/app/services/publish_status.py
"""Single source of truth for a clip's headline publish status.

Inputs are both cheap and already computed elsewhere:
  * has_draft        — un-applied review_items exist (ReviewItemsRepo.list_pending_clips)
  * version_state    — newest clip_versions.publish_state for the clip (or None)
  * version_num      — that newest version's number (or None)

Precedence: failed/conflict > publishing > draft > live > none.
Returns (ClipPublishState, version_num_or_None) so callers can render
'Live v3' / 'Publishing…' / 'Draft' / 'Failed' from one place.
"""

from __future__ import annotations

from backend.app.models.annotation import ClipPublishState


def resolve_publish_status(
    *, has_draft: bool, version_state: str | None, version_num: int | None
) -> tuple[ClipPublishState, int | None]:
    if version_state in ("failed", "conflict"):
        return (version_state, version_num)  # type: ignore[return-value]
    if version_state == "publishing":
        return ("publishing", version_num)
    if has_draft:
        return ("draft", version_num)
    if version_state in ("live", "superseded"):
        return ("live", version_num)
    return ("none", None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_publish_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/publish_status.py tests/unit/test_publish_status.py
git commit -m "feat(versions): clip_publish_status derivation (precedence + batched-ready)"
```

---

### Task 6: `PublishService` — materialize snapshot, version row, enqueue (with provenance)

**Files:**
- Create: `backend/app/services/publish_service.py`
- Modify: `backend/app/context.py` (add `clip_versions_repo` field + build `publish_service`; add LiveCtx delegations)
- Test: `tests/integration/test_publish_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_publish_service.py
import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.prompt import TargetMap
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.publish_service import PublishService, build_provenance_value
from backend.app.services.write_queue import WriteQueue


async def _seed_accepted_field(db):
    ar, ri = AnnotationsRepo(), ReviewItemsRepo()
    aid = await ar.insert(db, Annotation(
        catdv_clip_id=1, catdv_clip_name="Clip_1", prompt_version_id=0, job_id=None,
        model="gemini-2.5-flash", prompt_used="p", raw_response={}, structured_output=None,
        clip_snapshot={"modifyDate": "2026-06-17T00:00:00Z", "fps": 25.0},
    ))
    [item] = await ri.bulk_insert(db, [ReviewItem(
        annotation_id=aid, studio_run_id=None, catdv_clip_id=1, kind="field",
        target_identifier="pragafilm.genre", proposed_value="thriller",
    )])
    await ri.set_decision(db, item.id, "accepted")
    return aid


@pytest.mark.asyncio
async def test_publish_creates_publishing_version_and_enqueues(db, monkeypatch):
    await _seed_accepted_field(db)
    svc = PublishService(
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        clip_versions_repo=ClipVersionsRepo(),
        write_queue=WriteQueue(pending_ops_repo=PendingOperationsRepo(), review_items_repo=ReviewItemsRepo()),
        prompts_repo=_StubPrompts(),
        live_snapshot_loader=_stub_loader,
    )
    version_id = await svc.publish(db, clip_id=1, author="anna@example.com")
    assert version_id is not None

    cv = await ClipVersionsRepo().get(db, version_id)
    assert cv.publish_state == "publishing"
    assert cv.version_num == 1
    assert cv.author == "anna@example.com"
    assert cv.snapshot["fields"]["pragafilm.genre"] == "thriller"

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    assert rows, "ops enqueued"
    assert all(r["origin_clip_version_id"] == version_id for r in rows)
    assert any("pragafilm.anno_version" in r["op_json"] for r in rows), "provenance op present"


@pytest.mark.asyncio
async def test_publish_noop_when_nothing_accepted(db):
    svc = _svc()
    assert await svc.publish(db, clip_id=999, author=None) is None


def test_provenance_value_shape():
    s = build_provenance_value(version_num=3, author="you", model="gemini-2.5-flash",
                               ts="2026-06-17T10:44:00Z")
    assert s.startswith("#3 · you · ")
    assert "gemini-2.5-flash" in s
```

> The test stubs the prompt lookup (`_StubPrompts` returning a `TargetMap(fields={})` version) and the "current CatDV/cached state" loader (`_stub_loader` returning an empty live snapshot). Define both at the top of the test file:
>
> ```python
> class _StubVersion:
>     target_map = TargetMap(fields={})
>     prompt_version_id = 0
> class _StubPrompts:
>     async def get_version(self, conn, vid): return _StubVersion()
> async def _stub_loader(conn, clip_id):
>     return {"markers": [], "fields": {}, "notes": None, "bigNotes": None, "fps": 25.0, "modifyDate": None}
> def _svc():
>     return PublishService(
>         annotations_repo=AnnotationsRepo(), review_items_repo=ReviewItemsRepo(),
>         clip_versions_repo=ClipVersionsRepo(),
>         write_queue=WriteQueue(pending_ops_repo=PendingOperationsRepo(), review_items_repo=ReviewItemsRepo()),
>         prompts_repo=_StubPrompts(), live_snapshot_loader=_stub_loader)
> ```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_publish_service.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3a: Write the service**

```python
# backend/app/services/publish_service.py
"""PublishService — turns a clip's accepted working draft into an immutable
clip_versions row and drives it through the existing write queue.

Flow (see docs/specs/2026-06-17-clip-version-history-design.md §Publish):
  1. resolve accepted, annotation-bound review_items
  2. materialize the FULL committed snapshot (current live/CatDV state + accepted)
  3. insert clip_versions row (publishing) + diff vs the live parent
  4. enqueue ops via WriteQueue, stamped with the version id, plus one
     `SetField pragafilm.anno_version` provenance op
  5. mark items applied (done inside the write queue)

The SyncEngine flips the row live (Task 7) when CatDV confirms.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.model import SetField
from backend.app.models.annotation import ClipVersion
from backend.app.services.write_queue import etag_from_snapshot, fps_from_snapshot

PROVENANCE_FIELD = "pragafilm.anno_version"
SnapshotLoader = Callable[[aiosqlite.Connection, int], Awaitable[dict[str, Any]]]


def build_provenance_value(*, version_num: int, author: str | None, model: str | None, ts: str) -> str:
    return f"#{version_num} · {author or '—'} · {ts} · {model or '—'}"


class PublishService:
    def __init__(
        self,
        *,
        annotations_repo,
        review_items_repo,
        clip_versions_repo,
        write_queue,
        prompts_repo,
        live_snapshot_loader: SnapshotLoader,
    ) -> None:
        self._annotations = annotations_repo
        self._review_items = review_items_repo
        self._versions = clip_versions_repo
        self._wq = write_queue
        self._prompts = prompts_repo
        self._load_live = live_snapshot_loader

    async def publish(
        self, conn: aiosqlite.Connection, *, clip_id: int, author: str | None
    ) -> int | None:
        accepted = await self._review_items.list_by_clip(conn, clip_id, decision="accepted")
        accepted = [it for it in accepted if it.annotation_id is not None and it.applied_at is None]
        if not accepted:
            return None

        annotation = await self._annotations.get(conn, accepted[0].annotation_id)
        version = await self._prompts.get_version(conn, annotation.prompt_version_id)
        fps = fps_from_snapshot(annotation.clip_snapshot)

        parent = await self._versions.live_for_clip(conn, clip_id)
        base = dict(parent.snapshot) if parent is not None else await self._load_live(conn, clip_id)
        snapshot = _materialize(base, accepted, fps=fps)

        num = await self._versions.next_version_num(conn, clip_id)
        ts = datetime.now(UTC).isoformat()
        version_id = await self._versions.insert(conn, ClipVersion(
            catdv_clip_id=clip_id, version_num=num,
            parent_version_id=parent.id if parent else None,
            snapshot=snapshot, diff=_diff(base, snapshot),
            origin="publish", model=annotation.model,
            prompt_version_id=annotation.prompt_version_id, annotation_id=annotation.id,
            author=author, publish_state="publishing",
            expected_etag=etag_from_snapshot(annotation.clip_snapshot),
        ))

        provenance = SetField(
            identifier=PROVENANCE_FIELD,
            value=build_provenance_value(version_num=num, author=author, model=annotation.model, ts=ts),
        )
        await self._wq.enqueue_apply_for_clip(
            conn, clip_id=clip_id, accepted=accepted, target_map=version.target_map,
            expected_etag=etag_from_snapshot(annotation.clip_snapshot),
            annotation_id=annotation.id, fps=fps,
            clip_version_id=version_id, extra_ops=[provenance],
        )
        return version_id


def _materialize(base: dict[str, Any], accepted: list, *, fps: float) -> dict[str, Any]:
    """Lay accepted items on top of the base snapshot: markers add, fields set,
    notes/bigNotes set. Mirrors the op semantics in WriteQueue._items_to_change_ops."""
    markers = list(base.get("markers") or [])
    fields = dict(base.get("fields") or {})
    notes = base.get("notes")
    big_notes = base.get("bigNotes")
    for it in accepted:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "marker" and isinstance(value, dict):
            markers.append(value)
        elif it.kind == "field" and it.target_identifier:
            fields[it.target_identifier] = value.get("value") if isinstance(value, dict) and "value" in value else value
        elif it.kind == "note" and it.target_identifier:
            text = str(value.get("value")) if isinstance(value, dict) and "value" in value else str(value)
            if it.target_identifier == "bigNotes":
                big_notes = text
            else:
                notes = text
    return {"markers": markers, "fields": fields, "notes": notes, "bigNotes": big_notes}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Coarse delta for the History UI: added markers count + changed fields +
    whether notes changed. Best-effort provenance, not a merge base."""
    b_fields, a_fields = before.get("fields") or {}, after.get("fields") or {}
    changed = {k: a_fields[k] for k in a_fields if a_fields.get(k) != b_fields.get(k)}
    return {
        "markers_added": max(0, len(after.get("markers") or []) - len(before.get("markers") or [])),
        "fields_changed": changed,
        "notes_changed": (before.get("notes") != after.get("notes")),
        "big_notes_changed": (before.get("bigNotes") != after.get("bigNotes")),
    }
```

- [ ] **Step 3b: Wire into the context**

In `backend/app/context.py`:

1. Add the repo field on `CoreCtx` near `pending_ops_repo` (~line 109):
```python
    clip_versions_repo: ClipVersionsRepo = field(default_factory=ClipVersionsRepo)
```
with the import at the top: `from backend.app.repositories.clip_versions import ClipVersionsRepo`.

2. Add `publish_service: PublishService = field(init=False)` near `write_queue` (~line 124), import `from backend.app.services.publish_service import PublishService`, and build it right after `ctx.write_queue = WriteQueue(...)` (~line 160):
```python
        ctx.publish_service = PublishService(
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            clip_versions_repo=ctx.clip_versions_repo,
            write_queue=ctx.write_queue,
            prompts_repo=ctx.prompts_repo,
            live_snapshot_loader=_load_live_snapshot,
        )
```

3. Define `_load_live_snapshot` as a module-level helper in `context.py` that reads the clip's current cached state (offline-safe, DB-first per the cache discipline). Minimal implementation that satisfies the contract:
```python
async def _load_live_snapshot(conn, clip_id: int) -> dict:
    # No prior version: start from an empty committed state. The accepted
    # items are layered on top; markers ADD, so we never need the live
    # markers here, and notes/fields SET. (A future enhancement can hydrate
    # from clip_cache; empty base is correct because publish only writes the
    # accepted deltas to CatDV — the snapshot is our record of those deltas.)
    return {"markers": [], "fields": {}, "notes": None, "bigNotes": None, "fps": 25.0, "modifyDate": None}
```

4. Add LiveCtx delegations (mirroring `write_queue`, ~line 352):
```python
    @property
    def clip_versions_repo(self) -> ClipVersionsRepo:
        return self.core.clip_versions_repo

    @property
    def publish_service(self) -> PublishService:
        return self.core.publish_service
```

> Verify against `tests/unit/test_context_delegation.py` (the CoreCtx-⊆-LiveCtx drift guard) — both new accessors must be present on LiveCtx or that test fails. Run it in Step 4.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_publish_service.py tests/unit/test_context_delegation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/publish_service.py backend/app/context.py tests/integration/test_publish_service.py
git commit -m "feat(versions): PublishService (snapshot + version row + provenance op) wired into context"
```

---

### Task 7: SyncEngine flips the version live / failed / conflict

**Files:**
- Modify: `backend/app/services/sync_engine.py` (`__init__` add `clip_versions_repo`; `_handle_result`)
- Modify: `backend/app/context.py` (pass `clip_versions_repo=core.clip_versions_repo` to the `SyncEngine(...)` ctor, ~line 752)
- Test: `tests/integration/test_sync_engine_clip_version.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_sync_engine_clip_version.py
import pytest

from backend.app.archive.model import WriteResult
from backend.app.models.annotation import ClipVersion
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.services.sync_engine import SyncEngine


class _Provider:
    id = "catdv"
    def __init__(self, status): self._status = status
    async def apply_changes(self, change_set):
        return WriteResult(status=self._status, upstream_response={}, conflict_detail=None)


async def _enqueue_for_version(db, version_id):
    return await PendingOperationsRepo().insert_many(db, rows=[{
        "provider_id": "catdv", "provider_clip_id": "1", "op_kind": "SetField",
        "op_json": '{"kind":"SetField","identifier":"pragafilm.genre","value":"x"}',
        "origin_annotation_id": None, "origin_review_item_ids": None,
        "expected_etag": None, "origin_clip_version_id": version_id,
    }])


def _engine(db, status):
    return SyncEngine(
        provider=_Provider(status), pending_ops_repo=PendingOperationsRepo(),
        write_log_repo=_NoopLog(), connection_monitor=None, db_provider=lambda: db,
        review_items_repo=None, clip_versions_repo=ClipVersionsRepo(),
    )


class _NoopLog:
    async def record(self, *a, **k): pass


def _v(num, state): return ClipVersion(
    catdv_clip_id=1, version_num=num, snapshot={"markers": [], "fields": {}, "notes": None},
    origin="publish", publish_state=state)


@pytest.mark.asyncio
async def test_ok_flips_version_live_and_supersedes_prior(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "live"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v2)
    await _engine(db, "ok").drain_once()
    assert (await repo.get(db, v2)).publish_state == "live"
    assert (await repo.get(db, v1)).publish_state == "superseded"


@pytest.mark.asyncio
async def test_conflict_marks_version_conflict_and_keeps_prior_live(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(1, "live"))
    v2 = await repo.insert(db, _v(2, "publishing"))
    await _enqueue_for_version(db, v2)
    await _engine(db, "conflict").drain_once()
    assert (await repo.get(db, v2)).publish_state == "conflict"
    assert (await repo.get(db, v1)).publish_state == "live"
```

> Confirm `WriteResult`'s constructor kwargs (`status`, `upstream_response`, `conflict_detail`) by reading `backend/app/archive/model.py`; adjust the stub to its real shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_sync_engine_clip_version.py -v`
Expected: FAIL — `SyncEngine.__init__() got an unexpected keyword argument 'clip_versions_repo'`.

- [ ] **Step 3a: Engine constructor**

In `backend/app/services/sync_engine.py`, add to `__init__` params (after `review_items_repo`):
```python
        clip_versions_repo: Any = None,
```
and store it: `self._clip_versions = clip_versions_repo`.

- [ ] **Step 3b: Flip in `_handle_result`**

In `_handle_result`, inside the `if result.status == "ok":` branch, after the existing `review_items.mark_synced` block, add the version flip. In the `conflict` and `fatal/failed` branches, mark the version accordingly. Extract the version id from the rows (freshest wins):

```python
        # newest version id among this clip's drained rows (freshest publish)
        version_ids = [r.get("origin_clip_version_id") for r in rows if r.get("origin_clip_version_id")]
        version_id = max(version_ids) if version_ids else None

        if result.status == "ok":
            await self._pending.mark_applied(db, op_ids)
            if self._review_items is not None:
                ...  # existing mark_synced block unchanged
            if self._clip_versions is not None and version_id is not None:
                await self._clip_versions.mark_live(db, version_id)
            ...  # existing write_log.record block unchanged
        elif result.status == "conflict":
            ...  # existing mark_conflict block unchanged
            if self._clip_versions is not None and version_id is not None:
                await self._clip_versions.mark_conflict(db, version_id, reason="etag conflict")
        elif result.status == "retryable":
            ...  # unchanged (no version transition — still publishing)
        else:  # fatal
            ...  # existing mark_failed block unchanged
            if self._clip_versions is not None and version_id is not None:
                await self._clip_versions.mark_failed(db, version_id, reason="fatal")
```

Also handle the **retry-ceiling → failed** path: in `_retry_or_fail`, when it flips to `mark_failed`, also mark the version failed. Add a `clip_version_id` lookup there from `rows` and call `self._clip_versions.mark_failed(...)` in the ceiling branch.

- [ ] **Step 3c: Pass the repo in context**

In `backend/app/context.py` at the `SyncEngine(...)` construction (~line 752), add:
```python
        clip_versions_repo=core.clip_versions_repo,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_sync_engine_clip_version.py "tests/integration" -k "sync" -v`
Expected: PASS (new + existing sync tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync_engine.py backend/app/context.py tests/integration/test_sync_engine_clip_version.py
git commit -m "feat(versions): SyncEngine flips clip_version live/superseded/conflict/failed on drain"
```

---

### Task 8: Publish + History + Restore API routes

**Files:**
- Modify: `backend/app/routes/review.py` (route `apply_clip` → `publish_service`; add author; add versions list + restore routes)
- Create: `backend/app/services/restore_service.py`
- Test: `tests/integration/test_routes_versions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_versions.py
# Mirror the existing tests/integration/test_routes_review.py setup
# (install_live_ctx, _setenv, _seed). After accepting an item and POSTing
# /api/review/clips/1/apply, assert GET /api/review/clips/1/versions returns
# one version in 'publishing' (or 'live' once drained), and POST
# /api/review/clips/1/versions/{n}/restore creates a fresh draft.
#
# Pseudocode body (fill from the test_routes_review.py harness):
#   client.post("/api/review/items/{id}/decision", json={"decision": "accepted"})
#   client.post("/api/review/clips/1/apply")
#   r = client.get("/api/review/clips/1/versions"); assert len(r.json()) == 1
#   v = r.json()[0]; assert v["publish_state"] in ("publishing", "live")
```

Write this concretely against the `test_routes_review.py` harness (copy its `_setenv`, `install_live_ctx`, `_seed`, and `TestClient` construction verbatim, then add the three assertions above plus a restore assertion). The restore assertion:
```python
   rr = client.post("/api/review/clips/1/versions/1/restore")
   assert rr.status_code == 200
   items = client.get("/api/review/clips/1/items").json()
   assert any(it["decision"] == "pending" for it in items)  # restored as a fresh draft
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_routes_versions.py -v`
Expected: FAIL — 404 on `/versions`.

- [ ] **Step 3a: Restore service**

```python
# backend/app/services/restore_service.py
"""RestoreService — load a published clip_version's snapshot back into the
working draft as fresh, pending review_items. Publishing it forward creates a
NEW version (origin='restore'); history is never mutated. See spec §Restore."""

from __future__ import annotations

import aiosqlite

from backend.app.models.annotation import ReviewItem


class RestoreService:
    def __init__(self, *, clip_versions_repo, review_items_repo, annotations_repo):
        self._versions = clip_versions_repo
        self._review_items = review_items_repo
        self._annotations = annotations_repo

    async def restore_into_draft(
        self, conn: aiosqlite.Connection, *, clip_id: int, version_num: int
    ) -> int:
        versions = await self._versions.list_by_clip(conn, clip_id)
        target = next((v for v in versions if v.version_num == version_num), None)
        if target is None:
            raise LookupError(f"clip {clip_id} has no version {version_num}")

        # Pick an annotation_id for the recreated items (CHECK requires exactly
        # one of annotation_id/studio_run_id). Prefer the version's stored
        # annotation; fall back to the clip's latest annotation.
        annotation_id = target.annotation_id
        if annotation_id is None:
            anns = await self._annotations.list_by_clip(conn, clip_id)
            annotation_id = anns[0].id if anns else None
        if annotation_id is None:
            raise LookupError(f"clip {clip_id} has no annotation to anchor a restore")

        await self._review_items.clear_unapplied_for_clip(conn, clip_id)
        items = _snapshot_to_items(target.snapshot, clip_id, annotation_id)
        inserted = await self._review_items.bulk_insert(conn, items)
        return len(inserted)


def _snapshot_to_items(snapshot, clip_id, annotation_id) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for m in snapshot.get("markers") or []:
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="marker",
                                target_identifier=None, proposed_value=m))
    for ident, val in (snapshot.get("fields") or {}).items():
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="field",
                                target_identifier=ident, proposed_value=val))
    if snapshot.get("notes"):
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="note",
                                target_identifier="notes", proposed_value=snapshot["notes"]))
    if snapshot.get("bigNotes"):
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="note",
                                target_identifier="bigNotes", proposed_value=snapshot["bigNotes"]))
    return items
```

Add `clear_unapplied_for_clip` to `ReviewItemsRepo` (Task 9 adds the same method — implement it once there and reference it; if Task 9 runs after, add it here and remove the duplicate in Task 9):
```python
    async def clear_unapplied_for_clip(self, conn, clip_id: int) -> int:
        cur = await conn.execute(
            "DELETE FROM review_items WHERE catdv_clip_id = ? AND applied_at IS NULL",
            (clip_id,),
        )
        await conn.commit()
        return cur.rowcount or 0
```

Wire `RestoreService` into the context like `PublishService` (CoreCtx field built after `publish_service`, LiveCtx delegation).

- [ ] **Step 3b: Routes**

In `backend/app/routes/review.py`:
- Replace the body of `apply_clip`'s enqueue with `await ctx.publish_service.publish(ctx.db, clip_id=clip_id, author=_author(request))`, where `_author` reads `getattr(getattr(request.state, "current_user", None), "email", None)`. Keep the HX/JSON response branching. Keep `_notify_sync`.
- Add:
```python
@router.get("/clips/{clip_id}/versions")
async def list_versions(request: Request, clip_id: int):
    ctx = get_core_ctx(request)
    versions = await ctx.clip_versions_repo.list_by_clip(ctx.db, clip_id)
    return [v.model_dump() for v in versions]


@router.post("/clips/{clip_id}/versions/{version_num}/restore")
async def restore_version(request: Request, clip_id: int, version_num: int):
    ctx = get_core_ctx(request)
    try:
        n = await ctx.restore_service.restore_into_draft(ctx.db, clip_id=clip_id, version_num=version_num)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"restored_items": n}


@router.post("/clips/{clip_id}/versions/{version_num}/restore-and-publish")
async def restore_and_publish(request: Request, clip_id: int, version_num: int):
    ctx = get_core_ctx(request)
    try:
        await ctx.restore_service.restore_into_draft(ctx.db, clip_id=clip_id, version_num=version_num)
        for it in await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="pending"):
            await ctx.review_items_repo.set_decision(ctx.db, it.id, "accepted")
        version_id = await ctx.publish_service.publish(
            ctx.db, clip_id=clip_id, author=_author(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    _notify_sync(request)
    return {"published_version_id": version_id}
```

> Note: `publish()` reads `origin` as `"publish"`. For a restore-forward, set `origin="restore"` — simplest is a `publish(..., origin="publish")` kwarg threaded into the `ClipVersion(origin=...)` insert. Add that kwarg to `PublishService.publish` (default `"publish"`) and pass `origin="restore"` from the restore-and-publish route, and from `apply_clip` when the draft was produced by a restore. For v1, the manual Restore→review→Publish path uses the normal Publish button, so it records `origin="publish"`; the dedicated one-click records `origin="restore"`. Document this in the route docstring.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_routes_versions.py tests/integration/test_routes_review.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/restore_service.py backend/app/routes/review.py backend/app/context.py backend/app/repositories/review_items.py tests/integration/test_routes_versions.py
git commit -m "feat(versions): publish via PublishService + versions list + restore routes"
```

---

### Task 9: Re-run clears the prior working draft (fix orphaning)

**Files:**
- Modify: `backend/app/services/annotator.py` (clip-annotate finalize: clear prior un-applied items before inserting the new run's items)
- Modify: `backend/app/repositories/review_items.py` (`clear_unapplied_for_clip` — added in Task 8; if Task 9 lands first, add it here)
- Test: `tests/integration/test_rerun_replaces_draft.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_rerun_replaces_draft.py
import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo


@pytest.mark.asyncio
async def test_clear_unapplied_for_clip_only_drops_unapplied(db):
    ri = ReviewItemsRepo()
    a, b = await ri.bulk_insert(db, [
        ReviewItem(annotation_id=None, studio_run_id=1, catdv_clip_id=1, kind="note",
                   target_identifier="notes", proposed_value="old"),
        ReviewItem(annotation_id=None, studio_run_id=1, catdv_clip_id=1, kind="note",
                   target_identifier="notes", proposed_value="kept"),
    ])
    await ri.mark_applied(db, [b.id])  # b is already published
    dropped = await ri.clear_unapplied_for_clip(db, 1)
    assert dropped == 1
    remaining = [it.id for it in await ri.list_by_clip(db, 1)]
    assert remaining == [b.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_rerun_replaces_draft.py -v`
Expected: FAIL — `clear_unapplied_for_clip` missing (unless Task 8 already added it; then this only verifies behavior).

- [ ] **Step 3: Implement + call from annotator finalize**

Add `clear_unapplied_for_clip` to `ReviewItemsRepo` (see Task 8 Step 3a for the code). In `backend/app/services/annotator.py`, find where a clip-annotate run inserts its `review_items` (near line ~652, after `annotations_repo.insert`) and call `await review_items_repo.clear_unapplied_for_clip(conn, clip_id)` **before** `bulk_insert` of the new run's items, so a re-run replaces (not appends to) the working draft. Leave studio-run finalize as-is (it already `delete_for_studio_run`).

> Read the finalize block first to place the call on the clip-annotate path only — do not clear when the run is a studio run (those are keyed by `studio_run_id` and already deduped).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_rerun_replaces_draft.py "tests/integration" -k "annotat" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/review_items.py backend/app/services/annotator.py tests/integration/test_rerun_replaces_draft.py
git commit -m "fix(versions): re-run replaces the working draft (clear prior un-applied items)"
```

---

### Task 10: Idempotent backfill — synthetic `live` v1 for already-published clips

**Files:**
- Create: `backend/app/services/clip_versions_backfill.py`
- Modify: `backend/app/context.py` (call once at boot, after `enum_service.reconcile_seeds()`)
- Test: `tests/integration/test_clip_versions_backfill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_clip_versions_backfill.py
import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.clip_versions_backfill import backfill_clip_versions


@pytest.mark.asyncio
async def test_backfill_creates_one_live_v1_for_synced_clip(db):
    ar, ri = AnnotationsRepo(), ReviewItemsRepo()
    aid = await ar.insert(db, Annotation(
        catdv_clip_id=5, catdv_clip_name="C5", prompt_version_id=0, job_id=None,
        model="m", prompt_used="p", raw_response={}, structured_output=None, clip_snapshot={}))
    [it] = await ri.bulk_insert(db, [ReviewItem(
        annotation_id=aid, studio_run_id=None, catdv_clip_id=5, kind="field",
        target_identifier="pragafilm.genre", proposed_value="drama")])
    await ri.mark_applied(db, [it.id])
    await ri.mark_synced(db, [it.id])

    created = await backfill_clip_versions(db, ClipVersionsRepo())
    assert created == 1
    versions = await ClipVersionsRepo().list_by_clip(db, 5)
    assert len(versions) == 1
    assert versions[0].publish_state == "live"
    assert versions[0].author == "—"

    # idempotent
    assert await backfill_clip_versions(db, ClipVersionsRepo()) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_clip_versions_backfill.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the backfill**

```python
# backend/app/services/clip_versions_backfill.py
"""One-time, idempotent backfill: synthesize a 'live' v1 for every clip that
already has synced review_items but no clip_versions row, so History isn't
empty for clips published before this feature shipped. Best-effort: snapshot
from the last synced annotation's items; author='—', no etag. Runs at boot."""

from __future__ import annotations

import aiosqlite

from backend.app.models.annotation import ClipVersion


async def backfill_clip_versions(conn: aiosqlite.Connection, versions_repo) -> int:
    cur = await conn.execute(
        """
        SELECT DISTINCT ri.catdv_clip_id
          FROM review_items ri
         WHERE ri.synced_at IS NOT NULL
           AND ri.annotation_id IS NOT NULL
           AND ri.catdv_clip_id NOT IN (SELECT catdv_clip_id FROM clip_versions)
        """
    )
    clip_ids = [int(r[0]) for r in await cur.fetchall()]
    created = 0
    for clip_id in clip_ids:
        cur2 = await conn.execute(
            """
            SELECT kind, target_identifier, proposed_value, edited_value, annotation_id, model_model
              FROM (
                SELECT ri.kind, ri.target_identifier, ri.proposed_value, ri.edited_value,
                       ri.annotation_id, a.model AS model_model
                  FROM review_items ri JOIN annotations a ON a.id = ri.annotation_id
                 WHERE ri.catdv_clip_id = ? AND ri.synced_at IS NOT NULL
              )
            """,
            (clip_id,),
        )
        rows = await cur2.fetchall()
        snapshot, model, annotation_id = _snapshot_from_rows(rows)
        await versions_repo.insert(conn, ClipVersion(
            catdv_clip_id=clip_id, version_num=1, snapshot=snapshot, diff=None,
            origin="publish", model=model, annotation_id=annotation_id,
            author="—", publish_state="live"))
        created += 1
    return created


def _snapshot_from_rows(rows):
    import json
    markers, fields, notes, big = [], {}, None, None
    model, annotation_id = None, None
    for kind, ident, proposed, edited, ann_id, m in rows:
        model, annotation_id = m, ann_id
        value = json.loads(edited) if edited is not None else json.loads(proposed)
        if kind == "marker" and isinstance(value, dict):
            markers.append(value)
        elif kind == "field" and ident:
            fields[ident] = value
        elif kind == "note":
            text = str(value)
            if ident == "bigNotes":
                big = text
            else:
                notes = text
    return {"markers": markers, "fields": fields, "notes": notes, "bigNotes": big}, model, annotation_id
```

In `backend/app/context.py`, after `await ctx.enum_service.reconcile_seeds()`:
```python
        from backend.app.services.clip_versions_backfill import backfill_clip_versions
        await backfill_clip_versions(ctx.db, ctx.clip_versions_repo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_clip_versions_backfill.py -v`
Expected: PASS (both the create and the idempotent re-run).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/clip_versions_backfill.py backend/app/context.py tests/integration/test_clip_versions_backfill.py
git commit -m "feat(versions): idempotent boot backfill of synthetic live v1 for published clips"
```

---

### Task 11: Phase-1 gate — lint, import contracts, full suite

- [ ] **Step 1: Run the import-linter + ruff**

Run: `lint-imports && ruff check backend/app`
Expected: PASS. (Repos must not import services; routes must not import `httpx`. `PublishService`/`RestoreService` live in `services/`, repos stay leaves.)

- [ ] **Step 2: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS. Investigate any failures before Phase 2.

- [ ] **Step 3: Commit (only if any fixups were needed)**

```bash
git add -A && git commit -m "chore(versions): phase-1 gate fixups (lint + suite green)"
```

---

## Phase 2 — UI surfaces (template-string guarded; ADR 0001 — no JS test runner)

Sits on Phase 1. Each task reuses an existing component (per CLAUDE.md "explore before implementing") and is guarded by a template-string assertion the way `tests/integration/test_routes_review.py` already guards `acceptApplyAll`/`navClip`.

> **Before starting Phase 2:** read `backend/app/templates/pages/clip_detail.html`, `backend/app/templates/pages/_clips_row_cells.html`, `backend/app/templates/_sync_chip_inner.html`, `backend/app/templates/components/_ui.html` (the `menu`, `menu_item`, `modal`, `status_pill`, `button` macros), `backend/app/static/review.js`, and `backend/app/static/studio.js` (the compare/diff block). Confirm exact include paths and macro signatures before editing.

### Task 12: Clip-detail headline pill + History dropdown + Restore

**Files:**
- Modify: `backend/app/routes/pages/clips.py` (`clip_detail_page`: attach `versions` + `publish_status`)
- Create: `backend/app/templates/pages/_clip_history_menu.html`
- Modify: `backend/app/templates/pages/clip_detail.html` (include the menu + headline pill)
- Modify: `backend/app/static/review.js` (Restore / Restore-and-publish fetch + toast + draft-data rehydrate)
- Test: `tests/integration/test_routes_pages.py` (extend) + `tests/integration/test_clip_history_render.py`

- [ ] **Step 1: Write the failing render test**

```python
# tests/integration/test_clip_history_render.py
# Using the install_live_ctx + TestClient harness from test_routes_pages.py:
#   seed a clip with a live clip_version (insert via ClipVersionsRepo),
#   GET /clips/{id}, assert the History control + headline pill render.
def test_clip_detail_shows_history_and_headline(client_and_ctx):
    client, ctx = client_and_ctx
    # ... seed a live version for clip 1 ...
    html = client.get("/clips/1").text
    assert 'data-history-menu' in html       # the dropdown root
    assert 'Live v' in html or 'data-publish-status' in html
```

Build this against the real `test_routes_pages.py` harness (copy its client/ctx setup). The exact seed uses `ctx.clip_versions_repo.insert(...)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_clip_history_render.py -v`
Expected: FAIL — markers absent.

- [ ] **Step 3a: Route context**

In `clip_detail_page` (`backend/app/routes/pages/clips.py`), after `ctx_dict["draft"] = ...`, add:
```python
    versions = await ctx.clip_versions_repo.list_by_clip(ctx.db, clip_id)
    newest = versions[0] if versions else None
    has_draft = ctx_dict["draft"].get("has_draft", False)
    from backend.app.services.publish_status import resolve_publish_status
    state, num = resolve_publish_status(
        has_draft=has_draft,
        version_state=newest.publish_state if newest else None,
        version_num=newest.version_num if newest else None,
    )
    ctx_dict["versions"] = [v.model_dump() for v in versions]
    ctx_dict["publish_status"] = {"state": state, "version_num": num}
```

- [ ] **Step 3b: History menu partial**

```jinja
{# backend/app/templates/pages/_clip_history_menu.html
   Version history dropdown. Reuses ui.menu / ui.menu_item + popover(). #}
{% from "components/_ui.html" import menu, menu_item, status_pill, button %}
<div data-history-menu>
  {% call menu(label="History", icon="clock") %}
    {% for v in versions %}
      {% set st = v.publish_state %}
      {% set pill_state = 'ok' if st == 'live' else ('accent' if st == 'publishing' else ('bad' if st in ('failed','conflict') else '')) %}
      <div class="menu-row">
        <span>#{{ v.version_num }} · {{ v.author or '—' }} · {{ v.model or '—' }}</span>
        {{ status_pill(st, pill_state) }}
        {% if st != 'live' %}
          {{ button("Restore", attrs='data-restore="%d"' % v.version_num, variant="link") }}
        {% endif %}
      </div>
    {% else %}
      {{ menu_item("No history yet") }}
    {% endfor %}
  {% endcall %}
</div>
```

> Adjust `menu` / `menu_item` / `status_pill` / `button` invocation to their real macro signatures (read `_ui.html`). The headline pill: in `clip_detail.html`, render `status_pill` from `publish_status.state` with a label like `Live v{{ publish_status.version_num }}` when live.

- [ ] **Step 3c: Include + headline in `clip_detail.html`**

Add near the draft/published panel header:
```jinja
{% set ps = publish_status %}
{% set ps_label = ('Live v' ~ ps.version_num) if ps.state == 'live'
   else {'draft':'Draft · unpublished','publishing':'Publishing…','failed':'Failed','conflict':'Conflict','none':''}[ps.state] %}
<span data-publish-status="{{ ps.state }}">{{ status_pill(ps_label, _pill_state(ps.state)) if ps_label }}</span>
{% include "pages/_clip_history_menu.html" %}
```

> Define `_pill_state` inline as a Jinja `{% set %}` map or compute the state in the route and pass it. Keep it consistent with the partial.

- [ ] **Step 3d: review.js Restore handlers**

In `backend/app/static/review.js`, add a delegated click handler for `[data-restore]` that POSTs `/api/review/clips/{clipId}/versions/{n}/restore`, then re-fetches `/api/review/clips/{clipId}/draft-data` to rehydrate the draft panel, and pushes a success toast via `Alpine.store('toast').push(...)`. No `location.reload()`. Mirror the existing apply handler's fetch + toast pattern in the same file.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_clip_history_render.py tests/integration/test_routes_pages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages/clips.py backend/app/templates/pages/_clip_history_menu.html backend/app/templates/pages/clip_detail.html backend/app/static/review.js tests/integration/test_clip_history_render.py
git commit -m "feat(versions): clip-detail History dropdown + publish-status headline + Restore"
```

---

### Task 13: Clips-list unified status badge (batched)

**Files:**
- Modify: `backend/app/routes/pages/clips.py` (`clips_list`: batched status per row)
- Modify: `backend/app/templates/pages/_clips_row_cells.html` (render the badge)
- Test: `tests/integration/test_clips_page_perf.py` (extend the N+1 guard) + a render assertion

- [ ] **Step 1: Write the failing N+1 + render test**

Extend `tests/integration/test_clips_page_perf.py` (or add `tests/integration/test_clips_status_badge.py`) to:
- seed 3 clips, two with live versions and one with a draft;
- assert the rendered tbody shows `Live v`, `Draft`, etc.;
- wrap the per-row status derivation in `assert_query_count` and assert the statement count is **constant** for 3 vs 30 clips (one batched `newest_state_by_clip` + one `list_pending_clips`, not per-row).

```python
# sketch
from tests._helpers.query_count import assert_query_count
async with assert_query_count(ctx.db, max_n=SOME_CONST):
    # call the helper that builds the row status map for N clips
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_clips_status_badge.py -v`
Expected: FAIL — badge absent / N+1.

- [ ] **Step 3: Batched derivation in the route**

In `clips_list` (`backend/app/routes/pages/clips.py`), after the `pending_rows` block that builds `pmap`, add a single batched version-state read and merge it into each row:
```python
    clip_ids = [row["id"] for row in ctx_dict["clips"]]
    version_states = await ctx.clip_versions_repo.newest_state_by_clip(ctx.db, clip_ids)
    from backend.app.services.publish_status import resolve_publish_status
    for row in ctx_dict["clips"]:
        st, num = version_states.get(row["id"], (None, None))
        has_draft = row["id"] in pmap
        state, vnum = resolve_publish_status(has_draft=has_draft, version_state=st, version_num=num)
        row["publish_state"] = state
        row["publish_version_num"] = vnum
```

In `_clips_row_cells.html`, render a `status_pill` from `row.publish_state` (label `Live v{{ row.publish_version_num }}` when live, else the mapped label). Keep the existing `draft_label` counts cell or fold it in — your call, but don't remove the draft signal entirely.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_clips_status_badge.py tests/integration/test_clips_page_perf.py -v`
Expected: PASS (badge renders; statement count constant across clip counts).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages/clips.py backend/app/templates/pages/_clips_row_cells.html tests/integration/test_clips_status_badge.py
git commit -m "feat(versions): clips-list unified publish-status badge (batched, N+1-guarded)"
```

---

### Task 14: Topbar sync chip reads the unified status

**Files:**
- Modify: `backend/app/templates/_sync_chip_inner.html` (label from publish-state vocabulary)
- Modify: the route/view that populates the chip counts (find it: `grep -rn "count_actionable\|_sync_chip" backend/app/routes`)
- Test: `tests/integration/test_connection_chip_render.py` or the sync-chip render test (extend)

- [ ] **Step 1: Write the failing test**

Extend the existing sync-chip render test to assert the chip surfaces `publishing` and `failed` counts using the unified vocabulary (e.g. text `Publishing` / `Failed` rather than raw enum). Seed a `publishing` and a `failed` clip_version, render the chip inner partial, assert the labels.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration -k "sync_chip or connection_chip" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

The chip already aggregates `count_actionable` (queued/problems) from `pending_operations`. Keep that, but relabel using the publish-state vocabulary so it matches the rest of the UI (`Publishing…` for in-motion, `Failed`/`Conflict` for problems). If a count of clips-in-each-publish-state is wanted instead, add a small `count_by_publish_state` to `ClipVersionsRepo` (one grouped query) and use it. Prefer reusing `count_actionable` to avoid a second source of truth unless the labels can't be made consistent.

- [ ] **Step 4: Run tests** — `python -m pytest tests/integration -k "sync_chip or connection_chip" -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/_sync_chip_inner.html tests/integration/<the_chip_test>.py
git commit -m "feat(versions): topbar sync chip uses the unified publish-state vocabulary"
```

---

### Task 15: Re-run confirm dialog (only when an unpublished draft exists)

**Files:**
- Modify: the annotate/re-run trigger template (find it: `grep -rln "Annotate\|annotate\b" backend/app/templates/pages/clip_detail.html backend/app/static/*.js`)
- Modify: the relevant JS to gate the re-run on a confirm when `publish_status.state == 'draft'`
- Use `{{ ui.modal(...) }}` + `.modal-body` / `.modal-actions` (ADR 0063) — never a hand-rolled modal
- Test: template-string guard asserting the modal markup + the gating exists

- [ ] **Step 1: Write the failing guard test**

Add to a routes/pages test: GET `/clips/{id}` for a clip in `draft` state and assert the re-run confirm modal markup is present (e.g. a `data-rerun-confirm` attribute and the copy "last published" / "replaces your current unpublished draft"). For a clip with no draft, assert the confirm is not required (the annotate button has no `data-rerun-confirm` gate or the gate is inert).

- [ ] **Step 2: Run test to verify it fails** → FAIL (markup absent).

- [ ] **Step 3: Implement** the `ui.modal` confirm with the copy from the spec mockup, shown only when `publish_status.state == 'draft'`. Wire the annotate trigger to open the modal first in that case; otherwise proceed directly. Toast on completion; no reload.

- [ ] **Step 4: Run the test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/clip_detail.html backend/app/static/<file>.js tests/integration/<test>.py
git commit -m "feat(versions): re-run confirm dialog when an unpublished draft exists"
```

---

### Task 16: Per-version diff view (reuse Studio compare)

**Files:**
- Modify: `backend/app/templates/pages/_clip_history_menu.html` (a "what changed" affordance per version)
- Reuse: the Studio compare diff styling/markup (read `backend/app/static/studio.js` + its compare partial; extract a shared partial if the diff renderer isn't already one)
- Test: template-string guard asserting the diff view renders the stored `diff` (markers_added / fields_changed / notes_changed)

- [ ] **Step 1: Write the failing guard test** — seed a version with a `diff`, render the history menu, assert the diff summary appears (e.g. "+2 markers", a changed field name).

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** by rendering `v.diff` in the history row's expanded view, reusing the Studio compare diff CSS classes (do not invent new ones — CLAUDE.md design-language guard). If the Studio diff is inline-only, extract the diff list into a shared partial and include it in both places.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_clip_history_menu.html tests/integration/<test>.py
git commit -m "feat(versions): per-version diff summary in History (reuses Studio compare styling)"
```

---

### Task 17: Phase-2 gate + ADR

- [ ] **Step 1: Full suite + lint** — `python -m pytest -q && lint-imports && ruff check backend/app` → PASS. Confirm `tests/unit/test_design_language_guard.py` is green (no hand-rolled `*-menu`/`modal-*`/raw-hex).
- [ ] **Step 2: Manual acceptance** — run the dev server (use the `server-start` skill) and walk the 8 Manual acceptance flows in the spec (§Manual acceptance flows). Tick each or note the breaking step.
- [ ] **Step 3: Write the ADR** — add `docs/adr/00NN-clip-version-history-publish-snapshots.md` (next free number) in MADR-lite format (Context / Alternatives / Decision / Consequences), capturing: snapshot-on-publish version model, local-canonical store + `pragafilm.anno_version` breadcrumb, replace-on-rerun, publish-state-centric unified status, and Approach A (snapshot layer, not a spine rewrite). Update the index table in `docs/decisions.md`.
- [ ] **Step 4: Commit**

```bash
git add docs/adr/00NN-clip-version-history-publish-snapshots.md docs/decisions.md
git commit -m "docs(adr): clip version history — publish snapshots + unified status (Approach A)"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- `clip_versions` table → Task 1. Repo → Task 2. `origin_clip_version_id` → Tasks 1, 3.
- Fixed `clip_publish_state` enum + Literal + guard → Tasks 3a (Literal), 4 (enum).
- Status derivation (one place, precedence) → Task 5; consumed in Tasks 12 (detail), 13 (list), 14 (chip).
- Publish flow (materialize snapshot, version row, provenance op, enqueue) → Task 6; SyncEngine flip → Task 7.
- Restore-forward (never mutate history) + one-click → Task 8.
- Re-run replaces draft (fix orphaning) → Task 9.
- Backfill synthetic v1 → Task 10.
- UI: detail History + headline (12), clips-list badge (13), topbar chip (14), re-run confirm (15), diff view (16).
- Provenance `pragafilm.anno_version` → Task 6 (`PROVENANCE_FIELD`, `build_provenance_value`).
- Tests: unit (status, enum, provenance shape), integration (publish/sync/restore/re-run/backfill), N+1 guard (13), template-string guards (12–16), manual flows (17). All present.

**Placeholder scan:** No "TBD/TODO". A few steps say "read X before relying on the exact signature" — these are *verification* notes against real files (macro signatures, `WriteResult`/`TargetMap`/`chunked_in_clause` shapes), not missing content; the code to write is shown in full. The two `00NN` tokens (migration `0023`, the ADR number) are deliberate per the repo's collision rule, with explicit selection instructions.

**Type consistency:** `ClipVersion`, `ClipVersionsRepo` methods (`insert`/`get`/`next_version_num`/`list_by_clip`/`live_for_clip`/`mark_live`/`mark_failed`/`mark_conflict`/`newest_state_by_clip`), `resolve_publish_status(has_draft, version_state, version_num) -> (state, num)`, `PublishService.publish(conn, *, clip_id, author, origin='publish')`, `RestoreService.restore_into_draft(conn, *, clip_id, version_num)`, `WriteQueue.enqueue_apply_for_clip(..., clip_version_id, extra_ops)`, `ReviewItemsRepo.clear_unapplied_for_clip(conn, clip_id)`, `PROVENANCE_FIELD = "pragafilm.anno_version"` — names are used identically across tasks.
