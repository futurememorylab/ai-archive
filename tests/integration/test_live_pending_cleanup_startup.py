from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.startup import run_startup_cleanup

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_startup_cleanup_drops_stale_pending(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="old", clip_id=1, prompt_version=None)
        two_h_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        await conn.execute(
            "UPDATE live_sessions SET created_at=? WHERE id='old'",
            (two_h_ago,),
        )
        await conn.commit()

        n = await run_startup_cleanup(conn)
        assert n >= 1
        with pytest.raises(LookupError):
            await repo.get(conn, "old")
