"""JobsRepo — persists / reads `jobs` and `job_items`. Called by the
jobs route and the annotator service."""

from datetime import UTC, datetime

import aiosqlite

from backend.app.models.job import ItemStatus, Job, JobItem, JobStatus


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


TRANSIENT_STATUSES = ("resolving", "uploading", "prompting")


class JobsRepo:
    async def create_job(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_ids: list[int],
        kind: str | None = None,
    ) -> int:
        cur = await conn.execute(
            """
            INSERT INTO jobs (prompt_version_id, status, created_at, total_clips, kind)
            VALUES (?, 'pending', ?, ?, ?)
            """,
            (prompt_version_id, _now_iso(), len(clip_ids), kind),
        )
        job_id = cur.lastrowid
        assert job_id is not None
        for clip_id in clip_ids:
            await conn.execute(
                "INSERT INTO job_items (job_id, catdv_clip_id, status) VALUES (?, ?, 'pending')",
                (job_id, clip_id),
            )
        await conn.commit()
        return job_id

    async def get_job(self, conn: aiosqlite.Connection, job_id: int) -> Job:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        return Job(
            id=row[0], prompt_version_id=row[1], status=row[2],
            total_clips=row[3], notes=row[4], kind=row[5],
        )

    async def list_jobs(self, conn: aiosqlite.Connection, *, limit: int = 50) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind "
            "FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            Job(
                id=r[0], prompt_version_id=r[1], status=r[2],
                total_clips=r[3], notes=r[4], kind=r[5],
            )
            for r in await cur.fetchall()
        ]

    async def update_status(
        self, conn: aiosqlite.Connection, job_id: int, status: JobStatus
    ) -> None:
        if status == "running":
            await conn.execute(
                "UPDATE jobs SET status = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                (status, _now_iso(), job_id),
            )
        elif status in ("completed", "failed", "cancelled"):
            await conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
                (status, _now_iso(), job_id),
            )
        else:
            await conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        await conn.commit()

    async def list_items(self, conn: aiosqlite.Connection, job_id: int) -> list[JobItem]:
        cur = await conn.execute(
            """
            SELECT id, job_id, catdv_clip_id, status, error_message, annotation_id
            FROM job_items WHERE job_id = ? ORDER BY id
            """,
            (job_id,),
        )
        return [
            JobItem(
                id=r[0],
                job_id=r[1],
                catdv_clip_id=r[2],
                status=r[3],
                error_message=r[4],
                annotation_id=r[5],
            )
            for r in await cur.fetchall()
        ]

    async def update_item_status(
        self,
        conn: aiosqlite.Connection,
        item_id: int,
        status: ItemStatus,
        *,
        error: str | None = None,
    ) -> None:
        await conn.execute(
            "UPDATE job_items SET status = ?, error_message = ? WHERE id = ?",
            (status, error, item_id),
        )
        await conn.commit()

    async def attach_annotation(
        self, conn: aiosqlite.Connection, item_id: int, annotation_id: int
    ) -> None:
        await conn.execute(
            "UPDATE job_items SET annotation_id = ? WHERE id = ?",
            (annotation_id, item_id),
        )
        await conn.commit()

    async def list_running(self, conn: aiosqlite.Connection) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind "
            "FROM jobs WHERE status = 'running' ORDER BY id DESC",
        )
        return [
            Job(
                id=r[0], prompt_version_id=r[1], status=r[2],
                total_clips=r[3], notes=r[4], kind=r[5],
            )
            for r in await cur.fetchall()
        ]

    async def progress(
        self, conn: aiosqlite.Connection, job_id: int
    ) -> tuple[int, int, int]:
        """(done, total, errors) for a job. 'done' = items past the
        in-flight statuses (pending/resolving/uploading/prompting)."""
        cur = await conn.execute(
            """
            SELECT
              SUM(CASE WHEN status NOT IN
                  ('pending','resolving','uploading','prompting') THEN 1 ELSE 0 END),
              COUNT(*),
              SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)
            FROM job_items WHERE job_id = ?
            """,
            (job_id,),
        )
        row = await cur.fetchone()
        return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))

    async def reset_transient(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute(
            f"UPDATE job_items SET status = 'pending' WHERE status IN "
            f"({','.join('?' * len(TRANSIENT_STATUSES))})",
            TRANSIENT_STATUSES,
        )
        await conn.commit()
        return cur.rowcount or 0
