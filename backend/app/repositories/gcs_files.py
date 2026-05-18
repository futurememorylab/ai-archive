from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GcsFilesRepo:
    async def upsert(self, conn: aiosqlite.Connection, *,
                     clip_id: int, gcs_uri: str, mime_type: str,
                     size_bytes: int, sha256: str) -> None:
        now = _now_iso()
        await conn.execute(
            """
            INSERT INTO gcs_files
              (catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256, uploaded_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(catdv_clip_id) DO UPDATE SET
              gcs_uri = excluded.gcs_uri,
              mime_type = excluded.mime_type,
              size_bytes = excluded.size_bytes,
              sha256 = excluded.sha256,
              uploaded_at = excluded.uploaded_at,
              last_used_at = excluded.last_used_at
            """,
            (clip_id, gcs_uri, mime_type, size_bytes, sha256, now, now),
        )
        await conn.commit()

    async def get(self, conn: aiosqlite.Connection, clip_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256,
                   uploaded_at, last_used_at
            FROM gcs_files WHERE catdv_clip_id = ?
            """,
            (clip_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("catdv_clip_id", "gcs_uri", "mime_type", "size_bytes", "sha256",
             "uploaded_at", "last_used_at"),
            row,
        ))

    async def touch(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute(
            "UPDATE gcs_files SET last_used_at = ? WHERE catdv_clip_id = ?",
            (_now_iso(), clip_id),
        )
        await conn.commit()
