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

_COLS = [
    "event_id", "occurred_at", "install_id", "app_version", "kind",
    "archive_id", "user_ref", "job_id", "clip_id", "clip_name",
    "prompt_version_id", "prompt_hash", "schema_hash",
    "prompt_chars_rendered", "model",
    "media_kind", "media_duration_secs", "media_width", "media_height",
    "media_fps", "media_bytes", "media_ext", "media_resolution_setting",
    "preprocess", "vertex_project", "vertex_location", "ai_store_kind",
    "status", "error_class", "finish_reason", "attempt_count", "duration_s",
    "tokens_in", "tokens_in_text", "tokens_in_video", "tokens_in_audio",
    "tokens_in_image", "tokens_cached", "tokens_out", "tokens_thinking",
    "cost_usd", "pricing_version",
    "est_tokens_in", "est_tokens_out_p50", "est_tokens_out_p90",
    "est_cost_usd_p50", "est_cost_usd_p90", "est_confidence",
    "output_chars", "review_item_count", "attrs",
]


class RunTelemetryRepo:
    async def insert(
        self, conn: aiosqlite.Connection, rec: RunTelemetryRecord
    ) -> int:
        data = rec.model_dump()
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

    async def recent_input_ratios(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
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
        cur = await conn.execute(
            f"SELECT CAST(({col_expr}) AS REAL) / media_duration_secs "
            "FROM run_telemetry "
            "WHERE model = ? AND media_kind = ? AND status = 'ok' "
            f"AND COALESCE(media_duration_secs, 0) > 0 AND ({col_expr}) > 0 "
            "ORDER BY id DESC LIMIT ?",
            (model, media_kind, limit),
        )
        return [r[0] for r in await cur.fetchall()]

    async def recent_output_rates(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
        prompt_hash: str | None = None,
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
            "model = ?", "media_kind = ?", "status = 'ok'",
            "COALESCE(finish_reason,'') != 'MAX_TOKENS'",
            f"{bill} > 0",
        ]
        params: list = [model, media_kind]
        if media_kind != "image":
            where.append("COALESCE(media_duration_secs, 0) > 0")
        if prompt_hash is not None:
            where.append("prompt_hash = ?")
            params.append(prompt_hash)
        params.append(limit)
        cur = await conn.execute(
            f"SELECT {value} FROM run_telemetry WHERE {' AND '.join(where)} "
            "ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [r[0] for r in await cur.fetchall()]
