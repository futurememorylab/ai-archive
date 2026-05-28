# Prompt Studio Output via Review Items (Option A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Prompt Studio render its Output card through the same `panels` dict pipeline the clip-detail page uses, by persisting normalized `review_items` rows for every studio run (and reading from those rows on render).

**Architecture:** Reuse the clip-detail data path end-to-end. At studio run finalization, run `target_map.expand` over the structured Gemini JSON exactly like the annotation path does, then bulk-insert the resulting `ReviewItem`s linked to `studio_run_id` (instead of `annotation_id`). On render, load review items by `studio_run_id`, pass them through `services/draft_view.build_draft_view`, and feed the resulting panels into the existing `_anno_panels.html` partial. Delete the bespoke `services/studio_panels.py` adapter and its bug-prone shape assumptions. Same change for the player overlay: derive scene ranges from review items, not raw `output_json`.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, Pydantic, Jinja2, Alpine.js, HTMX. SQLite as backing store. pytest + pytest-asyncio.

---

## File Structure

**Migration (new):**
- `backend/migrations/0014_review_items_studio_run.sql` — Rebuilds `review_items` with `annotation_id` nullable, adds `studio_run_id INTEGER REFERENCES studio_run(id)`, adds CHECK constraint, adds index. Standard SQLite table-recreate pattern.

**Models (modify):**
- `backend/app/models/annotation.py` — `ReviewItem.annotation_id` becomes `Optional[int]`; add `studio_run_id: Optional[int]`.

**Repositories (modify):**
- `backend/app/repositories/review_items.py` — `bulk_insert` writes both columns; add `list_by_studio_run(conn, studio_run_id)`; `_row` reads both columns from a widened SELECT.

**Services (modify + delete):**
- `backend/app/services/target_map.py` — `expand()` accepts an `owner` discriminator (`annotation_id=…` xor `studio_run_id=…`) instead of always `annotation_id`.
- `backend/app/services/annotator.py` — `_finalize_studio` calls `target_map.expand` with `studio_run_id`, persists the items via `review_items_repo.bulk_insert`.
- `backend/app/services/draft_view.py` — `build_draft_view` adds `fps` + `big_notes` to its output dict (so studio render gets all the keys `_anno_panels.html` reads).
- **Delete:** `backend/app/services/studio_panels.py` (replaced by `build_draft_view`).

**Routes (modify):**
- `backend/app/routes/pages/studio.py` — `_studio_run` route: load review items by `studio_run_id`, call `build_draft_view`. `_build_overlay_row`: load review items kind='marker' by `studio_run_id`, return flat `{in_secs, out_secs, name}` dicts. `_studio_prompt_card`: load review items + version, hand to template via the same `panels` key.

**Templates (modify):**
- `backend/app/templates/pages/_studio_run_output.html` — references `panels` / `version` / `run` unchanged; just becomes the rendered output of `build_draft_view` shape.
- `backend/app/templates/pages/_studio_prompt_card.html` — no structural change; embedded `_anno_panels.html` now has the Alpine state it needs (provided by `studioPromptCard`).

**Frontend (modify):**
- `backend/app/static/studio.js` — `studioPromptCard` Alpine data: add `tab: 'markers'`, `historyLoaded: false`, `historyHtml: ''`, stub `loadHistory()` (Studio has no per-run history view in v1, so it's a noop placeholder that hides the History tab).

**Tests (modify + delete + add):**
- **Delete:** `tests/unit/test_studio_panels_adapter.py` (adapter is gone).
- Modify: `tests/unit/test_annotator_studio_branch.py` — assert `review_items.bulk_insert` IS called for studio kind, with items linked by `studio_run_id`. Use the **real** Gemini shape (`{"in": {"secs": …}, "out": {"secs": …}}`) in mocked output.
- Modify: `tests/integration/test_studio_run_output_reuse.py` — fixtures use nested-secs shape; assert rendered HTML contains marker articles.
- Modify: `tests/integration/test_studio_player_overlay.py` — fixtures use nested-secs shape (the seeding goes via review_items, not output_json).
- Add: `tests/unit/test_review_items_repo_studio.py` — repo round-trips items with `studio_run_id`.
- Add: `tests/integration/test_studio_review_items_e2e.py` — end-to-end: nested Gemini JSON → expand → review_items by studio_run_id → `/studio/_run` returns rendered panels with markers and fields.

---

## Task 1: Migration — review_items.studio_run_id + nullable annotation_id

**Files:**
- Create: `backend/migrations/0014_review_items_studio_run.sql`
- Test: `tests/unit/test_migration_0014.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/unit/test_migration_0014.py`:

```python
"""0014 migration: review_items gets studio_run_id + nullable annotation_id."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.mark.asyncio
async def test_0014_adds_studio_run_id_column(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, Path("backend/migrations"))

        cur = await conn.execute("PRAGMA table_info(review_items)")
        rows = await cur.fetchall()
        col_by_name = {r[1]: r for r in rows}

        assert "studio_run_id" in col_by_name, "studio_run_id column missing"
        # annotation_id must allow NULL now (notnull flag = 0)
        assert col_by_name["annotation_id"][3] == 0, "annotation_id must be nullable"


@pytest.mark.asyncio
async def test_0014_check_constraint_enforces_exactly_one_owner(tmp_path: Path):
    """A row must have exactly one of (annotation_id, studio_run_id) set."""
    import aiosqlite

    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, Path("backend/migrations"))

        # Seed prerequisite rows to satisfy FKs in subsequent insert tests.
        await conn.execute(
            "INSERT INTO prompts(id, name, description, archived, created_at, updated_at) "
            "VALUES (1, 'p', NULL, 0, '2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO prompt_versions(id, prompt_id, version_num, state, "
            "body, target_map, output_schema, model, created_at, updated_at) "
            "VALUES (1, 1, 1, 'draft', 'x', '{}', '{}', 'm', "
            "'2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO annotations(id, catdv_clip_id, catdv_clip_name, "
            "prompt_version_id, model, prompt_used, raw_response, "
            "structured_output, clip_snapshot, created_at) "
            "VALUES (1, 1, 'c', 1, 'm', 'p', '{}', '{}', '{}', '2026-05-28T00:00:00Z')"
        )
        await conn.execute(
            "INSERT INTO studio_run(id, prompt_version_id, clip_id, status) "
            "VALUES (1, 1, 1, 'ok')"
        )
        await conn.commit()

        # Both NULL → reject
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO review_items(annotation_id, studio_run_id, "
                "catdv_clip_id, kind, proposed_value, decision) "
                "VALUES (NULL, NULL, 1, 'marker', '{}', 'pending')"
            )
            await conn.commit()
        await conn.rollback()

        # Both set → reject
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO review_items(annotation_id, studio_run_id, "
                "catdv_clip_id, kind, proposed_value, decision) "
                "VALUES (1, 1, 1, 'marker', '{}', 'pending')"
            )
            await conn.commit()
        await conn.rollback()

        # annotation_id only → ok
        await conn.execute(
            "INSERT INTO review_items(annotation_id, studio_run_id, "
            "catdv_clip_id, kind, proposed_value, decision) "
            "VALUES (1, NULL, 1, 'marker', '{}', 'pending')"
        )
        # studio_run_id only → ok
        await conn.execute(
            "INSERT INTO review_items(annotation_id, studio_run_id, "
            "catdv_clip_id, kind, proposed_value, decision) "
            "VALUES (NULL, 1, 1, 'marker', '{}', 'pending')"
        )
        await conn.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0014.py -v`
Expected: FAIL — column does not exist yet.

- [ ] **Step 3: Write the migration**

Create `backend/migrations/0014_review_items_studio_run.sql`:

```sql
-- 0014: review_items can belong to EITHER an annotation (CatDV-bound) OR a
-- studio_run (Studio iteration, never written to CatDV).
-- SQLite can't ALTER a NOT NULL constraint, so rebuild the table.

CREATE TABLE review_items_new (
  id                 INTEGER PRIMARY KEY,
  annotation_id      INTEGER REFERENCES annotations(id),
  studio_run_id      INTEGER REFERENCES studio_run(id),
  catdv_clip_id      INTEGER NOT NULL,
  kind               TEXT    NOT NULL,
  target_identifier  TEXT,
  proposed_value     TEXT    NOT NULL,
  edited_value       TEXT,
  decision           TEXT    NOT NULL,
  decided_at         TEXT,
  applied_at         TEXT,
  CHECK ((annotation_id IS NOT NULL AND studio_run_id IS NULL)
      OR (annotation_id IS NULL AND studio_run_id IS NOT NULL))
);

INSERT INTO review_items_new
  (id, annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier,
   proposed_value, edited_value, decision, decided_at, applied_at)
SELECT
   id, annotation_id, NULL,           catdv_clip_id, kind, target_identifier,
   proposed_value, edited_value, decision, decided_at, applied_at
FROM review_items;

DROP TABLE review_items;
ALTER TABLE review_items_new RENAME TO review_items;

CREATE INDEX idx_review_items_annotation ON review_items(annotation_id);
CREATE INDEX idx_review_items_studio_run ON review_items(studio_run_id);
CREATE INDEX idx_review_items_clip       ON review_items(catdv_clip_id);
CREATE INDEX idx_review_items_decision   ON review_items(decision);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0014.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run full unit suite to confirm no regressions on existing migrations**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: previously-passing tests still pass (some studio-related tests will start failing — those are addressed in later tasks).

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0014_review_items_studio_run.sql tests/unit/test_migration_0014.py
git commit -m "studio: migration 0014 — review_items.studio_run_id + nullable annotation_id"
```

---

## Task 2: ReviewItem model — optional annotation_id, add studio_run_id

**Files:**
- Modify: `backend/app/models/annotation.py`

- [ ] **Step 1: Edit the model**

Edit `backend/app/models/annotation.py` lines 25-34 — replace the existing `ReviewItem` class with:

```python
class ReviewItem(BaseModel):
    id: int | None = None
    annotation_id: int | None = None
    studio_run_id: int | None = None
    catdv_clip_id: int
    kind: Literal["marker", "note", "field"]
    target_identifier: str | None = None
    proposed_value: dict[str, Any] | list[Any] | str | int | float | bool | None
    edited_value: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    decision: Literal["pending", "accepted", "rejected"] = "pending"
    applied_at: str | None = None
```

- [ ] **Step 2: Sanity-check that imports still resolve**

Run: `.venv/bin/python -c "from backend.app.models.annotation import ReviewItem; ReviewItem(catdv_clip_id=1, kind='marker', proposed_value={})"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/annotation.py
git commit -m "studio: ReviewItem gets optional studio_run_id; annotation_id now optional"
```

---

## Task 3: ReviewItemsRepo — write/read studio_run_id

**Files:**
- Modify: `backend/app/repositories/review_items.py`
- Test: `tests/unit/test_review_items_repo_studio.py`

- [ ] **Step 1: Write the failing repo test**

Create `tests/unit/test_review_items_repo_studio.py`:

```python
"""ReviewItemsRepo — studio_run_id round-trip and list_by_studio_run."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.models.annotation import ReviewItem
from backend.app.repositories.review_items import ReviewItemsRepo


async def _seed(conn) -> None:
    await conn.execute(
        "INSERT INTO prompts(id, name, description, archived, created_at, updated_at) "
        "VALUES (1, 'p', NULL, 0, '2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')"
    )
    await conn.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, "
        "body, target_map, output_schema, model, created_at, updated_at) "
        "VALUES (1, 1, 1, 'draft', 'x', '{}', '{}', 'm', "
        "'2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')"
    )
    await conn.execute(
        "INSERT INTO studio_run(id, prompt_version_id, clip_id, status) "
        "VALUES (1, 1, 42, 'ok')"
    )
    await conn.execute(
        "INSERT INTO studio_run(id, prompt_version_id, clip_id, status) "
        "VALUES (2, 1, 99, 'ok')"
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_bulk_insert_with_studio_run_id_persists(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, Path("backend/migrations"))
        await _seed(conn)
        repo = ReviewItemsRepo()
        items = [
            ReviewItem(
                studio_run_id=1, catdv_clip_id=42, kind="marker",
                proposed_value={"in": {"secs": 1.0}, "out": {"secs": 2.0}, "name": "a"},
            ),
            ReviewItem(
                studio_run_id=1, catdv_clip_id=42, kind="field",
                target_identifier="pragafilm.dekada",
                proposed_value={"value": "30.léta"},
            ),
        ]
        inserted = await repo.bulk_insert(conn, items)
        assert all(it.id is not None for it in inserted)
        assert all(it.studio_run_id == 1 and it.annotation_id is None for it in inserted)


@pytest.mark.asyncio
async def test_list_by_studio_run_filters(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, Path("backend/migrations"))
        await _seed(conn)
        repo = ReviewItemsRepo()
        await repo.bulk_insert(conn, [
            ReviewItem(studio_run_id=1, catdv_clip_id=42, kind="marker",
                       proposed_value={"name": "r1m"}),
            ReviewItem(studio_run_id=2, catdv_clip_id=99, kind="marker",
                       proposed_value={"name": "r2m"}),
        ])
        run1_items = await repo.list_by_studio_run(conn, 1)
        assert len(run1_items) == 1
        assert run1_items[0].proposed_value == {"name": "r1m"}
        run2_items = await repo.list_by_studio_run(conn, 2)
        assert len(run2_items) == 1
        assert run2_items[0].proposed_value == {"name": "r2m"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_review_items_repo_studio.py -v`
Expected: FAIL — repo lacks `studio_run_id` handling.

- [ ] **Step 3: Update the repo**

Edit `backend/app/repositories/review_items.py`:

Replace `bulk_insert` (lines 28-52) with:

```python
    async def bulk_insert(
        self, conn: aiosqlite.Connection, items: list[ReviewItem]
    ) -> list[ReviewItem]:
        inserted: list[ReviewItem] = []
        for it in items:
            cur = await conn.execute(
                """
                INSERT INTO review_items
                  (annotation_id, studio_run_id, catdv_clip_id, kind,
                   target_identifier, proposed_value, edited_value, decision)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending')
                """,
                (
                    it.annotation_id,
                    it.studio_run_id,
                    it.catdv_clip_id,
                    it.kind,
                    it.target_identifier,
                    json.dumps(it.proposed_value, ensure_ascii=False, default=_json_default),
                ),
            )
            it.id = cur.lastrowid
            it.decision = "pending"
            inserted.append(it)
        await conn.commit()
        return inserted
```

Replace `get` (lines 54-66) — widen the SELECT:

```python
    async def get(self, conn: aiosqlite.Connection, item_id: int) -> ReviewItem:
        cur = await conn.execute(
            """
            SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                   target_identifier, proposed_value, edited_value, decision, applied_at
            FROM review_items WHERE id = ?
            """,
            (item_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"review_item {item_id} not found")
        return self._row(row)
```

Replace `list_by_clip` (lines 68-91) — widen the SELECT in both branches:

```python
    async def list_by_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, decision: str | None = None
    ) -> list[ReviewItem]:
        if decision is not None:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                       target_identifier, proposed_value, edited_value, decision, applied_at
                FROM review_items WHERE catdv_clip_id = ? AND decision = ?
                ORDER BY id
                """,
                (clip_id, decision),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                       target_identifier, proposed_value, edited_value, decision, applied_at
                FROM review_items WHERE catdv_clip_id = ?
                ORDER BY id
                """,
                (clip_id,),
            )
        return [self._row(r) for r in await cur.fetchall()]
```

Add the new method right after `list_by_clip`:

```python
    async def list_by_studio_run(
        self, conn: aiosqlite.Connection, studio_run_id: int
    ) -> list[ReviewItem]:
        cur = await conn.execute(
            """
            SELECT id, annotation_id, studio_run_id, catdv_clip_id, kind,
                   target_identifier, proposed_value, edited_value, decision, applied_at
            FROM review_items WHERE studio_run_id = ?
            ORDER BY id
            """,
            (studio_run_id,),
        )
        return [self._row(r) for r in await cur.fetchall()]
```

Replace `_row` (lines 133-145) to read the new column ordering:

```python
    @staticmethod
    def _row(row) -> ReviewItem:
        return ReviewItem(
            id=row[0],
            annotation_id=row[1],
            studio_run_id=row[2],
            catdv_clip_id=row[3],
            kind=row[4],
            target_identifier=row[5],
            proposed_value=json.loads(row[6]),
            edited_value=json.loads(row[7]) if row[7] is not None else None,
            decision=row[8],
            applied_at=row[9] if len(row) > 9 else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_review_items_repo_studio.py -v`
Expected: PASS.

Also run the existing repo tests to confirm clip-detail still works:

Run: `.venv/bin/python -m pytest tests/unit/ -k review_items -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/review_items.py tests/unit/test_review_items_repo_studio.py
git commit -m "studio: ReviewItemsRepo persists studio_run_id; list_by_studio_run helper"
```

---

## Task 4: `target_map.expand` accepts studio_run_id

**Files:**
- Modify: `backend/app/services/target_map.py`
- Test: `tests/unit/test_target_map_studio_run_id.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_target_map_studio_run_id.py`:

```python
"""target_map.expand: items inherit either annotation_id OR studio_run_id."""

import pytest

from backend.app.models.prompt import TargetMap
from backend.app.services.target_map import expand


def _tmap() -> TargetMap:
    return TargetMap.model_validate({
        "scenes": {"kind": "markers"},
        "summary_cz": {"kind": "note", "target": "pragafilm.popis.materialu"},
        "decade": {"kind": "field", "identifier": "pragafilm.dekada"},
    })


def test_expand_with_studio_run_id_sets_studio_run_id_only():
    structured = {
        "scenes": [{"in": {"secs": 1.0}, "out": {"secs": 2.0}, "name": "a"}],
        "summary_cz": "krátký",
        "decade": "30.léta",
    }
    items = expand(
        structured, _tmap(),
        studio_run_id=7, catdv_clip_id=42, clip_duration_secs=10.0,
    )
    assert items, "expected at least one item"
    for it in items:
        assert it.studio_run_id == 7
        assert it.annotation_id is None


def test_expand_with_annotation_id_sets_annotation_id_only():
    structured = {"scenes": [{"in": {"secs": 1.0}, "out": {"secs": 2.0}, "name": "a"}]}
    items = expand(
        structured, _tmap(),
        annotation_id=3, catdv_clip_id=42, clip_duration_secs=10.0,
    )
    for it in items:
        assert it.annotation_id == 3
        assert it.studio_run_id is None


def test_expand_rejects_both_owners():
    with pytest.raises(ValueError):
        expand({}, _tmap(), annotation_id=1, studio_run_id=1, catdv_clip_id=1)


def test_expand_rejects_neither_owner():
    with pytest.raises(ValueError):
        expand({}, _tmap(), catdv_clip_id=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_target_map_studio_run_id.py -v`
Expected: FAIL — `expand` doesn't accept `studio_run_id` yet.

- [ ] **Step 3: Update `target_map.expand` and `_expand_one`**

Edit `backend/app/services/target_map.py`. Replace the `expand` function (lines 43-63) and `_expand_one` (lines 66-108):

```python
def expand(
    structured: dict[str, Any],
    target_map: TargetMap,
    *,
    catdv_clip_id: int,
    annotation_id: int | None = None,
    studio_run_id: int | None = None,
    clip_duration_secs: float | None = None,
) -> list[ReviewItem]:
    """Walk target_map; emit one ReviewItem per concrete change.

    Exactly one of `annotation_id` or `studio_run_id` must be supplied.
    `clip_duration_secs`, if supplied, is used to drop or clamp marker
    timestamps that fall outside the clip — Gemini occasionally hallucinates
    content past the end on multi-minute video.
    """
    if (annotation_id is None) == (studio_run_id is None):
        raise ValueError(
            "expand() requires exactly one of annotation_id or studio_run_id"
        )
    items: list[ReviewItem] = []
    for key, entry in target_map.fields.items():
        if key not in structured or structured[key] is None:
            continue
        value = structured[key]
        items.extend(_expand_one(
            entry, value,
            annotation_id=annotation_id,
            studio_run_id=studio_run_id,
            catdv_clip_id=catdv_clip_id,
            clip_duration_secs=clip_duration_secs,
        ))
    return items


def _expand_one(
    entry: TargetEntry,
    value: Any,
    *,
    annotation_id: int | None,
    studio_run_id: int | None,
    catdv_clip_id: int,
    clip_duration_secs: float | None = None,
) -> list[ReviewItem]:
    if entry.kind == "markers":
        if not isinstance(value, list):
            return []
        markers = (
            _filter_markers(value, clip_duration_secs) if clip_duration_secs is not None else value
        )
        return [
            ReviewItem(
                annotation_id=annotation_id,
                studio_run_id=studio_run_id,
                catdv_clip_id=catdv_clip_id,
                kind="marker",
                proposed_value=m,
            )
            for m in markers
        ]
    if entry.kind == "field":
        return [
            ReviewItem(
                annotation_id=annotation_id,
                studio_run_id=studio_run_id,
                catdv_clip_id=catdv_clip_id,
                kind="field",
                target_identifier=entry.identifier,
                proposed_value=value,
            )
        ]
    if entry.kind == "note":
        return [
            ReviewItem(
                annotation_id=annotation_id,
                studio_run_id=studio_run_id,
                catdv_clip_id=catdv_clip_id,
                kind="note",
                target_identifier=entry.target,
                proposed_value=value,
            )
        ]
    return []
```

- [ ] **Step 4: Update the one existing caller**

The annotator at `backend/app/services/annotator.py:288-294` calls `expand` with positional `annotation_id`. Update to use kw-only:

Edit `backend/app/services/annotator.py` — find the existing call in `_finalize_annotation`:

```python
        review = expand(
            structured,
            version.target_map,
            annotation_id=annotation_id,
            catdv_clip_id=item.catdv_clip_id,
            clip_duration_secs=duration_secs or None,
        )
```

Already kw-only — no change needed at this site. Verify by re-reading the file around line 288.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_target_map_studio_run_id.py tests/unit/test_target_map.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/target_map.py tests/unit/test_target_map_studio_run_id.py
git commit -m "studio: target_map.expand accepts annotation_id xor studio_run_id"
```

---

## Task 5: Annotator `_finalize_studio` persists review_items

**Files:**
- Modify: `backend/app/services/annotator.py`
- Modify: `tests/unit/test_annotator_studio_branch.py`

- [ ] **Step 1: Update the existing test to expect review_items are inserted**

Edit `tests/unit/test_annotator_studio_branch.py`:

Replace the docstring header (lines 1-7) with:

```python
"""Annotator service — studio path persists to studio_run AND review_items.

We assert that for a job with kind='studio':
  * No annotation row is inserted (annotations_repo.insert not called).
  * The matching studio_run row transitions to status='ok' with output_json.
  * review_items ARE inserted, linked by studio_run_id (so the UI can
    render markers/fields/notes through the same panels pipeline the
    clip-detail page uses).
"""
```

In `test_studio_kind_persists_run_skips_catdv_write` (around line 100), change the mocked Gemini output to use the real nested-secs shape:

```python
    gemini.annotate = MagicMock(return_value={
        "text": json.dumps({
            "scenes": [
                {"name": "s1",
                 "in":  {"secs": 0.0},
                 "out": {"secs": 5.0}},
            ],
        }),
        "raw": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}},
    })
```

And replace the existing assertions (around lines 121-128) with:

```python
    # Assertion: CatDV-side annotations write was NOT called
    annotations.insert.assert_not_called()

    # review_items WERE inserted — once for the marker. Verify the items
    # carry studio_run_id (not annotation_id) so the studio render path
    # can find them.
    assert review_items.bulk_insert.await_count == 1
    inserted_items = review_items.bulk_insert.await_args.args[1]
    assert len(inserted_items) == 1
    assert inserted_items[0].kind == "marker"
    assert inserted_items[0].studio_run_id == run_id
    assert inserted_items[0].annotation_id is None

    # studio_run completed ok with output
    run = await runs.get(db, run_id)
    assert run.status == "ok"
    assert run.output_json == {
        "scenes": [{"name": "s1", "in": {"secs": 0.0}, "out": {"secs": 5.0}}],
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_annotator_studio_branch.py::test_studio_kind_persists_run_skips_catdv_write -v`
Expected: FAIL — `review_items.bulk_insert` has 0 awaits.

- [ ] **Step 3: Wire review_items insertion into `_finalize_studio`**

Edit `backend/app/services/annotator.py`. Replace `_process_item` (lines 140-219) — only the studio branch and its call site need to change. Update the studio call to pass version, duration_secs, review_items_repo:

```python
async def _process_item(
    *,
    db, item, version, kind,
    archive, proxy_resolver, ai_store, gemini,
    annotations_repo, review_items_repo,
    jobs_repo, studio_runs_repo: StudioRunsRepo,
    event_bus, topic,
) -> None:
    clip_key = ("catdv", str(item.catdv_clip_id))

    upload = await ai_store.status(clip_key)

    if upload is None:
        await jobs_repo.update_item_status(db, item.id, "resolving")
        await event_bus.publish(topic, {"item_id": item.id, "status": "resolving"})
        try:
            local_path: Path = await proxy_resolver.path_for_clip_id(item.catdv_clip_id)
        except ProxyNotFound:
            msg = (
                f"clip {item.catdv_clip_id} is not locally cached and not in "
                f"AI store — cache the clip on /clips first, or reconnect to CatDV"
            )
            await jobs_repo.update_item_status(db, item.id, "error", error=msg)
            await event_bus.publish(
                topic, {"item_id": item.id, "status": "error", "error": msg}
            )
            if kind == "studio":
                run_id = await studio_runs_repo.find_latest_id_for_job_clip(
                    db, job_id=item.job_id, clip_id=item.catdv_clip_id
                )
                if run_id is not None:
                    await studio_runs_repo.complete_error(db, run_id, error=msg)
            return

        await jobs_repo.update_item_status(db, item.id, "uploading")
        await event_bus.publish(topic, {"item_id": item.id, "status": "uploading"})
        mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
        upload = await ai_store.ensure_uploaded(clip_key, local_path, mime)

    file_ref = await ai_store.reference_for_gemini(upload)

    canonical = await archive.get_clip(str(item.catdv_clip_id))
    clip_snapshot: dict[str, Any] = dict(canonical.provider_data)
    duration_secs = float(canonical.duration_secs or 0.0)

    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    rendered_body = _render_prompt(version.body, duration_secs=duration_secs)
    t0 = time.monotonic()
    result = gemini.annotate(
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
    )
    elapsed_s = time.monotonic() - t0

    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    if kind == "studio":
        await _finalize_studio(
            db, item, version, structured, result, elapsed_s, duration_secs,
            studio_runs_repo, review_items_repo, jobs_repo, event_bus, topic,
        )
    else:
        await _finalize_annotation(
            db, item, version, structured, result, rendered_body,
            clip_snapshot, duration_secs,
            annotations_repo, review_items_repo, jobs_repo,
            event_bus, topic,
        )
```

Replace `_finalize_studio` (lines 222-261) with:

```python
async def _finalize_studio(
    db, item, version, structured, result, elapsed_s, duration_secs,
    studio_runs_repo: StudioRunsRepo, review_items_repo, jobs_repo,
    event_bus, topic,
) -> None:
    """Studio path: persist to studio_run + review_items (linked by
    studio_run_id), skip annotations. The studio UI renders from
    review_items through the same panels pipeline clip_detail uses."""
    run_id = await studio_runs_repo.find_latest_id_for_job_clip(
        db, job_id=item.job_id, clip_id=item.catdv_clip_id
    )
    if run_id is None:
        await jobs_repo.update_item_status(db, item.id, "error", error="studio_run not found")
        await event_bus.publish(
            topic, {"item_id": item.id, "status": "error", "error": "studio_run not found"}
        )
        return

    usage = (result.get("raw") or {}).get("usageMetadata") or {}
    tokens_in = int(usage.get("promptTokenCount", 0) or 0)
    tokens_out = int(usage.get("candidatesTokenCount", 0) or 0)
    cost_usd = 0.0  # cost calc lives elsewhere; not implemented in v1

    if structured is None:
        await studio_runs_repo.complete_error(db, run_id, error="model returned non-JSON or empty")
        await jobs_repo.update_item_status(db, item.id, "error", error="non-JSON output")
        await event_bus.publish(
            topic, {"item_id": item.id, "status": "error", "error": "non-JSON output"}
        )
        return

    await studio_runs_repo.complete_ok(
        db, run_id,
        output_json=structured,
        duration_s=elapsed_s,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )

    review = expand(
        structured,
        version.target_map,
        studio_run_id=run_id,
        catdv_clip_id=item.catdv_clip_id,
        clip_duration_secs=duration_secs or None,
    )
    if review:
        await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "studio_run_id": run_id}
    )
```

Also: at the top of `backend/app/services/annotator.py`, the import for `expand` already exists at line 28 (`from backend.app.services.target_map import expand`). Verify it's there; if not, add it.

- [ ] **Step 4: Run the unit test**

Run: `.venv/bin/python -m pytest tests/unit/test_annotator_studio_branch.py -v`
Expected: PASS on all three test cases.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/annotator.py tests/unit/test_annotator_studio_branch.py
git commit -m "studio: _finalize_studio also persists review_items via target_map.expand"
```

---

## Task 6: `build_draft_view` adds fps + big_notes; remove studio_panels.py

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Delete: `backend/app/services/studio_panels.py`
- Delete: `tests/unit/test_studio_panels_adapter.py`

- [ ] **Step 1: Update `build_draft_view` to include fps and big_notes**

Edit `backend/app/services/draft_view.py`. Replace `build_draft_view` (lines 48-88):

```python
def build_draft_view(
    annotation: Annotation | None,
    review_items: list[ReviewItem],
    *,
    prompt_name: str | None = None,
    version_num: int | None = None,
    created_at: str | None = None,
    fps: float = 25.0,
) -> dict[str, Any]:
    """Returns the `panels` dict consumed by templates/pages/_anno_panels.html.

    Used by both the clip-detail draft view and the studio output card.
    Studio callers pass `annotation=None` and `review_items` loaded by
    `studio_run_id`; the dict shape is identical."""
    if annotation is None and not review_items:
        return {
            "has_draft": False,
            "annotation_id": None,
            "created_at": created_at,
            "prompt_name": prompt_name,
            "version_num": version_num,
            "model": None,
            "markers": [],
            "fields": [],
            "notes": None,
            "big_notes": None,
            "fps": fps,
        }
    markers = [_marker_from_review(it) for it in review_items if it.kind == "marker"]
    markers.sort(key=lambda m: m["in_secs"])
    fields = [_field_from_review(it) for it in review_items if it.kind == "field"]
    fields.sort(key=lambda f: f["identifier"])
    note_texts = [
        _fix(str(it.proposed_value)) or ""
        for it in review_items
        if it.kind == "note" and it.proposed_value is not None
    ]
    notes = "\n\n".join(t for t in note_texts if t) or None
    return {
        "has_draft": True,
        "annotation_id": annotation.id if annotation else None,
        "created_at": created_at,
        "prompt_name": prompt_name,
        "version_num": version_num,
        "model": annotation.model if annotation else None,
        "markers": markers,
        "fields": fields,
        "notes": notes,
        "big_notes": None,
        "fps": fps,
    }
```

(The `_field_from_review` function on line 32-45 will be picked up unchanged. Note: for studio runs, the schema wraps field values as `{"value": ..., "evidence_secs": [...]}`. Update `_field_from_review` to unwrap:)

Replace `_field_from_review` (lines 32-45):

```python
def _field_from_review(item: ReviewItem) -> dict[str, Any]:
    identifier = item.target_identifier or ""
    value = item.proposed_value
    # Studio schemas wrap field values as {"value": ..., "evidence_secs": [...]}.
    # Clip-detail annotations historically pass raw values. Unwrap when present.
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, list):
        value_str = ", ".join(_fix(str(v)) or "" for v in value)
    elif value is None:
        value_str = ""
    else:
        value_str = _fix(str(value)) or ""
    return {
        "identifier": identifier,
        "name": identifier.split(".")[-1],
        "value": value_str,
    }
```

Same for notes — also wrap-aware. Replace the `note_texts` list comprehension inside `build_draft_view` with:

```python
    note_texts: list[str] = []
    for it in review_items:
        if it.kind != "note" or it.proposed_value is None:
            continue
        raw = it.proposed_value
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        text = _fix(str(raw)) or ""
        note_texts.append(text)
```

- [ ] **Step 2: Run existing draft_view tests**

Run: `.venv/bin/python -m pytest tests/ -k draft_view -v`
Expected: existing tests pass (build_draft_view's annotation path is backwards-compatible).

- [ ] **Step 3: Delete the obsolete adapter and its tests**

```bash
rm backend/app/services/studio_panels.py
rm tests/unit/test_studio_panels_adapter.py
```

- [ ] **Step 4: Grep for and remove remaining studio_panels imports**

Run: `grep -rn "studio_panels\|panels_from_studio_run" backend/ tests/`

Edit each hit to remove the import and call site. Expected hits (from earlier exploration):
- `backend/app/routes/pages/studio.py` lines 287, 311, 336, 356 — these all get replaced in Task 7.

For now, leave them — Task 7 finishes the job.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py backend/app/services/studio_panels.py tests/unit/test_studio_panels_adapter.py
git commit -m "studio: build_draft_view gains fps/big_notes; remove studio_panels adapter"
```

---

## Task 7: Studio routes render via build_draft_view

**Files:**
- Modify: `backend/app/routes/pages/studio.py`

- [ ] **Step 1: Rewrite `_build_overlay_row` to use review_items**

Edit `backend/app/routes/pages/studio.py`. Replace `_build_overlay_row` (lines 182-205):

```python
async def _build_overlay_row(
    ctx, clip_id: int, version_id: int, *, cls: str
) -> dict | None:
    """Resolve scenes + label for one version on one clip, sourced from
    review_items (not raw output_json) so the timeline overlay matches
    the Output card."""
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError:
        return None
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=version_id, clip_id=clip_id
    )
    ranges: list[dict] = []
    if run is not None and run.id is not None:
        items = await ctx.review_items_repo.list_by_studio_run(ctx.db, run.id)
        for it in items:
            if it.kind != "marker" or not isinstance(it.proposed_value, dict):
                continue
            pv = it.proposed_value
            in_part = pv.get("in") or {}
            out_part = pv.get("out") or {}
            in_secs = in_part.get("secs") if isinstance(in_part, dict) else None
            out_secs = out_part.get("secs") if isinstance(out_part, dict) else None
            if in_secs is None:
                continue
            ranges.append({
                "in_secs": float(in_secs),
                "out_secs": float(out_secs) if out_secs is not None else None,
                "name": pv.get("name") or "",
            })
    return {
        "key": f"v{v.version_num}",
        "ranges": ranges,
        "cls": cls,
        "alpine_list": None,
        "x_show": None,
    }
```

- [ ] **Step 2: Rewrite `_studio_prompt_card` to load via review_items**

Replace `_studio_prompt_card` (lines 273-327):

```python
@router.get("/studio/_prompt_card", response_class=HTMLResponse)
async def _studio_prompt_card(
    request: Request,
    side: Literal["cur", "cmp"],
    prompt_version_id: int,
    clip_id: int | None = None,
):
    """Renders one prompt-card. Used by HTMX swaps from the version chip
    and by the initial cmp materialization.

    404 on missing version. With clip_id, the Output tab pre-loads the
    run partial; without, the Output tab shows the focus-a-clip
    empty-state.
    """
    from backend.app.services.draft_view import build_draft_view

    ctx = get_ctx(request)
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError as exc:
        raise HTTPException(404, f"version {prompt_version_id} not found") from exc

    _, versions = await ctx.prompts_repo.get_with_versions(ctx.db, version.prompt_id)

    run = None
    panels: dict | None = None
    fps = 25.0
    if clip_id is not None:
        run = await ctx.studio_runs_repo.latest_for_pair(
            ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
        )
        if ctx.archive:
            try:
                clip = await ctx.archive.get_clip(str(clip_id))
                fps = float(clip.fps or 25.0)
            except Exception:  # noqa: BLE001
                pass
        items = (
            await ctx.review_items_repo.list_by_studio_run(ctx.db, run.id)
            if run is not None and run.id is not None
            else []
        )
        panels = build_draft_view(
            annotation=None,
            review_items=items,
            prompt_name=None,
            version_num=version.version_num,
            created_at=run.finished_at if run else None,
            fps=fps,
        )

    version_dict = version.model_dump()
    return templates.TemplateResponse(
        request,
        "pages/_studio_prompt_card.html",
        {
            "side": side,
            "active_version": version_dict,
            "version": version_dict,
            "versions": [v.model_dump() for v in versions],
            "clip_id": clip_id,
            "run": run.model_dump() if run else None,
            "panels": panels,
            "clip": {"fps": fps},
        },
    )
```

- [ ] **Step 3: Rewrite `_studio_run` to load via review_items**

Replace `_studio_run` (lines 330-367):

```python
@router.get("/studio/_run", response_class=HTMLResponse)
async def _studio_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
):
    from backend.app.services.draft_view import build_draft_view

    ctx = get_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError:
        version = None

    fps = 25.0
    if ctx.archive:
        try:
            clip = await ctx.archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
        except Exception:  # noqa: BLE001
            pass

    items = (
        await ctx.review_items_repo.list_by_studio_run(ctx.db, run.id)
        if run is not None and run.id is not None
        else []
    )
    panels = build_draft_view(
        annotation=None,
        review_items=items,
        version_num=version.version_num if version else None,
        created_at=run.finished_at if run else None,
        fps=fps,
    )

    return templates.TemplateResponse(
        request,
        "pages/_studio_run_output.html",
        {
            "run": run.model_dump() if run else None,
            "version": version.model_dump() if version else None,
            "panels": panels,
            "clip": {"fps": fps},
        },
    )
```

- [ ] **Step 4: Confirm no remaining studio_panels references**

Run: `grep -rn "studio_panels\|panels_from_studio_run" backend/ tests/`
Expected: zero hits.

- [ ] **Step 5: Run the studio route tests**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_prompt_card_route.py tests/integration/test_studio_run_output_reuse.py tests/integration/test_studio_player_overlay.py -v`

Expected: some tests fail because their seeded output_json uses flat `in_secs` instead of nested `in.secs`. Task 8 updates the fixtures.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/pages/studio.py
git commit -m "studio: routes render via build_draft_view from review_items"
```

---

## Task 8: Update existing integration tests to use real Gemini shape

**Files:**
- Modify: `tests/integration/test_studio_run_output_reuse.py`
- Modify: `tests/integration/test_studio_player_overlay.py`

- [ ] **Step 1: Read the current fixture helper**

Read `tests/integration/test_studio_run_output_reuse.py` lines 1-50 to find the `_seed_run` helper.

- [ ] **Step 2: Update `_seed_run` to also seed review_items**

In `tests/integration/test_studio_run_output_reuse.py`, change every test that calls `_seed_run` to also seed normalized review_items. Add a new helper after `_seed_run`:

```python
def _seed_run_with_items(
    app, *, version_id, clip_id, scenes=None, fields=None, notes=None,
):
    """Seed a studio_run + review_items rows.

    `scenes` is a list of dicts in REAL Gemini shape:
        {"name": "...", "in": {"secs": float}, "out": {"secs": float}}
    `fields` is a dict identifier→value (passed through as proposed_value).
    `notes` is a dict identifier→str.
    """
    import asyncio
    import json
    import aiosqlite

    db_path = app.state.ctx.settings.data_dir / "app.db"

    async def _go():
        async with aiosqlite.connect(db_path) as db:
            output_json = {}
            if scenes is not None:
                output_json["scenes"] = scenes
            if fields is not None:
                output_json.update(fields)
            if notes is not None:
                output_json.update(notes)

            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) VALUES "
                "(?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps(output_json)),
            )
            run_id = cur.lastrowid

            # review_items: markers from scenes, fields, notes
            for scene in (scenes or []):
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, proposed_value, decision) VALUES (?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "marker", json.dumps(scene)),
                )
            for ident, val in (fields or {}).items():
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, target_identifier, proposed_value, decision) "
                    "VALUES (?, ?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "field", ident, json.dumps(val)),
                )
            for ident, val in (notes or {}).items():
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, target_identifier, proposed_value, decision) "
                    "VALUES (?, ?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "note", ident, json.dumps(val)),
                )
            await db.commit()
            return run_id

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_go())
    finally:
        loop.close()
```

Then update each test in the file that calls `_seed_run({...flat output_json...})`:
- Find each `_seed_run(..., output_json={"scenes": [{"in_secs": …, "out_secs": …, …}], …})` call.
- Replace with `_seed_run_with_items(..., scenes=[{"name": …, "in": {"secs": …}, "out": {"secs": …}}], fields={...})`.

Example: change

```python
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, output_json={
        "scenes": [{"in_secs": 1.0, "out_secs": 2.0, "name": "scene-a"}],
        "summary": "krátký",
    })
```

to

```python
    _seed_run_with_items(main_mod.app, version_id=vid, clip_id=12041,
        scenes=[{"name": "scene-a", "in": {"secs": 1.0}, "out": {"secs": 2.0}}],
        fields={"pragafilm.popis.materialu": "krátký"},
    )
```

…with the corresponding target_map in `_make_prompt_with_version` set so `summary` maps via `kind:"field"`.

- [ ] **Step 3: Apply the same seeder change to test_studio_player_overlay.py**

Read `tests/integration/test_studio_player_overlay.py` lines 80-150 (the area where scenes are seeded). For each `output_json={"scenes": [{"in_secs": …}]}`, switch to the new nested shape AND also seed review_items rows for those scenes (the overlay now reads review_items, not output_json).

A reusable helper can be added to the test module — copy `_seed_run_with_items` from the previous file.

- [ ] **Step 4: Run the updated tests**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_run_output_reuse.py tests/integration/test_studio_player_overlay.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_studio_run_output_reuse.py tests/integration/test_studio_player_overlay.py
git commit -m "studio: tests seed review_items with real Gemini-nested shape"
```

---

## Task 9: Studio Alpine state — tab + history stubs

**Files:**
- Modify: `backend/app/static/studio.js`
- Modify: `backend/app/templates/pages/_anno_panels.html`

- [ ] **Step 1: Add tab + history state to studioPromptCard**

Edit `backend/app/static/studio.js`. Find `Alpine.data('studioPromptCard', ...)` (line ~323) and add four properties at the top of the returned object:

```javascript
  Alpine.data('studioPromptCard', (side = 'cur') => ({
    side,
    diff: false,
    dirty: false,
    // _anno_panels.html (the shared output renderer) reads `tab`, `seek`,
    // `historyLoaded`, `historyHtml`, `loadHistory` from its enclosing
    // Alpine scope. Clip-detail provides these via `player()` + a tab
    // mix-in. Studio doesn't have a per-run history view in v1, so the
    // History tab is suppressed (see _anno_panels.html change) and
    // loadHistory is a noop.
    tab: 'markers',
    historyLoaded: true,
    historyHtml: '',
    loadHistory() {},
```

- [ ] **Step 2: Suppress the History tab when no history loader is wired**

Edit `backend/app/templates/pages/_anno_panels.html`. The current `show_history` default is True (lines 27-34 and 74-77). The shared partial template needs to honour `show_history = False` from the studio side.

Currently the include in `_studio_run_output.html` line 29 sets `{% with show_history = False %}`. Verify the template logic on `show_history is not defined or show_history` — that branch correctly hides the tab when `show_history = False`. No change needed if existing logic works.

Run a quick grep to confirm:

Run: `grep -n "show_history" backend/app/templates/pages/_anno_panels.html`
Expected: line 27 (`{% if show_history is not defined or show_history %}`) and line 74 (same), both guarding the History tab. No edit needed.

- [ ] **Step 3: Manual smoke test**

Run the dev server (use the `server-start` skill discipline — check no existing instance, then start):

```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN || echo "port free"
.venv/bin/uvicorn backend.app.main:app --port 8765 &
```

(Skip if there's no available CatDV seat — Task 10 e2e test covers it deterministically.)

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/studio.js
git commit -m "studio: prompt card provides tab/history stubs for shared anno-panels"
```

---

## Task 10: End-to-end integration test

**Files:**
- Create: `tests/integration/test_studio_review_items_e2e.py`

- [ ] **Step 1: Write the e2e test**

Create `tests/integration/test_studio_review_items_e2e.py`:

```python
"""End-to-end: nested Gemini JSON → annotator → review_items → /studio/_run.

Confirms Option A wiring: the studio output card renders markers/fields
sourced from review_items (linked by studio_run_id), through the same
build_draft_view pipeline clip-detail uses.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from backend.app import main as main_mod


def _new_event_loop_run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_studio_render_after_run_shows_markers_and_fields(tmp_path, monkeypatch):
    # Spin up the FastAPI app pointing at a tmp data dir
    monkeypatch.setenv("CATDV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CATDV_PROVIDER", "fs")  # offline-safe provider
    app = main_mod.app

    with TestClient(app) as client:
        ctx = app.state.ctx
        db_path = ctx.settings.data_dir / "app.db"

        # Seed: prompt with a markers + field + note target_map; a draft version
        async def _seed():
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "INSERT INTO prompts(id, name, description, archived, "
                    "created_at, updated_at) "
                    "VALUES (1, 'p', NULL, 0, '2026-05-28T00:00:00Z', "
                    "'2026-05-28T00:00:00Z')"
                )
                await db.execute(
                    "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, "
                    "target_map, output_schema, model, created_at, updated_at) "
                    "VALUES (1, 1, 1, 'draft', 'do x', ?, '{}', 'gemini-2.5-pro', "
                    "'2026-05-28T00:00:00Z', '2026-05-28T00:00:00Z')",
                    (json.dumps({
                        "scenes": {"kind": "markers"},
                        "decade": {"kind": "field", "identifier": "pragafilm.dekada"},
                        "summary_cz": {"kind": "note", "target": "pragafilm.popis"},
                    }),),
                )
                cur = await db.execute(
                    "INSERT INTO studio_run(prompt_version_id, clip_id, status, model) "
                    "VALUES (1, 42, 'pending', 'gemini-2.5-pro')"
                )
                run_id = cur.lastrowid
                cur2 = await db.execute(
                    "INSERT INTO jobs(prompt_version_id, status, kind, created_at, "
                    "total_clips) VALUES (1, 'pending', 'studio', "
                    "'2026-05-28T00:00:00Z', 1)"
                )
                job_id = cur2.lastrowid
                await db.execute(
                    "INSERT INTO job_items(job_id, catdv_clip_id, status) "
                    "VALUES (?, 42, 'pending')",
                    (job_id,),
                )
                await db.execute(
                    "UPDATE studio_run SET job_id = ? WHERE id = ?", (job_id, run_id)
                )
                await db.commit()
                return run_id, job_id

        run_id, job_id = _new_event_loop_run(_seed())

        # Stub the externals: AI store says "already uploaded"; gemini returns
        # the REAL nested-secs shape; archive returns a fake clip with duration.
        ctx.ai_store = MagicMock()
        ctx.ai_store.status = AsyncMock(return_value=MagicMock(handle="gs://x"))
        ctx.ai_store.reference_for_gemini = AsyncMock(return_value={"uri": "gs://x"})
        ctx.archive = MagicMock()
        ctx.archive.get_clip = AsyncMock(return_value=MagicMock(
            provider_data={"name": "clip-42"},
            duration_secs=10.0,
            fps=25.0,
        ))
        ctx.gemini = MagicMock()
        ctx.gemini.annotate = MagicMock(return_value={
            "text": json.dumps({
                "scenes": [
                    {"name": "scene-a", "in": {"secs": 1.0}, "out": {"secs": 4.0}},
                    {"name": "scene-b", "in": {"secs": 5.0}, "out": {"secs": 9.0}},
                ],
                "decade": {"value": "30.léta"},
                "summary_cz": {"value": "krátký souhrn"},
            }),
            "raw": {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}},
        })
        ctx.proxy_resolver = MagicMock()

        # Run the annotator job synchronously
        from backend.app.services.annotator import run_job

        _new_event_loop_run(run_job(
            db=ctx.db, job_id=job_id,
            archive=ctx.archive, proxy_resolver=ctx.proxy_resolver,
            ai_store=ctx.ai_store, gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            jobs_repo=ctx.jobs_repo, prompts_repo=ctx.prompts_repo,
            studio_runs_repo=ctx.studio_runs_repo,
        ))

        # Hit the studio output endpoint and assert markers + field render
        r = client.get(f"/studio/_run?prompt_version_id=1&clip_id=42")
        assert r.status_code == 200
        html = r.text
        assert "scene-a" in html, "first marker name missing from rendered output"
        assert "scene-b" in html, "second marker name missing from rendered output"
        assert "pragafilm.dekada" in html, "field identifier missing"
        assert "30.léta" in html, "unwrapped field value missing"
        # _anno_panels marker articles include @click="seek(N)" — confirms
        # the shared partial rendered, not the bespoke ro-scene markup.
        assert '@click="seek(' in html
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_review_items_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: PASS overall. Any remaining failures are likely fixture-shape leftovers from Task 8 — fix in-place by mirroring the pattern.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_studio_review_items_e2e.py
git commit -m "studio: e2e test — nested Gemini JSON → review_items → rendered panels"
```

---

## Task 11: ADR + spec update

**Files:**
- Create: `docs/adr/0036-studio-output-via-review-items.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0036-studio-output-via-review-items.md`:

```markdown
# 0036. Prompt Studio output renders via review_items, not raw output_json

**Date:** 2026-05-28
**Status:** Accepted

## Context

The Prompt Studio Output card was rendering through a bespoke
`services/studio_panels.py` adapter that re-parsed `studio_run.output_json`
at render time. The adapter assumed a flat `{in_secs, out_secs}` scene shape,
but Gemini emits the seeded-schema's nested `{"in": {"secs": …}, "out":
{"secs": …}}` shape — so the Output card showed 0 markers after a successful
run. Field values arrived wrapped as `{"value": …, "evidence_secs": [...]}`
and were rendered as Python dict reprs. Notes (target_map `kind:"note"`)
were lumped into the fields panel.

Meanwhile the clip-detail page handles the identical Gemini output without
any of these problems because it normalizes once at write time via
`target_map.expand()` → `review_items`, then renders from the DB.

## Alternatives

1. **Fix the studio_panels adapter** to unwrap nested secs + value
   envelopes. Smallest diff, but leaves two parallel "Gemini JSON → panels"
   code paths that must stay in sync forever.

2. **Reuse `target_map.expand` in-memory** on every studio render. No
   schema change, but still two read paths (DB-backed vs JSON-derived) and
   no future hook for History/compare-by-items.

3. **(Chosen) Persist review_items for studio runs.** Add a nullable
   `studio_run_id` FK to `review_items` with a CHECK that exactly one of
   `(annotation_id, studio_run_id)` is set. Studio's `_finalize_studio`
   calls `target_map.expand(..., studio_run_id=run_id, ...)` and bulk-
   inserts items. Both clip-detail and studio render through
   `build_draft_view`. One normalizer (`target_map.expand`), one renderer
   (`build_draft_view` + `_anno_panels.html`), one shape (the normalized
   `panels` dict).

## Decision

Persist review_items for studio runs (alternative 3). Studio runs never
write to CatDV, but their normalized review_items live in the DB alongside
annotation-bound items, discriminated by the owner column. The bespoke
`studio_panels.py` adapter is deleted.

## Consequences

- Migration 0014 rebuilds `review_items` to allow nullable `annotation_id`
  and adds `studio_run_id` + CHECK constraint. Existing rows migrate
  unchanged.
- `target_map.expand` takes the owner discriminator as a keyword-only
  argument; exactly one must be supplied (runtime guard).
- `services/studio_panels.py` is gone. Anyone touching the studio Output
  card now edits `services/draft_view.py` + `templates/pages/_anno_panels.html`,
  same as for clip-detail.
- Player overlay also reads from review_items, so the timeline matches
  whatever the Output card shows by construction.
- Future Studio "History" / "compare runs" features get review_items as
  their foundation for free.
```

- [ ] **Step 2: Update `docs/decisions.md` with the new entry**

Read `docs/decisions.md` and append a new row to the index table for ADR 0036, following the existing format.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0036-studio-output-via-review-items.md docs/decisions.md
git commit -m "docs: ADR 0036 — studio output renders via review_items"
```

---

## Manual acceptance flows

1. **Studio Run produces visible markers.** Navigate to `/studio?prompt_id=1`. Click a folder, click a clip card (focuses the player). Click the **Output** tab on the cur prompt-card. Click **Run · v1**. Wait until the running indicator clears. Expected: the Output card shows the Markers tab with at least one marker article showing SMPTE timecodes; Fields tab shows non-scene values without `{'value': …}` wrapping; timeline overlay below the player shows range bars matching the markers.

2. **Studio compared run still works.** With cur version showing output, click **+ Compare** → pick a different version. Expected: cmp card renders; if cmp has a prior run on this clip, Output tab shows its markers/fields under the same renderer. Switching the cmp version repopulates without page reload.

3. **Clip detail draft view unaffected.** Navigate to `/clips/<id>` for a clip with an annotation. Expected: Markers/Fields/Notes/History panels render exactly as before — no shape regression, no missing data.

4. **Empty state for a focused clip with no run.** In `/studio`, focus a clip that has no run for the active version. Open Output tab. Expected: "No run yet. Hit Run…" empty-state copy. No error in dev tools.

5. **Migration safety on existing DB.** Stop the dev server (graceful TERM, confirm CatDV seat released). Restart. Expected: `0014_review_items_studio_run.sql` applies once; existing annotation-bound `review_items` rows still load on `/clips/<id>`.
