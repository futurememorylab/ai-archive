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
        run_group: str | None = None,
    ) -> int:
        cur = await conn.execute(
            """
            INSERT INTO jobs
              (prompt_version_id, status, created_at, total_clips, kind, run_group)
            VALUES (?, 'pending', ?, ?, ?, ?)
            """,
            (prompt_version_id, _now_iso(), len(clip_ids), kind, run_group),
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
            "SELECT id, prompt_version_id, status, total_clips, notes, kind, run_group "
            "FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        return Job(
            id=row[0],
            prompt_version_id=row[1],
            status=row[2],
            total_clips=row[3],
            notes=row[4],
            kind=row[5],
            run_group=row[6],
        )

    async def list_jobs(self, conn: aiosqlite.Connection, *, limit: int = 50) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind, run_group "
            "FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            Job(
                id=r[0],
                prompt_version_id=r[1],
                status=r[2],
                total_clips=r[3],
                notes=r[4],
                kind=r[5],
                run_group=r[6],
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
            "SELECT id, prompt_version_id, status, total_clips, notes, kind, run_group "
            "FROM jobs WHERE status = 'running' AND COALESCE(kind, '') != 'studio' "
            "ORDER BY id DESC",
        )
        return [
            Job(
                id=r[0],
                prompt_version_id=r[1],
                status=r[2],
                total_clips=r[3],
                notes=r[4],
                kind=r[5],
                run_group=r[6],
            )
            for r in await cur.fetchall()
        ]

    async def progress(self, conn: aiosqlite.Connection, job_id: int) -> tuple[int, int, int]:
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

    async def phase_counts(self, conn: aiosqlite.Connection, job_id: int) -> dict[str, int]:
        """Per-phase item counts for the topbar indicator's phase breakdown:
        `caching` (resolving+uploading — proxy fetch + GCS upload), `annotating`
        (prompting — the Gemini call), `queued` (pending), `done`, `error`.
        One grouped query. Lets the user see the slow upload phase instead of a
        bare 'Annotating X/Y'. See ADR 0093."""
        cur = await conn.execute(
            "SELECT status, COUNT(*) FROM job_items WHERE job_id = ? GROUP BY status",
            (job_id,),
        )
        rows = {s: int(n) for s, n in await cur.fetchall()}
        in_flight = ("pending", "resolving", "uploading", "prompting", "error")
        return {
            "caching": rows.get("resolving", 0) + rows.get("uploading", 0),
            "annotating": rows.get("prompting", 0),
            "queued": rows.get("pending", 0),
            "error": rows.get("error", 0),
            "done": sum(n for s, n in rows.items() if s not in in_flight),
        }

    async def reset_transient(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute(
            f"UPDATE job_items SET status = 'pending' WHERE status IN "
            f"({','.join('?' * len(TRANSIENT_STATUSES))})",
            TRANSIENT_STATUSES,
        )
        await conn.commit()
        return cur.rowcount or 0

    # --- Batches hub aggregation (read-only, offline-safe) -------------
    # A "batch" = a group of jobs sharing a run_group, OR a singleton job with
    # no run_group (keyed 'job:<id>'). Studio jobs are excluded. Each method
    # below issues a single grouped query — never a per-batch loop — so the
    # /batches read path stays O(1) in batch count (ADR 0046).

    _BATCHES_SQL = """
        WITH batch AS (
          SELECT
            COALESCE(j.run_group, 'job:' || j.id)               AS batch_key,
            MIN(j.id)                                           AS primary_job_id,
            MIN(j.created_at)                                   AS started_at,
            COUNT(DISTINCT j.prompt_version_id)                 AS prompt_count,
            GROUP_CONCAT(j.id)                                  AS job_ids_csv,
            SUM(CASE WHEN j.status = 'running' THEN 1 ELSE 0 END) AS running_jobs
          FROM jobs j
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        ),
        items AS (
          SELECT
            COALESCE(j.run_group, 'job:' || j.id) AS batch_key,
            COUNT(*) AS ran,  -- total items dispatched across all statuses
            SUM(CASE WHEN ji.status = 'error' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN ji.status NOT IN
                ('pending','resolving','uploading','prompting','error')
                THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN ji.status IN
                ('pending','resolving','uploading','prompting')
                THEN 1 ELSE 0 END) AS in_flight
          FROM job_items ji
          JOIN jobs j ON j.id = ji.job_id
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        ),
        reviewed AS (
          SELECT
            COALESCE(j.run_group, 'job:' || j.id) AS batch_key,
            COUNT(DISTINCT ri.catdv_clip_id) AS awaiting_clips,
            MIN(ri.catdv_clip_id) AS first_pending_clip_id
          FROM jobs j
          JOIN annotations a ON a.job_id = j.id
          JOIN review_items ri ON ri.annotation_id = a.id AND ri.applied_at IS NULL
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        ),
        syncing AS (
          -- Clips in the batch with a write-back still in the queue
          -- (pending/in_flight). Sourced from pending_operations — the SAME
          -- source as the topbar sync chip (PendingOperationsRepo.count_actionable)
          -- — so the batch "Syncing N" pill and the chip can never disagree.
          -- (review_items.synced_at is unreliable for historical applies made
          -- before that column existed, so it must NOT drive this.)
          SELECT
            COALESCE(j.run_group, 'job:' || j.id) AS batch_key,
            COUNT(DISTINCT ji.catdv_clip_id) AS syncing_clips
          FROM jobs j
          JOIN job_items ji ON ji.job_id = j.id
          JOIN pending_operations po
            ON po.provider_id = 'catdv'
           AND po.provider_clip_id = CAST(ji.catdv_clip_id AS TEXT)
           AND po.status IN ('pending', 'in_flight')
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        ),
        problems AS (
          -- Clips in the batch whose write-back FAILED (exhausted retries) or
          -- hit a CONFLICT — the queue is stuck, the change never reached CatDV.
          -- Same source as the topbar sync chip, so the batch row and the chip
          -- can never disagree. Without this, a stuck write-back would let the
          -- batch read green "Applied" (see ADR 0096).
          SELECT
            COALESCE(j.run_group, 'job:' || j.id) AS batch_key,
            COUNT(DISTINCT ji.catdv_clip_id) AS problem_clips
          FROM jobs j
          JOIN job_items ji ON ji.job_id = j.id
          JOIN pending_operations po
            ON po.provider_id = 'catdv'
           AND po.provider_clip_id = CAST(ji.catdv_clip_id AS TEXT)
           AND po.status IN ('failed', 'conflict')
          WHERE COALESCE(j.kind, '') != 'studio'
          GROUP BY batch_key
        )
        SELECT
          b.batch_key                   AS batch_key,
          b.primary_job_id              AS primary_job_id,
          b.started_at                  AS started_at,
          b.job_ids_csv                 AS job_ids_csv,
          b.prompt_count                AS prompt_count,
          b.running_jobs                AS running_jobs,
          p.name                        AS prompt_name,
          pv.version_num                AS version_num,
          pv.model                      AS model,
          COALESCE(i.ran, 0)            AS ran,
          COALESCE(i.failed, 0)         AS failed,
          COALESCE(i.completed, 0)      AS completed,
          COALESCE(i.in_flight, 0)      AS in_flight,
          COALESCE(r.awaiting_clips, 0) AS awaiting_clips,
          r.first_pending_clip_id       AS first_pending_clip_id,
          COALESCE(s.syncing_clips, 0)  AS syncing_clips,
          COALESCE(pr.problem_clips, 0) AS problem_clips
        FROM batch b
        JOIN jobs pj ON pj.id = b.primary_job_id
        LEFT JOIN prompt_versions pv ON pv.id = pj.prompt_version_id
        LEFT JOIN prompts p ON p.id = pv.prompt_id
        LEFT JOIN items i ON i.batch_key = b.batch_key
        LEFT JOIN reviewed r ON r.batch_key = b.batch_key
        LEFT JOIN syncing s ON s.batch_key = b.batch_key
        LEFT JOIN problems pr ON pr.batch_key = b.batch_key
        ORDER BY b.started_at DESC, b.primary_job_id DESC
        LIMIT ?
    """

    async def list_batches(self, conn: aiosqlite.Connection, *, limit: int = 50) -> list[dict]:
        """One row per batch (run_group, or 'job:<id>' singleton), newest
        first. `job_ids` is the sorted list of member job ids."""
        cur = await conn.execute(self._BATCHES_SQL, (limit,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]
        for r in rows:
            csv = r.pop("job_ids_csv") or ""
            r["job_ids"] = sorted(int(x) for x in csv.split(",") if x)
        return rows

    async def count_total_batches(self, conn: aiosqlite.Connection) -> int:
        """Grand total of distinct batches (run_groups + singleton jobs),
        excluding studio jobs. Powers the 'Batches' metric."""
        cur = await conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT COALESCE(run_group, 'job:' || id) AS bk
              FROM jobs WHERE COALESCE(kind, '') != 'studio'
              GROUP BY bk
            )
            """
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def failed_items_for_jobs(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> list[dict]:
        """Failed (status='error') items across the given jobs, with the clip
        name resolved from clip_cache when available. `job_ids` is bounded by
        the page's batch limit, so a single IN clause is safe (one statement,
        not a per-row loop)."""
        if not job_ids:
            return []
        placeholders = ",".join("?" * len(job_ids))
        sql = f"""
            SELECT ji.job_id        AS job_id,
                   ji.catdv_clip_id AS catdv_clip_id,
                   ji.error_message AS error_message,
                   cc.name          AS clip_name
            FROM job_items ji
            LEFT JOIN clip_cache cc
              ON cc.provider_id = 'catdv'
             AND cc.provider_clip_id = CAST(ji.catdv_clip_id AS TEXT)
            WHERE ji.status = 'error' AND ji.job_id IN ({placeholders})
            ORDER BY ji.job_id, ji.catdv_clip_id
        """
        cur = await conn.execute(sql, tuple(job_ids))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]
