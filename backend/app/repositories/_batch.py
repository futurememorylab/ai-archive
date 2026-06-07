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
    keys: Iterable[tuple],
    *,
    chunk_size: int = 400,
) -> Iterator[tuple[str, list]]:
    """Yield `(sql, params)` pairs for batched `WHERE … IN (…)` SQL.

    Args:
        keys: iterable of 1- or 2-tuples (uniform within one call). Each
            tuple becomes one `(?)` / `(?, ?)` row. Single-column callers
            wrap their keys as 1-tuples: `[(job_id,) for job_id in ids]`
            and write `WHERE job_id IN ({fragment})`.
        chunk_size: max keys per chunk. Default 400 keeps the
            per-statement parameter count under 800 for the 2-column case.

    Yields:
        `(sql_fragment, params_list)` where `sql_fragment` is
        `"(?), (?), …"` or `"(?, ?), (?, ?), …"` and `params_list` is the
        flattened parameter list.

    Raises:
        ValueError: any element of `keys` is not a 1- or 2-tuple.
    """
    chunk: list[tuple] = []
    width: int | None = None
    for k in keys:
        if not (isinstance(k, tuple) and len(k) in (1, 2)):
            raise ValueError(f"chunked_in_clause requires 1- or 2-tuple keys; got {k!r}")
        if width is None:
            width = len(k)
        elif len(k) != width:
            raise ValueError(f"chunked_in_clause keys must be uniform width; got {k!r}")
        chunk.append(k)
        if len(chunk) >= chunk_size:
            yield _format(chunk)
            chunk = []
    if chunk:
        yield _format(chunk)


def _format(keys: list[tuple]) -> tuple[str, list]:
    width = len(keys[0])
    row = "(" + ", ".join("?" * width) + ")"
    sql = ", ".join([row] * len(keys))
    params: list = [v for k in keys for v in k]
    return sql, params
