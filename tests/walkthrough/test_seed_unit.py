"""Unit tests for walkthrough seeding."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.draft_view import build_draft_view
from tests.walkthrough import seed
from tests.walkthrough.fakes import CLIP_ID, DECADE_IDENT


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
