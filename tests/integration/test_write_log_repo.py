import pytest

from backend.app.repositories.write_log import WriteLogRepo


@pytest.mark.asyncio
async def test_record_writes_log_row(db):
    repo = WriteLogRepo()
    await repo.record(
        db,
        catdv_clip_id=42,
        annotation_id=None,
        payload={"markers": [{"name": "x"}]},
        response={"ID": 42, "modifyDate": "2026-05-18"},
        status="ok",
    )
    cur = await db.execute("SELECT count(*) FROM write_log WHERE catdv_clip_id = 42")
    assert (await cur.fetchone())[0] == 1
