"""One-time, idempotent backfill: synthesize a 'live' v1 for every clip that
already has synced review_items but no clip_versions row, so History isn't
empty for clips published before this feature shipped. Best-effort: snapshot
from the last synced annotation's items; author='—', no etag. Runs at boot."""

from __future__ import annotations

import json
from itertools import groupby

import aiosqlite

from backend.app.models.annotation import ClipVersion


async def backfill_clip_versions(conn: aiosqlite.Connection, versions_repo) -> int:
    # One grouped read for ALL un-backfilled clips (ordered so we can group in
    # Python), then the inserts in a single transaction — not a SELECT + commit
    # per clip. This runs synchronously at boot, so an N+1 stalls startup
    # linearly with publish history. See ADR 0046.
    cur = await conn.execute(
        """
        SELECT ri.catdv_clip_id, ri.kind, ri.target_identifier, ri.proposed_value,
               ri.edited_value, ri.annotation_id, a.model AS model
          FROM review_items ri JOIN annotations a ON a.id = ri.annotation_id
         WHERE ri.synced_at IS NOT NULL
           AND ri.catdv_clip_id NOT IN (SELECT catdv_clip_id FROM clip_versions)
         ORDER BY ri.catdv_clip_id, ri.id
        """
    )
    all_rows = await cur.fetchall()
    created = 0
    for clip_id, group in groupby(all_rows, key=lambda r: int(r[0])):
        snapshot, model, annotation_id = _snapshot_from_rows([r[1:] for r in group])
        await versions_repo.insert(
            conn,
            ClipVersion(
                catdv_clip_id=clip_id,
                version_num=1,
                snapshot=snapshot,
                diff=None,
                origin="publish",
                model=model,
                annotation_id=annotation_id,
                author="—",
                publish_state="live",
            ),
            commit=False,
        )
        created += 1
    if created:
        await conn.commit()
    return created


def _snapshot_from_rows(rows):
    markers, fields, notes, big = [], {}, None, None
    model, annotation_id = None, None
    for kind, ident, proposed, edited, ann_id, m in rows:
        model, annotation_id = m, ann_id
        value = json.loads(edited) if edited is not None else json.loads(proposed)
        if kind == "marker" and isinstance(value, dict):
            markers.append(value)
        elif kind == "field" and ident:
            fields[ident] = value
        elif kind == "note":
            text = str(value)
            if ident == "bigNotes":
                big = text
            else:
                notes = text
    return (
        {"markers": markers, "fields": fields, "notes": notes, "bigNotes": big},
        model,
        annotation_id,
    )
