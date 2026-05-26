from backend.app.ui.pagination import page_offsets


def test_first_page_has_no_prev():
    assert page_offsets(offset=0, limit=50, total=240) == (None, 50)


def test_middle_page_has_both():
    assert page_offsets(offset=50, limit=50, total=240) == (0, 100)


def test_last_page_has_no_next():
    assert page_offsets(offset=200, limit=50, total=240) == (150, None)


def test_single_page_has_neither():
    assert page_offsets(offset=0, limit=50, total=30) == (None, None)


def test_empty_has_neither():
    assert page_offsets(offset=0, limit=50, total=0) == (None, None)
