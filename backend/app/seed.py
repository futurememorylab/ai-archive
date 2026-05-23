"""Default-prompt seeders — idempotently insert the bundled `seeds/*.json`
prompts on first boot. Called from the FastAPI lifespan."""

import json
from pathlib import Path

import aiosqlite

from backend.app.repositories.prompts import PromptsRepo


async def seed_default_prompt(conn: aiosqlite.Connection, *, seed_path: Path) -> None:
    """Insert the default prompt + v1@production if no prompt by that name exists."""
    raw = seed_path.read_text()  # noqa: ASYNC240  # sync read at startup is acceptable in lifespan
    data = json.loads(raw)
    cur = await conn.execute("SELECT 1 FROM prompts WHERE name = ?", (data["name"],))
    if await cur.fetchone():
        return
    repo = PromptsRepo()
    await repo.create_with_initial_version(
        conn,
        name=data["name"],
        description=data.get("description"),
        body=data["prompt"],
        target_map=data["target_map"],
        output_schema=data["output_schema"],
        model=data["model"],
        initial_state="production",
    )


async def seed_live_system_instruction(
    conn: aiosqlite.Connection,
    *,
    seed_path: Path,
) -> None:
    """Insert the Czech Live system-instruction prompt + v1@production if missing."""
    data = json.loads(seed_path.read_text())  # noqa: ASYNC240
    cur = await conn.execute("SELECT 1 FROM prompts WHERE name = ?", (data["name"],))
    if await cur.fetchone():
        return
    repo = PromptsRepo()
    await repo.create_with_initial_version(
        conn,
        name=data["name"],
        description=data.get("description"),
        body=data["prompt"],
        target_map=data.get("target_map", {}),
        output_schema=data.get("output_schema", {}),
        model=data["model"],
        initial_state="production",
    )
