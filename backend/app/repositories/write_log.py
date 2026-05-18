import json
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WriteLogRepo:
    async def record(
        self,
        conn: aiosqlite.Connection,
        *,
        catdv_clip_id: int,
        annotation_id: int | None,
        payload: dict[str, Any],
        response: dict[str, Any] | str,
        status: Literal["ok", "error"],
    ) -> None:
        response_str = (
            json.dumps(response, ensure_ascii=False)
            if isinstance(response, (dict, list))
            else str(response)
        )
        await conn.execute(
            """
            INSERT INTO write_log
              (catdv_clip_id, annotation_id, payload, response, status, written_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                catdv_clip_id,
                annotation_id,
                json.dumps(payload, ensure_ascii=False),
                response_str,
                status,
                _now_iso(),
            ),
        )
        await conn.commit()
