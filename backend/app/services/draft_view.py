"""Map an Annotation + its ReviewItems into the right-aside view-model.

The result is a dict with the same `markers / fields / notes` shapes the
existing Published view renders, so the Markers / Fields / Notes panels
can render Draft or Published through the same Jinja partial.
"""
from __future__ import annotations

from typing import Any

from backend.app.models.annotation import Annotation, ReviewItem


def build_draft_view(
    annotation: Annotation | None,
    review_items: list[ReviewItem],
) -> dict[str, Any]:
    if annotation is None:
        return {
            "has_draft": False,
            "annotation_id": None,
            "created_at": None,
            "prompt_name": None,
            "version_num": None,
            "model": None,
            "markers": [],
            "fields": [],
            "notes": None,
        }
    return {
        "has_draft": True,
        "annotation_id": annotation.id,
        "created_at": None,
        "prompt_name": None,
        "version_num": None,
        "model": annotation.model,
        "markers": [],
        "fields": [],
        "notes": None,
    }
