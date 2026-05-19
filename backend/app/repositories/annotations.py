import json
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.annotation import Annotation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class AnnotationsRepo:
    async def insert(self, conn: aiosqlite.Connection, ann: Annotation) -> int:
        cur = await conn.execute(
            """
            INSERT INTO annotations
              (catdv_clip_id, catdv_clip_name, template_id, job_id, model, prompt_used,
               raw_response, structured_output, clip_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ann.catdv_clip_id,
                ann.catdv_clip_name,
                ann.template_id,
                ann.job_id,
                ann.model,
                ann.prompt_used,
                json.dumps(ann.raw_response, ensure_ascii=False, default=_json_default),
                json.dumps(ann.structured_output, ensure_ascii=False, default=_json_default)
                if ann.structured_output is not None
                else "null",
                json.dumps(ann.clip_snapshot, ensure_ascii=False, default=_json_default),
                _now_iso(),
            ),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, annotation_id: int) -> Annotation:
        cur = await conn.execute(
            """
            SELECT id, catdv_clip_id, catdv_clip_name, template_id, job_id, model,
                   prompt_used, raw_response, structured_output, clip_snapshot
            FROM annotations WHERE id = ?
            """,
            (annotation_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"annotation {annotation_id} not found")
        return self._row(row)

    async def list_by_clip(self, conn: aiosqlite.Connection, clip_id: int) -> list[Annotation]:
        cur = await conn.execute(
            """
            SELECT id, catdv_clip_id, catdv_clip_name, template_id, job_id, model,
                   prompt_used, raw_response, structured_output, clip_snapshot
            FROM annotations WHERE catdv_clip_id = ? ORDER BY id DESC
            """,
            (clip_id,),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def search(self, conn: aiosqlite.Connection, query: str) -> list[int]:
        cur = await conn.execute(
            "SELECT rowid FROM annotations_fts WHERE annotations_fts MATCH ?",
            (query,),
        )
        return [r[0] for r in await cur.fetchall()]

    @staticmethod
    def _row(row) -> Annotation:
        structured_raw = row[8]
        structured = None if structured_raw == "null" else json.loads(structured_raw)
        return Annotation(
            id=row[0],
            catdv_clip_id=row[1],
            catdv_clip_name=row[2],
            template_id=row[3],
            job_id=row[4],
            model=row[5],
            prompt_used=row[6],
            raw_response=json.loads(row[7]),
            structured_output=structured,
            clip_snapshot=json.loads(row[9]),
        )
