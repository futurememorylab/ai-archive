"""WorkspaceManager.prepare must NOT lock a clip into permanent 'error'
state for transient provider failures. A VPN flap mid-prep would
otherwise leave the user with workspace clips that never recover."""

from pathlib import Path

import httpx
import pytest

from backend.app.archive.errors import NotFoundError
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.services.workspace_manager import WorkspaceManager

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


class _TransientProvider:
    """Raises transport errors on every get_clip; capabilities.media_is_local
    True so we only test the metadata branch."""

    class _Caps:
        media_is_local = True

    capabilities = _Caps()

    async def get_clip(self, pcid: str):
        request = httpx.Request("GET", "http://example/x")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("flaky", request=request, response=response)


class _NotFoundProvider:
    class _Caps:
        media_is_local = True

    capabilities = _Caps()

    async def get_clip(self, pcid: str):
        raise NotFoundError(f"{pcid} not found")


@pytest.mark.asyncio
async def test_prepare_marks_transient_error_not_permanent(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = WorkspacesRepo()
        ws_id = await repo.create(
            conn, name="ws", provider_id="catdv", catalog_id="1"
        )
        await repo.add_clips(conn, ws_id, [("catdv", "42")])

        mgr = WorkspaceManager(
            workspaces_repo=repo,
            provider=_TransientProvider(),
            proxy_resolver=None,
            db_provider=lambda: conn,
        )
        events = await mgr.prepare_all(ws_id)

        states = [ev.state for ev in events]
        assert "error" not in states, (
            f"transient transport error should NOT be terminal; got {states}"
        )
        # The state should be 'transient_error' so the user can retry.
        assert any(ev.state == "transient_error" for ev in events), states


@pytest.mark.asyncio
async def test_prepare_marks_permanent_error_for_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = WorkspacesRepo()
        ws_id = await repo.create(
            conn, name="ws", provider_id="catdv", catalog_id="1"
        )
        await repo.add_clips(conn, ws_id, [("catdv", "42")])

        mgr = WorkspaceManager(
            workspaces_repo=repo,
            provider=_NotFoundProvider(),
            proxy_resolver=None,
            db_provider=lambda: conn,
        )
        events = await mgr.prepare_all(ws_id)
        assert any(ev.state == "error" for ev in events)
