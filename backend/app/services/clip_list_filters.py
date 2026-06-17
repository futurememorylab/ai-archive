"""Resolve clip-list filters (Cache, Annotations) to a candidate clip_id set.

The clip list page accepts two filters:

  * ``cache``  one of ``any|none|local|ai``
  * ``anno``   one of ``any|for_review|applied|none|has_any``

CatDV doesn't know about either dimension — both live in our SQLite. When
any filter is active we resolve a candidate ``set[int]`` of CatDV clip IDs
locally, intersect them, then paginate.

The two "absence" filters (``cache=none``, ``anno=none``) are bounded to
the **universe of clips we've already observed** in any local table —
list pages, metadata cache, proxy cache, ai_store_files, annotations,
review items. A clip that exists upstream but has never been listed will
not appear under those filters; that's the documented price of the
local-first strategy.
"""

from __future__ import annotations

from typing import Literal

import aiosqlite

from backend.app.repositories.review_items import FOR_REVIEW_WHERE

CacheFilter = Literal["any", "none", "local", "ai"]
AnnoFilter = Literal["any", "for_review", "applied", "none", "has_any"]

CACHE_VALUES: tuple[CacheFilter, ...] = ("any", "none", "local", "ai")
ANNO_VALUES: tuple[AnnoFilter, ...] = (
    "any",
    "for_review",
    "applied",
    "none",
    "has_any",
)


def normalize_cache(value: str | None) -> CacheFilter:
    return value if value in CACHE_VALUES else "any"  # type: ignore[return-value]


def normalize_anno(value: str | None) -> AnnoFilter:
    return value if value in ANNO_VALUES else "any"  # type: ignore[return-value]


def is_active(
    cache: CacheFilter, anno: AnnoFilter, batch: int | list[int] | None = None
) -> bool:
    # `batch` may be a single job id, a list of job ids, or None — a non-empty
    # value of either form means the batch filter is active.
    return cache != "any" or anno != "any" or bool(batch)


async def _ids_with_media_local(db: aiosqlite.Connection, provider_id: str) -> set[int]:
    cur = await db.execute(
        "SELECT DISTINCT catdv_clip_id FROM proxy_cache "
        "WHERE provider_id = ? AND catdv_clip_id IS NOT NULL",
        (provider_id,),
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def _ids_with_media_ai(db: aiosqlite.Connection, provider_id: str) -> set[int]:
    cur = await db.execute(
        "SELECT DISTINCT catdv_clip_id FROM ai_store_files "
        "WHERE provider_id = ? AND catdv_clip_id IS NOT NULL",
        (provider_id,),
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def _ids_with_annotation_review_state(db: aiosqlite.Connection, *, applied: bool) -> set[int]:
    """Clip IDs with at least one review_item matching the predicate.

    applied=True  → has a write-back enqueued (applied_at set): the "Applied" filter.
    applied=False → the clip's LATEST annotation still has an UNDECIDED proposal
        (applied_at NULL, not rejected): the "Awaiting review" filter. Rejected
        items are decided (though applied_at stays NULL), and items from a
        superseded older annotation aren't the current draft — both are excluded,
        matching the draft panel (which shows only the latest annotation). Without
        that, fully-decided / re-annotated clips showed "Awaiting review" with 0
        proposals to act on.
    """
    where = "applied_at IS NOT NULL" if applied else FOR_REVIEW_WHERE
    cur = await db.execute(f"SELECT DISTINCT catdv_clip_id FROM review_items WHERE {where}")
    return {int(r[0]) for r in await cur.fetchall()}


async def _ids_in_jobs(db: aiosqlite.Connection, job_ids: list[int]) -> set[int]:
    """All clip IDs belonging to the given jobs (every job_item), regardless
    of per-item status — so the Batch view shows the whole run and each
    clip's progress (queued / processing / done / failed) is surfaced
    separately. Accepts several job ids because one bulk action creates one
    job per media kind; the indicator links to all of them at once."""
    if not job_ids:
        return set()
    placeholders = ",".join("?" * len(job_ids))
    cur = await db.execute(
        f"SELECT DISTINCT catdv_clip_id FROM job_items WHERE job_id IN ({placeholders})",
        job_ids,
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def _ids_with_any_annotation(db: aiosqlite.Connection) -> set[int]:
    cur = await db.execute("SELECT DISTINCT catdv_clip_id FROM annotations")
    return {int(r[0]) for r in await cur.fetchall()}


async def _known_clip_id_universe(
    db: aiosqlite.Connection, *, provider_id: str, catalog_id: str
) -> set[int]:
    """Best-effort union of every clip ID we've ever stored locally.

    Used as the universe for the "absence" filters (no-cache, no-anno) so
    that "none" can't return the empty set when we obviously know about
    clips. Includes:

      * clip_list_cache pages for (provider_id, catalog_id) — every item
        in the JSON payload contributes its provider_clip_id.
      * clip_cache, proxy_cache, ai_store_files, annotations, review_items —
        any clip we've ever touched.
    """
    ids: set[int] = set()

    # clip_list_cache: each items_json is a JSON array of clip dicts where
    # `key` is [provider_id, provider_clip_id]. json_each() over the
    # outer array gives one row per clip; we project the second element.
    cur = await db.execute(
        """
        SELECT DISTINCT CAST(je.value ->> '$.key[1]' AS INTEGER)
          FROM clip_list_cache, json_each(items_json) je
         WHERE clip_list_cache.provider_id = ?
           AND clip_list_cache.catalog_id  = ?
        """,
        (provider_id, catalog_id),
    )
    for r in await cur.fetchall():
        if r[0] is not None:
            ids.add(int(r[0]))

    # clip_cache was added in PR 3 with only (provider_id, provider_clip_id).
    cur = await db.execute(
        "SELECT DISTINCT provider_clip_id FROM clip_cache WHERE provider_id = ?",
        (provider_id,),
    )
    for r in await cur.fetchall():
        if r[0] is not None:
            ids.add(int(r[0]))

    for table in ("proxy_cache", "ai_store_files", "annotations", "review_items"):
        cur = await db.execute(
            f"SELECT DISTINCT catdv_clip_id FROM {table} WHERE catdv_clip_id IS NOT NULL"
        )
        for r in await cur.fetchall():
            ids.add(int(r[0]))

    return ids


async def resolve(
    db: aiosqlite.Connection,
    *,
    provider_id: str,
    catalog_id: str,
    cache: CacheFilter,
    anno: AnnoFilter,
    host_local_proxies: bool = False,
    batch: int | list[int] | None = None,
) -> set[int] | None:
    """Resolve filters to a candidate clip_id set.

    Returns ``None`` when no filter is active (caller should use the
    normal CatDV-paginated path). Returns a possibly-empty set when at
    least one filter is set.
    """
    if host_local_proxies:
        # `local` matches every clip (so the cache predicate contributes
        # nothing) and `none` matches nothing (early-return empty set).
        if cache == "none":
            return set()
        cache = "any"
    if not is_active(cache, anno, batch):
        return None

    universe: set[int] | None = None

    async def get_universe() -> set[int]:
        nonlocal universe
        if universe is None:
            universe = await _known_clip_id_universe(
                db, provider_id=provider_id, catalog_id=catalog_id
            )
        return universe

    candidate: set[int] | None = None

    if cache != "any":
        if cache == "local":
            cache_set = await _ids_with_media_local(db, provider_id)
        elif cache == "ai":
            cache_set = await _ids_with_media_ai(db, provider_id)
        else:  # "none"
            ml = await _ids_with_media_local(db, provider_id)
            ai = await _ids_with_media_ai(db, provider_id)
            cache_set = (await get_universe()) - ml - ai
        candidate = cache_set

    if anno != "any":
        if anno == "for_review":
            anno_set = await _ids_with_annotation_review_state(db, applied=False)
        elif anno == "applied":
            anno_set = await _ids_with_annotation_review_state(db, applied=True)
        elif anno == "has_any":
            anno_set = await _ids_with_any_annotation(db)
        else:  # "none"
            ann = await _ids_with_any_annotation(db)
            anno_set = (await get_universe()) - ann
        candidate = anno_set if candidate is None else candidate & anno_set

    if batch:
        job_ids = [batch] if isinstance(batch, int) else list(batch)
        batch_set = await _ids_in_jobs(db, job_ids)
        candidate = batch_set if candidate is None else candidate & batch_set

    assert candidate is not None  # at least one filter active by definition
    return candidate
