"""RunTelemetryRepo — one row per Gemini call (both run kinds).

Local history for the estimator AND the dormant Phase-2 outbox
(sent_at / send_attempts). Rows are never deleted. Aggregate readers
return small float lists (LIMIT-bounded); percentile math happens in
Python — SQLite has no percentile function and the lists are ≤50 rows.

Stats hygiene (spec §6): only status='ok' rows count; output stats
also exclude finish_reason='MAX_TOKENS' (truncated runs would drag
the output estimate down — the wrong direction for customer quotes).
Output rates use BILLABLE output (tokens_out + tokens_thinking).
"""

import json

import aiosqlite

from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories._batch import chunked_in_clause

# Insert columns, derived from the model so they can't drift apart.
# RunTelemetryRecord mirrors the table minus id (autoincrement) and
# sent_at / send_attempts (DB defaults; Phase-2 flusher owns them);
# model_fields preserves declaration order.
_COLS = list(RunTelemetryRecord.model_fields)


class RunTelemetryRepo:
    async def insert(self, conn: aiosqlite.Connection, rec: RunTelemetryRecord) -> int:
        data = rec.model_dump()
        # Empty dict collapses to NULL (no-signal); pydantic guarantees
        # attrs is dict | None, never a pre-serialized string.
        data["attrs"] = json.dumps(data["attrs"]) if data["attrs"] else None
        placeholders = ", ".join("?" for _ in _COLS)
        cur = await conn.execute(
            f"INSERT INTO run_telemetry({', '.join(_COLS)}) VALUES ({placeholders})",
            tuple(data[c] for c in _COLS),
        )
        rid = cur.lastrowid
        assert rid is not None
        await conn.commit()
        return rid

    # --- Actual-cost readers (UI surfaces; offline-safe DB lookups) -----
    # Both sum cost_usd (NULL → 0) over ALL rows for the job(s) — including
    # error rows, since a failed attempt still burned tokens, and per-clip
    # retries, since total spend on a clip is the sum of its attempts.
    # Batched via chunked_in_clause (ADR 0046); GROUP-BY sums are merged
    # with += across chunks so a key split over two chunks stays correct.

    async def cost_sums_by_job(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> dict[int, float]:
        """{job_id: total cost_usd} for the given jobs. Missing jobs are
        simply absent from the dict. Powers the batches-list cost column
        (sum a batch's member job_ids in Python)."""
        out: dict[int, float] = {}
        for fragment, params in chunked_in_clause((j,) for j in job_ids):
            cur = await conn.execute(
                f"SELECT job_id, COALESCE(SUM(cost_usd), 0) "
                f"FROM run_telemetry WHERE job_id IN ({fragment}) "
                f"GROUP BY job_id",
                tuple(params),
            )
            for jid, total in await cur.fetchall():
                out[int(jid)] = out.get(int(jid), 0.0) + float(total)
        return out

    async def cost_totals_by_clip(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> dict[int, float]:
        """{clip_id: total cost_usd} across the given jobs. Rows with a
        NULL clip_id are skipped (can't key them). Powers per-clip and
        per-annotation cost displays — both want a clip's total spend
        within a set of jobs, not a per-(job, clip) breakdown."""
        out: dict[int, float] = {}
        for fragment, params in chunked_in_clause((j,) for j in job_ids):
            cur = await conn.execute(
                f"SELECT clip_id, COALESCE(SUM(cost_usd), 0) "
                f"FROM run_telemetry "
                f"WHERE job_id IN ({fragment}) AND clip_id IS NOT NULL "
                f"GROUP BY clip_id",
                tuple(params),
            )
            for cid, total in await cur.fetchall():
                out[int(cid)] = out.get(int(cid), 0.0) + float(total)
        return out

    async def recent_input_ratios(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
        media_resolution: str | None = None,
        limit: int = 50,
    ) -> list[float]:
        """tokens_in_<media>/second for recent ok runs — calibrates the
        deterministic input constant. Picks the modality column matching
        the media kind."""
        col_expr = {
            "video+audio": "COALESCE(tokens_in_video, 0) + COALESCE(tokens_in_audio, 0)",
            "video": "COALESCE(tokens_in_video, 0)",
            "audio": "COALESCE(tokens_in_audio, 0)",
            "image": "COALESCE(tokens_in_image, 0)",
        }.get(media_kind, "COALESCE(tokens_in_video, 0)")
        res_clause = " AND media_resolution_setting = ?" if media_resolution is not None else ""
        params: list = [model, media_kind]
        if media_resolution is not None:
            params.append(media_resolution)
        params.append(limit)
        # id is insertion order == recency today; occurred_at may differ
        # if Phase-2 ever back-fills older events.
        cur = await conn.execute(
            f"SELECT CAST(({col_expr}) AS REAL) / media_duration_secs "
            "FROM run_telemetry "
            "WHERE model = ? AND media_kind = ? AND status = 'ok' "
            f"AND COALESCE(media_duration_secs, 0) > 0 AND ({col_expr}) > 0{res_clause} "
            "ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [r[0] for r in await cur.fetchall()]

    async def recent_output_rates(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
        prompt_hash: str | None = None,
        media_resolution: str | None = None,
        limit: int = 50,
    ) -> list[float]:
        """Billable output per media-second (per-item for images) for
        recent ok, non-truncated runs."""
        bill = "(COALESCE(tokens_out,0) + COALESCE(tokens_thinking,0))"
        value = (
            f"CAST({bill} AS REAL)"
            if media_kind == "image"
            else f"CAST({bill} AS REAL) / media_duration_secs"
        )
        where = [
            "model = ?",
            "media_kind = ?",
            "status = 'ok'",
            "COALESCE(finish_reason,'') != 'MAX_TOKENS'",
            f"{bill} > 0",
        ]
        params: list = [model, media_kind]
        if media_kind != "image":
            where.append("COALESCE(media_duration_secs, 0) > 0")
        if prompt_hash is not None:
            where.append("prompt_hash = ?")
            params.append(prompt_hash)
        if media_resolution is not None:
            where.append("media_resolution_setting = ?")
            params.append(media_resolution)
        params.append(limit)
        cur = await conn.execute(
            f"SELECT {value} FROM run_telemetry WHERE {' AND '.join(where)} "
            "ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [r[0] for r in await cur.fetchall()]
