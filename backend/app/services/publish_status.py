# backend/app/services/publish_status.py
"""Single source of truth for a clip's headline publish status.

The in-flight / failed / conflict signal comes from `pending_operations` — the
durable write queue, which the SyncEngine updates on EVERY path. This is the
same source the topbar sync chip and the batches "N failed to sync" surface use,
so all three agree. We deliberately do NOT key the headline off
`clip_versions.publish_state`: that is a derived copy and any code path that
forgets to update it leaves a clip stuck reading "Publishing…" forever. The
version table is consulted only for *which* version is live (the number).

Inputs (all cheap, all already computed by the caller's batched reads):
  * has_draft        — un-applied review_items exist (list_pending_clips)
  * pending_write    — clip has pending/in_flight write ops
  * failed_write     — clip has failed write ops
  * conflict_write   — clip has conflicted write ops
  * live_version_num — the clip's live clip_version number (or None)

Precedence: conflict > failed > publishing > draft > live > none.
Returns (ClipPublishState, version_num_or_None) so callers can render
'Live v3' / 'Publishing…' / 'Draft' / 'Failed' / 'Conflict' from one place.
"""

from __future__ import annotations

from backend.app.models.annotation import ClipPublishState


def resolve_publish_status(
    *,
    has_draft: bool,
    pending_write: bool = False,
    failed_write: bool = False,
    conflict_write: bool = False,
    live_version_num: int | None = None,
) -> tuple[ClipPublishState, int | None]:
    if conflict_write:
        return ("conflict", live_version_num)
    if failed_write:
        return ("failed", live_version_num)
    if pending_write:
        return ("publishing", live_version_num)
    if has_draft:
        return ("draft", live_version_num)
    if live_version_num is not None:
        return ("live", live_version_num)
    return ("none", None)
