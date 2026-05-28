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
