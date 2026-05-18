import pytest

from backend.app.models.template import Template
from backend.app.repositories.templates import TemplatesRepo


@pytest.fixture
def repo() -> TemplatesRepo:
    return TemplatesRepo()


@pytest.mark.asyncio
async def test_create_and_get(db, repo):
    tpl = Template(
        name="scenes",
        prompt="describe scenes",
        output_schema={"type": "object"},
        target_map={"scenes": {"kind": "markers"}},
        model="gemini-2.5-pro",
    )
    new_id = await repo.create(db, tpl)
    assert new_id > 0

    loaded = await repo.get(db, new_id)
    assert loaded.name == "scenes"
    assert loaded.target_map.fields["scenes"].kind == "markers"


@pytest.mark.asyncio
async def test_list_excludes_archived(db, repo):
    a = await repo.create(db, Template(name="a", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"))
    b = await repo.create(db, Template(name="b", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"))
    await repo.archive(db, b)
    ids = [t.id for t in await repo.list_active(db)]
    assert ids == [a]


@pytest.mark.asyncio
async def test_unique_name(db, repo):
    tpl = Template(name="dup", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m")
    await repo.create(db, tpl)
    with pytest.raises(Exception):
        await repo.create(db, tpl)
