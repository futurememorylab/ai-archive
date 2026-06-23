"""Admin calibrate launch + projected-cost endpoints (PR4a A4, refined).

The Calibrate dialog launches a sweep over ANY number of clips (≥1). For
each resolution × 2 repeats it runs the *eligible* clips telemetry-only
(record_only). HIGH applies only to image clips — Vertex rejects HIGH for
video/audio ("HIGH media resolution only for single images") — so a
resolution with no eligible clip is skipped (all-video selection → only
low+medium = 4 jobs, no high). The launch route reports the job count via
the X-Calibration-Jobs response header.

Harness: the same on-disk reload-and-seed pattern as
test_admin_prompts_tab.py. The launch route needs a LiveCtx — the
in-process test app boots offline (app.state.live_ctx is None), so the
live-required tests set a minimal stub on app.state.live_ctx AND
monkeypatch start_job_in_background to a no-op recorder. That cleanly
exercises the orchestration without running real background tasks.
"""

import asyncio
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed_prompt(db_path) -> int:
    """Insert one prompt + version; return the version_id."""
    import aiosqlite

    from backend.app.repositories.prompts import PromptsRepo

    async with aiosqlite.connect(db_path) as conn:
        repo = PromptsRepo()
        _pid, vid = await repo.create_with_initial_version(
            conn,
            name="CalPrompt",
            description="test prompt",
            body="Identify scenes.",
            target_map={"scenes": {"kind": "markers"}},
            output_schema={"type": "object"},
            model="gemini-2.5-flash-lite",
            media_resolution="low",
        )
        return vid


async def _seed_clip(db_path, clip_id: int, handle: str) -> None:
    """Seed one clip_cache row whose upstream_handle drives media-kind
    classification (`.jpg` → image, `.mov` → video+audio)."""
    import aiosqlite

    from backend.app.archive.model import CanonicalClip, MediaRef
    from backend.app.repositories.clip_cache import ClipCacheRepo

    async with aiosqlite.connect(db_path) as conn:
        clip = CanonicalClip(
            key=("catdv", str(clip_id)),
            name=f"clip {clip_id}",
            duration_secs=12.5,
            fps=25.0,
            markers=(),
            fields={},
            notes={},
            media=MediaRef(
                mime_type=None,
                size_bytes=None,
                cached_path=None,
                upstream_handle=handle,
            ),
            provider_data={},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        await ClipCacheRepo().upsert(conn, clip=clip, catalog_id="test-catalog")
        await conn.commit()


class _LiveStub:
    """Minimal stand-in for LiveCtx — start_job_in_background is
    monkeypatched, so the stub is never dereferenced beyond the
    `is None` check in the route."""


def _recorder(calls: list[dict]):
    def _rec(core, live, job_id, **kw):
        calls.append({"job_id": job_id, **kw})

    return _rec


def test_calibrate_offline_503(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = None
        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["1", "2", "3"]},
        )
        assert r.status_code == 503


def test_calibrate_requires_at_least_one_clip(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = _LiveStub()
        # Omitting clip_ids → FastAPI 422 for the required Form field.
        r = client.post(f"/admin/prompts/{vid}/calibrate", data={})
        assert r.status_code == 422


def test_calibrate_unknown_version_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompt(tmp_path / "app.db"))
        client.app.state.live_ctx = _LiveStub()
        r = client.post(
            "/admin/prompts/999999/calibrate",
            data={"clip_ids": ["1", "2", "3"]},
        )
        assert r.status_code == 404


def test_calibrate_one_clip_succeeds(monkeypatch, tmp_path):
    """A single IMAGE clip → high is eligible → 6 jobs (low/medium/high × 2)."""
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "photo.jpg"))
        client.app.state.live_ctx = _LiveStub()

        calls: list[dict] = []
        from backend.app.routes.pages import admin as admin_mod

        monkeypatch.setattr(admin_mod, "start_job_in_background", _recorder(calls))

        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["11"]},
        )
        assert r.status_code == 200
        assert r.headers["X-Calibration-Jobs"] == "6"
        assert len(calls) == 6
        resolutions = sorted(c["force_resolution"] for c in calls)
        assert resolutions == ["high", "high", "low", "low", "medium", "medium"]
        assert all(c["record_only"] is True for c in calls)


def test_calibrate_video_clips_skip_high(monkeypatch, tmp_path):
    """All-video selection → only low+medium (4 jobs); NO high job, and no
    recorded launch carries force_resolution='high'."""
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "shotA.mov"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 22, "shotB.mov"))
        client.app.state.live_ctx = _LiveStub()

        calls: list[dict] = []
        from backend.app.routes.pages import admin as admin_mod

        monkeypatch.setattr(admin_mod, "start_job_in_background", _recorder(calls))

        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["11", "22"]},
        )
        assert r.status_code == 200
        assert r.headers["X-Calibration-Jobs"] == "4"
        assert len(calls) == 4
        resolutions = sorted(c["force_resolution"] for c in calls)
        assert resolutions == ["low", "low", "medium", "medium"]
        # Critically: a HIGH job must NEVER contain a video clip → no high at all.
        assert all(c["force_resolution"] != "high" for c in calls)

        # Every created job holds both eligible (video) clips.
        from backend.app.repositories.jobs import JobsRepo
        import aiosqlite

        async def _check():
            async with aiosqlite.connect(tmp_path / "app.db") as conn:
                cur = await conn.execute(
                    "SELECT id FROM jobs WHERE run_group LIKE 'calibration:%'"
                )
                job_ids = [row[0] for row in await cur.fetchall()]
                assert len(job_ids) == 4
                repo = JobsRepo()
                for jid in job_ids:
                    items = await repo.list_items(conn, jid)
                    assert sorted(i.catdv_clip_id for i in items) == [11, 22]

        asyncio.run(_check())


def test_calibrate_mixed_high_only_images(monkeypatch, tmp_path):
    """1 image + 1 video → low/medium jobs hold BOTH clips; the high jobs
    hold ONLY the image clip."""
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "photo.jpg"))  # image
        asyncio.run(_seed_clip(tmp_path / "app.db", 22, "shot.mov"))  # video
        client.app.state.live_ctx = _LiveStub()

        calls: list[dict] = []
        from backend.app.routes.pages import admin as admin_mod

        monkeypatch.setattr(admin_mod, "start_job_in_background", _recorder(calls))

        r = client.post(
            f"/admin/prompts/{vid}/calibrate",
            data={"clip_ids": ["11", "22"]},
        )
        assert r.status_code == 200
        assert r.headers["X-Calibration-Jobs"] == "6"

        # Map each recorded launch to its persisted job items.
        from backend.app.repositories.jobs import JobsRepo
        import aiosqlite

        async def _items_by_job():
            async with aiosqlite.connect(tmp_path / "app.db") as conn:
                repo = JobsRepo()
                out = {}
                for c in calls:
                    items = await repo.list_items(conn, c["job_id"])
                    out[c["job_id"]] = sorted(i.catdv_clip_id for i in items)
                return out

        items = asyncio.run(_items_by_job())
        for c in calls:
            clip_ids = items[c["job_id"]]
            if c["force_resolution"] == "high":
                assert clip_ids == [11]  # image only — never the video clip
            else:
                assert clip_ids == [11, 22]


def test_calibrate_estimate_returns_projected_cost(monkeypatch, tmp_path):
    """Image clips: runs include high → 3 res × 2 × N."""
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "a.jpg"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 22, "b.jpg"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 33, "c.jpg"))
        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": [11, 22, 33]},
        )
        assert r.status_code == 200
        body = r.json()
        assert "projected_cost_usd" in body  # number or null
        assert body["runs"] == 18  # 3 res × 2 repeats × 3 image clips


def test_calibrate_estimate_video_skips_high(monkeypatch, tmp_path):
    """Video clips: high contributes 0 → only low+medium → 2 res × 2 × N."""
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 11, "a.mov"))
        asyncio.run(_seed_clip(tmp_path / "app.db", 22, "b.mov"))
        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": [11, 22]},
        )
        assert r.status_code == 200
        body = r.json()
        assert "projected_cost_usd" in body
        assert body["runs"] == 8  # 2 res (low+medium) × 2 repeats × 2 clips


def test_calibrate_estimate_empty_clip_ids(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        vid = asyncio.run(_seed_prompt(tmp_path / "app.db"))
        r = client.post(
            f"/admin/prompts/{vid}/calibrate/estimate",
            json={"clip_ids": []},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"projected_cost_usd": None, "runs": 0}
