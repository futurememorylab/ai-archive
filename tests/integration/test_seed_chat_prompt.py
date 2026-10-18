from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.prompts import PromptsRepo
from backend.app.seed import seed_system_instruction

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"


@pytest.mark.asyncio
async def test_seed_inserts_prompt_when_missing(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await seed_system_instruction(
            conn,
            seed_path=SEEDS / "chat_system_instruction_cs.json",
        )
        repo = PromptsRepo()
        prompt = await repo.get_by_name(conn, "chat.system_instruction.cs")
        assert prompt is not None


@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        seed = SEEDS / "chat_system_instruction_cs.json"
        await seed_system_instruction(conn, seed_path=seed)
        await seed_system_instruction(conn, seed_path=seed)
        cur = await conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE name = ?",
            ("chat.system_instruction.cs",),
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 1
