import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as c:
        await apply_migrations(c, MIGRATIONS)
        yield c


@pytest.mark.asyncio
async def test_insert_pending_and_get(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=3)
    s = await repo.get(conn, "abc")
    assert s.state == "pending"
    assert s.clip_id == 42
    assert s.prompt_version == 3


@pytest.mark.asyncio
async def test_mark_active_sets_started_at(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    s = await repo.get(conn, "abc")
    assert s.state == "active"
    assert s.started_at is not None


@pytest.mark.asyncio
async def test_mark_ended_persists_transcript_and_reason(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    transcript = [{"role": "user", "text": "ahoj", "ts": 1}]
    await repo.mark_ended(
        conn,
        "abc",
        end_reason="user_stop",
        transcript_json=json.dumps(transcript, ensure_ascii=False),
        frame_count=2,
    )
    s = await repo.get(conn, "abc")
    assert s.state == "ended"
    assert s.end_reason == "user_stop"
    assert s.ended_at is not None
    assert s.frame_count == 2
    assert json.loads(s.transcript_json) == transcript


@pytest.mark.asyncio
async def test_set_summary_is_idempotent(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(conn, "abc", end_reason="user_stop", transcript_json="[]")
    await repo.set_summary(conn, "abc", "První shrnutí.")
    overwrote = await repo.set_summary(conn, "abc", "Druhý pokus.")
    assert overwrote is False
    s = await repo.get(conn, "abc")
    assert s.summary_cs == "První shrnutí."


@pytest.mark.asyncio
async def test_list_by_clip_desc(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="a", clip_id=42, prompt_version=None)
    await repo.insert_pending(conn, id="b", clip_id=42, prompt_version=None)
    await repo.insert_pending(conn, id="c", clip_id=99, prompt_version=None)
    rows = await repo.list_by_clip(conn, 42)
    ids = [r.id for r in rows]
    assert set(ids) == {"a", "b"}


@pytest.mark.asyncio
async def test_get_missing_raises(conn):
    repo = LiveSessionsRepo()
    with pytest.raises(LookupError):
        await repo.get(conn, "no-such-id")


@pytest.mark.asyncio
async def test_cleanup_stale_pending_reaps_only_old_pending(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="old", clip_id=1, prompt_version=None)
    await repo.insert_pending(conn, id="fresh", clip_id=1, prompt_version=None)
    await repo.insert_pending(conn, id="active-old", clip_id=1, prompt_version=None)
    await repo.mark_active(conn, "active-old")

    two_h_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    await conn.execute(
        "UPDATE live_sessions SET created_at = ? WHERE id IN ('old','active-old')",
        (two_h_ago,),
    )
    await conn.commit()

    reaped = await repo.cleanup_stale_pending(conn, older_than_hours=1)
    assert reaped == 1
    assert (await repo.get(conn, "fresh")).id == "fresh"
    assert (await repo.get(conn, "active-old")).id == "active-old"
    with pytest.raises(LookupError):
        await repo.get(conn, "old")
