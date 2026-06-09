from backend.app.uploaded_ids import (
    UPLOAD_ID_BASE,
    is_uploaded,
    to_clip_id,
    to_pk,
)


def test_base_is_one_billion():
    assert UPLOAD_ID_BASE == 1_000_000_000


def test_roundtrip_pk_to_clip_id_and_back():
    assert to_clip_id(1) == 1_000_000_001
    assert to_pk(1_000_000_001) == 1


def test_is_uploaded_predicate():
    assert is_uploaded(1_000_000_000) is True
    assert is_uploaded(1_000_000_999) is True
    assert is_uploaded(999_999_999) is False   # plausible CatDV id
    assert is_uploaded(42) is False
