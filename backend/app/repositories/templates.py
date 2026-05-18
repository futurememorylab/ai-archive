import json
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.template import Template


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TemplatesRepo:
    async def create(self, conn: aiosqlite.Connection, tpl: Template) -> int:
        now = _now_iso()
        cur = await conn.execute(
            """
            INSERT INTO templates (name, description, prompt, output_schema, target_map,
                                   model, created_at, updated_at, archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                tpl.name,
                tpl.description,
                tpl.prompt,
                json.dumps(tpl.output_schema),
                tpl.target_map.model_dump_json(),
                tpl.model,
                now,
                now,
            ),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, template_id: int) -> Template:
        cur = await conn.execute(
            """
            SELECT id, name, description, prompt, output_schema, target_map,
                   model, archived
            FROM templates WHERE id = ?
            """,
            (template_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"template {template_id} not found")
        return self._row_to_template(row)

    async def list_active(self, conn: aiosqlite.Connection) -> list[Template]:
        cur = await conn.execute(
            """
            SELECT id, name, description, prompt, output_schema, target_map,
                   model, archived
            FROM templates WHERE archived = 0
            ORDER BY id
            """
        )
        return [self._row_to_template(r) for r in await cur.fetchall()]

    async def archive(self, conn: aiosqlite.Connection, template_id: int) -> None:
        await conn.execute(
            "UPDATE templates SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), template_id),
        )
        await conn.commit()

    async def update(self, conn: aiosqlite.Connection, template_id: int, tpl: Template) -> None:
        await conn.execute(
            """
            UPDATE templates SET name=?, description=?, prompt=?, output_schema=?,
                                 target_map=?, model=?, updated_at=?
            WHERE id=?
            """,
            (
                tpl.name,
                tpl.description,
                tpl.prompt,
                json.dumps(tpl.output_schema),
                tpl.target_map.model_dump_json(),
                tpl.model,
                _now_iso(),
                template_id,
            ),
        )
        await conn.commit()

    @staticmethod
    def _row_to_template(row) -> Template:
        return Template(
            id=row[0],
            name=row[1],
            description=row[2],
            prompt=row[3],
            output_schema=json.loads(row[4]),
            target_map=json.loads(row[5]),
            model=row[6],
            archived=bool(row[7]),
        )
