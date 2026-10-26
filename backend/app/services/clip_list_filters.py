"""Resolve clip-list filters (Cache, Annotations) to a candidate clip_id set.

The clip list page accepts two filters:

  * ``cache``  one of ``any|none|local|ai``
  * ``anno``   one of ``any|for_review|applied|none|has_any``

CatDV doesn't know about either dimension — both live in our SQLite. When
any filter is active we resolve a candidate ``set[int]`` of CatDV clip IDs
locally, intersect them, then paginate.

Results never include clips known to belong to another catalog (from the
cached list pages and the catalog tag on clip_cache rows), so a filter
always stays inside the catalog picked in the topbar.

The two "absence" filters (``cache=none``, ``anno=none``) are bounded to
the clips known to be in the active catalog. A clip that exists upstream
but has never been listed or opened will not appear under those filters;
that's the documented price of the local-first strategy.
"""

from __future__ import annotations

from typing import Literal

import aiosqlite

from backend.app.repositories.clip_versions import ClipVersionsRepo
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


def is_active(cache: CacheFilter, anno: AnnoFilter, batch: int | list[int] | None = None) -> bool:
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


async def _anno_set(
    db: aiosqlite.Connection, anno: AnnoFilter, *, provider_id: str, universe: set[int]
) -> set[int]:
    """Clip IDs matching one Annotation-status filter value (not "any").

    A clip whose AI annotations were removed from the archive counts as not
    annotated again (and not in the archive) until it is annotated anew.
    """
    if anno == "for_review":
        return await _ids_with_annotation_review_state(db, applied=False)
    versions = ClipVersionsRepo()
    if anno == "applied":
        applied = await _ids_with_annotation_review_state(db, applied=True)
        return applied - await versions.reset_clip_ids(
            db, provider_id=provider_id, ignore_newer_annotations=True
        )
    annotated = await _ids_with_any_annotation(db) - await versions.reset_clip_ids(
        db, provider_id=provider_id
    )
    return annotated if anno == "has_any" else universe - annotated


async def _catalog_membership(
    db: aiosqlite.Connection, *, provider_id: str, catalog_id: str
) -> tuple[set[int], set[int]]:
    """Split every clip ID we have a catalog for into (this catalog, others).

    The cache / annotation / batch tables carry no catalog column, so filter
    results are checked against this; without it a filter showed clips from
    whichever catalog they were cached or annotated in. "This catalog" is
    also the universe for the "absence" filters (no-cache, no-anno). Sources:

      * clip_list_cache pages: every item in a page's JSON payload belongs
        to that page's catalog.
      * clip_cache rows, tagged with the catalog CatDV reported when the
        clip was fetched (clips opened from a batch without being listed).
    """
    # clip_list_cache: each items_json is a JSON array of clip dicts where
    # `key` is [provider_id, provider_clip_id]. json_each() over the
    # outer array gives one row per clip; we project the second element.
    cur = await db.execute(
        """
        SELECT DISTINCT clip_list_cache.catalog_id,
               CAST(je.value ->> '$.key[1]' AS INTEGER)
          FROM clip_list_cache, json_each(items_json) je
         WHERE clip_list_cache.provider_id = ?
        UNION
        SELECT DISTINCT catalog_id, CAST(provider_clip_id AS INTEGER)
          FROM clip_cache
         WHERE provider_id = ? AND provider_clip_id GLOB '[0-9]*'
        """,
        (provider_id, provider_id),
    )
    here: set[int] = set()
    known: set[int] = set()
    for cat, cid in await cur.fetchall():
        if cid is None:
            continue
        known.add(int(cid))
        if str(cat) == catalog_id:
            here.add(int(cid))
    return here, known - here


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

    universe, other_catalogs = await _catalog_membership(
        db, provider_id=provider_id, catalog_id=catalog_id
    )

    candidate: set[int] | None = None

    if cache != "any":
        if cache == "local":
            cache_set = await _ids_with_media_local(db, provider_id)
        elif cache == "ai":
            cache_set = await _ids_with_media_ai(db, provider_id)
        else:  # "none"
            ml = await _ids_with_media_local(db, provider_id)
            ai = await _ids_with_media_ai(db, provider_id)
            cache_set = universe - ml - ai
        candidate = cache_set

    if anno != "any":
        anno_set = await _anno_set(db, anno, provider_id=provider_id, universe=universe)
        candidate = anno_set if candidate is None else candidate & anno_set

    if batch:
        job_ids = [batch] if isinstance(batch, int) else list(batch)
        batch_set = await _ids_in_jobs(db, job_ids)
        candidate = batch_set if candidate is None else candidate & batch_set

    assert candidate is not None  # at least one filter active by definition
    # Filters narrow the active catalog; they never reach into another one.
    # A clip with no known catalog yet is kept: hydrating it tags its catalog
    # in clip_cache, so it is scoped from the next render on.
    return candidate - other_catalogs
