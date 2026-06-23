"""Per-prompt-version media_resolution override column.

NULL = use the model's default_media_resolution. Versioned with the prompt
(clones copy it). See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md §2.
"""

import pytest

from backend.app.repositories.prompts import PromptsRepo

pytestmark = pytest.mark.asyncio


async def test_version_media_resolution_roundtrip(db):
    repo = PromptsRepo()
    prompt_id, vid = await repo.create_with_initial_version(
        db,
        name="p",
        description=None,
        body="b",
        target_map={},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    # v1 defaults to NULL (use model default).
    assert (await repo.get_version(db, vid)).media_resolution is None

    # update_version on a draft persists the override.
    await repo.update_version(
        db,
        vid,
        body="b2",
        target_map={},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
        media_resolution="low",
    )
    assert (await repo.get_version(db, vid)).media_resolution == "low"

    # create_version clone copies the source's media_resolution.
    new_vid = await repo.create_version(db, prompt_id, from_version_id=vid)
    assert (await repo.get_version(db, new_vid)).media_resolution == "low"


async def test_create_with_initial_version_accepts_media_resolution(db):
    repo = PromptsRepo()
    _, vid = await repo.create_with_initial_version(
        db,
        name="p2",
        description=None,
        body="b",
        target_map={},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
        media_resolution="high",
    )
    assert (await repo.get_version(db, vid)).media_resolution == "high"
