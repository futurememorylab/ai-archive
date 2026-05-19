from backend.app.archive.model import ConflictDetail, WriteResult


def test_conflict_detail_carries_diff_payload():
    cd = ConflictDetail(
        kind="modified",
        expected_etag="v1",
        actual_etag="v2",
        fields={"pragafilm.theme": {"local": "x", "remote": "y"}},
    )
    assert cd.kind == "modified"
    assert cd.fields["pragafilm.theme"]["remote"] == "y"


def test_conflict_detail_defaults_are_empty():
    cd = ConflictDetail(kind="deleted")
    assert cd.expected_etag is None
    assert cd.actual_etag is None
    assert cd.fields == {}


def test_write_result_can_carry_conflict_detail():
    cd = ConflictDetail(kind="modified", expected_etag="v1", actual_etag="v2")
    wr = WriteResult(
        status="conflict",
        upstream_response={},
        new_etag=None,
        conflict_detail=cd,
    )
    assert wr.status == "conflict"
    assert wr.conflict_detail is cd


def test_write_result_ok_default_has_no_conflict_detail():
    wr = WriteResult(status="ok", upstream_response={"x": 1}, new_etag="abc")
    assert wr.conflict_detail is None
