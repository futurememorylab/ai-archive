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
from tests._helpers.live_ctx import install_live_ctx


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
    # Redesigned: the draft panel is Alpine-driven; no server-rendered
    # data-draft-empty hook. When there is no annotation the draft_arrays are
    # empty, so the serialised draftMarkers/draftFields/draftNotes arrays in the
    # page x-data are all []. Assert the page renders and the card panel markup
    # (review-bar) + empty arrays are present.
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        r = client.get("/clips/101")
        assert r.status_code == 200
        assert "review-bar" in r.text
        assert "draftFields: []" in r.text
        # Annotate dropdown migrated onto the shared popover/menu module.
        assert "popover-panel menu" in r.text
        assert "menu-item" in r.text
        assert "annotate-menu" not in r.text


def test_clip_detail_renders_draft_when_annotation_exists(monkeypatch, tmp_path):
    # Redesigned: draft items are serialised into the page x-data (draftMarkers
    # JSON), not rendered as raw HTML. The marker name "Scene 1" appears as a
    # JSON value inside the Alpine data on the page; the review bar is present.
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        _run(_seed_annotation_with_marker(ctx, clip_id=101))
        r = client.get("/clips/101")
        assert r.status_code == 200
        assert "review-bar" in r.text
        assert "Scene 1" in r.text  # present as a JSON value in draftMarkers


def test_clips_draft_partial_returns_empty_state(monkeypatch, tmp_path):
    # Redesigned: _anno_draft.html is a pure Alpine template; there is no
    # server-rendered empty-state hook. The partial renders the review-bar
    # and card-panel scaffolding regardless; Alpine shows "No proposals to
    # review." client-side when draftMarkers/draftFields/draftNotes are empty.
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        r = client.get("/clips/101/draft")
        assert r.status_code == 200
        assert "review-bar" in r.text
        # Body is a partial — must not include the full page layout.
        assert "<html" not in r.text.lower()


def test_clips_draft_partial_returns_populated_when_annotation_exists(
    monkeypatch,
    tmp_path,
):
    # Redesigned: _anno_draft.html is a pure Alpine template — draft item names
    # are NOT rendered server-side in this partial (they live in the page-level
    # x-data JSON in the full clip-detail render). The partial renders the
    # card scaffolding (review-bar, ri-card loop templates) and remains a partial.
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        _run(_seed_annotation_with_marker(ctx, clip_id=101))
        r = client.get("/clips/101/draft")
        assert r.status_code == 200
        assert "review-bar" in r.text
        assert "ri-card" in r.text
        assert "<html" not in r.text.lower()


def test_draft_marker_card_is_click_to_seek(monkeypatch, tmp_path):
    # Clicking anywhere on a draft marker card should jump the player to the
    # marker in-point and play (seek() already does both). The whole .ri-marker
    # card carries @click="seek(m.in_secs)" — mirroring the published marker
    # <article> — while the editor and action buttons stop propagation so
    # editing/deleting doesn't also seek+play.
    import re

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        r = client.get("/clips/101/draft")
        assert r.status_code == 200
        # The marker card element itself is the click target.
        assert re.search(
            r'class="ri-card ri-marker"[^>]*@click="seek\(m\.in_secs\)"',
            r.text,
        ), "draft marker card must be click-to-seek"
        # Editor + actions stop the click so they don't trigger a seek.
        assert "@click.stop" in r.text


def test_clips_draft_partial_returns_404_when_clip_missing(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive(()))
        r = client.get("/clips/999999/draft")
        assert r.status_code == 404


def test_clips_published_partial_renders_refreshable_panels(monkeypatch, tmp_path):
    # GET /clips/{id}/published returns just the refreshable published panels
    # (the swap target review.js replaces after a writeback), with the markers
    # JSON island for the player timeline. It must be a partial, not a full page.
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        r = client.get("/clips/101/published")
        assert r.status_code == 200
        assert 'id="published-panels"' in r.text
        assert 'id="published-markers-data"' in r.text  # JSON island for timeline
        assert "anno-tabs" in r.text  # the panels themselves rendered
        assert "<html" not in r.text.lower()  # partial, not full layout


def test_clips_published_partial_404_when_clip_missing(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive(()))
        r = client.get("/clips/999999/published")
        assert r.status_code == 404


class _OfflineMonitor:
    """Connection monitor stub reporting CatDV offline (the scale-to-zero
    cloud boot state: tunnel/CatDV not connected). layout.html derives
    mode == "offline" from this, so the `mode == "online"` template branch
    is false — mirroring the cloud condition the operator hit."""

    is_forced = False
    _forced_offline = False

    def current_state(self):
        from backend.app.services.connection_monitor import ConnectionState

        return ConnectionState.offline


async def _seed_ai_store_row(ctx, clip_id: int = 101) -> None:
    """Record an AI-store (GCS) upload for the clip so media_ai.present=True
    while media_local stays absent — the cloud media layout."""
    await ctx.ai_store_files_repo.upsert(
        ctx.db,
        store_id="gcs:catdav-proxies",
        clip_id=clip_id,
        gcs_uri=f"gs://catdav-proxies/clips/{clip_id}.mov",
        mime_type="video/quicktime",
        size_bytes=1000,
        sha256="deadbeef",
    )
    await ctx.db.commit()


# `toggleOpen()` (the Annotate button's @click handler) is unique to
# _annotate_dropdown.html, so it is a precise probe for whether the Annotate
# dropdown rendered (the shared "popover-panel menu" string also appears in
# unrelated topbar menus).
_ANNOTATE_MARKER = "toggleOpen()"


def test_annotate_available_offline_when_media_in_ai_store(monkeypatch, tmp_path):
    """Cloud parity: CatDV offline (mode != online) but the clip's proxy is
    already in the AI store (GCS) → the Annotate button must still render.
    The annotator reads bytes from GCS, which is tunnel-independent, so
    annotation works offline for any clip already uploaded."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        install_live_ctx(
            client.app,
            archive=FakeArchive((_canonical(101),)),
            connection_monitor=_OfflineMonitor(),
        )
        _run(_seed_ai_store_row(ctx, clip_id=101))
        r = client.get("/clips/101")
        assert r.status_code == 200
        assert _ANNOTATE_MARKER in r.text, (
            "Annotate dropdown must render offline when the clip's media is in GCS"
        )


def test_annotate_hidden_offline_when_no_media_anywhere(monkeypatch, tmp_path):
    """Guard: offline AND no media in any cache layer → no Annotate button.
    With no proxy locally or in GCS and CatDV unreachable, a run could not
    fetch the media, so offering the button would only produce an error."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(
            client.app,
            archive=FakeArchive((_canonical(101),)),
            connection_monitor=_OfflineMonitor(),
        )
        r = client.get("/clips/101")
        assert r.status_code == 200
        assert _ANNOTATE_MARKER not in r.text
