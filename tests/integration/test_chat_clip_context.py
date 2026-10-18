"""The clip chat's context carries every published field (with the archive's
field names) and the clip record's own details, not only pragafilm.*."""

import dataclasses

import pytest

from backend.app.archive.model import FieldDef, FieldValue
from backend.app.routes.pages.clips import _build_clip_view_model_for_live
from tests.integration.test_clip_detail_draft import FakeArchive, _canonical


class _Archive(FakeArchive):
    async def list_field_definitions(self):
        return [
            FieldDef(
                identifier="pragafilm.dekáda.natočení",
                name="Dekáda natočení",
                type="text",
                is_multi=False,
                is_editable=True,
                picklist_values=None,
                provider_data={},
            )
        ]


@pytest.mark.asyncio
async def test_context_has_all_fields_names_and_record_details():
    clip = dataclasses.replace(
        _canonical(101),
        fields={
            "pragafilm.dekáda.natočení": FieldValue("pragafilm.dekáda.natočení", "20.léta"),
            "catdv.tape": FieldValue("catdv.tape", "Kazeta 7"),
        },
        provider_data={
            "ID": 101,
            "name": "Clip_101",
            "format": "video/quicktime",
            "media": {"codec": "H.264", "width": 720, "height": 576},
            "modifyDate": "2026-05-18T10:00:00Z",
            "posterID": 9000,
            "catalogID": 3,
        },
    )

    class _Ctx:
        db = None
        archive = _Archive((clip,))

    out = await _build_clip_view_model_for_live(_Ctx(), 101)
    assert out["fields"] == {"pragafilm.dekáda.natočení": "20.léta", "catdv.tape": "Kazeta 7"}
    assert out["field_names"] == {"pragafilm.dekáda.natočení": "Dekáda natočení"}
    assert out["archive_info"] == {
        "Formát": "video/quicktime · H.264 · 720×576",
        "modifyDate": "2026-05-18T10:00:00Z",
    }
