"""chunked_in_clause builds parameter-safe `WHERE (a, b) IN (...)` SQL
in chunks so we never exceed SQLite's SQLITE_LIMIT_VARIABLE_NUMBER (default
999, raised to 32766 in newer builds). Used by every batched repository
read to replace per-key loops."""

import pytest

from backend.app.repositories._batch import chunked_in_clause


def test_empty_keys_yields_nothing():
    assert list(chunked_in_clause([])) == []


def test_single_key_one_chunk():
    chunks = list(chunked_in_clause([("catdv", "42")]))
    assert len(chunks) == 1
    sql, params = chunks[0]
    assert sql == "(?, ?)"
    assert params == ["catdv", "42"]


def test_multiple_keys_one_chunk_under_limit():
    keys = [("catdv", str(i)) for i in range(5)]
    chunks = list(chunked_in_clause(keys, chunk_size=10))
    assert len(chunks) == 1
    sql, params = chunks[0]
    assert sql == "(?, ?), (?, ?), (?, ?), (?, ?), (?, ?)"
    assert params == ["catdv", "0", "catdv", "1", "catdv", "2", "catdv", "3", "catdv", "4"]


def test_keys_split_across_chunks_at_chunk_size():
    keys = [("catdv", str(i)) for i in range(7)]
    chunks = list(chunked_in_clause(keys, chunk_size=3))
    assert len(chunks) == 3
    assert chunks[0][1] == ["catdv", "0", "catdv", "1", "catdv", "2"]
    assert chunks[1][1] == ["catdv", "3", "catdv", "4", "catdv", "5"]
    assert chunks[2][1] == ["catdv", "6"]


def test_default_chunk_size_is_safe_for_sqlite_default_999():
    keys = [("catdv", str(i)) for i in range(1000)]
    chunks = list(chunked_in_clause(keys))
    assert all(len(params) <= 998 for _, params in chunks)


def test_raises_on_non_pair_tuple():
    with pytest.raises(ValueError, match="2-tuple"):
        list(chunked_in_clause([("catdv", "42", "extra")]))  # type: ignore[list-item]
