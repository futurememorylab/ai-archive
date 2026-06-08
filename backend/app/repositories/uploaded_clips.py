"""UploadedClipsRepo — per-upload metadata for studio uploaded clips.

Keyed externally by the synthetic `clip_id` (UPLOAD_ID_BASE + row id);
internally the table PK is the small positive `id`. `create` owns the
identity: it inserts, derives `stored_filename = f"{clip_id}{ext}"`, and
returns the PK so the caller can compute the clip_id via uploaded_ids.
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.repositories._batch import chunked_in_clause
from backend.app.uploaded_ids import to_clip_id, to_pk

_COLS = (
    "id", "original_filename", "stored_filename", "mime",
    "size_bytes", "duration_secs", "width", "height", "created_at",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: tuple) -> dict[str, Any]:
    d = dict(zip(_COLS, row, strict=True))
    d["clip_id"] = to_clip_id(int(d["id"]))
    return d


class UploadedClipsRepo:
    async def create(
        self,
        conn: aiosqlite.Connection,
        *,
        original_filename: str,
        mime: str,
        size_bytes: int,
        ext: str,
        duration_secs: float | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO uploaded_clip(original_filename, stored_filename, mime, "
            "size_bytes, duration_secs, width, height, created_at) "
            "VALUES (?, '', ?, ?, ?, ?, ?, ?)",
            (original_filename, mime, size_bytes, duration_secs, width, height, _now_iso()),
        )
        pk = cur.lastrowid
        assert pk is not None
        stored = f"{to_clip_id(pk)}{ext}"
        await conn.execute(
            "UPDATE uploaded_clip SET stored_filename = ? WHERE id = ?", (stored, pk)
        )
        await conn.commit()
        return pk

    async def get(self, conn: aiosqlite.Connection, clip_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM uploaded_clip WHERE id = ?",
            (to_pk(clip_id),),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def get_many(
        self, conn: aiosqlite.Connection, clip_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        pks = [(to_pk(cid),) for cid in clip_ids]
        for fragment, params in chunked_in_clause(pks):
            cur = await conn.execute(
                f"SELECT {', '.join(_COLS)} FROM uploaded_clip WHERE id IN ({fragment})",
                params,
            )
            for row in await cur.fetchall():
                d = _row_to_dict(row)
                out[d["clip_id"]] = d
        return out

    async def delete(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute("DELETE FROM uploaded_clip WHERE id = ?", (to_pk(clip_id),))
        await conn.commit()
