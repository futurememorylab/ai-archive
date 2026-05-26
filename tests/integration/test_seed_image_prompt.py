from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.prompts import PromptsRepo
from backend.app.seed import seed_default_prompt

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"


@pytest.mark.asyncio
async def test_image_seed_creates_image_prompt_without_markers(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        await seed_default_prompt(conn, seed_path=SEEDS / "image_template.json")
        repo = PromptsRepo()
        prompt = await repo.get_by_name(conn, "Image description + era (Czech)")
        assert prompt is not None
        assert prompt.media_kind == "image"
        version = await repo.get_production_version(conn, prompt.id)
        tm = version.target_map.model_dump(exclude_unset=True)
        assert all(entry.get("kind") != "markers" for entry in tm.values())
        assert tm["summary_cz"]["target"] == "pragafilm.popis.materialu"
        assert tm["decade"]["identifier"] == "pragafilm.dekáda.natočení"
        assert tm["years"]["identifier"] == "pragafilm.rok.natočení"


@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path: Path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        await seed_default_prompt(conn, seed_path=SEEDS / "image_template.json")
        await seed_default_prompt(conn, seed_path=SEEDS / "image_template.json")
        cur = await conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE name = 'Image description + era (Czech)'"
        )
        assert (await cur.fetchone())[0] == 1
