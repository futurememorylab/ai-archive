import json

import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.write_queue import WriteQueue


async def _seed(conn):
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    items = ReviewItemsRepo()
    _, vid = await prompts.create_with_initial_version(
        conn,
        name="t",
        description=None,
        body="p",
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
            "summary": {"kind": "note", "target": "notes", "mode": "append"},
            "ovr": {"kind": "note", "target": "bigNotes", "mode": "replace"},
        },
        output_schema={},
        model="m",
    )
    aid = await annotations.insert(
        conn,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Clip_1",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": 1, "name": "Clip_1", "markers": [], "fields": {}},
        ),
    )
    seeded = await items.bulk_insert(
        conn,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="marker",
                proposed_value={
                    "name": "scene-a",
                    "in": {"frm": 0, "secs": 0.0},
                    "out": {"frm": 25, "secs": 1.0},
                },
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="marker",
                proposed_value={
                    "name": "scene-b",
                    "in": {"frm": 25, "secs": 1.0},
                    "out": {"frm": 50, "secs": 2.0},
                },
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="field",
                target_identifier="pragafilm.dekáda.natočení",
                proposed_value="30.léta",
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="note",
                target_identifier="notes",
                proposed_value="append-text",
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="note",
                target_identifier="bigNotes",
                proposed_value="replace-text",
            ),
        ],
    )
    for it in seeded:
        await items.set_decision(conn, it.id, "accepted")
    # re-fetch with applied_at column populated.
    accepted = await items.list_by_clip(conn, 1, decision="accepted")
    version = await prompts.get_version(conn, vid)
    return accepted, version, aid


def _make_queue():
    return WriteQueue(
        pending_ops_repo=PendingOperationsRepo(),
        review_items_repo=ReviewItemsRepo(),
    )


@pytest.mark.asyncio
async def test_enqueue_apply_groups_markers_into_single_op(db):
    accepted, version, aid = await _seed(db)
    q = _make_queue()
    op_ids = await q.enqueue_apply(
        db,
        clip_key=("catdv", "1"),
        items=accepted,
        target_map=version.target_map,
        expected_etag="2026-05-19",
        annotation_id=aid,
        fps=25.0,
    )
    assert len(op_ids) >= 1
    rows = await PendingOperationsRepo().list_pending(db)
    marker_rows = [r for r in rows if r["op_kind"] == "AddMarkers"]
    assert len(marker_rows) == 1
    op = json.loads(marker_rows[0]["op_json"])
    assert len(op["markers"]) == 2


@pytest.mark.asyncio
async def test_enqueue_apply_emits_set_field_and_note_ops(db):
    accepted, version, aid = await _seed(db)
    q = _make_queue()
    await q.enqueue_apply(
        db,
        clip_key=("catdv", "1"),
        items=accepted,
        target_map=version.target_map,
        expected_etag=None,
        annotation_id=aid,
        fps=25.0,
    )
    rows = await PendingOperationsRepo().list_pending(db)
    kinds = {r["op_kind"] for r in rows}
    assert "AddMarkers" in kinds
    assert "SetField" in kinds
    assert "AppendNote" in kinds
    assert "ReplaceNote" in kinds


@pytest.mark.asyncio
async def test_enqueue_apply_marks_review_items_applied_atomically(db):
    accepted, version, aid = await _seed(db)
    q = _make_queue()
    await q.enqueue_apply(
        db,
        clip_key=("catdv", "1"),
        items=accepted,
        target_map=version.target_map,
        expected_etag=None,
        annotation_id=aid,
        fps=25.0,
    )
    items = ReviewItemsRepo()
    after = await items.list_by_clip(db, 1, decision="accepted")
    assert all(it.applied_at is not None for it in after)


@pytest.mark.asyncio
async def test_enqueue_apply_is_idempotent_on_second_call(db):
    accepted, version, aid = await _seed(db)
    q = _make_queue()
    ids1 = await q.enqueue_apply(
        db,
        clip_key=("catdv", "1"),
        items=accepted,
        target_map=version.target_map,
        expected_etag=None,
        annotation_id=aid,
        fps=25.0,
    )
    assert ids1

    items = ReviewItemsRepo()
    accepted_again = await items.list_by_clip(db, 1, decision="accepted")
    ids2 = await q.enqueue_apply(
        db,
        clip_key=("catdv", "1"),
        items=accepted_again,
        target_map=version.target_map,
        expected_etag=None,
        annotation_id=aid,
        fps=25.0,
    )
    assert ids2 == []
    rows = await PendingOperationsRepo().list_pending(db)
    assert len(rows) == len(ids1)


@pytest.mark.asyncio
async def test_enqueue_apply_captures_expected_etag_per_row(db):
    accepted, version, aid = await _seed(db)
    q = _make_queue()
    await q.enqueue_apply(
        db,
        clip_key=("catdv", "1"),
        items=accepted,
        target_map=version.target_map,
        expected_etag="modify-date-v1",
        annotation_id=aid,
        fps=25.0,
    )
    rows = await PendingOperationsRepo().list_pending(db)
    assert rows
    assert all(r["expected_etag"] == "modify-date-v1" for r in rows)
