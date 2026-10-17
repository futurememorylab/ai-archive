"""Batch naming rules (docs/specs/2026-10-17-batches-redesign-design.md).

A batch is named after what's in it, not after its prompt. The browser
pre-fills the New batch / Annotate selected name field (search text, else
the rule below); the server applies `default_batch_name` when a caller sends
no name. Migration 0032 backfills existing batches with the same rule in SQL,
so keep the two in step.
"""

from __future__ import annotations

MAX_LEN = 120


def default_batch_name(first_clip_name: str | None, first_clip_id: int, clip_count: int) -> str:
    """The first clip's name (else "Clip <id>"), plus "and N more" when the
    batch holds other clips."""
    base = (first_clip_name or "").strip() or f"Clip {first_clip_id}"
    return f"{base} and {clip_count - 1} more" if clip_count > 1 else base


def clean_batch_name(raw: str | None) -> str | None:
    """Collapse whitespace and cap the length; None when nothing is left."""
    name = " ".join((raw or "").split())
    return name[:MAX_LEN] or None
