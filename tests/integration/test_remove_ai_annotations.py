"""Remove AI annotations: drop a clip's AI markers + AI description from the
archive so it can be annotated again from scratch, never touching human data.
Spec: docs/specs/2026-10-23-remove-ai-annotations-design.md."""

import asyncio
import importlib
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.archive.change_set_json import change_op_from_json
from backend.app.archive.model import (
    AI_MARKER_CATEGORY,
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    ReplaceAIMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.payload import build_put_payload
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_versions import ClipVersionsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from tests._helpers.live_ctx import install_live_ctx
from tests.integration.test_clip_list_filters import CATALOG, PROVIDER, _seed_list_cache
from tests.integration.test_publish_service import (
    _accept_marker,
    _seed_annotation,
    _seed_prompt_version,
    _svc,
)


async def _queued_ops(db, version_id: int):
    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    rows = [r for r in rows if r["origin_clip_version_id"] == version_id]
    return rows, [change_op_from_json(r["op_json"]) for r in rows]


async def _undecided_item(db, aid: int) -> int:
    [item] = await ReviewItemsRepo().bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                studio_run_id=None,
                catdv_clip_id=1,
                kind="marker",
                target_identifier=None,
                proposed_value={"name": "draft", "in": {"secs": 3.0}},
            )
        ],
    )
    assert item.id is not None
    return item.id


async def _publish_live(svc, db) -> int:
    version_id = await svc.publish(db, clip_id=1, author=None)
    assert version_id is not None
    await ClipVersionsRepo().mark_live(db, version_id)
    return version_id


@pytest.mark.asyncio
async def test_remove_queues_only_owned_clears_and_records_a_removal(db):
    vid = await _seed_prompt_version(db)
    svc = _svc()
    aid = await _seed_annotation(db, vid)
    await _accept_marker(db, aid, "a", 1.0)
    await _publish_live(svc, db)
    draft_id = await _undecided_item(db, aid)

    version_id = await svc.remove_ai(db, clip_id=1, author="anna@example.com")

    rows, ops = await _queued_ops(db, version_id)
    assert rows and ops == [
        ReplaceAIMarkers(desired=()),
        SetField(identifier="pragafilm.popis.ai", value=""),
        ReplaceNote(target="pragafilm.popis.ai", text=""),
    ]
    assert all(r["expected_etag"] is None for r in rows)

    cv = await ClipVersionsRepo().get(db, version_id)
    assert (cv.origin, cv.publish_state, cv.author) == ("removed", "publishing", "anna@example.com")
    assert cv.snapshot["markers"] == []
    assert "pragafilm.popis.ai" not in cv.snapshot["fields"]
    assert cv.annotation_id == aid

    # The draft is discarded; the applied item stays as the record.
    items = await ReviewItemsRepo().list_by_clip(db, 1)
    assert draft_id not in {it.id for it in items}
    assert all(it.applied_at is not None for it in items) and items


@pytest.mark.asyncio
async def test_removed_clip_reads_not_annotated_until_it_is_annotated_again(db):
    vid = await _seed_prompt_version(db)
    svc = _svc()
    aid = await _seed_annotation(db, vid)
    await _accept_marker(db, aid, "a", 1.0)
    await _publish_live(svc, db)
    repo = ClipVersionsRepo()
    assert await repo.live_version_num_by_clip(db, [1]) == {1: 1}

    version_id = await svc.remove_ai(db, clip_id=1, author=None)
    assert await repo.reset_clip_ids(db) == {1}  # already while it is being written
    await repo.mark_live(db, version_id)
    assert await repo.live_version_num_by_clip(db, [1]) == {}
    assert await repo.reset_clip_ids(db, clip_id=1) == {1}

    # A new run ends the reset (it has a draft again) but nothing of ours is
    # back in the archive until that run is published.
    await _seed_annotation(db, vid)
    assert await repo.reset_clip_ids(db) == set()
    assert await repo.reset_clip_ids(db, ignore_newer_annotations=True) == {1}


@pytest.mark.asyncio
async def test_filters_treat_a_removed_clip_as_not_annotated(db):
    from backend.app.services.clip_list_filters import resolve

    await _seed_list_cache(db, [1, 2])
    vid = await _seed_prompt_version(db)
    svc = _svc()
    aid = await _seed_annotation(db, vid)
    await _accept_marker(db, aid, "a", 1.0)
    await _publish_live(svc, db)

    async def anno(value):
        return await resolve(db, provider_id=PROVIDER, catalog_id=CATALOG, cache="any", anno=value)

    assert await anno("applied") == {1}
    assert await anno("none") == {2}
    await svc.remove_ai(db, clip_id=1, author=None)
    assert await anno("applied") == set()
    assert await anno("has_any") == set()
    assert await anno("none") == {1, 2}


def test_payload_drops_only_ai_markers_and_the_ai_description():
    """The write the removal sends keeps every human marker byte-identical and
    writes no field but the AI description."""
    human = {"name": "Human", "category": "Interview", "in": {"frm": 25, "fmt": 25.0}}
    legacy = {"name": "[Scene] old app marker", "category": "Scene", "in": {"frm": 50}}
    ai = {"name": "[Scene] AI", "category": AI_MARKER_CATEGORY, "in": {"frm": 75}}
    current = {
        "fps": 25.0,
        "markers": [human, ai, legacy],
        "fields": {"pragafilm.popis.materialu": "human text", "pragafilm.popis.ai": "AI text"},
    }
    ops = [
        ReplaceAIMarkers(desired=()),
        SetField(identifier="pragafilm.popis.ai", value=""),
        ReplaceNote(target="pragafilm.popis.ai", text=""),
    ]
    payload = build_put_payload(current=current, ops=ops)
    assert payload == {"markers": [human, legacy], "fields": {"pragafilm.popis.ai": ""}}


# --- route + clip page ------------------------------------------------------


def _clip(*, with_ai: bool) -> CanonicalClip:
    tc = Timecode(secs=1.0, fps=25.0)
    markers = [Marker(name="Human", in_=tc, out=None, category="Interview")]
    fields = {"pragafilm.popis.materialu": FieldValue("pragafilm.popis.materialu", "human")}
    if with_ai:
        markers += [
            Marker(name="[Scene] A", in_=tc, out=None, category=AI_MARKER_CATEGORY),
            Marker(name="[Scene] B", in_=tc, out=None, category=AI_MARKER_CATEGORY),
        ]
        fields["pragafilm.popis.ai"] = FieldValue("pragafilm.popis.ai", "summary")
    return CanonicalClip(
        key=("catdv", "1"),
        name="Clip_1",
        duration_secs=60.0,
        fps=25.0,
        markers=tuple(markers),
        fields=fields,
        notes={},
        media=MediaRef(
            mime_type="video/mp4", size_bytes=None, cached_path=None, upstream_handle="1"
        ),
        provider_data={"ID": 1, "name": "Clip_1"},
        fetched_at=datetime.now(UTC),
    )


class _Archive:
    def __init__(self, clip):
        self._clip = clip

    async def get_clip(self, clip_id_str):
        return self._clip


def _client(monkeypatch, tmp_path):
    from tests.integration.test_routes_review import _setenv

    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_clip_page_offers_removal_with_a_confirm_naming_what_goes(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_Archive(_clip(with_ai=True)))
        r = client.get("/clips/1")
        assert r.status_code == 200
        assert 'data-test="remove-ai"' in r.text
        assert "Remove AI annotations?" in r.text
        assert "This deletes 2 AI markers and the AI description from the archive." in r.text
        assert "Human markers, the human description and all other metadata stay" in r.text


def test_clip_page_hides_removal_without_ai_in_the_archive(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_Archive(_clip(with_ai=False)))
        r = client.get("/clips/1")
        assert r.status_code == 200
        assert 'data-test="remove-ai"' not in r.text


def test_remove_route_queues_the_removal_and_empties_the_draft(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        ctx = cast(FastAPI, client.app).state.core_ctx

        async def _seed():
            vid = await _seed_prompt_version(ctx.db)
            aid = await AnnotationsRepo().insert(
                ctx.db,
                Annotation(
                    catdv_clip_id=1,
                    catdv_clip_name="Clip_1",
                    prompt_version_id=vid,
                    model="m",
                    prompt_used="p",
                    raw_response={},
                    structured_output={},
                    clip_snapshot={"fps": 25.0},
                ),
            )
            await _undecided_item(ctx.db, aid)

        asyncio.run(_seed())
        install_live_ctx(client.app, archive=_Archive(_clip(with_ai=True)))
        r = client.post("/api/review/clips/1/remove-ai")
        assert r.status_code == 200
        version_id = r.json()["version_id"]
        cv = asyncio.run(ctx.clip_versions_repo.get(ctx.db, version_id))
        assert cv.origin == "removed"
        d = client.get("/api/review/clips/1/draft-data").json()
        assert d["markers"] == [] and d["applied_count"] == 0


def test_clip_page_does_not_offer_a_second_removal_while_one_is_queued(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_Archive(_clip(with_ai=True)))
        assert client.post("/api/review/clips/1/remove-ai").status_code == 200
        r = client.get("/clips/1")
        assert "aiRemoved: true" in r.text


def test_merged_changeset_keeps_the_conflict_check_of_a_queued_publish():
    from backend.app.services.sync_engine import _freshest_etag

    rows = [{"expected_etag": "old"}, {"expected_etag": "new"}, {"expected_etag": None}]
    assert _freshest_etag(rows) == "new"
    assert _freshest_etag([{"expected_etag": None}]) is None
