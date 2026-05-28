# Draft Review & Accept Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing (unused-by-UI) `review_items` accept/apply machinery to a real UI: a consolidated `/review` backlog page, a human-in-the-loop review queue that reuses the clip page, and a kind-filtered bulk "yolo" apply — for both video and image clips.

**Architecture:** No storage/schema changes. We add three backend seams (a cross-clip pending query, a `GET /pending` + `POST /apply-batch` pair, and a shared per-clip apply helper extracted from `apply_clip`), then build the UI by reusing existing partials (`_video_list.html`, `_pager.html`, `_anno_panels.html`) and extracting the Cache page's inline selection JS into a shared `row_select.js` that both pages consume. HITL review is the existing `/clips/{id}` page rendered in a `?review=1` mode with per-item accept/edit/reject controls and an "Apply & next" queue.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, Jinja2, HTMX, Alpine.js, pytest + pytest-asyncio (`asyncio_mode = "auto"`), TestClient.

**Conventions for this plan:**
- Run a single test: `uv run pytest tests/integration/test_x.py::test_name -v` (use `uv run` if the repo uses uv; otherwise `pytest ...` inside the venv — match what the other tests in that file do).
- Full backend gate before any "done" claim: `uv run pytest -q && uv run ruff check backend tests`.
- Frontend has no unit tests; frontend tasks are verified by the **Manual acceptance flows** in the spec (`docs/specs/2026-05-27-draft-review-accept-design.md`) on a running server. Start/stop the server via the `server-start` / `server-stop` skills (CatDV seat discipline).
- Commit after every task.
- **Verification sequencing (user policy):** automated tests run anytime — they're fully isolated (per-test temp SQLite via the `db` fixture / `DATA_DIR`→`tmp_path`; `CATDV_OFFLINE=true` → no seat, no network; the real `./data/app.db` and CatDV are never touched). Mid-implementation, only **read-only** manual checks are allowed (page renders, navigation, badge shows). **Any manual step that writes upstream (applies drafts to CatDV / takes a seat) is DEFERRED to a single final verification pass** the user green-lights — do NOT run apply-to-CatDV per task. Where a task's manual step below includes an apply action, perform only the read-only portion inline and move the apply portion to the final pass.

---

## File Structure

**Backend (new behavior, additive):**
- `backend/app/repositories/review_items.py` — add `list_pending_clips()` and `count_pending_clips()`.
- `backend/app/services/write_queue.py` — add `enqueue_apply_for_clip()` (the resolution logic currently inline in `routes/review.py::apply_clip`).
- `backend/app/routes/review.py` — refactor `apply_clip` onto the helper; add `GET /pending`, `GET /pending/count`, `POST /apply-batch`.
- `backend/app/services/draft_view.py` — carry per-item `id`, `kind`, `decision` so review-mode controls can target items.
- `backend/app/routes/pages/review.py` (new) — the `/review` page handler (+ HTMX table swap). Registered in `routes/pages/__init__.py`.
- `backend/app/routes/pages/clips.py` — `clip_detail_page` accepts `?review=1`, passes `review_mode` + queue context.

**Templates (reuse-first):**
- `backend/app/templates/pages/review.html` (new) — page shell modeled on `cache_page.html`.
- `backend/app/templates/pages/_review_head_cells.html`, `_review_row_cells.html` (new) — trailing columns for `_video_list.html`.
- `backend/app/templates/pages/_anno_panels.html` — add `review_mode` per-item controls.
- `backend/app/templates/pages/clip_detail.html` — review action bar (review mode only).
- `backend/app/templates/pages/_rail.html` + `backend/app/templates/icons/_review.svg` (new) — Review nav + badge.

**Static JS (extract, don't clone):**
- `backend/app/static/row_select.js` (new) — shared selection factory extracted from `cache_page.html`'s `cacheSel()`.
- `backend/app/templates/cache_page.html` — refactored to consume `row_select.js` (inline `cacheSel()` removed; Cache behavior unchanged).
- `backend/app/static/review.js` (new) — review-queue session list + navigation + per-item decision calls + bulk apply.

---

## Task 1: Cross-clip pending query in `ReviewItemsRepo`

**Files:**
- Modify: `backend/app/repositories/review_items.py`
- Test: `tests/integration/test_review_items_repo.py`

A "pending clip" = a clip with ≥1 `review_items` row where `applied_at IS NULL`. The row carries per-kind counts and metadata from the clip's most-recent annotation that owns pending items (`MAX(annotation_id)`), with an optional `job_id` filter.

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_review_items_repo.py` (reuse the file's existing seeding helpers/fixtures; if it builds a repo + in-memory/temp DB, follow that exact pattern). This test assumes a helper that inserts an annotation + review items for a clip — mirror whatever the existing tests in this file already do to create rows.

```python
async def test_list_pending_clips_groups_and_counts(review_db):
    # review_db: (conn, repos) per this file's existing fixture pattern.
    conn, repos = review_db
    annos, items_repo = repos.annotations, repos.review_items

    aid = await annos.insert(conn, _annotation(catdv_clip_id=42, name="Clip_42", job_id=7))
    await items_repo.bulk_insert(conn, [
        _ri(aid, 42, "marker", {"name": "a", "in": {"secs": 0.0}, "out": {"secs": 1.0}}),
        _ri(aid, 42, "marker", {"name": "b", "in": {"secs": 1.0}, "out": {"secs": 2.0}}),
        _ri(aid, 42, "field", "x", target="pragafilm.dekáda.natočení"),
    ])

    rows = await items_repo.list_pending_clips(conn, limit=50, offset=0)
    assert len(rows) == 1
    row = rows[0]
    assert row["catdv_clip_id"] == 42
    assert row["catdv_clip_name"] == "Clip_42"
    assert row["job_id"] == 7
    assert row["marker_count"] == 2
    assert row["field_count"] == 1
    assert row["note_count"] == 0


async def test_list_pending_clips_excludes_applied(review_db):
    conn, repos = review_db
    aid = await repos.annotations.insert(conn, _annotation(catdv_clip_id=42, name="Clip_42", job_id=7))
    items = await repos.review_items.bulk_insert(conn, [
        _ri(aid, 42, "field", "x", target="f.a"),
    ])
    await repos.review_items.mark_applied(conn, [items[0].id])
    rows = await repos.review_items.list_pending_clips(conn, limit=50, offset=0)
    assert rows == []


async def test_list_pending_clips_job_filter(review_db):
    conn, repos = review_db
    a7 = await repos.annotations.insert(conn, _annotation(catdv_clip_id=1, name="c1", job_id=7))
    a8 = await repos.annotations.insert(conn, _annotation(catdv_clip_id=2, name="c2", job_id=8))
    await repos.review_items.bulk_insert(conn, [_ri(a7, 1, "field", "x", target="f.a")])
    await repos.review_items.bulk_insert(conn, [_ri(a8, 2, "field", "y", target="f.a")])
    rows = await repos.review_items.list_pending_clips(conn, job_id=7, limit=50, offset=0)
    assert [r["catdv_clip_id"] for r in rows] == [1]


async def test_count_pending_clips(review_db):
    conn, repos = review_db
    a = await repos.annotations.insert(conn, _annotation(catdv_clip_id=1, name="c1", job_id=7))
    await repos.review_items.bulk_insert(conn, [_ri(a, 1, "field", "x", target="f.a")])
    assert await repos.review_items.count_pending_clips(conn) == 1
```

> If `test_review_items_repo.py` doesn't already expose a `review_db` fixture and `_annotation` / `_ri` builders, add small module-level helpers at the top of the test file modeled on `_seed()` in `tests/integration/test_routes_review.py` (which shows the exact `Annotation` and `ReviewItem` constructor args). Keep them local to the test module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_review_items_repo.py -k "pending_clips or count_pending" -v`
Expected: FAIL — `AttributeError: 'ReviewItemsRepo' object has no attribute 'list_pending_clips'`.

- [ ] **Step 3: Implement `list_pending_clips` + `count_pending_clips`**

Add to `ReviewItemsRepo` in `backend/app/repositories/review_items.py`:

```python
    async def list_pending_clips(
        self,
        conn: aiosqlite.Connection,
        *,
        job_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """One row per clip with un-applied review items, newest first.

        Counts are over items with applied_at IS NULL. Clip metadata comes
        from the most-recent annotation owning those items (MAX(annotation_id)).
        """
        params: list = []
        job_clause = ""
        if job_id is not None:
            job_clause = "AND a.job_id = ?"
            params.append(job_id)
        sql = f"""
            SELECT
              ri.catdv_clip_id                                   AS catdv_clip_id,
              MAX(ri.annotation_id)                              AS annotation_id,
              SUM(CASE WHEN ri.kind = 'marker' THEN 1 ELSE 0 END) AS marker_count,
              SUM(CASE WHEN ri.kind = 'field'  THEN 1 ELSE 0 END) AS field_count,
              SUM(CASE WHEN ri.kind = 'note'   THEN 1 ELSE 0 END) AS note_count,
              a.catdv_clip_name                                  AS catdv_clip_name,
              a.job_id                                           AS job_id,
              a.prompt_version_id                                AS prompt_version_id,
              a.created_at                                       AS created_at
            FROM review_items ri
            JOIN annotations a
              ON a.id = (
                SELECT MAX(ri2.annotation_id)
                FROM review_items ri2
                WHERE ri2.catdv_clip_id = ri.catdv_clip_id
                  AND ri2.applied_at IS NULL
              )
            WHERE ri.applied_at IS NULL {job_clause}
            GROUP BY ri.catdv_clip_id
            ORDER BY a.created_at DESC, ri.catdv_clip_id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cur = await conn.execute(sql, tuple(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in await cur.fetchall()]

    async def count_pending_clips(
        self, conn: aiosqlite.Connection, *, job_id: int | None = None
    ) -> int:
        params: list = []
        job_clause = ""
        if job_id is not None:
            job_clause = (
                "AND ri.annotation_id IN "
                "(SELECT id FROM annotations WHERE job_id = ?)"
            )
            params.append(job_id)
        cur = await conn.execute(
            f"""
            SELECT COUNT(DISTINCT ri.catdv_clip_id)
            FROM review_items ri
            WHERE ri.applied_at IS NULL {job_clause}
            """,
            tuple(params),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/integration/test_review_items_repo.py -k "pending_clips or count_pending" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/review_items.py tests/integration/test_review_items_repo.py
git commit -m "feat(review): cross-clip pending query (list/count_pending_clips)"
```

---

## Task 2: Extract shared per-clip apply helper

**Files:**
- Modify: `backend/app/services/write_queue.py`
- Modify: `backend/app/routes/review.py:42-75` (`apply_clip`)
- Test: `tests/integration/test_routes_review.py` (existing apply test must still pass)

Move the annotation/version/etag/fps resolution out of the route into `WriteQueue`, so the single-clip route and the new batch endpoint share one implementation.

- [ ] **Step 1: Add the helper to `WriteQueue`**

In `backend/app/services/write_queue.py`, add a method that resolves a clip's accepted items and enqueues them. It must take the repos it needs via the existing `ctx`-style dependencies the route already has access to; to avoid coupling `WriteQueue` to `annotations_repo`/`prompts_repo`, accept the already-resolved pieces:

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
        )
```

> Rationale: the *resolution* of `accepted`, `target_map`, `expected_etag`, `fps` from repos is shared by extracting a route-level helper (next step), keeping `WriteQueue` free of repo dependencies. `enqueue_apply_for_clip` removes the only differing bit (building `clip_key` from an int) so both call sites are identical.

- [ ] **Step 2: Extract a route-level resolver in `routes/review.py`**

Add a private async helper that both `apply_clip` and `apply-batch` call:

```python
async def _resolve_and_enqueue_clip(ctx, clip_id: int) -> int:
    """Resolve a clip's accepted items + apply context and enqueue them.
    Returns the number of ops queued."""
    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return 0
    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    version = await ctx.prompts_repo.get_version(ctx.db, annotation.prompt_version_id)
    op_ids = await ctx.write_queue.enqueue_apply_for_clip(
        ctx.db,
        clip_id=clip_id,
        accepted=accepted,
        target_map=version.target_map,
        expected_etag=etag_from_snapshot(annotation.clip_snapshot),
        annotation_id=annotation.id,
        fps=fps_from_snapshot(annotation.clip_snapshot),
    )
    return len(op_ids)
```

Then rewrite the body of `apply_clip` to use it:

```python
@router.post("/clips/{clip_id}/apply")
async def apply_clip(request: Request, clip_id: int):
    ctx = get_ctx(request)
    if ctx.write_queue is None:
        raise HTTPException(503, "write queue not initialized")
    queued = await _resolve_and_enqueue_clip(ctx, clip_id)
    if queued and ctx.sync_engine is not None:
        ctx.sync_engine.notify()
    return {"queued": queued, "applied": queued}
```

- [ ] **Step 3: Run the existing apply test to verify no behavior change**

Run: `uv run pytest tests/integration/test_routes_review.py -v`
Expected: PASS — including `test_apply_clip_enqueues_and_drains_via_sync_engine` (asserts `body["queued"] >= 1`).

- [ ] **Step 4: Run the write_queue tests**

Run: `uv run pytest tests/integration/test_write_queue.py -v`
Expected: PASS (the underlying `enqueue_apply` is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/write_queue.py backend/app/routes/review.py
git commit -m "refactor(review): extract shared per-clip apply helper"
```

---

## Task 3: `GET /pending`, `GET /pending/count`, `POST /apply-batch`

**Files:**
- Modify: `backend/app/routes/review.py`
- Test: `tests/integration/test_routes_review.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/integration/test_routes_review.py` (reuse `_make_app`, `_seed`, `_run`, `TestClient` already in the file):

```python
def test_pending_lists_clip(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))  # seeds clip 1 with 1 marker + 1 field, all pending
        r = client.get("/api/review/pending")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        row = body["clips"][0]
        assert row["catdv_clip_id"] == 1
        assert row["marker_count"] == 1
        assert row["field_count"] == 1


def test_pending_count(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/api/review/pending/count")
        assert r.status_code == 200
        assert r.json()["count"] == 1


def test_apply_batch_marks_and_enqueues_filtered_by_kind(monkeypatch, tmp_path):
    from backend.app.archive.model import ChangeSet, WriteResult
    from backend.app.repositories.pending_operations import PendingOperationsRepo
    from backend.app.repositories.write_log import WriteLogRepo
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.sync_engine import SyncEngine

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))

        class FakeArchive:
            id = "catdv"
            async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
                return WriteResult(status="ok", upstream_response={"ID": 1, "modifyDate": "x"})

        class AlwaysOnline:
            def current_state(self):
                return ConnectionState.online

        ctx.archive = FakeArchive()
        ctx.sync_engine = SyncEngine(
            provider=ctx.archive,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=AlwaysOnline(),
            db_provider=lambda: ctx.db,
        )

        # Only markers; the field should remain pending.
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1], "kinds": ["marker"]})
        assert r.status_code == 200
        assert r.json()["clips"] == 1
        assert r.json()["queued"] >= 1

        items = client.get("/api/review/clips/1/items").json()
        markers = [it for it in items if it["kind"] == "marker"]
        fields = [it for it in items if it["kind"] == "field"]
        assert all(it["applied_at"] for it in markers)       # marker applied
        assert all(it["applied_at"] is None for it in fields) # field untouched


def test_apply_batch_defaults_all_kinds(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        # No kinds key -> all kinds. (Offline is fine; we only assert queued.)
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1]})
        assert r.status_code == 200
        assert r.json()["queued"] >= 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_routes_review.py -k "pending or apply_batch" -v`
Expected: FAIL with 404 (routes not defined).

- [ ] **Step 3: Implement the routes**

Add to `backend/app/routes/review.py`:

```python
class ApplyBatch(BaseModel):
    clip_ids: list[int]
    kinds: list[str] | None = None


@router.get("/pending")
async def list_pending(request: Request, job_id: int | None = None, offset: int = 0, limit: int = 50):
    ctx = get_ctx(request)
    rows = await ctx.review_items_repo.list_pending_clips(
        ctx.db, job_id=job_id, limit=limit, offset=offset
    )
    total = await ctx.review_items_repo.count_pending_clips(ctx.db, job_id=job_id)
    return {"clips": rows, "total": total, "offset": offset, "limit": limit}


@router.get("/pending/count")
async def pending_count(request: Request, job_id: int | None = None):
    ctx = get_ctx(request)
    return {"count": await ctx.review_items_repo.count_pending_clips(ctx.db, job_id=job_id)}


@router.post("/apply-batch")
async def apply_batch(request: Request, body: ApplyBatch):
    ctx = get_ctx(request)
    if ctx.write_queue is None:
        raise HTTPException(503, "write queue not initialized")
    kinds = set(body.kinds) if body.kinds else {"marker", "field", "note"}
    if not kinds <= {"marker", "field", "note"}:
        raise HTTPException(400, "kinds must be a subset of marker|field|note")

    total_queued = 0
    clips_touched = 0
    for clip_id in body.clip_ids:
        pending = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision=None)
        to_accept = [it for it in pending if it.applied_at is None and it.kind in kinds]
        if not to_accept:
            continue
        for it in to_accept:
            await ctx.review_items_repo.set_decision(ctx.db, it.id, "accepted")
        queued = await _resolve_and_enqueue_clip(ctx, clip_id)
        if queued:
            clips_touched += 1
            total_queued += queued
    if total_queued and ctx.sync_engine is not None:
        ctx.sync_engine.notify()
    return {"clips": clips_touched, "queued": total_queued}
```

> Note: `_resolve_and_enqueue_clip` (Task 2) re-reads `decision="accepted"` items, so after `set_decision(..., "accepted")` the marker rows are enqueued. Because `enqueue_apply` skips `applied_at IS NOT NULL`, the un-targeted kinds (still pending, never accepted) are not applied — exactly the kind filter behavior.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/integration/test_routes_review.py -k "pending or apply_batch" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Full backend gate + commit**

```bash
uv run pytest -q && uv run ruff check backend tests
git add backend/app/routes/review.py tests/integration/test_routes_review.py
git commit -m "feat(review): GET /pending, /pending/count, POST /apply-batch"
```

---

## Task 4: Carry item id/kind/decision in the draft view-model

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py` (create if absent; this is pure-function unit testable)

Review-mode controls need each rendered item's `review_item.id`, `kind`, and current `decision`. Add them without changing the existing published shape (extra keys are ignored by current templates).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_draft_view.py`:

```python
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.services.draft_view import build_draft_view


def _ann() -> Annotation:
    return Annotation(
        id=5, catdv_clip_id=1, catdv_clip_name="c", prompt_version_id=1,
        model="m", prompt_used="p", raw_response={}, structured_output={},
        clip_snapshot={},
    )


def test_markers_carry_item_id_kind_decision():
    items = [
        ReviewItem(id=11, annotation_id=5, catdv_clip_id=1, kind="marker",
                   proposed_value={"name": "a", "in": {"secs": 0.0}, "out": {"secs": 1.0}},
                   decision="pending"),
        ReviewItem(id=12, annotation_id=5, catdv_clip_id=1, kind="field",
                   target_identifier="f.a", proposed_value="v", decision="accepted"),
    ]
    view = build_draft_view(_ann(), items)
    assert view["markers"][0]["item_id"] == 11
    assert view["markers"][0]["decision"] == "pending"
    assert view["fields"][0]["item_id"] == 12
    assert view["fields"][0]["decision"] == "accepted"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_draft_view.py -v`
Expected: FAIL — `KeyError: 'item_id'`.

- [ ] **Step 3: Implement**

In `backend/app/services/draft_view.py`, thread the source `ReviewItem` into the per-item builders and add the three keys:

```python
def _marker_from_review(item: ReviewItem) -> dict[str, Any]:
    pv: dict[str, Any] = item.proposed_value if isinstance(item.proposed_value, dict) else {}
    in_part = pv.get("in") or {}
    out_part = pv.get("out")
    return {
        "item_id": item.id,
        "kind": "marker",
        "decision": item.decision,
        "name": _fix(pv.get("name")) or "",
        "category": pv.get("category"),
        "description": _fix(pv.get("description")),
        "in_secs": float(in_part.get("secs", 0.0)),
        "out_secs": float(out_part["secs"])
        if isinstance(out_part, dict) and "secs" in out_part
        else None,
        "color": pv.get("color"),
    }


def _field_from_review(item: ReviewItem) -> dict[str, Any]:
    identifier = item.target_identifier or ""
    value = item.proposed_value
    if isinstance(value, list):
        value_str = ", ".join(_fix(str(v)) or "" for v in value)
    elif value is None:
        value_str = ""
    else:
        value_str = _fix(str(value)) or ""
    return {
        "item_id": item.id,
        "kind": "field",
        "decision": item.decision,
        "identifier": identifier,
        "name": identifier.split(".")[-1],
        "value": value_str,
    }
```

> `notes` stays a joined string today; review-mode note controls are out of v1 scope for inline editing — notes are accepted/rejected at the kind level via the bulk path and via the per-clip Apply. (If a note-level toggle is wanted later, add an `items` list to the notes panel; not required for the acceptance flows.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_draft_view.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(review): carry item id/kind/decision in draft view-model"
```

---

## Task 5: `/review` page handler + templates (reusing `_video_list`)

**Files:**
- Create: `backend/app/routes/pages/review.py`
- Modify: `backend/app/routes/pages/__init__.py`
- Create: `backend/app/templates/pages/review.html`, `_review_head_cells.html`, `_review_row_cells.html`
- Test: `tests/integration/test_routes_review.py` (page smoke test)

The handler mirrors `cache_page` (`backend/app/routes/cache.py:179-290`): full page on normal GET, table-only partial on `HX-Request`. It hydrates each pending row into the `_video_list.html` row contract (`select_value`, `thumb_url`, `name`, `row_href`, plus counts) and applies the media-type filter using `is_image_path` on the cached clip.

- [ ] **Step 1: Write the failing smoke test**

Add to `tests/integration/test_routes_review.py`:

```python
def test_review_page_renders(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/review")
        assert r.status_code == 200
        assert "Clip_1" in r.text
        assert "row-check" in r.text  # selection scaffold present


def test_review_page_htmx_returns_table_only(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/review", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "<table" in r.text
        assert "<aside" not in r.text  # no full layout chrome
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_routes_review.py -k review_page -v`
Expected: FAIL with 404.

- [ ] **Step 3: Create the page handler**

Create `backend/app/routes/pages/review.py`:

```python
"""Consolidated draft-review page (/review): lists clips with un-applied
review items, with batch (job) and media-type filters. Mirrors the cache
page's full-page / HTMX-partial split."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.app.deps import get_ctx
from backend.app.media_kind import is_image_path

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


def _counts_label(row: dict) -> str:
    parts = []
    if row["marker_count"]:
        parts.append(f'{row["marker_count"]} markers')
    if row["field_count"]:
        parts.append(f'{row["field_count"]} fields')
    if row["note_count"]:
        parts.append(f'{row["note_count"]} notes')
    return " · ".join(parts) or "—"


@router.get("/review", response_class=HTMLResponse)
async def review_page(
    request: Request,
    job_id: int | None = None,
    media: str | None = None,  # "video" | "image" | None
    offset: int = 0,
    limit: int = 50,
) -> HTMLResponse:
    ctx = get_ctx(request)
    is_htmx = request.headers.get("HX-Request") == "true"

    pending = await ctx.review_items_repo.list_pending_clips(
        ctx.db, job_id=job_id, limit=limit, offset=offset
    )
    total = await ctx.review_items_repo.count_pending_clips(ctx.db, job_id=job_id)

    rows = []
    for p in pending:
        clip_id = p["catdv_clip_id"]
        clip = await ctx.clip_cache_repo.get_by_key(ctx.db, "catdv", str(clip_id))
        media_path = clip.media_path if clip and getattr(clip, "media_path", None) else None
        kind = "image" if is_image_path(media_path) else "video"
        if media in ("video", "image") and kind != media:
            continue
        rows.append({
            "select_value": f"catdv/{clip_id}",
            "catdv_clip_id": clip_id,
            "cache": None,
            "thumb_url": f"/api/media/{clip_id}/thumb",
            "name": p["catdv_clip_name"],
            "name_sub": None,
            "row_href": f"/clips/{clip_id}?review=1",
            "row_class": None,
            "row_bytes": None,
            "kind": kind,
            "counts_label": _counts_label(p),
            "marker_count": p["marker_count"],
            "field_count": p["field_count"],
            "note_count": p["note_count"],
            "created_at": p["created_at"],
        })

    # Recent jobs for the batch filter dropdown.
    jobs = await ctx.jobs_repo.list_jobs(ctx.db, limit=50)

    metric = {
        "clips": total,
        "markers": sum(p["marker_count"] for p in pending),
        "fields": sum(p["field_count"] for p in pending),
        "notes": sum(p["note_count"] for p in pending),
    }

    from backend.app.ui.pagination import page_offsets
    prev_offset, next_offset = page_offsets(offset, limit, total)

    ctx_dict = {
        "rows": rows,
        "total": total,
        "offset": offset,
        "limit": limit,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
        "filters": {"job_id": job_id, "media": media or ""},
        "jobs": jobs,
        "metric": metric,
    }
    if is_htmx:
        return templates.TemplateResponse(request, "pages/_review_table.html", ctx_dict)
    return templates.TemplateResponse(request, "pages/review.html", ctx_dict)
```

> Verify the exact accessors before finishing: `ctx.clip_cache_repo` and the clip object's media-path attribute name (grep `media_path` / `clip_cache` usage in `backend/app/services/proxy_resolver.py` and `routes/pages/clips.py`). If the canonical clip exposes the path differently, adapt `media_path` accordingly. `page_offsets` lives in `backend/app/ui/pagination.py` (used by `cache.py`).

- [ ] **Step 4: Register the router**

Modify `backend/app/routes/pages/__init__.py`:

```python
from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router
from backend.app.routes.pages.review import router as review_router
from backend.app.routes.pages.studio import router as studio_router

page_routers = [clips_router, prompts_router, studio_router, review_router]

__all__ = [
    "page_routers", "clips_router", "prompts_router", "studio_router", "review_router",
]
```

- [ ] **Step 5: Create the table partial**

Create `backend/app/templates/pages/_review_table.html`:

```jinja
{% with head_cells = "pages/_review_head_cells.html",
        row_cells = "pages/_review_row_cells.html",
        cache_label = "Cache",
        colspan = 6,
        empty_msg = "No drafts awaiting review." %}
  {% include "pages/_video_list.html" %}
{% endwith %}
{% set _q = 'limit=' ~ limit %}
{% if filters.job_id is not none %}{% set _q = _q ~ '&job_id=' ~ filters.job_id %}{% endif %}
{% if filters.media %}{% set _q = _q ~ '&media=' ~ filters.media %}{% endif %}
{% set _range = ((offset + 1) ~ '–' ~ (offset + rows|length) ~ ' of ' ~ total) if rows else ('0 of ' ~ total) %}
{% with prev_url = ('/review?' ~ _q ~ '&offset=' ~ prev_offset) if prev_offset is not none else none,
        next_url = ('/review?' ~ _q ~ '&offset=' ~ next_offset) if next_offset is not none else none,
        range_label = _range,
        hx_target = "#review-table-region" %}
  {% include "pages/_pager.html" %}
{% endwith %}
```

- [ ] **Step 6: Create the trailing column partials**

Create `backend/app/templates/pages/_review_head_cells.html`:

```jinja
<th class="col-type">Type</th>
<th class="col-counts">Drafts</th>
<th class="col-batch">Batch</th>
```

Create `backend/app/templates/pages/_review_row_cells.html`:

```jinja
<td class="cell-type mono">{{ row.kind }}</td>
<td class="cell-counts">{{ row.counts_label }}</td>
<td class="cell-batch mono muted">{{ row.created_at }}</td>
```

- [ ] **Step 7: Create the page shell**

Create `backend/app/templates/pages/review.html` (mirrors `cache_page.html` structure; selection wiring lands in Task 6 — for now reference `reviewSel()` which Task 6 provides, but the page renders without JS errors because Alpine simply no-ops an undefined component until then; if running between tasks, the bulk bar just won't function):

```jinja
{% extends "pages/layout.html" %}
{% block rail_active %}review{% endblock %}
{% block title %}Review · CatDV Annotator{% endblock %}
{% block crumb %}
  <span class="crumb"><span class="strong">Draft review</span></span>
{% endblock %}
{% block body %}
<div class="page review-page" x-data="reviewSel()">

  <div class="page-hdr">
    <h1>Review</h1>
    <span class="meta">accept · edit · apply</span>
    <span class="grow"></span>
    <button class="btn ghost" type="button" onclick="location.reload()">Refresh</button>
  </div>

  <div class="metric-strip">
    <div class="metric"><div class="m-label">Clips awaiting review</div>
      <div class="m-value">{{ metric.clips }}</div></div>
    <div class="metric"><div class="m-label">Markers</div>
      <div class="m-value">{{ metric.markers }}</div></div>
    <div class="metric"><div class="m-label">Fields</div>
      <div class="m-value">{{ metric.fields }}</div></div>
    <div class="metric"><div class="m-label">Notes</div>
      <div class="m-value">{{ metric.notes }}</div></div>
  </div>

  <details class="cache-extra-filters">
    <summary>Filters <span class="mono muted">{{ total }} clips</span></summary>
    <form action="/review" method="get" class="filters-form">
      <label>Batch:
        <select name="job_id">
          <option value="">All</option>
          {% for j in jobs %}
            <option value="{{ j.id }}" {% if filters.job_id == j.id %}selected{% endif %}>
              #{{ j.id }} · {{ j.notes or j.kind or "job" }} ({{ j.total_clips }})
            </option>
          {% endfor %}
        </select>
      </label>
      <label>Media:
        <select name="media">
          <option value="" {% if not filters.media %}selected{% endif %}>All</option>
          <option value="video" {% if filters.media == 'video' %}selected{% endif %}>Video</option>
          <option value="image" {% if filters.media == 'image' %}selected{% endif %}>Image</option>
        </select>
      </label>
      <button type="submit" class="bulk-btn">Apply</button>
    </form>
  </details>

  <div class="bulkbar" x-show="count > 0" x-cloak>
    <span class="bulk-count"><b x-text="count"></b> selected</span>
    <span class="grow"></span>
    <label class="kindtoggle"><input type="checkbox" x-model="kinds.marker"> Markers</label>
    <label class="kindtoggle"><input type="checkbox" x-model="kinds.field"> Fields</label>
    <label class="kindtoggle"><input type="checkbox" x-model="kinds.note"> Notes</label>
    <button type="button" class="bulk-btn" @click="clearSel()">Clear</button>
    <button type="button" class="bulk-btn" @click="reviewSelected()">Review selected →</button>
    <button type="button" class="bulk-btn bulk-btn-danger" @click="applySelected()">Apply drafts (selected)</button>
  </div>

  <div id="review-table-region" class="cache-listwrap">
    {% include "pages/_review_table.html" %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 8: Run the smoke tests**

Run: `uv run pytest tests/integration/test_routes_review.py -k review_page -v`
Expected: PASS (both). If `ctx.clip_cache_repo`/media-path accessor names differ, fix per the Step-3 note until green.

- [ ] **Step 9: Full gate + commit**

```bash
uv run pytest -q && uv run ruff check backend tests
git add backend/app/routes/pages/review.py backend/app/routes/pages/__init__.py \
        backend/app/templates/pages/review.html \
        backend/app/templates/pages/_review_table.html \
        backend/app/templates/pages/_review_head_cells.html \
        backend/app/templates/pages/_review_row_cells.html \
        tests/integration/test_routes_review.py
git commit -m "feat(review): /review page handler + table partials"
```

---

## Task 6: Extract shared selection model (`row_select.js`); refactor Cache page onto it; wire `reviewSel()`

**Files:**
- Create: `backend/app/static/row_select.js`
- Modify: `backend/app/templates/cache_page.html` (remove inline `cacheSel()`, consume factory)
- Modify: `backend/app/templates/pages/layout.html` (load `row_select.js`) — or include per page; match how existing static JS is loaded.
- Create: `backend/app/static/review.js` (the `reviewSel()` component + queue helpers)
- Verify: Manual acceptance flow #10 (Cache page regression) + #1–#3 (review list/select).

This is the no-clone task. `row_select.js` exposes a generic factory; both pages build their component on top of it.

- [ ] **Step 1: Create the shared factory**

Create `backend/app/static/row_select.js` (verbatim generalization of `cacheSel()` from `cache_page.html:128-196`):

```javascript
// Shared row-selection model for the cache + review list pages.
// Returns an object meant to be spread into an Alpine component via
// Object.assign, so callers can add page-specific actions.
function rowSelect() {
  return {
    count: 0,
    totalBytes: 0,
    _selected() {
      return Array.from(document.querySelectorAll('.row-check:checked'));
    },
    _selectedKeys() {
      return this._selected().map(el => el.value.split('/'));
    },
    _recount() {
      const sel = this._selected();
      this.count = sel.length;
      this.totalBytes = sel.reduce(
        (acc, el) => acc + parseInt(el.dataset.bytes || '0', 10), 0);
    },
    bytesHuman(n) {
      if (!n) return '0 B';
      const u = ['B','KB','MB','GB','TB'];
      let i = 0;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
    },
    initSelection() {
      document.addEventListener('change', e => {
        if (e.target.classList.contains('row-check')) this._recount();
        if (e.target.id === 'row-select-all') {
          document.querySelectorAll('.row-check').forEach(
            cb => cb.checked = e.target.checked);
          this._recount();
        }
      });
      document.body.addEventListener('htmx:afterSwap', () => this._recount());
    },
    clearSel() {
      document.querySelectorAll('.row-check:checked').forEach(cb => cb.checked = false);
      this._recount();
    },
  };
}
```

- [ ] **Step 2: Refactor the Cache page onto the factory**

In `backend/app/templates/cache_page.html`, replace the inline `function cacheSel() {...}` block with a thin wrapper that spreads `rowSelect()` and keeps the cache-specific bulk actions. The `x-data="cacheSel()"` stays. Add `init()` calling `initSelection()`:

```html
<script>
  function cacheSel() {
    return Object.assign(rowSelect(), {
      init() { this.initSelection(); },
      async bulkPrefetch(keys) {
        keys = keys || this._selectedKeys();
        if (keys.length === 0) return;
        const r = await fetch('/api/cache/prefetch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clip_keys: keys }),
        });
        if (r.ok) htmx.ajax('GET', window.location.href, '#cache-table-region');
      },
      async bulkEvict(keys) {
        keys = keys || this._selectedKeys();
        if (keys.length === 0) return;
        if (!confirm(`Purge local media for ${keys.length} clip(s)?`)) return;
        const r = await fetch('/api/cache/bulk-evict', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clip_keys: keys, layers: ['media-local'], force: false }),
        });
        if (r.ok) htmx.ajax('GET', window.location.href, '#cache-table-region');
      },
    });
  }
</script>
```

> The old `init()` body (the two event listeners) now lives in `initSelection()`; behavior is identical. Confirm `bytesHuman`/`totalBytes` are still referenced by the cache bulk bar (they are, in `cache_page.html`).

- [ ] **Step 3: Load `row_select.js` before the pages that use it**

In `backend/app/templates/pages/layout.html`, add a `<script src=".../row_select.js">` in the same place other static JS is loaded (grep `static` in `layout.html` to match the existing URL prefix, e.g. `/static/`). It must load before Alpine initializes the page component. If the cache page currently relies on Alpine being deferred, keep the same ordering.

- [ ] **Step 4: Create `reviewSel()` with review-specific actions**

Create `backend/app/static/review.js`:

```javascript
function reviewSel() {
  return Object.assign(rowSelect(), {
    kinds: { marker: true, field: true, note: true },
    init() { this.initSelection(); },
    _selectedClipIds() {
      // select_value is "catdv/<id>"
      return this._selected().map(el => parseInt(el.value.split('/')[1], 10));
    },
    _activeKinds() {
      return Object.entries(this.kinds).filter(([, on]) => on).map(([k]) => k);
    },
    reviewSelected() {
      const ids = this._selectedClipIds();
      if (!ids.length) return;
      sessionStorage.setItem('catdv:reviewQueue', JSON.stringify(ids));
      location.href = `/clips/${ids[0]}?review=1`;
    },
    async applySelected() {
      const clip_ids = this._selectedClipIds();
      const kinds = this._activeKinds();
      if (!clip_ids.length || !kinds.length) return;
      if (!confirm(`Apply ${kinds.join(', ')} drafts for ${clip_ids.length} clip(s)?`)) return;
      const r = await fetch('/api/review/apply-batch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_ids, kinds }),
      });
      if (r.ok) htmx.ajax('GET', window.location.href, '#review-table-region');
    },
  });
}
```

Load `review.js` on the review page (add a `{% block %}` script include in `review.html`, or include in layout like `row_select.js`). Match the project's existing per-page JS loading convention (grep how `studio.js` / `player.js` are loaded).

- [ ] **Step 5: Manual verification (server required)**

Start the server (use the `server-start` skill). Then:
- `/cache`: select rows, Select-all, Clear, Re-fetch, Purge selected — all behave as before (Manual acceptance flow #10). **This is the regression guard for the extraction.**
- `/review`: rows show; selecting rows reveals the bulk bar with a live count; kind toggles render; "Review selected →" navigates to the first clip with `?review=1`; "Apply drafts (selected)" posts and the table refreshes (covers flows #1–#3 partially; full apply behavior validated after Task 8).
Stop the server (use the `server-stop` skill) — verify the seat-release log lines.

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/row_select.js backend/app/static/review.js \
        backend/app/templates/cache_page.html backend/app/templates/pages/layout.html \
        backend/app/templates/pages/review.html
git commit -m "refactor(ui): extract shared row_select factory; add reviewSel()"
```

---

## Task 7: Rail "Review" entry + pending badge

**Files:**
- Create: `backend/app/templates/icons/_review.svg`
- Modify: `backend/app/templates/pages/_rail.html`

The badge count is fetched client-side (the rail already runs a small `<script>`), so no per-handler context injection is needed.

- [ ] **Step 1: Add the icon**

Create `backend/app/templates/icons/_review.svg` (a simple checklist glyph, matching the stroke style of the other rail icons — open `icons/_clips.svg` to copy the `viewBox`/`stroke` attributes exactly):

```svg
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
```

- [ ] **Step 2: Add the rail button + badge**

In `backend/app/templates/pages/_rail.html`, add after the Studio button (before Cache):

```jinja
<a class="rail-btn{% if _active == 'review' %} active{% endif %}"
   id="rail-review" href="/review" title="Review drafts">{% include "icons/_review.svg" %}
   <span class="rail-badge" id="review-badge" hidden></span></a>
```

And in the existing `<script>` block in `_rail.html`, append a fetch that fills the badge:

```javascript
    fetch('/api/review/pending/count')
      .then(r => r.ok ? r.json() : { count: 0 })
      .then(d => {
        var b = document.getElementById('review-badge');
        if (b && d.count > 0) { b.textContent = d.count; b.hidden = false; }
      })
      .catch(function(){ /* offline-safe: leave badge hidden */ });
```

- [ ] **Step 3: Add minimal badge CSS**

In `backend/app/static/app.css`, add a `.rail-badge` rule (small pill, top-right of the rail button). Match the visual language of existing rail/topbar pills (grep `.rail-btn` and `pill` in `app.css`). Example:

```css
.rail-btn { position: relative; }
.rail-badge {
  position: absolute; top: 2px; right: 2px;
  min-width: 16px; height: 16px; padding: 0 4px;
  border-radius: 8px; font-size: 10px; line-height: 16px;
  text-align: center; background: var(--accent, #c33); color: #fff;
}
```

- [ ] **Step 4: Manual verification**

Start server. Every page's rail shows a Review icon; with pending drafts present the badge shows the clip count; on `/review` the icon is active. Apply all drafts for a clip → reload → badge decrements. Stop server gracefully.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/icons/_review.svg backend/app/templates/pages/_rail.html backend/app/static/app.css
git commit -m "feat(review): rail Review entry + pending badge"
```

---

## Task 8: HITL review mode — per-item controls, action bar, queue navigation

**Files:**
- Modify: `backend/app/routes/pages/clips.py` (`clip_detail_page` accepts `review`)
- Modify: `backend/app/templates/pages/_anno_panels.html` (review-mode controls)
- Modify: `backend/app/templates/pages/clip_detail.html` (review action bar)
- Modify/extend: `backend/app/static/review.js` (queue nav + per-item decisions)
- Test: `tests/integration/test_routes_review.py` (review-mode flag renders controls)

- [ ] **Step 1: Write the failing test**

```python
def test_clip_detail_review_mode_renders_item_controls(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/clips/1?review=1")
        assert r.status_code == 200
        assert "review-item-toggle" in r.text   # per-item accept control
        assert "Apply &amp; next" in r.text or "Apply & next" in r.text
```

> If `/clips/1` requires the clip to be in cache and `_seed` doesn't populate `clip_cache`, extend `_seed` (or this test) to upsert a minimal `CanonicalClip` for clip 1 via `ctx.clip_cache_repo.upsert` so the detail page renders. Grep `clip_cache_repo.upsert` usage in existing tests for the exact constructor.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_routes_review.py -k review_mode -v`
Expected: FAIL (no `review-item-toggle` in output; handler ignores `review`).

- [ ] **Step 3: Pass `review_mode` + queue context from the handler**

In `backend/app/routes/pages/clips.py::clip_detail_page`, accept the query param and add context:

```python
@router.get("/clips/{clip_id}", response_class=HTMLResponse)
async def clip_detail_page(request: Request, clip_id: int, review: int | None = None):
    ...
    ctx_dict = clip_detail(clip, cache_status=cache_status)
    ...
    ctx_dict["draft"] = await _build_draft_for_clip(ctx, clip_id)
    ctx_dict["review_mode"] = bool(review)
    return templates.TemplateResponse(request, "pages/clip_detail.html", ctx_dict)
```

- [ ] **Step 4: Add per-item controls to `_anno_panels.html`**

Gate new controls on a `review_mode` flag (passed through the `{% include %}` — the draft include in `clip_detail.html` must forward it). For each marker/field article, when `review_mode` and the item has `item_id`, render an accept checkbox (pre-checked unless `decision == 'rejected'`) plus an edit affordance. Markers example (add inside the `<article class="marker">` header):

```jinja
{% if review_mode and m.item_id is defined %}
  <label class="review-item-toggle" onclick="event.stopPropagation()">
    <input type="checkbox" class="ri-accept" data-item-id="{{ m.item_id }}"
           {% if m.decision != 'rejected' %}checked{% endif %}>
    keep
  </label>
{% endif %}
```

Fields example (in `.field-row`):

```jinja
{% if review_mode and f.item_id is defined %}
  <label class="review-item-toggle">
    <input type="checkbox" class="ri-accept" data-item-id="{{ f.item_id }}"
           {% if f.decision != 'rejected' %}checked{% endif %}>
    keep
  </label>
  <input class="ri-edit" data-item-id="{{ f.item_id }}" value="{{ f.value }}">
{% endif %}
```

> `_anno_panels.html` is shared with the Published view and Studio; `review_mode` is undefined there, so `{% if review_mode ... %}` is false and nothing changes for them (regression-safe). Pass `review_mode` into the draft panels include in `_anno_draft.html` / `clip_detail.html` where `panels` is built.

- [ ] **Step 5: Add the review action bar to `clip_detail.html`**

Inside the `.anno-col` aside, render (only in review mode) a bar with progress + buttons, wired to `review.js`:

```jinja
{% if review_mode %}
<div class="review-actionbar" x-data="reviewQueue({{ clip.id }})">
  <span class="review-progress mono" x-text="progressLabel()"></span>
  <span class="grow"></span>
  <button type="button" class="ca-btn" @click="skip()">Skip</button>
  <button type="button" class="ca-btn ca-btn-live" @click="applyAndNext()">Apply &amp; next →</button>
</div>
{% endif %}
```

- [ ] **Step 6: Implement queue nav + per-item decisions in `review.js`**

Append to `backend/app/static/review.js`:

```javascript
function reviewQueue(clipId) {
  return {
    queue: [],
    init() {
      try { this.queue = JSON.parse(sessionStorage.getItem('catdv:reviewQueue') || '[]'); }
      catch (e) { this.queue = []; }
      // Persist each item decision as the reviewer toggles/edits.
      document.addEventListener('change', e => {
        if (e.target.classList.contains('ri-accept')) {
          this._decide(e.target.dataset.itemId,
                       e.target.checked ? 'accepted' : 'rejected');
        }
        if (e.target.classList.contains('ri-edit')) {
          this._decide(e.target.dataset.itemId, 'accepted', e.target.value);
        }
      });
    },
    _idx() { return this.queue.indexOf(clipId); },
    progressLabel() {
      const i = this._idx();
      return i >= 0 ? `${i + 1} / ${this.queue.length}` : '';
    },
    async _decide(itemId, decision, editedValue) {
      const body = { decision };
      if (editedValue !== undefined) body.edited_value = editedValue;
      await fetch(`/api/review/items/${itemId}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    },
    _next() {
      const i = this._idx();
      if (i >= 0 && i + 1 < this.queue.length) {
        location.href = `/clips/${this.queue[i + 1]}?review=1`;
      } else {
        location.href = '/review';
      }
    },
    skip() { this._next(); },
    async applyAndNext() {
      await fetch(`/api/review/clips/${clipId}/apply`, { method: 'POST' });
      this._next();
    },
  };
}
```

> Decisions are saved on each toggle/edit (`change` events), so `Apply & next` only needs to POST the per-clip `apply`. Pre-accepted default: the checkboxes render `checked`, but the DB rows are still `pending` until the reviewer touches them — so on a clip the reviewer doesn't touch at all, nothing is accepted and `apply` is a no-op (matches spec "Skip leaves a clip pending"). To honor "pre-accepted opt-out" literally (untouched = accepted), `applyAndNext()` first accepts all currently-checked items:

```javascript
    async applyAndNext() {
      const checked = Array.from(document.querySelectorAll('.ri-accept:checked'));
      await Promise.all(checked.map(cb => this._decide(cb.dataset.itemId, 'accepted')));
      await fetch(`/api/review/clips/${clipId}/apply`, { method: 'POST' });
      this._next();
    },
```

Use this second `applyAndNext()` (it makes the pre-checked state authoritative on apply). Remove the simpler version.

- [ ] **Step 7: Run the render test**

Run: `uv run pytest tests/integration/test_routes_review.py -k review_mode -v`
Expected: PASS.

- [ ] **Step 8: Full backend gate**

Run: `uv run pytest -q && uv run ruff check backend tests`
Expected: PASS.

- [ ] **Step 9: Manual acceptance (server required)**

Start server. Walk the spec's Manual acceptance flows #1–#9 end to end:
- #4 HITL: open a video clip from `/review`, untick one marker, edit a field, **Apply & next** advances and pushes accepted+edited (verify on the clip's Published tab).
- #5 progress + end-of-queue returns to `/review`; unticked marker not applied.
- #6 Skip leaves a clip pending.
- #7 image clip: Fields/Notes only, no broken player, Apply & next works.
- #8 offline: `/review` loads; apply enqueues; reconnect drains.
- #9 idempotency: re-apply → no duplicate markers.
Stop server gracefully; confirm seat-release log lines.

- [ ] **Step 10: Commit**

```bash
git add backend/app/routes/pages/clips.py \
        backend/app/templates/pages/_anno_panels.html \
        backend/app/templates/pages/_anno_draft.html \
        backend/app/templates/pages/clip_detail.html \
        backend/app/static/review.js \
        backend/app/static/app.css \
        tests/integration/test_routes_review.py
git commit -m "feat(review): HITL review mode — item controls, action bar, queue nav"
```

---

## Task 9: ADR + docs

**Files:**
- Create: `docs/adr/NNNN-draft-review-accept-ui.md` (next number after the current highest in `docs/adr/`)
- Modify: `docs/decisions.md` (index table)

Per CLAUDE.md, record the non-obvious design calls: (a) HITL reuses the clip page rather than a new screen; (b) extracting `row_select.js` and refactoring the Cache page onto it; (c) yolo = select-clips + kind-filter (not per-item bulk); (d) pre-accepted-opt-out default.

- [ ] **Step 1: Find the next ADR number**

Run: `ls docs/adr/ | sort | tail -3`
Use one higher than the highest `NNNN`.

- [ ] **Step 2: Write the ADR** (MADR-lite: `# NNNN. Title`, `**Date:** 2026-05-27`, `**Status:** Accepted`, `## Context`, `## Alternatives`, `## Decision`, `## Consequences`) covering the four calls above.

- [ ] **Step 3: Add the row to `docs/decisions.md`** index table.

- [ ] **Step 4: Commit**

```bash
git add docs/adr docs/decisions.md
git commit -m "docs(adr): draft review & accept UI decisions"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- `/review` page + rail badge → Tasks 5, 7. ✓
- HITL queue reusing clip page + Apply & next + pre-accepted opt-out → Tasks 4, 8. ✓
- Yolo select + kind filter → Tasks 3, 6. ✓
- Images (fields/notes only) → media filter in Task 5; marker section already gated on `clip.duration_secs` in `clip_detail.html`; verified in flow #7. ✓
- Offline-safe → no behavior change to apply pipeline; `/review` is DB-only; flow #8. ✓
- Extract-don't-clone (selection model + apply helper) → Tasks 2, 6, with Cache regression flow #10. ✓
- Counts semantics (`applied_at IS NULL`) → Task 1. ✓

**Type/name consistency:** `list_pending_clips`/`count_pending_clips` (Task 1) used identically in Task 3/5; `enqueue_apply_for_clip` (Task 2) called in Task 3; `rowSelect()`/`initSelection()`/`clearSel()` (Task 6) consumed by `cacheSel()` and `reviewSel()`; `reviewQueue()` + `ri-accept`/`ri-edit`/`review-item-toggle` consistent across Task 8 template + JS; `?review=1` → `review_mode` consistent across handler + templates.

**Open verifications flagged inline (do them while implementing, not blockers):** exact `ctx.clip_cache_repo` accessor + clip media-path attribute name (Task 5 Step 3); static-JS load location/prefix in `layout.html` (Task 6 Step 3); `_seed` may need a `clip_cache` upsert for `/clips/{id}` to render (Task 8 Step 1).
