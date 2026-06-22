"""Unit tests for FakeArchive search filtering + the injected clip catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.archive.model import ClipQuery
from tests.walkthrough.fakes import (
    CLIP_NAME,
    NO_RESULTS_TERM,
    SEARCH_MATCH_NAMES,
    SEARCH_NONMATCH_NAME,
    SEARCH_TERM,
    FakeArchive,
    build_clips,
)


def _video(tmp_path: Path) -> Path:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"\x00" * 2048)  # build_clips reads .stat().st_size
    return path


@pytest.fixture
def archive(tmp_path: Path) -> FakeArchive:
    return FakeArchive(build_clips(_video(tmp_path)))


async def test_search_filters_by_name(archive: FakeArchive):
    page = await archive.list_clips(None, ClipQuery(text=SEARCH_TERM))
    names = {c.name for c in page.items}
    assert names == set(SEARCH_MATCH_NAMES)
    assert page.total == len(SEARCH_MATCH_NAMES)
    assert SEARCH_NONMATCH_NAME not in names


async def test_search_is_case_insensitive(archive: FakeArchive):
    page = await archive.list_clips(None, ClipQuery(text=SEARCH_TERM.lower()))
    assert page.total == len(SEARCH_MATCH_NAMES)


async def test_no_match_term_yields_empty_page(archive: FakeArchive):
    page = await archive.list_clips(None, ClipQuery(text=NO_RESULTS_TERM))
    assert page.items == ()
    assert page.total == 0


async def test_empty_query_lists_all_including_canonical_clip(archive: FakeArchive):
    page = await archive.list_clips(None, ClipQuery())
    assert CLIP_NAME in {c.name for c in page.items}
    assert page.total >= len(SEARCH_MATCH_NAMES) + 1


async def test_pagination_slices_but_total_is_full_count(archive: FakeArchive):
    full = await archive.list_clips(None, ClipQuery())
    page = await archive.list_clips(None, ClipQuery(offset=1, limit=2))
    assert len(page.items) == 2
    assert (page.offset, page.limit) == (1, 2)
    assert page.total == full.total


async def test_get_clip_finds_each_catalog_member(archive: FakeArchive):
    full = await archive.list_clips(None, ClipQuery())
    for clip in full.items:
        got = await archive.get_clip(clip.key[1])
        assert got.key == clip.key
