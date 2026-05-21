from pathlib import Path

import pytest  # noqa: F401

from backend.app.repositories.prompts import PromptsRepo
from backend.app.seed import seed_default_prompt

SEED = Path(__file__).resolve().parents[2] / "backend" / "seeds" / "default_template.json"


@pytest.mark.asyncio
async def test_seed_inserts_prompt_only_once(db):
    await seed_default_prompt(db, seed_path=SEED)
    await seed_default_prompt(db, seed_path=SEED)

    repo = PromptsRepo()
    rows = await repo.list_active(db)
    assert len(rows) == 1
    assert rows[0].name == "Scene markers + Czech summary + era"
