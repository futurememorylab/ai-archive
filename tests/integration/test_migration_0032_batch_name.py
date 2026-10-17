"""0032 names every existing batch after its contents: the first clip's cached
name (else the name its latest annotation recorded, else "Clip <id>") plus
"and N more" for the batch's other distinct clips, written to every job of the
batch. Studio jobs stay unnamed."""

import shutil
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SQL = (MIGRATIONS / "0032_jobs_batch_name.sql").read_text()


async def _pre_0032_db(tmp_path, conn):
    """Migrate a scratch copy of every migration before 0032."""
    older = tmp_path / "older"
    older.mkdir()
    for p in MIGRATIONS.iterdir():
        if p.suffix in (".sql", ".txt") and p.name < "0032":
            shutil.copy(p, older / p.name)
    await apply_migrations(conn, older)


async def _version(conn) -> int:
    cur = await conn.execute(
        "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
        "VALUES ('p', NULL, 0, '2026-01-01', '2026-01-01')"
    )
    pid = cur.lastrowid
    cur = await conn.execute(
        "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
        "output_schema, model, created_at, updated_at) "
        "VALUES (?, 1, 'production', 'b', '{}', '{}', 'm', '2026-01-01', '2026-01-01')",
        (pid,),
    )
    return cur.lastrowid


async def _job(conn, vid, clip_ids, *, run_group=None, kind=None) -> int:
    cur = await conn.execute(
        "INSERT INTO jobs (prompt_version_id, status, created_at, total_clips, run_group, kind) "
        "VALUES (?, 'completed', '2026-01-01', ?, ?, ?)",
        (vid, len(clip_ids), run_group, kind),
    )
    jid = cur.lastrowid
    for cid in clip_ids:
        await conn.execute(
            "INSERT INTO job_items (job_id, catdv_clip_id, status) VALUES (?, ?, 'review_ready')",
            (jid, cid),
        )
    return jid


async def _cache_name(conn, clip_id, name):
    await conn.execute(
        "INSERT INTO clip_cache (provider_id, provider_clip_id, name, catalog_id, "
        "duration_secs, fps, canonical_json, fetched_at) "
        "VALUES ('catdv', ?, ?, '7', 1, 25, '{}', '2026-01-01')",
        (str(clip_id), name),
    )


async def _annotated_as(conn, vid, job_id, clip_id, name):
    await conn.execute(
        "INSERT INTO annotations (catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, "
        "model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-01-01')",
        (clip_id, name, vid, job_id),
    )


async def _names(conn) -> dict[int, str | None]:
    cur = await conn.execute("SELECT id, batch_name FROM jobs ORDER BY id")
    return {r[0]: r[1] for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_backfills_existing_batches_from_their_clips(tmp_path):
    async with open_db(tmp_path / "t.db") as conn:
        await _pre_0032_db(tmp_path, conn)
        vid = await _version(conn)
        await _cache_name(conn, 101, "SIKL_0412 Výlet do Tater")
        await _cache_name(conn, 201, "Praha 1925")
        single = await _job(conn, vid, [201])
        # One bulk action = two per-kind jobs sharing a run_group: 3 distinct clips.
        bulk_a = await _job(conn, vid, [101, 102], run_group="rg-1")
        bulk_b = await _job(conn, vid, [103], run_group="rg-1")
        uncached = await _job(conn, vid, [999])
        # Not in clip_cache, but annotated: the annotation kept the clip's name.
        annotated = await _job(conn, vid, [301, 302])
        await _annotated_as(conn, vid, annotated, 301, "Harbour cranes, reel 3")
        studio = await _job(conn, vid, [101], kind="studio")
        empty = await _job(conn, vid, [])
        await conn.commit()

        await conn.executescript(SQL)

        names = await _names(conn)
        assert names[single] == "Praha 1925"
        assert names[bulk_a] == "SIKL_0412 Výlet do Tater and 2 more"
        assert names[bulk_b] == "SIKL_0412 Výlet do Tater and 2 more"
        assert names[uncached] == "Clip 999"
        assert names[annotated] == "Harbour cranes, reel 3 and 1 more"
        assert names[studio] is None
        assert names[empty] is None


@pytest.mark.asyncio
async def test_runs_on_an_empty_database(tmp_path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute("SELECT batch_name FROM jobs")
        assert await cur.fetchall() == []
