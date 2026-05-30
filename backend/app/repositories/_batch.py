"""Batched SQL helpers shared across repositories.

`chunked_in_clause` builds parameter-safe `WHERE (a, b) IN ((?, ?), …)`
fragments in chunks bounded by SQLite's SQLITE_LIMIT_VARIABLE_NUMBER
(default 999 in older builds, 32766 in 3.32+). Default chunk_size=400
keeps the per-statement parameter count under 800 for the (provider_id,
provider_clip_id) two-column case, comfortably under the 999 floor.

Yields `(sql_fragment, params_list)` pairs that a caller wraps with the
table-specific SELECT and concatenates results across chunks.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar

_T = TypeVar("_T")


def chunked_in_clause(
    keys: Iterable[tuple[str, str]],
    *,
    chunk_size: int = 400,
) -> Iterator[tuple[str, list[str]]]:
    """Yield `(sql, params)` pairs for batched `WHERE (a, b) IN (…)` SQL.

    Args:
        keys: iterable of 2-tuples. Each tuple becomes one `(?, ?)` row.
        chunk_size: max keys per chunk. Default 400 keeps the
            per-statement parameter count under 800.

    Yields:
        `(sql_fragment, params_list)` where `sql_fragment` is
        `"(?, ?), (?, ?), …"` and `params_list` is the flattened
        parameter list.

    Raises:
        ValueError: any element of `keys` is not a 2-tuple.
    """
    chunk: list[tuple[str, str]] = []
    for k in keys:
        if not (isinstance(k, tuple) and len(k) == 2):
            raise ValueError(f"chunked_in_clause requires 2-tuple keys; got {k!r}")
        chunk.append(k)
        if len(chunk) >= chunk_size:
            yield _format(chunk)
            chunk = []
    if chunk:
        yield _format(chunk)


def _format(keys: list[tuple[str, str]]) -> tuple[str, list[str]]:
    sql = ", ".join(["(?, ?)"] * len(keys))
    params: list[str] = []
    for a, b in keys:
        params.append(a)
        params.append(b)
    return sql, params
