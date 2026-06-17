"""Render guard test for Task 15: re-run confirm dialog.

When a clip has an unpublished draft (publish_status.state == 'draft'),
GET /clips/{id} must render:
- data-rerun-confirm="true" attribute on the page
- the confirm copy: "replaces your current unpublished draft"
- the copy: "restorable"

When there is NO unpublished draft, the page must NOT require confirmation:
- data-rerun-confirm="false" (or absent / not "true")
"""
import asyncio
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.archive.model import CanonicalClip, MediaRef
from tests._helpers.live_ctx import install_live_ctx


def _canonical(clip_id: int = 12041) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name="Test Clip Rerun Confirm",
        duration_secs=120.0,
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
        provider_data={"ID": clip_id, "name": "Test Clip Rerun Confirm"},
        fetched_at=datetime.now(UTC),
    )


class FakeArchive:
    def __init__(self, clip: CanonicalClip):
        self._clip = clip

    async def get_clip(self, clip_id_str: str) -> CanonicalClip:
        if clip_id_str == str(self._clip.key[1]):
            return self._clip
        from backend.app.archive.errors import ProviderError
        raise ProviderError("not found")


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def _seed_unapplied_draft(ctx, clip_id: int) -> None:
    """Seed a pending (un-applied) review item so has_draft=True, no live version."""
    import asyncio
    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo

    async def _do() -> None:
        prompts = PromptsRepo()
        _, vid = await prompts.create_with_initial_version(
            ctx.db, name="p", description=None, body="b",
            target_map={}, output_schema={}, model="m",
        )
        jobs = JobsRepo()
        jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[clip_id])
        cur = await ctx.db.execute(
            "INSERT INTO annotations "
            "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
            " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
            "VALUES (?, 'C', ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-17T00:00:00')",
            (clip_id, vid, jid),
        )
        ann_id = cur.lastrowid
        # A pending (un-applied, un-decided) review item → has_draft=True
        await ctx.db.execute(
            "INSERT INTO review_items "
            "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
            " proposed_value, edited_value, decision, applied_at) "
            "VALUES (?, NULL, ?, 'marker', NULL, '{}', NULL, 'pending', NULL)",
            (ann_id, clip_id),
        )
        await ctx.db.commit()

    asyncio.run(_do())


def test_rerun_confirm_present_when_draft(monkeypatch, tmp_path):
    """A clip with an unpublished draft must render the re-run confirm modal markup."""
    clip_id = 12041
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        _seed_unapplied_draft(ctx, clip_id)
        install_live_ctx(client.app, archive=FakeArchive(_canonical(clip_id)))

        r = client.get(f"/clips/{clip_id}")
        assert r.status_code == 200

        # The annotate trigger must carry data-rerun-confirm="true"
        assert 'data-rerun-confirm="true"' in r.text, (
            "Expected data-rerun-confirm=\"true\" when publish_status.state == 'draft'"
        )
        # The confirm copy must be present
        assert "replaces your current unpublished draft" in r.text, (
            "Expected confirm copy about replacing unpublished draft"
        )
        assert "restorable" in r.text, (
            "Expected 'restorable' in confirm copy"
        )


def test_rerun_confirm_absent_when_no_draft(monkeypatch, tmp_path):
    """A clip with no draft must NOT require the re-run confirm."""
    clip_id = 12041
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive(_canonical(clip_id)))

        r = client.get(f"/clips/{clip_id}")
        assert r.status_code == 200

        # Must NOT carry data-rerun-confirm="true"
        assert 'data-rerun-confirm="true"' not in r.text, (
            "Expected no re-run confirm when there is no unpublished draft"
        )
