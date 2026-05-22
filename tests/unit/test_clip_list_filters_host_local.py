from pathlib import Path

import pytest
import pytest_asyncio

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services import clip_list_filters as f

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest_asyncio.fixture
async def memdb(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        yield conn


@pytest.mark.asyncio
async def test_cache_local_short_circuits_to_no_filter(memdb):
    result = await f.resolve(
        memdb,
        provider_id="catdv",
        catalog_id="881507",
        cache="local",
        anno="any",
        host_local_proxies=True,
    )
    # `None` means "no filter active" — caller takes the standard
    # CatDV-paginated path, i.e. every clip is included.
    assert result is None


@pytest.mark.asyncio
async def test_cache_none_short_circuits_to_empty_set(memdb):
    result = await f.resolve(
        memdb,
        provider_id="catdv",
        catalog_id="881507",
        cache="none",
        anno="any",
        host_local_proxies=True,
    )
    assert result == set()


@pytest.mark.asyncio
async def test_cache_filter_ignored_in_host_local_when_anno_active(memdb):
    """When `anno` is active too, host-local just drops the cache predicate —
    the anno predicate still applies."""
    # Seed one review_item so the anno=for_review predicate returns {42}.
    await memdb.execute(
        "INSERT INTO prompts(id, name, created_at, updated_at) "
        "VALUES (1, 'p', '2024-01-01', '2024-01-01')"
    )
    await memdb.execute(
        "INSERT INTO prompt_versions(id, prompt_id, version_num, state, body, "
        "target_map, output_schema, model, created_at, updated_at) "
        "VALUES (1, 1, 1, 'production', 'b', '{}', '{}', 'm', '2024-01-01', '2024-01-01')"
    )
    await memdb.execute(
        "INSERT INTO annotations(id, catdv_clip_id, catdv_clip_name, "
        "prompt_version_id, model, prompt_used, raw_response, structured_output, "
        "clip_snapshot, created_at) "
        "VALUES (1, 42, 'c', 1, 'm', 'p', '{}', '{}', '{}', '2024-01-01')"
    )
    await memdb.execute(
        "INSERT INTO review_items(id, annotation_id, catdv_clip_id, kind, "
        "proposed_value, decision, applied_at) "
        "VALUES (1, 1, 42, 'set', 'v', 'accepted', NULL)"
    )
    await memdb.commit()
    result = await f.resolve(
        memdb,
        provider_id="catdv",
        catalog_id="881507",
        cache="local",          # would normally restrict
        anno="for_review",
        host_local_proxies=True,
    )
    assert result == {42}
