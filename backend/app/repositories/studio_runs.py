"""StudioRunsRepo — persists studio run history and serves UI lookups.

One row per execution; never deleted. UI queries:
  * latest_for_pair(version, clip)        — right-pane output
  * versions_run_on_clip(clip)            — clip-card run-dot indicators
"""

import json
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.studio import StudioRun


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_RUN_COLS = (
    "id, prompt_version_id, clip_id, job_id, status, output_json, "
    "duration_s, tokens_in, tokens_out, cost_usd, model, error, "
    "started_at, finished_at"
)


def _row_to_run(row) -> StudioRun:
    return StudioRun(
        id=row[0],
        prompt_version_id=row[1],
        clip_id=row[2],
        job_id=row[3],
        status=row[4],
        output_json=json.loads(row[5]) if row[5] else None,
        duration_s=row[6],
        tokens_in=row[7],
        tokens_out=row[8],
        cost_usd=row[9],
        model=row[10],
        error=row[11],
        started_at=row[12],
        finished_at=row[13],
    )


class StudioRunsRepo:
    async def create_pending(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_id: int,
        model: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_run(prompt_version_id, clip_id, status, model, started_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (prompt_version_id, clip_id, model, _now_iso()),
        )
        rid = cur.lastrowid
        assert rid is not None
        await conn.commit()
        return rid

    async def attach_job(
        self, conn: aiosqlite.Connection, run_id: int, *, job_id: int
    ) -> None:
        await conn.execute(
            "UPDATE studio_run SET job_id = ? WHERE id = ?", (job_id, run_id)
        )
        await conn.commit()

    async def mark_running(self, conn: aiosqlite.Connection, run_id: int) -> None:
        await conn.execute(
            "UPDATE studio_run SET status = 'running' WHERE id = ?", (run_id,)
        )
        await conn.commit()

    async def complete_ok(
        self,
        conn: aiosqlite.Connection,
        run_id: int,
        *,
        output_json: dict,
        duration_s: float,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        await conn.execute(
            "UPDATE studio_run SET status = 'ok', output_json = ?, duration_s = ?, "
            "tokens_in = ?, tokens_out = ?, cost_usd = ?, finished_at = ? "
            "WHERE id = ?",
            (
                json.dumps(output_json),
                duration_s,
                tokens_in,
                tokens_out,
                cost_usd,
                _now_iso(),
                run_id,
            ),
        )
        await conn.commit()

    async def complete_error(
        self, conn: aiosqlite.Connection, run_id: int, *, error: str
    ) -> None:
        await conn.execute(
            "UPDATE studio_run SET status = 'error', error = ?, finished_at = ? "
            "WHERE id = ?",
            (error, _now_iso(), run_id),
        )
        await conn.commit()

    async def get(self, conn: aiosqlite.Connection, run_id: int) -> StudioRun:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_run WHERE id = ?", (run_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"studio_run {run_id} not found")
        return _row_to_run(row)

    async def latest_for_pair(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_id: int,
    ) -> StudioRun | None:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_run "
            "WHERE prompt_version_id = ? AND clip_id = ? "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
            (prompt_version_id, clip_id),
        )
        row = await cur.fetchone()
        return _row_to_run(row) if row else None

    async def versions_run_on_clip(
        self, conn: aiosqlite.Connection, *, clip_id: int
    ) -> list[int]:
        """Returns distinct prompt_version_ids that have a successful run on this clip."""
        cur = await conn.execute(
            "SELECT DISTINCT prompt_version_id FROM studio_run "
            "WHERE clip_id = ? AND status = 'ok'",
            (clip_id,),
        )
        return [r[0] for r in await cur.fetchall()]
