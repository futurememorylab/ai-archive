"""Synthetic clip-id scheme for uploaded studio clips.

Uploaded clips have no CatDV id but must flow through the source-blind
integer `clip_id` pipeline (set membership, runs, proxy/thumbnail
resolution, the `/media/{clip_id}` routes). We give each uploaded clip a
positive synthetic id `UPLOAD_ID_BASE + uploaded_clip.id`. Positive (not
negative) because FastAPI's `int` path converter regex is `[0-9]+` —
negative ids would 404 `/media/-5`. The range is disjoint from this
deployment's CatDV ids, so `is_uploaded` is an O(1) predicate.
"""

UPLOAD_ID_BASE = 1_000_000_000


def is_uploaded(clip_id: int) -> bool:
    return clip_id >= UPLOAD_ID_BASE


def to_clip_id(pk: int) -> int:
    return UPLOAD_ID_BASE + pk


def to_pk(clip_id: int) -> int:
    return clip_id - UPLOAD_ID_BASE
