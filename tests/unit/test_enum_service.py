from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.enum_values import EnumValuesRepo
from backend.app.services.enum_service import EnumError, EnumService


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


def _svc(db) -> EnumService:
    return EnumService(db_provider=lambda: db, repo=EnumValuesRepo())


@pytest.mark.asyncio
async def test_fixed_enum_served_from_registry_ignoring_db(db):
    svc = _svc(db)
    vals = await svc.values("toast_level")
    assert [v.value for v in vals] == ["info", "success", "error"]


@pytest.mark.asyncio
async def test_editable_empty_db_falls_back_to_registry_seed(db):
    svc = _svc(db)
    # No reconcile yet → DB empty → must fall back to seed, never empty.
    vals = await svc.generation_models()
    assert len(vals) == 8
    assert await svc.generation_default() == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_reconcile_materialises_seed_then_serves_from_db(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    rows = await EnumValuesRepo().all_rows(db, "gemini_generation_model")
    assert len(rows) == 8
    assert all(r.source == "seed" for r in rows)
    assert await svc.generation_default() == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_reconcile_does_not_revive_tombstone_or_clobber(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    await svc.remove_value("gemini_generation_model", "gemini-3.5-flash")
    await svc.set_default("gemini_generation_model", "gemini-2.5-flash")
    await svc.reconcile_seeds()  # second boot
    live = {v.value for v in await svc.generation_models()}
    assert "gemini-3.5-flash" not in live  # tombstone honoured
    assert await svc.generation_default() == "gemini-2.5-flash"  # edit preserved


@pytest.mark.asyncio
async def test_definitions_editable_only(db):
    svc = _svc(db)
    keys = {d.key for d in await svc.definitions(editable_only=True)}
    assert keys == {"gemini_generation_model"}


@pytest.mark.asyncio
async def test_add_and_remove_value(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    await svc.add_value("gemini_generation_model", "gemini-4.0-pro")
    assert "gemini-4.0-pro" in {v.value for v in await svc.generation_models()}
    await svc.remove_value("gemini_generation_model", "gemini-4.0-pro")
    assert "gemini-4.0-pro" not in {v.value for v in await svc.generation_models()}


@pytest.mark.asyncio
async def test_write_to_fixed_enum_refused(db):
    svc = _svc(db)
    with pytest.raises(EnumError):
        await svc.add_value("toast_level", "warning")


@pytest.mark.asyncio
async def test_cannot_disable_last_enabled(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    models = [v.value for v in await svc.generation_models()]
    # move the default to models[0] so the loop can disable models[1:] freely
    await svc.set_default("gemini_generation_model", models[0])
    # disable all but one
    for m in models[1:]:
        await svc.set_enabled("gemini_generation_model", m, enabled=False)
    with pytest.raises(EnumError):
        await svc.set_enabled("gemini_generation_model", models[0], enabled=False)


@pytest.mark.asyncio
async def test_set_default_clears_prior_and_requires_enabled(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    await svc.set_default("gemini_generation_model", "gemini-2.5-flash")
    assert await svc.generation_default() == "gemini-2.5-flash"
    # only one default remains
    defaults = [v for v in await svc.generation_models() if v.is_default]
    assert len(defaults) == 1
    # cannot set a disabled value as default
    await svc.set_enabled("gemini_generation_model", "gemini-2.5-pro", enabled=False)
    with pytest.raises(EnumError):
        await svc.set_default("gemini_generation_model", "gemini-2.5-pro")


@pytest.mark.asyncio
async def test_cannot_remove_or_disable_current_default(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    cur = await svc.generation_default()
    with pytest.raises(EnumError):
        await svc.remove_value("gemini_generation_model", cur)
    with pytest.raises(EnumError):
        await svc.set_enabled("gemini_generation_model", cur, enabled=False)


@pytest.mark.asyncio
async def test_duplicate_add_raises_enum_error(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    with pytest.raises(EnumError):
        await svc.add_value("gemini_generation_model", "gemini-2.5-pro")
