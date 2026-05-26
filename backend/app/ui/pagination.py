"""Pager offset math shared by the clips and cache list pages."""

from __future__ import annotations


def page_offsets(offset: int, limit: int, total: int) -> tuple[int | None, int | None]:
    """Return (prev_offset, next_offset) for a paged list, or None at an edge."""
    prev_offset = max(0, offset - limit) if offset > 0 else None
    next_offset = offset + limit if offset + limit < total else None
    return prev_offset, next_offset
