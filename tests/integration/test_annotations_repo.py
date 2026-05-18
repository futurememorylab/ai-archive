import pytest

from backend.app.models.annotation import Annotation
from backend.app.models.template import Template
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.templates import TemplatesRepo


@pytest.mark.asyncio
async def test_insert_and_get(db):
    templates = TemplatesRepo()
    template_id = await templates.create(
        db,
        Template(
            name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
        ),
    )

    repo = AnnotationsRepo()
    annotation_id = await repo.insert(
        db,
        Annotation(
            catdv_clip_id=42,
            catdv_clip_name="Test_Clip",
            template_id=template_id,
            job_id=None,
            model="gemini-2.5-pro",
            prompt_used="p",
            raw_response={"text": "..."},
            structured_output={"scenes": []},
            clip_snapshot={"ID": 42, "name": "Test_Clip"},
        ),
    )
    loaded = await repo.get(db, annotation_id)
    assert loaded.catdv_clip_id == 42
    assert loaded.structured_output == {"scenes": []}


@pytest.mark.asyncio
async def test_fts_search_finds_clip(db):
    templates = TemplatesRepo()
    template_id = await templates.create(
        db,
        Template(
            name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
        ),
    )
    repo = AnnotationsRepo()
    await repo.insert(
        db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Polčakovi rodina",
            template_id=template_id,
            job_id=None,
            model="m",
            prompt_used="popiš rodinu",
            raw_response={},
            structured_output={"summary": "rodinný portrét"},
            clip_snapshot={"ID": 1},
        ),
    )
    results = await repo.search(db, "rodinný")
    assert len(results) == 1
    results = await repo.search(db, "rodinny")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_list_by_clip_returns_latest_first(db):
    templates = TemplatesRepo()
    t = await templates.create(
        db,
        Template(
            name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
        ),
    )
    repo = AnnotationsRepo()
    first = await repo.insert(
        db,
        Annotation(
            catdv_clip_id=7,
            catdv_clip_name="x",
            template_id=t,
            model="m",
            prompt_used="v1",
            raw_response={},
            structured_output={},
            clip_snapshot={},
        ),
    )
    second = await repo.insert(
        db,
        Annotation(
            catdv_clip_id=7,
            catdv_clip_name="x",
            template_id=t,
            model="m",
            prompt_used="v2",
            raw_response={},
            structured_output={},
            clip_snapshot={},
        ),
    )
    rows = await repo.list_by_clip(db, 7)
    assert [r.id for r in rows] == [second, first]
