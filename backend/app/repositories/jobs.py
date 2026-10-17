"""JobsRepo — persists / reads `jobs` and `job_items`. Called by the
jobs route and the annotator service."""

from datetime import UTC, datetime

import aiosqlite

from backend.app.models.job import ItemStatus, Job, JobItem, JobStatus
from backend.app.repositories._batch import chunked_in_clause
from backend.app.repositories.review_items import for_review_where


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


TRANSIENT_STATUSES = ("resolving", "uploading", "prompting")

# Explicit partition of ItemStatus into the topbar phase buckets. Every status
# belongs to exactly one bucket (guarded by test_phase_statuses_partition_item_status),
# so a new status can't silently fall into 'done' the way a catch-all would.
_PHASE_STATUSES: dict[str, tuple[str, ...]] = {
    "queued": ("pending",),
    "caching": ("resolving", "uploading"),
    "annotating": ("prompting",),
    "error": ("error",),
    "done": ("annotated", "review_ready", "applied", "rejected", "cancelled"),
}


class JobsRepo:
    async def create_job(
        self,
        conn: aiosqlite.Connection,
        *,
        prompt_version_id: int,
        clip_ids: list[int],
        kind: str | None = None,
        run_group: str | None = None,
        force_resolution: str | None = None,
        record_only: bool = False,
        batch_name: str | None = None,
    ) -> int:
        cur = await conn.execute(
            """
            INSERT INTO jobs
              (prompt_version_id, status, created_at, total_clips, kind, run_group,
               force_resolution, record_only, batch_name)
            VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_version_id,
                _now_iso(),
                len(clip_ids),
                kind,
                run_group,
                force_resolution,
                1 if record_only else 0,
                batch_name,
            ),
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

    async def claim_next_job(self, conn: aiosqlite.Connection) -> int | None:
        """Atomically take the oldest `pending` job and mark it `running`.

        CAS, same shape as PrefetchQueueRepo.claim_next: SELECT the oldest
        pending id, then UPDATE guarded on status='pending'. If rowcount != 1
        another claim won the race (cannot happen with the single JobRunner,
        but kept for correctness), so return None. Returns the claimed job id
        or None when the queue is empty."""
        cur = await conn.execute(
            "SELECT id FROM jobs WHERE status = 'pending' ORDER BY created_at ASC, id ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            return None
        jid = int(row[0])
        upd = await conn.execute(
            "UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?) "
            " WHERE id = ? AND status = 'pending'",
            (_now_iso(), jid),
        )
        await conn.commit()
        if upd.rowcount != 1:
            return None
        return jid

    async def requeue_orphaned_running(self, conn: aiosqlite.Connection) -> int:
        """Resume jobs left 'running' by a killed worker. Mirrors the
        prefetcher's requeue_orphans (running -> re-runnable), the opposite of
        the old cancel_orphaned_running. A job runs only inside the single
        JobRunner; at boot nothing is in-flight, so any 'running' job is an
        orphan from a crash/restart/dev-reload. Flip it back to 'pending' so
        the worker re-claims it, and reset its stuck transient items
        (resolving/uploading/prompting) to 'pending' too — run_job only
        processes 'pending' items, so a transient item would otherwise be
        skipped on resume and hang forever. Terminal items (done/annotated/
        review_ready/applied/rejected/error/cancelled) are left as-is, so
        resume re-runs only unfinished work — in particular an 'error' item is
        terminal and is not re-run on resume. Returns the count of jobs
        requeued."""
        placeholders = ",".join("?" * len(TRANSIENT_STATUSES))
        await conn.execute(
            f"UPDATE job_items SET status = 'pending' "
            f" WHERE status IN ({placeholders}) "
            f"   AND job_id IN (SELECT id FROM jobs WHERE status = 'running')",
            TRANSIENT_STATUSES,
        )
        cur = await conn.execute(
            "UPDATE jobs SET status = 'pending', started_at = NULL WHERE status = 'running'"
        )
        await conn.commit()
        return cur.rowcount or 0

    async def get_job(self, conn: aiosqlite.Connection, job_id: int) -> Job:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind, run_group, "
            "force_resolution, record_only "
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
            force_resolution=row[7],
            record_only=bool(row[8]),
        )

    async def list_jobs(self, conn: aiosqlite.Connection, *, limit: int = 50) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, prompt_version_id, status, total_clips, notes, kind, run_group, "
            "batch_name FROM jobs ORDER BY id DESC LIMIT ?",
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
                batch_name=r[7],
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

    async def clip_status_by_jobs(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> dict[int, str]:
        """Merged `{catdv_clip_id: status}` across the given jobs' items in one
        chunked query — instead of a `list_items()` per job. Backs the batch
        run-status pills (clips list + the polled fragment), which are otherwise
        an N+1 over the batch's jobs on every poll. See ADR 0046."""
        out: dict[int, str] = {}
        for fragment, params in chunked_in_clause((j,) for j in job_ids):
            cur = await conn.execute(
                f"SELECT catdv_clip_id, status FROM job_items "
                f"WHERE job_id IN ({fragment}) ORDER BY job_id, id",
                params,
            )
            for clip_id, status in await cur.fetchall():
                out[int(clip_id)] = status
        return out

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
            # Plain studio (draft) runs stay hidden from the topbar indicator,
            # but calibration sweeps — which reuse kind='studio' (ADR 0116) —
            # are a deliberate admin action and must surface, so include any
            # job tagged with a calibration run_group.
            "SELECT id, prompt_version_id, status, total_clips, notes, kind, run_group "
            "FROM jobs WHERE status = 'running' "
            "AND (COALESCE(kind, '') != 'studio' OR run_group LIKE 'calibration:%') "
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

    async def find_running_item_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int
    ) -> dict | None:
        """The most recent running job touching `clip_id`, as
        `{"job_id", "item_status", "started_at"}`, or None. Powers the clip
        page's annotate-button resume after a reload (GET
        /api/jobs/active-for-clip). `started_at` (the job's created_at) lets the
        button resume its elapsed timer from the true run start, not from 0."""
        cur = await conn.execute(
            "SELECT ji.job_id, ji.status, j.created_at "
            "  FROM job_items ji JOIN jobs j ON j.id = ji.job_id "
            " WHERE ji.catdv_clip_id = ? AND j.status = 'running' "
            " ORDER BY ji.job_id DESC LIMIT 1",
            (clip_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {"job_id": int(row[0]), "item_status": row[1], "started_at": row[2]}

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
        return {
            phase: sum(rows.get(s, 0) for s in statuses)
            for phase, statuses in _PHASE_STATUSES.items()
        }

    async def reset_transient(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute(
            f"UPDATE job_items SET status = 'pending' WHERE status IN "
            f"({','.join('?' * len(TRANSIENT_STATUSES))})",
            TRANSIENT_STATUSES,
        )
        await conn.commit()
        return cur.rowcount or 0

    async def cancel_job(self, conn: aiosqlite.Connection, job_id: int) -> None:
        """User-initiated cancel of one job. Flips the job AND all its
        still-in-flight items (pending + transient) to 'cancelled' in a single
        commit so the state is internally consistent — a terminal job must not
        keep an in-flight item. Terminal items (done / review_ready / applied /
        rejected / error / cancelled) are left as-is.

        Idempotent: the in-flight task's CancelledError handler re-runs this to
        mop up an item that raced into a transient state after the cancel route
        ran its first sweep, so re-application must be a no-op on already-final
        rows."""
        await conn.execute(
            "UPDATE job_items SET status = 'cancelled' "
            " WHERE job_id = ? AND status IN ('pending', 'resolving', 'uploading', 'prompting')",
            (job_id,),
        )
        await conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
            (_now_iso(), job_id),
        )
        await conn.commit()

    async def reconcile_interrupted_job(self, conn: aiosqlite.Connection, job_id: int) -> None:
        """Reconcile a job whose worker task was interrupted by CancelledError.

        Two interrupts look identical to the worker; the job's DB status tells
        them apart (the cancel route flips the job to 'cancelled' BEFORE
        interrupting the task):
        - status == 'cancelled' → user cancel: mop up any item that raced into a
          transient state after the route's first sweep (idempotent cancel_job).
        - status == 'running' → shutdown drain (stop()): leave the job untouched
          so the next boot's requeue_orphaned_running resumes it. Cancelling here
          would make the job terminal and defeat resume on every graceful
          restart/deploy (fix #2, ADR 0125)."""
        job = await self.get_job(conn, job_id)
        if job.status == "cancelled":
            await self.cancel_job(conn, job_id)

    async def fail_job(
        self, conn: aiosqlite.Connection, job_id: int, *, error: str = "job failed"
    ) -> None:
        """Terminal-fail a job that errored at the job level rather than per-item
        — e.g. its prompt version was deleted, so run_job raised before it could
        finalise. Flips the job to 'failed' and any still-in-flight item (pending
        + transient) to 'error', in one commit. Guarded on status='running' so a
        racing cancel/complete is never clobbered. Without this the job lingers
        'running' forever (a phantom the clip page keeps 'resuming') and is
        re-claimed and re-crashed on every restart (fix #3, ADR 0125)."""
        cur = await conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ? "
            " WHERE id = ? AND status = 'running'",
            (_now_iso(), job_id),
        )
        if cur.rowcount:
            await conn.execute(
                "UPDATE job_items SET status = 'error', "
                "  error_message = COALESCE(error_message, ?) "
                " WHERE job_id = ? "
                "   AND status IN ('pending', 'resolving', 'uploading', 'prompting')",
                (error, job_id),
            )
        await conn.commit()

    # --- Batches hub aggregation (read-only, offline-safe) -------------
    # A "batch" = a group of jobs sharing a run_group, OR a singleton job with
    # no run_group (keyed 'job:<id>'). Studio jobs are excluded. Each method
    # below issues a single grouped query — never a per-batch loop — so the
    # /batches read path stays O(1) in batch count (ADR 0046).

    _BATCHES_SQL = f"""
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
            SUM(CASE WHEN ji.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN ji.status NOT IN
                ('pending','resolving','uploading','prompting','error','cancelled')
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
          -- The shared "awaiting review" predicate (latest annotation only, not
          -- rejected, not a suggestion) — the same one behind the topbar count
          -- and the for_review filter, so a batch can't stay "Awaiting review"
          -- on items nobody can act on (#123 suggestions stay un-applied).
          JOIN review_items ri ON ri.annotation_id = a.id
            AND {for_review_where("ri")}
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
          pj.batch_name                 AS batch_name,
          p.name                        AS prompt_name,
          pv.version_num                AS version_num,
          pv.model                      AS model,
          COALESCE(i.ran, 0)            AS ran,
          COALESCE(i.failed, 0)         AS failed,
          COALESCE(i.cancelled, 0)      AS cancelled,
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

    # --- Batch names + contents (docs/specs/2026-10-17-batches-redesign-design.md)

    async def rename_batch(self, conn: aiosqlite.Connection, job_ids: list[int], name: str) -> int:
        """Set `name` on every job of the batch(es) the given jobs belong to —
        the jobs themselves plus any job sharing their run_group, so a caller
        holding a stale subset of the job ids still renames the whole batch."""
        if not job_ids:
            return 0
        placeholders = ",".join("?" * len(job_ids))
        cur = await conn.execute(
            f"UPDATE jobs SET batch_name = ? "
            f"WHERE id IN ({placeholders}) "
            f"   OR run_group IN (SELECT run_group FROM jobs "
            f"                    WHERE id IN ({placeholders}) AND run_group IS NOT NULL)",
            (name, *job_ids, *job_ids),
        )
        await conn.commit()
        return cur.rowcount or 0

    async def delete_batch(self, conn: aiosqlite.Connection, job_ids: list[int]) -> int:
        """Delete the batch(es) the given jobs belong to (widened to their
        run_group, as rename_batch is): the jobs and their items. Annotations
        and Studio runs the batch produced stay, with job_id cleared, so its
        drafts and accepted results remain on their clips. Studio jobs are
        never touched. Raises ValueError while any of the jobs still runs.
        Returns the number of jobs deleted."""
        if not job_ids:
            return 0
        ph = ",".join("?" * len(job_ids))
        cur = await conn.execute(
            f"SELECT id FROM jobs "
            f"WHERE COALESCE(kind, '') != 'studio' AND (id IN ({ph}) "
            f"   OR run_group IN (SELECT run_group FROM jobs "
            f"                    WHERE id IN ({ph}) AND run_group IS NOT NULL))",
            (*job_ids, *job_ids),
        )
        ids = [int(r[0]) for r in await cur.fetchall()]
        if not ids:
            return 0
        ph = ",".join("?" * len(ids))
        in_flight = ("pending", *TRANSIENT_STATUSES)
        cur = await conn.execute(
            f"SELECT EXISTS (SELECT 1 FROM jobs WHERE id IN ({ph}) AND status = 'running') "
            f"    OR EXISTS (SELECT 1 FROM job_items WHERE job_id IN ({ph}) "
            f"               AND status IN ({','.join('?' * len(in_flight))}))",
            (*ids, *ids, *in_flight),
        )
        row = await cur.fetchone()
        if row and row[0]:
            raise ValueError("batch is still running")
        await conn.execute(f"UPDATE annotations SET job_id = NULL WHERE job_id IN ({ph})", ids)
        await conn.execute(f"UPDATE studio_run SET job_id = NULL WHERE job_id IN ({ph})", ids)
        await conn.execute(f"DELETE FROM job_items WHERE job_id IN ({ph})", ids)
        await conn.execute(f"DELETE FROM jobs WHERE id IN ({ph})", ids)
        await conn.commit()
        return len(ids)

    async def batch_names_for_jobs(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> dict[int, str | None]:
        """`{job_id: batch name}` — each job maps to its batch's primary (lowest
        id) job's name, the one the Batches page shows. One chunked query."""
        out: dict[int, str | None] = {}
        for fragment, params in chunked_in_clause((j,) for j in job_ids):
            cur = await conn.execute(
                f"""
                SELECT j.id, p.batch_name
                FROM jobs j
                JOIN jobs p ON p.id = CASE
                  WHEN j.run_group IS NULL THEN j.id
                  ELSE (SELECT MIN(x.id) FROM jobs x WHERE x.run_group = j.run_group)
                END
                WHERE j.id IN ({fragment})
                """,
                params,
            )
            out.update({int(r[0]): r[1] for r in await cur.fetchall()})
        return out

    async def batch_previews(
        self, conn: aiosqlite.Connection, batch_keys: list[str], *, first: int = 5
    ) -> dict[str, dict]:
        """Per batch key: the first `first` distinct clip ids in the order they
        were queued, and the distinct clip count. Feeds the thumbnail stack.
        Chunked by batch key so a batch's jobs are never split across chunks."""
        out: dict[str, dict] = {}
        for fragment, params in chunked_in_clause((k,) for k in batch_keys):
            cur = await conn.execute(
                f"""
                WITH it AS (
                  SELECT COALESCE(j.run_group, 'job:' || j.id) AS bk,
                         ji.catdv_clip_id AS clip_id,
                         MIN(ji.id) AS first_item
                  FROM job_items ji
                  JOIN jobs j ON j.id = ji.job_id
                  WHERE COALESCE(j.run_group, 'job:' || j.id) IN ({fragment})
                  GROUP BY bk, clip_id
                ),
                ranked AS (
                  SELECT bk, clip_id,
                         ROW_NUMBER() OVER (PARTITION BY bk ORDER BY first_item) AS rn,
                         COUNT(*) OVER (PARTITION BY bk) AS n
                  FROM it
                )
                SELECT bk, clip_id, n FROM ranked WHERE rn <= ? ORDER BY bk, rn
                """,
                (*params, first),
            )
            for bk, clip_id, n in await cur.fetchall():
                entry = out.setdefault(bk, {"clip_ids": [], "clips": int(n)})
                entry["clip_ids"].append(int(clip_id))
        return out

    async def batch_clip_rows(self, conn: aiosqlite.Connection, job_ids: list[int]) -> list[dict]:
        """Every distinct clip of one batch with what the Batches page needs to
        say where it stands: its latest item status and error, whether it still
        has suggestions to review (the shared for-review predicate), its
        write-back state from pending_operations ('saving' | 'problem' | None,
        the topbar sync chip's source) and its name (clip_cache, else its latest
        annotation). In queue order.
        `job_ids` is one batch's jobs (one per media kind), so a single IN
        clause is safe."""
        if not job_ids:
            return []
        ph = ",".join("?" * len(job_ids))
        sql = f"""
            WITH it AS (
              SELECT catdv_clip_id AS clip_id, MIN(id) AS first_item, MAX(id) AS last_item
              FROM job_items WHERE job_id IN ({ph})
              GROUP BY catdv_clip_id
            )
            SELECT it.clip_id                    AS clip_id,
                   -- clip_cache only holds clips someone opened; an annotated
                   -- clip's annotation recorded its name too.
                   COALESCE(cc.name, (
                     SELECT a.catdv_clip_name FROM annotations a
                     WHERE a.catdv_clip_id = it.clip_id ORDER BY a.id DESC LIMIT 1
                   ))                            AS name,
                   ji.status                     AS item_status,
                   ji.error_message              AS error,
                   EXISTS (
                     SELECT 1 FROM annotations a
                     JOIN review_items ri ON ri.annotation_id = a.id
                      AND {for_review_where("ri")}
                     WHERE a.job_id IN ({ph}) AND a.catdv_clip_id = it.clip_id
                   )                             AS awaiting,
                   (SELECT CASE
                      WHEN SUM(po.status IN ('failed', 'conflict')) > 0 THEN 'problem'
                      WHEN SUM(po.status IN ('pending', 'in_flight')) > 0 THEN 'saving'
                    END
                    FROM pending_operations po
                    WHERE po.provider_id = 'catdv'
                      AND po.provider_clip_id = CAST(it.clip_id AS TEXT)
                   )                             AS sync
            FROM it
            JOIN job_items ji ON ji.id = it.last_item
            LEFT JOIN clip_cache cc
              ON cc.provider_id = 'catdv'
             AND cc.provider_clip_id = CAST(it.clip_id AS TEXT)
            ORDER BY it.first_item
        """
        cur = await conn.execute(sql, (*job_ids, *job_ids))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]
        for r in rows:
            r["awaiting"] = bool(r["awaiting"])
        return rows
