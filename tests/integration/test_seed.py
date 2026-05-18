from pathlib import Path

import pytest

from backend.app.repositories.templates import TemplatesRepo
from backend.app.seed import seed_default_template


SEED = Path(__file__).resolve().parents[2] / "backend" / "seeds" / "default_template.json"


@pytest.mark.asyncio
async def test_seed_inserts_template_only_once(db):
    await seed_default_template(db, seed_path=SEED)
    await seed_default_template(db, seed_path=SEED)

    repo = TemplatesRepo()
    rows = await repo.list_active(db)
    assert len(rows) == 1
    assert rows[0].name == "Scene markers + Czech summary + era"
