# backend/app/services/publish_status.py
"""Single source of truth for a clip's headline publish status.

Inputs are both cheap and already computed elsewhere:
  * has_draft        — un-applied review_items exist (ReviewItemsRepo.list_pending_clips)
  * version_state    — newest clip_versions.publish_state for the clip (or None)
  * version_num      — that newest version's number (or None)

Precedence: failed/conflict > publishing > draft > live > none.
Returns (ClipPublishState, version_num_or_None) so callers can render
'Live v3' / 'Publishing…' / 'Draft' / 'Failed' from one place.
"""

from __future__ import annotations

from backend.app.models.annotation import ClipPublishState


def resolve_publish_status(
    *, has_draft: bool, version_state: str | None, version_num: int | None
) -> tuple[ClipPublishState, int | None]:
    if version_state in ("failed", "conflict"):
        return (version_state, version_num)  # type: ignore[return-value]
    if version_state == "publishing":
        return ("publishing", version_num)
    if has_draft:
        return ("draft", version_num)
    if version_state in ("live", "superseded"):
        return ("live", version_num)
    return ("none", None)
