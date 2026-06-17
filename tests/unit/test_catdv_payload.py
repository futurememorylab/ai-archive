from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    Marker,
    ReconcileMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.payload import build_put_payload


def _clip(markers=None, fields=None, notes=None, big_notes=None, fps=25.0):
    out = {"ID": 1, "name": "c", "fps": fps, "markers": markers or [], "fields": fields or {}}
    if notes is not None:
        out["notes"] = notes
    if big_notes is not None:
        out["bigNotes"] = big_notes
    return out


def test_no_ops_returns_empty_payload():
    assert build_put_payload(current=_clip(), ops=[]) == {}


def test_add_markers_appends_to_existing_and_normalizes_timecode():
    existing = [
        {
            "name": "m0",
            "in": {"frm": 0, "fmt": 25.0, "secs": 0.0, "txt": "0:00:00:00"},
            "out": {"frm": 25, "fmt": 25.0, "secs": 1.0, "txt": "0:00:01:00"},
        }
    ]
    op = AddMarkers(
        markers=[
            Marker(
                name="m1",
                in_=Timecode(secs=4.0, fps=25.0),
                out=Timecode(secs=6.0, fps=25.0),
            )
        ]
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert "markers" in payload
    assert len(payload["markers"]) == 2
    new_m = payload["markers"][1]
    assert new_m["in"]["frm"] == 100
    assert new_m["in"]["fmt"] == 25.0
    assert new_m["in"]["txt"] == "00:00:04:00"


def test_add_markers_dedupes_on_existing_in_frm():
    existing = [
        {
            "name": "m0",
            "in": {"frm": 100, "fmt": 25.0, "secs": 4.0, "txt": "0:00:04:00"},
            "out": {"frm": 150, "fmt": 25.0, "secs": 6.0, "txt": "0:00:06:00"},
        }
    ]
    op = AddMarkers(markers=[Marker(name="dup", in_=Timecode(secs=4.0, fps=25.0), out=None)])
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1


def test_add_markers_our_marker_overwrites_existing_at_same_frm():
    """Anti-mojibake: when our marker shares a timecode with an existing CatDV
    marker, OURS wins — re-publishing overwrites a progressively-corrupted
    name/category with the correct DB copy instead of re-submitting CatDV's
    (which is how 'Město' compounded into 'MÃÃ…sto' and overflowed)."""
    existing = [
        {
            "name": "MÃÃÃÃsto",  # CatDV's compounding-mojibake copy
            "in": {"frm": 100, "fmt": 25.0, "secs": 4.0, "txt": "0:00:04:00"},
        }
    ]
    op = AddMarkers(markers=[Marker(name="Město", in_=Timecode(secs=4.0, fps=25.0), out=None)])
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1
    assert payload["markers"][0]["name"] == "Město"  # ours, not the corrupted existing


def test_add_markers_preserves_untouched_existing_at_other_frm():
    existing = [{"name": "keep", "in": {"frm": 0, "fmt": 25.0, "secs": 0.0, "txt": "0:00:00:00"}}]
    op = AddMarkers(markers=[Marker(name="new", in_=Timecode(secs=4.0, fps=25.0), out=None)])
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert {m["name"] for m in payload["markers"]} == {"keep", "new"}


def test_set_field_writes_to_fields_map():
    op = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload == {"fields": {"pragafilm.dekáda.natočení": "30.léta"}}


def test_append_note_joins_with_separator_when_existing_present():
    # notes / bigNotes are top-level clip properties in CatDV, NOT user
    # fields — writing them under payload["fields"] sets a phantom field
    # and never updates the real Notes (mapping.from_catdv_clip reads them
    # top-level, so the write must mirror that). See ADR 0090.
    op = AppendNote(target="notes", text="new line")
    payload = build_put_payload(current=_clip(notes="old"), ops=[op])
    assert payload["notes"] == "old\n\n---\n\nnew line"
    assert "fields" not in payload


def test_append_note_writes_directly_when_no_existing():
    op = AppendNote(target="notes", text="new")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["notes"] == "new"
    assert "fields" not in payload


def test_replace_note_overrides_existing():
    op = ReplaceNote(target="bigNotes", text="fresh")
    payload = build_put_payload(current=_clip(big_notes="old"), ops=[op])
    assert payload["bigNotes"] == "fresh"
    assert "fields" not in payload


def test_note_targeting_a_user_field_still_writes_under_fields():
    # A note whose target is a user-defined text field (not notes/bigNotes)
    # routes through the fields map, symmetric with _existing_text's read.
    op = ReplaceNote(target="pragafilm.popis.materialu", text="popis")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["fields"]["pragafilm.popis.materialu"] == "popis"
    assert "notes" not in payload


def test_append_note_is_idempotent_when_already_appended_with_separator():
    # Re-draining an AppendNote (crash recovery, or a retry after a PUT the
    # server applied but whose response was lost) must NOT append again. The
    # live clip already ends with the appended segment → emit nothing.
    # See ADR 0091.
    op = AppendNote(target="notes", text="summary")
    payload = build_put_payload(current=_clip(notes="old\n\n---\n\nsummary"), ops=[op])
    assert payload == {}


def test_append_note_is_idempotent_when_text_is_the_whole_note():
    # First-ever append landed (no separator, note == the appended text).
    op = AppendNote(target="bigNotes", text="only")
    payload = build_put_payload(current=_clip(big_notes="only"), ops=[op])
    assert payload == {}


def test_append_note_still_appends_when_text_differs():
    # Guard: a genuinely new append is not suppressed by the idempotency check.
    op = AppendNote(target="notes", text="second")
    payload = build_put_payload(current=_clip(notes="old\n\n---\n\nfirst"), ops=[op])
    assert payload["notes"] == "old\n\n---\n\nfirst\n\n---\n\nsecond"


def test_two_appends_to_same_note_chain_not_clobber():
    # Two accepted notes for the same clip (e.g. two annotation runs) become
    # two AppendNote ops with the same target. The SyncEngine merges all of a
    # clip's pending ops into ONE ChangeSet, so both land in one payload. They
    # must CHAIN — dropping the earlier one is silent data loss.
    ops = [
        AppendNote(target="notes", text="first"),
        AppendNote(target="notes", text="second"),
    ]
    payload = build_put_payload(current=_clip(notes="old"), ops=ops)
    assert payload["notes"] == "old\n\n---\n\nfirst\n\n---\n\nsecond"


def test_two_appends_to_same_note_chain_with_no_existing():
    ops = [
        AppendNote(target="notes", text="a"),
        AppendNote(target="notes", text="b"),
    ]
    payload = build_put_payload(current=_clip(), ops=ops)
    assert payload["notes"] == "a\n\n---\n\nb"


def test_two_appends_to_same_user_field_chain_not_clobber():
    # Same clobber risk for note-mode writes routed through the fields map.
    ops = [
        AppendNote(target="pragafilm.popis", text="a"),
        AppendNote(target="pragafilm.popis", text="b"),
    ]
    payload = build_put_payload(current=_clip(), ops=ops)
    assert payload["fields"]["pragafilm.popis"] == "a\n\n---\n\nb"


def test_multiple_ops_combined_in_one_payload():
    op_m = AddMarkers(markers=[Marker(name="m", in_=Timecode(secs=2.0, fps=25.0), out=None)])
    op_f = SetField(identifier="pragafilm.barva", value="true")
    op_n = AppendNote(target="notes", text="x")
    payload = build_put_payload(current=_clip(), ops=[op_m, op_f, op_n])
    assert "markers" in payload
    assert payload["fields"]["pragafilm.barva"] == "true"
    assert payload["notes"] == "x"
    assert "notes" not in payload["fields"]


def test_reconcile_drops_our_later_markers_keeps_foreign():
    # Clip carries our A,B,C,D (25fps) plus a human marker H we never authored.
    existing = [
        {"name": "A", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}},
        {"name": "B", "in": {"frm": 200, "fmt": 25.0, "secs": 8.0}},
        {"name": "C", "in": {"frm": 300, "fmt": 25.0, "secs": 12.0}},
        {"name": "D", "in": {"frm": 400, "fmt": 25.0, "secs": 16.0}},
        {"name": "H", "in": {"frm": 500, "fmt": 25.0, "secs": 20.0}},
    ]
    op = ReconcileMarkers(
        desired=(
            Marker(name="A", in_=Timecode(secs=4.0, fps=0.0), out=None),
            Marker(name="B", in_=Timecode(secs=8.0, fps=0.0), out=None),
        ),
        drop_secs=(12.0, 16.0),
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert sorted(m["name"] for m in payload["markers"]) == ["A", "B", "H"]


def test_reconcile_overwrites_our_copy_at_shared_frame():
    existing = [{"name": "MÃÃsto", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}}]
    op = ReconcileMarkers(
        desired=(Marker(name="Město", in_=Timecode(secs=4.0, fps=0.0), out=None),),
        drop_secs=(),
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1
    assert payload["markers"][0]["name"] == "Město"


def test_reconcile_derives_frames_at_clip_fps_no_duplicate():
    # 30fps clip: our A,B already at frm 120/240 (4s,8s * 30). Re-asserting must
    # re-derive at 30fps (not 25) so no duplicate at frm 100/200 appears.
    existing = [
        {"name": "A", "in": {"frm": 120, "fmt": 30.0, "secs": 4.0}},
        {"name": "B", "in": {"frm": 240, "fmt": 30.0, "secs": 8.0}},
    ]
    op = ReconcileMarkers(
        desired=(
            Marker(name="A", in_=Timecode(secs=4.0, fps=0.0), out=None),
            Marker(name="B", in_=Timecode(secs=8.0, fps=0.0), out=None),
        ),
        drop_secs=(),
    )
    payload = build_put_payload(current=_clip(markers=existing, fps=30.0), ops=[op])
    assert sorted(m["in"]["frm"] for m in payload["markers"]) == [120, 240]


def test_reconcile_drops_foreign_marker_colliding_on_a_dropped_frame():
    # KNOWN LIMITATION (ADR 0101): CatDV markers carry no stable id, so reconcile
    # keys on the integer in-frame. A foreign / human marker sitting on the EXACT
    # frame of one of our dropped markers is indistinguishable from ours and is
    # removed. Pinned so any future change to this behaviour is deliberate.
    existing = [
        {"name": "ours_keep", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}},
        # human marker that happens to collide with the dropped 8.0s frame:
        {"name": "HUMAN", "in": {"frm": 200, "fmt": 25.0, "secs": 8.0}},
    ]
    op = ReconcileMarkers(
        desired=(Marker(name="ours_keep", in_=Timecode(secs=4.0, fps=0.0), out=None),),
        drop_secs=(8.0,),
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert sorted(m["name"] for m in payload["markers"]) == ["ours_keep"]
