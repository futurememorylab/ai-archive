from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.models.studio import AnnotationOutput
from backend.app.services.annotator import process_item


class _Version:
    id = 1
    body = "PROMPT BODY"
    output_schema = {"type": "object"}
    model = "gemini-x"
    target_map = {}


class _Canonical:
    duration_secs = 30.0
    provider_data = {"id": 42, "name": "P1010001"}


@pytest.mark.asyncio
async def test_process_item_returns_annotation_output_and_emits_statuses(tmp_path):
    local_path = tmp_path / "u.mov"
    local_path.write_bytes(b"\x00")

    resolver = MagicMock()
    resolver.path_for_clip_id = AsyncMock(return_value=local_path)

    archive = MagicMock()
    archive.get_clip = AsyncMock(return_value=_Canonical())

    upload = MagicMock()
    file_ref = MagicMock()
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value=upload)
    ai_store.reference_for_gemini = AsyncMock(return_value=file_ref)

    gemini = MagicMock()
    gemini.annotate = MagicMock(return_value={"text": '{"k":"v"}', "raw": {"x": 1}})

    statuses: list[str] = []

    async def on_status(s: str) -> None:
        statuses.append(s)

    out = await process_item(
        clip_resolver_arg=42,
        archive_lookup_arg="42",
        clip_key=("catdv", "42"),
        version=_Version(),
        proxy_resolver=resolver, archive=archive, ai_store=ai_store, gemini=gemini,
        on_status=on_status,
    )
    assert isinstance(out, AnnotationOutput)
    assert out.structured == {"k": "v"}
    assert out.raw == {"x": 1}
    assert out.model == "gemini-x"
    assert "PROMPT BODY" in out.prompt_used
    assert "30.00" in out.prompt_used      # duration anchor prepended
    assert statuses == ["resolving", "uploading", "prompting"]


@pytest.mark.asyncio
async def test_process_item_handles_non_json_gemini_response(tmp_path):
    local_path = tmp_path / "u.mov"
    local_path.write_bytes(b"\x00")
    resolver = MagicMock(); resolver.path_for_clip_id = AsyncMock(return_value=local_path)
    archive = MagicMock(); archive.get_clip = AsyncMock(return_value=_Canonical())
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())
    ai_store.reference_for_gemini = AsyncMock(return_value=MagicMock())
    gemini = MagicMock(); gemini.annotate = MagicMock(return_value={"text": "not json", "raw": {}})

    async def on_status(_):
        pass

    out = await process_item(
        clip_resolver_arg=42, archive_lookup_arg="42",
        clip_key=("catdv", "42"),
        version=_Version(),
        proxy_resolver=resolver, archive=archive, ai_store=ai_store, gemini=gemini,
        on_status=on_status,
    )
    assert out.structured is None
    assert out.raw_text == "not json"


@pytest.mark.asyncio
async def test_process_item_no_archive_no_duration_anchor(tmp_path):
    """When archive_lookup_arg is None (upload path), don't call archive.get_clip,
    and don't prepend the duration anchor (duration_secs stays 0)."""
    local_path = tmp_path / "u.mov"
    local_path.write_bytes(b"\x00")
    resolver = MagicMock(); resolver.path_for_clip_id = AsyncMock(return_value=local_path)
    ai_store = MagicMock()
    ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())
    ai_store.reference_for_gemini = AsyncMock(return_value=MagicMock())
    gemini = MagicMock(); gemini.annotate = MagicMock(return_value={"text": "{}", "raw": {}})

    async def on_status(_):
        pass

    out = await process_item(
        clip_resolver_arg="local-path",
        archive_lookup_arg=None,
        clip_key=("studio_upload", "abc"),
        version=_Version(),
        proxy_resolver=resolver, archive=None, ai_store=ai_store, gemini=gemini,
        on_status=on_status,
    )
    # No duration anchor when duration is 0
    assert out.prompt_used == "PROMPT BODY"
