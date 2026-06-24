"""Unit tests for walkthrough seeding."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.draft_view import build_draft_view
from tests.walkthrough import seed
from tests.walkthrough.fakes import CLIP_ID, DECADE_IDENT, PRODUCTION_PROMPT_NAME


def test_make_proxy_video_creates_playable_file(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = seed.make_proxy_video(tmp_path / "proxy.mp4", seconds=2)
    assert out.exists()
    assert out.stat().st_size > 1000  # a real encoded file, not a stub


async def test_seed_draft_produces_a_draft_view(db):
    await seed.seed_draft(db)
    ann = (await AnnotationsRepo().list_by_clip(db, CLIP_ID))[0]
    items = await ReviewItemsRepo().list_by_clip(db, CLIP_ID)
    view = build_draft_view(ann, items)
    assert view["has_draft"] is True
    decades = [f for f in view["fields"] if f["identifier"] == DECADE_IDENT]
    assert decades and decades[0]["value"] == "20.léta"


async def test_seed_production_prompt_is_pickable(db):
    """The seeded prompt must expose a non-null production version with a
    video media kind, or the bulk-annotate modal / New-batch picker (which
    only list prompts with current_production_version_id != null) have nothing
    to pick."""
    await seed.seed_production_prompt(db)
    prompts = PromptsRepo()
    prompt = await prompts.get_by_name(db, PRODUCTION_PROMPT_NAME)
    assert prompt is not None
    assert prompt.media_kind == "video"
    prod = await prompts.get_production_version(db, prompt.id)
    assert prod is not None
    assert prod.state == "production"


async def test_build_seed_db_is_readable_from_a_separate_connection(tmp_path: Path):
    """The seed DB is built on its own connection; a fresh, independent
    connection (mirroring the app's live connection) must see the committed
    draft — proving the seed/run database separation works."""
    db_file = tmp_path / "seed_template.db"
    await seed.build_seed_db(db_file)

    async with open_db(db_file) as conn:  # a different connection than seeding used
        ann = (await AnnotationsRepo().list_by_clip(conn, CLIP_ID))[0]
        items = await ReviewItemsRepo().list_by_clip(conn, CLIP_ID)
        view = build_draft_view(ann, items)
    assert view["has_draft"] is True
    decades = [f for f in view["fields"] if f["identifier"] == DECADE_IDENT]
    assert decades and decades[0]["value"] == "20.léta"


async def test_seeded_db_supports_annotation_status_filters(tmp_path: Path):
    """The seeded catalog + drafts let the annotation-status filters resolve:
    the fixture clip is 'awaiting review' and absent from 'not annotated', while
    a catalog clip with no annotation is the reverse."""
    from backend.app.services.clip_list_filters import resolve
    from tests.walkthrough.fakes import (
        CATALOG_ID,
        REVIEW_FIXTURE_CLIP_ID,
        build_clips,
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 2048)
    db_file = tmp_path / "seed_template.db"
    await seed.build_seed_db(db_file, clips=build_clips(video), catalog_id=CATALOG_ID)

    async with open_db(db_file) as conn:
        kw = dict(provider_id="catdv", catalog_id=CATALOG_ID, cache="any")
        for_review = await resolve(conn, anno="for_review", **kw)
        not_annotated = await resolve(conn, anno="none", **kw)

    assert REVIEW_FIXTURE_CLIP_ID in for_review
    assert REVIEW_FIXTURE_CLIP_ID not in not_annotated
    # 'not annotated' is non-empty (universe known from clip_list_cache) and the
    # fixture's pending clip never leaks into it.
    assert not_annotated
