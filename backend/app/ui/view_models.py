"""Map CanonicalClip + provider_data into flat dicts for Jinja templates.

Keeps templates logic-free: no `.provider_data["foo"]["bar"]` chains in HTML.
"""

from __future__ import annotations

import re
from typing import Any

import ftfy

from backend.app.archive.model import (
    AI_MARKER_CATEGORY,
    AI_OWNED_FIELDS,
    CanonicalClip,
    FieldValue,
    Marker,
)
from backend.app.media_kind import is_image_path

_YEAR_FIELD = "pragafilm.rok.natočení"
_DECADE_FIELD = "pragafilm.dekáda.natočení"
_PRAGAFILM_PREFIX = "pragafilm."

# An app-authored marker name carries its Gemini category as a "[Category] "
# prefix (see model.ai_marker_name). On display we peel it back into a category
# chip. The regex is deliberately strict: a leading [..] with a following space.
_AI_NAME_PREFIX = re.compile(r"^\[([^\]]+)\]\s(.*)$", re.DOTALL)


def _split_ai_prefix(name: str) -> tuple[str | None, str]:
    """Return (category, name) peeled from an AI marker's "[Category] name"
    display name. Without a prefix: (None, name)."""
    m = _AI_NAME_PREFIX.match(name or "")
    if m:
        return m.group(1), m.group(2)
    return None, name or ""


def _fix(s: str | None) -> str | None:
    """Repair Czech mojibake that lives in CatDV's legacy marker payloads.

    Some clips were imported via one or two rounds of UTF-8-as-Latin-1
    re-encoding. ftfy's `fix_text` handles the single-round case cleanly
    and never touches strings that already look fine. For the deeper
    cases (e.g. `koÃÂÃÂ¡rkem` → `kočárkem`) ftfy stops at half-fixed
    output, so we then peel additional `latin-1 → utf-8` rounds for as
    long as the round-trip is well-formed and keeps shrinking the
    string (a real fix always removes bytes — mojibake re-encodes one
    byte as two).
    """
    if not s:
        return s
    # Peel raw double/triple mojibake first while the string is still
    # entirely in the Latin-1 range; ftfy can only undo one round on its
    # own. A real fix always shortens the string (one mangled byte was
    # re-encoded as two), so progress is the stop condition.
    for _ in range(3):
        try:
            peeled = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if peeled == s or len(peeled) >= len(s):
            break
        s = peeled
    return ftfy.fix_text(s)


def _first_value(fv: FieldValue | None) -> str | None:
    if fv is None:
        return None
    v = fv.value
    if isinstance(v, list):
        return str(v[0]) if v else None
    return str(v) if v is not None else None


def clip_summary(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    """One row in the clips-list table."""
    clip_id = int(clip.key[1])
    return {
        "id": clip_id,
        "name": _fix(clip.name),
        "duration_secs": clip.duration_secs,
        "year": _first_value(clip.fields.get(_YEAR_FIELD)),
        "decade": _first_value(clip.fields.get(_DECADE_FIELD)),
        "marker_count": len(clip.markers),
        "cache": cache_status_view(cache_status) if cache_status else None,
        "thumb_url": f"/api/media/{clip_id}/thumb",
        "kind": _media_kind(clip.provider_data),
        "select_value": f"{clip.key[0]}/{clip_id}",
        "row_href": f"/clips/{clip_id}",
    }


def _marker_view(m: Marker) -> dict[str, Any]:
    """A published marker for the clip page (#123 provenance).

    `origin` is "ai" when the marker is app-owned (`category == "AI"`), else
    "human". For an AI marker the Gemini category is peeled back out of the
    "[Category] name" display name into `category`, and the bare name shown;
    human markers keep their stored name/category untouched.
    """
    is_ai = m.category == AI_MARKER_CATEGORY
    if is_ai:
        cat, bare = _split_ai_prefix(m.name or "")
        return {
            "name": _fix(bare),
            "in_secs": m.in_.secs,
            "out_secs": m.out.secs if m.out is not None else None,
            "description": _fix(m.description),
            "category": cat,
            "color": m.color,
            "origin": "ai",
        }
    return {
        "name": _fix(m.name),
        "in_secs": m.in_.secs,
        "out_secs": m.out.secs if m.out is not None else None,
        "description": _fix(m.description),
        "category": m.category,
        "color": m.color,
        "origin": "human",
    }


def _field_view(fv: FieldValue) -> dict[str, Any]:
    value = fv.value
    if isinstance(value, list):
        value_str = ", ".join(_fix(str(v)) or "" for v in value)
    elif value is None:
        value_str = ""
    else:
        value_str = _fix(str(value)) or ""
    return {
        "identifier": fv.identifier,
        "name": fv.identifier.split(".")[-1],
        "value": value_str,
        # The app owns pragafilm.popis.ai; every other field is human.
        "origin": "ai" if fv.identifier in AI_OWNED_FIELDS else "human",
    }


def _format_summary(provider_data: dict[str, Any]) -> str:
    media = provider_data.get("media") or {}
    bits: list[str] = []
    fmt = provider_data.get("format")
    if fmt:
        bits.append(str(fmt))
    codec = media.get("codec")
    if codec:
        bits.append(str(codec))
    w, h = media.get("width"), media.get("height")
    if w and h:
        bits.append(f"{w}×{h}")
    return " · ".join(bits)


def _media_kind(provider_data: dict[str, Any]) -> str:
    media = provider_data.get("media") or {}
    return "image" if is_image_path(media.get("filePath")) else "video"


# Description tab toggle: human first, then AI; empty ones are skipped.
_DESCRIPTION_FIELDS = (
    ("human", "Human", "pragafilm.popis.materialu"),
    ("ai", "AI", "pragafilm.popis.ai"),
)


def _descriptions(fields_view: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ident = {f["identifier"]: f for f in fields_view}
    out = []
    for key, label, ident in _DESCRIPTION_FIELDS:
        f = by_ident.get(ident)
        if f and f["value"].strip():
            out.append({"key": key, "label": label, "origin": f["origin"], "text": f["value"]})
    return out


def clip_detail(
    clip: CanonicalClip,
    cache_status: Any | None = None,
) -> dict[str, Any]:
    """Single-clip page view model."""
    clip_id = int(clip.key[1])
    fields_view = [
        _field_view(fv) for ident, fv in clip.fields.items() if ident.startswith(_PRAGAFILM_PREFIX)
    ]
    fields_view.sort(key=lambda f: f["identifier"])

    return {
        "clip": {
            "id": clip_id,
            "name": clip.name,
            "duration_secs": clip.duration_secs,
            "fps": clip.fps or 25.0,
            "format": _format_summary(clip.provider_data),
            "kind": _media_kind(clip.provider_data),
            "media_url": f"/api/media/{clip_id}",
            "markers": [_marker_view(m) for m in sorted(clip.markers, key=lambda m: m.in_.secs)],
            "fields": fields_view,
            "descriptions": _descriptions(fields_view),
            "notes": _fix(clip.provider_data.get("notes")) or None,
            "big_notes": _fix(clip.provider_data.get("bigNotes")) or None,
            "cache": cache_status_view(cache_status) if cache_status else None,
        },
    }


def cache_status_view(status) -> dict[str, Any]:
    """Render-ready cache hints for the badge + buttons."""
    md, ml, ai = status.layers

    def _shape(layer) -> dict[str, Any]:
        size = int(layer.size_bytes or 0)
        pinned = bool(layer.pinned_by_workspaces)
        return {
            "present": bool(layer.present),
            "pinned": pinned,
            "evictable": bool(layer.evictable),
            "size_bytes": size,
            "size_mb": size // (1024 * 1024),
        }

    return {
        "clip_key": list(status.clip_key),
        "metadata": _shape(md),
        "media_local": _shape(ml),
        "media_ai": _shape(ai),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _batch_headline(c: dict[str, int], running: bool) -> tuple[str, str, str]:
    """(section, headline, state) for one batch. Precedence: running, clips to
    review, write-backs that failed (never masked as green, same source as the
    topbar sync chip, ADR 0096), failed clips, write-backs in flight, stopped
    with no output, else all reviewed. Sections: 'attention' (Needs you),
    'running', 'done'."""
    if running:
        done = c["completed"] + c["failed"]
        return "running", f"Running · {done} of {c['ran']} done", "accent"
    if c["awaiting"]:
        return "attention", f"{_plural(c['awaiting'], 'clip')} to review", "info"
    if c["problems"]:
        return "attention", f"{c['problems']} couldn't save to CatDV", "bad"
    if c["failed"]:
        return "attention", f"{c['failed']} failed", "bad"
    if c["syncing"]:
        return "done", f"Saving {c['syncing']} to CatDV…", "accent"
    if c["cancelled"] and not c["completed"]:
        return "done", "Stopped", ""
    return "done", "All reviewed", "ok"


_SEGMENT_LABELS = {
    "reviewed": "reviewed",
    "review": "to review",
    "saving": "saving",
    "problem": "couldn't save",
    "running": "running",
    "failed": "failed",
    "stopped": "stopped",
}


def _batch_segments(c: dict[str, int]) -> list[dict[str, Any]]:
    """One entry per non-empty state, in bar order, for the segmented progress
    bar and its legend. Reviewed = finished clips not waiting on review or a
    write-back."""
    counts = {
        "reviewed": max(0, c["completed"] - c["awaiting"] - c["syncing"] - c["problems"]),
        "review": c["awaiting"],
        "saving": c["syncing"],
        "problem": c["problems"],
        "running": c["in_flight"],
        "failed": c["failed"],
        "stopped": c["cancelled"],
    }
    return [
        {"key": k, "count": n, "label": f"{n} {_SEGMENT_LABELS[k]}"}
        for k, n in counts.items()
        if n > 0
    ]


def batch_view(row: dict) -> dict:
    """Shape a `JobsRepo.list_batches` row into the dict a Batches page row
    renders (docs/specs/2026-10-17-batches-redesign-design.md). Pure function
    (no I/O) so it is unit-tested in isolation. The route adds the thumbnail
    preview (`thumbs`, `clips`) from `JobsRepo.batch_previews`."""
    c = {
        "ran": int(row["ran"]),
        "completed": int(row["completed"]),
        "failed": int(row["failed"]),
        "awaiting": int(row["awaiting_clips"]),
        "in_flight": int(row.get("in_flight", 0) or 0),
        # Clips whose write-backs are queued but not confirmed on CatDV, and
        # ones that failed / conflicted (pending_operations, ADR 0093 / 0096).
        # Optional for back-compat.
        "syncing": int(row.get("syncing_clips", 0) or 0),
        "problems": int(row.get("problem_clips", 0) or 0),
        # Items cancelled by orphan recovery or Cancel. Optional for back-compat.
        "cancelled": int(row.get("cancelled", 0) or 0),
    }
    running = int(row["running_jobs"]) > 0 or c["in_flight"] > 0
    section, headline, headline_state = _batch_headline(c, running)

    prompt = row.get("prompt_name") or "(prompt unavailable)"
    if row.get("prompt_name") and int(row.get("prompt_count", 1)) > 1:
        prompt = f"{prompt} + {int(row['prompt_count']) - 1} more"

    job_ids = list(row["job_ids"])
    started = row.get("started_at") or ""
    try:
        from datetime import datetime as _dt

        started = _dt.fromisoformat(started).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        pass

    # "Review" jumps straight into the review of the first un-reviewed clip
    # of this batch; fall back to the batch-filtered clips list if none.
    first_pending = row.get("first_pending_clip_id")
    review_href = (
        f"/clips/{int(first_pending)}?review=1"
        if first_pending is not None
        else f"/?batch={','.join(str(i) for i in job_ids)}&anno=for_review"
    )
    # The search page filtered to this batch: ALL its clips, any status.
    files_href = f"/?batch={','.join(str(i) for i in job_ids)}"

    return {
        "batch_key": row["batch_key"],
        "id": int(row["primary_job_id"]),
        "name": row.get("batch_name") or f"Batch #{int(row['primary_job_id'])}",
        "job_ids": job_ids,
        "prompt": prompt,
        "version": row.get("version_num"),
        "model": row.get("model") or "",
        "started": started,
        "ran": c["ran"],
        "completed": c["completed"],
        "failed": c["failed"],
        "awaiting": c["awaiting"],
        "running": running,
        "section": section,
        "headline": headline,
        "headline_state": headline_state,
        "segments": _batch_segments(c),
        "review_href": review_href,
        "files_href": files_href,
    }


_RUNNING_ITEM = frozenset({"resolving", "uploading", "prompting"})


def _clip_status(r: dict) -> tuple[int, str, str]:
    """(rank, label, pill state) of one clip in a batch; lower rank lists
    first, so what needs the user leads the Show clips list."""
    status = r["item_status"]
    if r["awaiting"]:
        return 0, "To review", "info"
    if status == "error":
        return 1, "Failed", "bad"
    if r["sync"] == "problem":
        return 1, "Couldn't save", "bad"
    if status in _RUNNING_ITEM:
        return 2, "Running", "accent"
    if status == "pending":
        return 3, "Waiting", ""
    if r["sync"] == "saving":
        return 4, "Saving to archive…", "accent"
    if status == "cancelled":
        return 6, "Stopped", ""
    return 5, "Reviewed", "ok"


def batch_clip_list(rows: list[dict], *, limit: int = 50) -> tuple[list[dict], int]:
    """Shape `JobsRepo.batch_clip_rows` for the shared `_video_list.html`:
    status-ordered (queue order within a status), capped at `limit`. Returns
    (items, total clip count)."""
    ranked = sorted(
        ((_clip_status(r), i, r) for i, r in enumerate(rows)), key=lambda t: (t[0][0], t[1])
    )
    items = [
        {
            "id": r["clip_id"],
            "name": r["name"] or f"Clip {r['clip_id']}",
            "thumb_url": f"/api/media/{r['clip_id']}/thumb",
            "row_href": f"/clips/{r['clip_id']}",
            "status": {"label": label, "state": state},
            "error": r["error"] if r["item_status"] == "error" else None,
        }
        for (_, label, state), _, r in ranked[:limit]
    ]
    return items, len(rows)


def draft_review_arrays(draft: dict) -> dict:
    """Shape a `build_draft_view` result into the Alpine arrays the Draft
    panel + 3-color timeline render from. Each item carries `item_id` and a
    `status` ("accepted" if its review_item decision is accepted, else
    "proposed"). Partitioning: applied items (applied_at set) are excluded
    from the arrays and counted in `applied_count` (they're syncing to
    CatDV); rejected items land in the `deleted` bucket so the panel can
    offer Restore; rejected+applied items appear nowhere. Pure function —
    unit-tested in isolation."""
    out = {
        "markers": [],
        "fields": [],
        "notes": [],
        "applied_count": 0,  # enqueued to the write queue (may still be syncing)
        "synced_count": 0,  # confirmed landed on CatDV (synced_at set)
        "deleted": {"markers": [], "fields": [], "notes": []},
    }
    if not draft.get("has_draft"):
        return out

    def _status(decision) -> str:
        return "accepted" if decision == "accepted" else "proposed"

    def _marker(m: dict) -> dict:
        # A draft marker is always app-owned (markers are AI). Its Gemini
        # category is the raw proposed one; no "[Category]" prefix yet (that is
        # applied only on write), so no peeling here.
        return {
            "item_id": m["item_id"],
            "status": _status(m.get("decision")),
            "name": m.get("name") or "",
            "category": m.get("category"),
            "description": m.get("description"),
            "in_secs": m["in_secs"],
            "out_secs": m.get("out_secs"),
            "color": m.get("color"),
            "origin": "ai_draft",
        }

    def _field(f: dict) -> dict:
        ident = f.get("identifier") or ""
        # A field/note item the app does NOT own is a suggestion: it is never
        # written to the archive, only offered for a human to copy (#123).
        suggestion = ident not in AI_OWNED_FIELDS
        return {
            "item_id": f["item_id"],
            "status": _status(f.get("decision")),
            "identifier": ident,
            "value": f.get("value", ""),
            "multi": bool(f.get("multi")),
            "origin": "ai_suggestion" if suggestion else "ai_draft",
            "suggestion": suggestion,
        }

    def _note(n: dict) -> dict:
        ident = n.get("identifier") or ""
        suggestion = ident not in AI_OWNED_FIELDS
        return {
            "item_id": n["item_id"],
            "status": _status(n.get("decision")),
            "text": n.get("text") or "",
            "identifier": ident,
            "origin": "ai_suggestion" if suggestion else "ai_draft",
            "suggestion": suggestion,
        }

    for src_key, out_key, shape in (
        ("markers", "markers", _marker),
        ("fields", "fields", _field),
        ("note_items", "notes", _note),
    ):
        for it in draft.get(src_key, []):
            rejected = it.get("decision") == "rejected"
            applied = it.get("applied_at") is not None
            if rejected and applied:
                continue  # gone upstream and unwanted: show nowhere
            if rejected:
                out["deleted"][out_key].append(shape(it))
            elif applied:
                out["applied_count"] += 1
                if it.get("synced_at") is not None:
                    out["synced_count"] += 1
            else:
                out[out_key].append(shape(it))
    return out
