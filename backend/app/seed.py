import json
from pathlib import Path

import aiosqlite

from backend.app.models.template import Template
from backend.app.repositories.templates import TemplatesRepo


async def seed_default_template(conn: aiosqlite.Connection, *, seed_path: Path) -> None:
    """Insert the default template if no template by the same name exists."""
    raw = seed_path.read_text()  # noqa: ASYNC240  # sync read at startup is acceptable in lifespan
    data = json.loads(raw)
    cur = await conn.execute("SELECT 1 FROM templates WHERE name = ?", (data["name"],))
    if await cur.fetchone():
        return
    repo = TemplatesRepo()
    tpl = Template(
        name=data["name"],
        description=data.get("description"),
        prompt=data["prompt"],
        output_schema=data["output_schema"],
        target_map=data["target_map"],
        model=data["model"],
    )
    await repo.create(conn, tpl)
