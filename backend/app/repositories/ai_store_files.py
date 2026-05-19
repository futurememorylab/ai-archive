from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIStoreFilesRepo:
    """DB-backed registry of AI input store uploads.

    Keyed on (store_id, catdv_clip_id). store_id is the AIInputStore's id,
    e.g. "gcs:my-bucket" or "gemini-files".
    """

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        store_id: str,
        clip_id: int,
        gcs_uri: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        expires_at: str | None = None,
    ) -> None:
        now = _now_iso()
        await conn.execute(
            """
            INSERT INTO ai_store_files
              (store_id, catdv_clip_id, gcs_uri, mime_type, size_bytes,
               sha256, uploaded_at, last_used_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id, catdv_clip_id) DO UPDATE SET
              gcs_uri      = excluded.gcs_uri,
              mime_type    = excluded.mime_type,
              size_bytes   = excluded.size_bytes,
              sha256       = excluded.sha256,
              uploaded_at  = excluded.uploaded_at,
              last_used_at = excluded.last_used_at,
              expires_at   = excluded.expires_at
            """,
            (store_id, clip_id, gcs_uri, mime_type, size_bytes, sha256,
             now, now, expires_at),
        )
        await conn.commit()

    async def get(
        self, conn: aiosqlite.Connection, *, store_id: str, clip_id: int
    ) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT store_id, catdv_clip_id, gcs_uri, mime_type, size_bytes,
                   sha256, uploaded_at, last_used_at, expires_at
            FROM ai_store_files
            WHERE store_id = ? AND catdv_clip_id = ?
            """,
            (store_id, clip_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(
            zip(
                (
                    "store_id",
                    "catdv_clip_id",
                    "gcs_uri",
                    "mime_type",
                    "size_bytes",
                    "sha256",
                    "uploaded_at",
                    "last_used_at",
                    "expires_at",
                ),
                row,
            )
        )

    async def touch(
        self, conn: aiosqlite.Connection, *, store_id: str, clip_id: int
    ) -> None:
        await conn.execute(
            "UPDATE ai_store_files SET last_used_at = ? "
            "WHERE store_id = ? AND catdv_clip_id = ?",
            (_now_iso(), store_id, clip_id),
        )
        await conn.commit()

    async def delete(
        self, conn: aiosqlite.Connection, *, store_id: str, clip_id: int
    ) -> None:
        await conn.execute(
            "DELETE FROM ai_store_files WHERE store_id = ? AND catdv_clip_id = ?",
            (store_id, clip_id),
        )
        await conn.commit()
