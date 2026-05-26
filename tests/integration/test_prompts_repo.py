"""PromptsRepo — prompt-level + version-level CRUD with invariants."""

import pytest

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


# ── version operations ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_version_default_clones_current_production(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    new_vid = await repo.create_version(db, pid)
    new_v = await repo.get_version(db, new_vid)
    assert new_v.version_num == 2
    assert new_v.state == "draft"
    assert new_v.body == "Identify scenes."  # cloned


@pytest.mark.asyncio
async def test_create_version_fallback_to_latest_when_no_production(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    # v1 is draft; no production exists.
    new_vid = await repo.create_version(db, pid)
    new_v = await repo.get_version(db, new_vid)
    assert new_v.version_num == 2
    assert new_v.state == "draft"


@pytest.mark.asyncio
async def test_create_version_explicit_from_version_id(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    body2 = {**_vbody(), "body": "v2 body"}
    await repo.update_version(db, v1, **body2)  # still draft, mutate
    await repo.promote_version(db, pid, v1)  # v1 production
    v2_id = await repo.create_version(db, pid)
    await repo.update_version(
        db,
        v2_id,
        body="v2-edited",
        target_map=body2["target_map"],
        output_schema=body2["output_schema"],
        model=body2["model"],
    )
    v3_id = await repo.create_version(db, pid, from_version_id=v1)
    assert (await repo.get_version(db, v3_id)).body == "v2 body"


@pytest.mark.asyncio
async def test_update_version_on_draft_persists(db):
    repo = PromptsRepo()
    _, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.update_version(
        db,
        vid,
        body="new body",
        target_map={"s": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash",
    )
    v = await repo.get_version(db, vid)
    assert v.body == "new body"
    assert v.model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_update_version_on_production_raises(db):
    repo = PromptsRepo()
    pid, vid = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, vid)
    with pytest.raises(VersionImmutableError) as excinfo:
        await repo.update_version(db, vid, body="x", target_map={}, output_schema={}, model="m")
    assert excinfo.value.state == "production"


@pytest.mark.asyncio
async def test_update_version_on_archived_raises(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)  # v1 now archived
    v1_state = (await repo.get_version(db, v1)).state
    assert v1_state == "archived"
    with pytest.raises(VersionImmutableError):
        await repo.update_version(db, v1, body="x", target_map={}, output_schema={}, model="m")


@pytest.mark.asyncio
async def test_promote_demotes_previous_production(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)
    assert (await repo.get_version(db, v1)).state == "archived"
    assert (await repo.get_version(db, v2)).state == "production"


@pytest.mark.asyncio
async def test_promote_only_one_production_per_prompt(db):
    """Sanity: the partial unique index actually fires under repo.promote."""
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)
    # After promotes there is exactly one row with state='production'.
    cur = await db.execute(
        "SELECT COUNT(*) FROM prompt_versions WHERE prompt_id = ? AND state = 'production'",
        (pid,),
    )
    assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_version_num_monotonic(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    v2 = await repo.create_version(db, pid)
    v3 = await repo.create_version(db, pid)
    assert (await repo.get_version(db, v2)).version_num == 2
    assert (await repo.get_version(db, v3)).version_num == 3


@pytest.mark.asyncio
async def test_duplicate_copies_current_production_into_new_prompt_draft(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description="d", **_vbody())
    await repo.promote_version(db, pid, v1)
    new_pid, new_vid = await repo.duplicate(db, pid)
    new_prompt, versions = await repo.get_with_versions(db, new_pid)
    assert new_prompt.name == "Copy of P"
    assert new_prompt.description == "d"
    assert len(versions) == 1
    assert versions[0].id == new_vid
    assert versions[0].state == "draft"
    assert versions[0].body == "Identify scenes."


@pytest.mark.asyncio
async def test_duplicate_walks_past_existing_copy_names(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.duplicate(db, pid)  # creates "Copy of P"
    pid2, _ = await repo.duplicate(db, pid)
    p, _ = await repo.get_with_versions(db, pid2)
    assert p.name == "Copy of P (2)"


@pytest.mark.asyncio
async def test_duplicate_skips_archived_name_collisions(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    copy1, _ = await repo.duplicate(db, pid)
    await repo.archive(db, copy1)  # archived but UNIQUE still applies
    pid3, _ = await repo.duplicate(db, pid)
    p, _ = await repo.get_with_versions(db, pid3)
    assert p.name == "Copy of P (2)"


@pytest.mark.asyncio
async def test_duplicate_with_explicit_name_and_description(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description="orig", **_vbody())
    new_pid, _ = await repo.duplicate(db, pid, name="My Variant", description="new desc")
    p, versions = await repo.get_with_versions(db, new_pid)
    assert p.name == "My Variant"
    assert p.description == "new desc"
    assert versions[0].state == "draft"
    assert versions[0].body == "Identify scenes."


@pytest.mark.asyncio
async def test_duplicate_with_explicit_name_collision_raises(db):
    import aiosqlite

    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.create_with_initial_version(db, name="Taken", description=None, **_vbody())
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.duplicate(db, pid, name="Taken")


@pytest.mark.asyncio
async def test_promote_on_archived_raises(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    v2 = await repo.create_version(db, pid)
    await repo.promote_version(db, pid, v2)  # v1 now archived
    assert (await repo.get_version(db, v1)).state == "archived"
    with pytest.raises(VersionImmutableError):
        await repo.promote_version(db, pid, v1)


@pytest.mark.asyncio
async def test_promote_on_already_production_is_noop(db):
    repo = PromptsRepo()
    pid, v1 = await repo.create_with_initial_version(db, name="P", description=None, **_vbody())
    await repo.promote_version(db, pid, v1)
    state_before = (await repo.get_version(db, v1)).state
    await repo.promote_version(db, pid, v1)  # no-op
    assert (await repo.get_version(db, v1)).state == state_before == "production"


@pytest.mark.asyncio
async def test_create_version_cross_prompt_from_version_id_raises(db):
    repo = PromptsRepo()
    pid_a, vid_a = await repo.create_with_initial_version(
        db, name="A", description=None, **_vbody()
    )
    pid_b, _ = await repo.create_with_initial_version(db, name="B", description=None, **_vbody())
    with pytest.raises(LookupError):
        await repo.create_version(db, pid_b, from_version_id=vid_a)


# ── media_kind ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_persists_media_kind(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(
        db, name="img", description=None, body="b",
        target_map={"summary_cz": {"kind": "note", "target": "t"}},
        output_schema={}, model="m", media_kind="image",
    )
    prompt, _ = await repo.get_with_versions(db, pid)
    assert prompt.media_kind == "image"


@pytest.mark.asyncio
async def test_create_defaults_media_kind_any(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(
        db, name="def", description=None, body="b",
        target_map={}, output_schema={}, model="m",
    )
    prompt, _ = await repo.get_with_versions(db, pid)
    assert prompt.media_kind == "any"


@pytest.mark.asyncio
async def test_update_metadata_sets_media_kind(db):
    repo = PromptsRepo()
    pid, _ = await repo.create_with_initial_version(
        db, name="x", description=None, body="b",
        target_map={}, output_schema={}, model="m",
    )
    await repo.update_metadata(db, pid, media_kind="video")
    prompt, _ = await repo.get_with_versions(db, pid)
    assert prompt.media_kind == "video"
