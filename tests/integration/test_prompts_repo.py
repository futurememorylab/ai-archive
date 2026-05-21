"""PromptsRepo — prompt-level + version-level CRUD with invariants."""
import pytest

from backend.app.models.prompt import Prompt, PromptVersion
from backend.app.repositories.prompts import (
    PromptsRepo,
    VersionImmutableError,
)


def _vbody() -> dict:
    return {
        "body": "Identify scenes.",
        "target_map": {"scenes": {"kind": "markers"}},
        "output_schema": {"type": "object"},
        "model": "gemini-2.5-pro",
    }


@pytest.mark.asyncio
async def test_create_with_initial_version_yields_v1_draft(db):
    repo = PromptsRepo()
    prompt_id, version_id = await repo.create_with_initial_version(
        db, name="P1", description="d", **_vbody()
    )
    prompt, versions = await repo.get_with_versions(db, prompt_id)
    assert prompt.name == "P1"
    assert prompt.archived is False
    assert len(versions) == 1
    assert versions[0].id == version_id
    assert versions[0].version_num == 1
    assert versions[0].state == "draft"


@pytest.mark.asyncio
async def test_list_active_excludes_archived(db):
    repo = PromptsRepo()
    p1, _ = await repo.create_with_initial_version(db, name="A", description=None, **_vbody())
    p2, _ = await repo.create_with_initial_version(db, name="B", description=None, **_vbody())
    await repo.archive(db, p1)
    active = await repo.list_active(db)
    assert [p.name for p in active] == ["B"]
    archived = await repo.list_archived(db)
    assert [p.name for p in archived] == ["A"]


@pytest.mark.asyncio
async def test_archive_then_restore_idempotent(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.archive(db, pid)
    await repo.archive(db, pid)  # idempotent
    p, _ = await repo.get_with_versions(db, pid)
    assert p.archived is True
    await repo.restore(db, pid)
    await repo.restore(db, pid)  # idempotent
    p, _ = await repo.get_with_versions(db, pid)
    assert p.archived is False


@pytest.mark.asyncio
async def test_archive_preserves_version_states(db):
    repo = PromptsRepo()
    pid, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, vid)
    await repo.archive(db, pid)
    _, versions = await repo.get_with_versions(db, pid)
    assert versions[0].state == "production"


@pytest.mark.asyncio
async def test_update_metadata_name_and_description(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="Old", description="d1", **_vbody())
    await repo.update_metadata(db, pid, name="New", description="d2")
    p, _ = await repo.get_with_versions(db, pid)
    assert p.name == "New"
    assert p.description == "d2"


@pytest.mark.asyncio
async def test_update_metadata_unique_name_collision_raises(db):
    repo = PromptsRepo()
    await repo.create_with_initial_version(db, name="A", description=None, **_vbody())
    pid, _ = await repo.create_with_initial_version(db, name="B", description=None, **_vbody())
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.update_metadata(db, pid, name="A", description=None)


@pytest.mark.asyncio
async def test_get_version_returns_loaded_pydantic(db):
    repo = PromptsRepo()
    pid, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    v = await repo.get_version(db, vid)
    assert v.id == vid
    assert v.prompt_id == pid
    assert v.body == "Identify scenes."
    assert v.target_map.fields["scenes"].kind == "markers"


@pytest.mark.asyncio
async def test_get_version_unknown_raises_lookup(db):
    repo = PromptsRepo()
    with pytest.raises(LookupError):
        await repo.get_version(db, 9999)
