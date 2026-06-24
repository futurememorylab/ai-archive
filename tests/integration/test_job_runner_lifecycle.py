"""JobRunner is wired into the live context and recovers orphaned jobs at
boot (ADR 0125).

Non-negotiable assertions (per the plan):
  (a) build_context populates `live.job_runner` whenever the live stack
      run_job needs is present (a real proxy_resolver);
  (b) a job left 'running' by a killed worker is requeued by
      `job_runner.start()` and run to completion.

Harness mirrors test_context_manual_boot.py: PROXY_SOURCE=rest yields a real
proxy_resolver, and GcsService/GeminiService are stubbed (CI has no GCP creds).
The seeded orphan's single item is already terminal ('annotated'), so the
resumed run_job is a no-op — it never calls Gemini/the resolver — and the job
finalises to 'completed'. That exercises the whole wired path
(start → requeue → claim → run_job → complete) without any network.
"""

import asyncio
import importlib

import pytest


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Build the live stack (the session conftest defaults CATDV_OFFLINE=true).
    monkeypatch.setenv("CATDV_OFFLINE", "false")


async def _build_live(monkeypatch, tmp_path):
    from backend.app.services import gcs as gcs_mod
    from backend.app.services import gemini as gemini_mod

    class _StubGcs:
        def __init__(self, *args, **kwargs):
            self.bucket_name = "b"
            self._bucket = type("FakeBucket", (), {"exists": staticmethod(lambda: True)})()

    class _StubGemini:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)

    from backend.app import context as ctx_mod

    importlib.reload(ctx_mod)
    from backend.app.settings import Settings

    return await ctx_mod.build_context(Settings(), init_external=True)


@pytest.mark.asyncio
async def test_build_context_wires_job_runner(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    core, live = await _build_live(monkeypatch, tmp_path)
    try:
        assert live is not None
        assert live.proxy_resolver is not None
        assert live.job_runner is not None
    finally:
        await (live or core).aclose()


@pytest.mark.asyncio
async def test_boot_resume_requeues_and_runs_orphan(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    core, live = await _build_live(monkeypatch, tmp_path)
    try:
        from backend.app.repositories.prompts import PromptsRepo

        _pid, vid = await PromptsRepo().create_with_initial_version(
            core.db, name="p", description=None, body="b",
            target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
        )
        # Seed an orphaned 'running' job whose only item is already terminal,
        # so the resumed run_job has nothing to process (no network).
        jid = await core.jobs_repo.create_job(core.db, prompt_version_id=vid, clip_ids=[10])
        await core.jobs_repo.update_status(core.db, jid, "running")
        items = await core.jobs_repo.list_items(core.db, jid)
        await core.jobs_repo.update_item_status(core.db, items[0].id, "annotated")

        await live.job_runner.start()
        # The worker requeues (running→pending), claims, and runs it.
        for _ in range(100):
            status = (await core.jobs_repo.get_job(core.db, jid)).status
            if status == "completed":
                break
            await asyncio.sleep(0.02)
        assert (await core.jobs_repo.get_job(core.db, jid)).status == "completed"
    finally:
        await (live or core).aclose()
