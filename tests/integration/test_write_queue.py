import json

import pytest

from backend.app.archive.model import ReplaceAIMarkers, SetField
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.write_queue import WriteQueue, _items_to_change_ops


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
            "ai_summary": {"kind": "note", "target": "pragafilm.popis.ai", "mode": "replace"},
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
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="note",
                target_identifier="pragafilm.popis.ai",
                proposed_value="shrnutí",
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
    marker_rows = [r for r in rows if r["op_kind"] == "ReplaceAIMarkers"]
    assert len(marker_rows) == 1
    op = json.loads(marker_rows[0]["op_json"])
    assert len(op["desired"]) == 2
    # the additive AddMarkers op is no longer emitted
    assert not any(r["op_kind"] == "AddMarkers" for r in rows)


@pytest.mark.asyncio
async def test_enqueue_apply_emits_only_owned_field_and_note_ops(db):
    """The app owns only AI markers and pragafilm.popis.ai. Non-owned field /
    note items (decade, top-level notes, bigNotes) are suggestions — never
    enqueued — while the owned summary note IS."""
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
    assert "ReplaceAIMarkers" in kinds
    # the owned summary note (pragafilm.popis.ai, replace mode) is enqueued
    assert "ReplaceNote" in kinds
    note_targets = {
        json.loads(r["op_json"]).get("target")
        for r in rows
        if r["op_kind"] in ("ReplaceNote", "AppendNote")
    }
    assert note_targets == {"pragafilm.popis.ai"}
    # no non-owned field / note op was enqueued
    assert "SetField" not in kinds  # decade is a suggestion, not a write
    assert "AppendNote" not in kinds  # top-level notes is a suggestion


@pytest.mark.asyncio
async def test_enqueue_apply_marks_only_owned_items_applied(db):
    """Items turned into owned ops are marked applied; suggestion items (never
    written) stay un-applied so they remain visible as copy-me suggestions."""
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
    applied = {it.target_identifier for it in after if it.applied_at is not None}
    unapplied = {it.target_identifier for it in after if it.applied_at is None}
    # markers (target None) and the owned note are applied
    assert None in applied  # the marker items
    assert "pragafilm.popis.ai" in applied
    # the suggestions stay un-applied
    assert "pragafilm.dekáda.natočení" in unapplied
    assert "notes" in unapplied
    assert "bigNotes" in unapplied


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


@pytest.mark.asyncio
async def test_enqueue_apply_window_serialized_on_the_connection(db):
    """Through WriteSerializedConnection, the write lock is held across the
    whole commit=False window (insert_many + mark_applied) and released only
    after conn.commit() — so no concurrent writer can split it. See ADR 0126."""
    from backend.app.db import WriteSerializedConnection
    from backend.app.services.write_queue import WriteQueue

    accepted, version, aid = await _seed(db)
    sdb = WriteSerializedConnection(db)
    pending = PendingOperationsRepo()
    items = ReviewItemsRepo()
    q = WriteQueue(pending_ops_repo=pending, review_items_repo=items)

    lock_states: list[tuple[str, bool]] = []
    orig_insert = pending.insert_many
    orig_mark = items.mark_applied

    async def spy_insert(conn, *, rows, commit=True):
        # commit=False opens the transaction; the serializer holds the lock.
        result = await orig_insert(conn, rows=rows, commit=commit)
        lock_states.append(("insert", sdb.locked()))
        return result

    async def spy_mark(conn, item_ids, *, commit=True):
        result = await orig_mark(conn, item_ids, commit=commit)
        lock_states.append(("mark_applied", sdb.locked()))
        return result

    pending.insert_many = spy_insert
    items.mark_applied = spy_mark

    await q.enqueue_apply(
        sdb,
        clip_key=("catdv", "1"),
        items=accepted,
        target_map=version.target_map,
        expected_etag=None,
        annotation_id=aid,
        fps=25.0,
    )

    # Lock held across the open window, released once conn.commit() lands.
    assert lock_states == [("insert", True), ("mark_applied", True)]
    assert not sdb.locked()


def test_edited_marker_dict_round_trips():
    """edited_value dict on a marker item → ReplaceAIMarkers op with the edits."""
    from backend.app.models.prompt import TargetMap

    item = ReviewItem(
        id=1,
        annotation_id=1,
        catdv_clip_id=1,
        kind="marker",
        proposed_value={"name": "Original", "in": {"secs": 0.0}, "out": {"secs": 1.0}},
        decision="accepted",
        edited_value={
            "name": "Edited",
            "category": "X",
            "description": "d",
            "in": {"secs": 2.0},
            "out": {"secs": 5.0},
        },
    )
    target_map = TargetMap(root={"scenes": {"kind": "markers"}})
    ops_with_origin = _items_to_change_ops([item], target_map, fps=25.0)

    assert len(ops_with_origin) == 1
    op, origin_ids = ops_with_origin[0]
    assert isinstance(op, ReplaceAIMarkers)
    assert len(op.desired) == 1
    m = op.desired[0]
    assert m.name == "Edited"
    assert m.in_.secs == 2.0
    assert m.out is not None and m.out.secs == 5.0
    assert m.description == "d"
    assert m.category == "X"
    assert origin_ids == [1]


def test_no_marker_items_emits_no_marker_op():
    """A fields-only draft (e.g. an image prompt) must NOT emit a marker op, so
    publishing it never wipes the clip's existing AI markers."""
    from backend.app.models.prompt import TargetMap

    item = ReviewItem(
        id=9,
        annotation_id=1,
        catdv_clip_id=1,
        kind="note",
        target_identifier="pragafilm.popis.ai",
        proposed_value="summary",
        decision="accepted",
    )
    target_map = TargetMap(
        root={"s": {"kind": "note", "target": "pragafilm.popis.ai", "mode": "replace"}}
    )
    ops_with_origin = _items_to_change_ops([item], target_map, fps=25.0)
    kinds = {type(op).__name__ for op, _ in ops_with_origin}
    assert "ReplaceAIMarkers" not in kinds
    assert kinds == {"ReplaceNote"}


def test_owned_field_round_trips_but_non_owned_is_dropped():
    """A field item targeting the owned field round-trips to a SetField; a field
    item outside AI_OWNED_FIELDS is a suggestion and produces no op."""
    from backend.app.models.prompt import TargetMap

    owned = ReviewItem(
        id=2,
        annotation_id=1,
        catdv_clip_id=1,
        kind="field",
        target_identifier="pragafilm.popis.ai",
        proposed_value="old",
        decision="accepted",
        edited_value=["x", "y"],
    )
    suggestion = ReviewItem(
        id=3,
        annotation_id=1,
        catdv_clip_id=1,
        kind="field",
        target_identifier="pragafilm.dekáda.natočení",
        proposed_value="30.léta",
        decision="accepted",
    )
    target_map = TargetMap(
        root={
            "a": {"kind": "field", "identifier": "pragafilm.popis.ai"},
            "b": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        }
    )
    ops_with_origin = _items_to_change_ops([owned, suggestion], target_map, fps=25.0)

    assert len(ops_with_origin) == 1
    op, origin_ids = ops_with_origin[0]
    assert isinstance(op, SetField)
    assert op.identifier == "pragafilm.popis.ai"
    assert op.value == ["x", "y"]
    assert origin_ids == [2]
