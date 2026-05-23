"""Integration tests for the `draft` view-model wired into /clips/{id}.

Task 6: the clip_detail_page route should load the latest annotation for
the clip (or None), filter its review_items, and pass a `draft` view-model
to the template. The template emits a hidden hook
`data-draft-empty="true|false"` we can assert against; the real draft
markup lands in Task 8.
"""

import asyncio
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    MediaRef,
)


def _canonical(clip_id: int = 101, name: str = "Clip_101") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=10.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": name},
        fetched_at=datetime.now(UTC),
    )


class FakeArchive:
    def __init__(self, clips: tuple[CanonicalClip, ...] = ()):
        self._clips = clips

    async def list_clips(self, catalog, query):
        return ClipPage(
            items=self._clips, total=len(self._clips), offset=query.offset, limit=query.limit
        )

    async def get_clip(self, clip_id_str):
        for c in self._clips:
            if c.key[1] == clip_id_str:
                return c
        raise ProviderError("not found")


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _seed_annotation_with_marker(ctx, clip_id: int = 101) -> int:
    from backend.app.models.annotation import Annotation, ReviewItem

    _, vid = await ctx.prompts_repo.create_with_initial_version(
        ctx.db,
        name="scene-tagger",
        description=None,
        body="describe scenes",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={},
        model="gemini-2.5-pro",
    )
    aid = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=clip_id,
            catdv_clip_name=f"Clip_{clip_id}",
            prompt_version_id=vid,
            model="gemini-2.5-pro",
            prompt_used="describe scenes",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": clip_id, "name": f"Clip_{clip_id}", "markers": [], "fields": {}},
        ),
    )
    await ctx.review_items_repo.bulk_insert(
        ctx.db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=clip_id,
                kind="marker",
                proposed_value={
                    "name": "Scene 1",
                    "in": {"frm": 0, "secs": 0.0},
                    "out": {"frm": 25, "secs": 1.0},
                },
            ),
        ],
    )
    return aid


def test_clip_detail_renders_empty_draft_when_no_annotation(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(101),))
        r = client.get("/clips/101")
        assert r.status_code == 200
        assert 'data-draft-empty="true"' in r.text


def test_clip_detail_renders_draft_when_annotation_exists(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        ctx.archive = FakeArchive((_canonical(101),))
        _run(_seed_annotation_with_marker(ctx, clip_id=101))
        r = client.get("/clips/101")
        assert r.status_code == 200
        assert 'data-draft-empty="true"' not in r.text
        assert "Scene 1" in r.text


def test_clips_draft_partial_returns_empty_state(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(101),))
        r = client.get("/clips/101/draft")
        assert r.status_code == 200
        assert 'data-draft-empty="true"' in r.text
        # Body is a partial — must not include the full page layout.
        assert "<html" not in r.text.lower()


def test_clips_draft_partial_returns_populated_when_annotation_exists(
    monkeypatch,
    tmp_path,
):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        ctx.archive = FakeArchive((_canonical(101),))
        _run(_seed_annotation_with_marker(ctx, clip_id=101))
        r = client.get("/clips/101/draft")
        assert r.status_code == 200
        assert "Scene 1" in r.text
        assert "<html" not in r.text.lower()


def test_clips_draft_partial_returns_404_when_clip_missing(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.app.state.ctx.archive = FakeArchive(())
        r = client.get("/clips/999999/draft")
        assert r.status_code == 404
