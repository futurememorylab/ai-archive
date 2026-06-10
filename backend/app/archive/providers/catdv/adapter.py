"""CatDV ArchiveProvider adapter — implements ArchiveProvider on top of
CatdvClient. Uses ClipCache / FieldDefCache / ClipListCache repos when
offline; translates CatDV REST errors into ProviderError variants."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    NotFoundError,
    RetryableError,
)
from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    ConflictDetail,
    FieldDef,
    WriteResult,
)
from backend.app.archive.provider import ProviderCapabilities, ProviderHealth
from backend.app.archive.providers.catdv.mapping import (
    field_def_from_catdv,
    from_catdv_clip,
)
from backend.app.services.catdv_client import (
    CatdvAuthError,
    CatdvBusyError,
    CatdvClient,
    CatdvError,
)


class CatdvArchiveAdapter:
    id = "catdv"
    capabilities = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes", "bigNotes"}),
        supports_field_create=False,
        supports_etag=False,
        media_is_local=False,
        write_atomicity="per-clip",
    )

    def __init__(
        self,
        *,
        client: CatdvClient | None,
        clip_cache_repo: Any = None,
        field_def_cache_repo: Any = None,
        clip_list_cache_repo: Any = None,
        db_provider: Callable[[], Any] | None = None,
        clip_cache_ttl_hours: int = 168,
        clip_list_cache_ttl_minutes: int = 10,
        clock: Callable[[], datetime] | None = None,
        default_catalog_id: str = "",
        is_online_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._client = client
        self._clip_cache = clip_cache_repo
        self._field_def_cache = field_def_cache_repo
        self._clip_list_cache = clip_list_cache_repo
        self._db_provider = db_provider
        self._ttl = timedelta(hours=clip_cache_ttl_hours)
        self._list_ttl = timedelta(minutes=clip_list_cache_ttl_minutes)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._default_catalog_id = default_catalog_id
        self._is_online_provider = is_online_provider

    def _is_online(self) -> bool:
        if self._is_online_provider is None:
            return True
        return bool(self._is_online_provider())

    # --- health -------------------------------------------------------

    async def health(self) -> ProviderHealth:
        from time import perf_counter

        # Health is the recovery path: the ConnectionMonitor calls this
        # to decide whether to flip back online after a probe. Gating
        # on the cached `_is_online()` would make recovery impossible —
        # we'd always answer "offline" and the monitor would stay
        # offline forever. Only the absent-client case short-circuits.
        if self._client is None:
            return ProviderHealth(ok=False, reachable=False, detail="offline")
        t0 = perf_counter()
        try:
            await self._client.health()
        except CatdvAuthError as exc:
            return ProviderHealth(ok=False, reachable=True, detail=f"auth: {exc}")
        except CatdvBusyError as exc:
            return ProviderHealth(ok=False, reachable=True, detail=f"busy: {exc}")
        except CatdvError as exc:
            # health() has a return-type contract (always ProviderHealth);
            # the other five except-CatdvError blocks raise NotFoundError on
            # NOT_FOUND for downstream is_provider_not_found() narrowing.
            # Here we collapse all CatdvError variants — including NOT_FOUND
            # (which on the /api/info endpoint means misconfigured base URL,
            # not a missing clip) — into ok=False so ConnectionMonitor can
            # report offline cleanly.
            return ProviderHealth(ok=False, reachable=False, detail=str(exc))
        latency_ms = (perf_counter() - t0) * 1000.0
        return ProviderHealth(ok=True, latency_ms=latency_ms)

    # --- read API -----------------------------------------------------

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        if not self._is_online():
            return await self._list_clips_from_cache(catalog, query)

        cached = await self._read_list_from_cache(catalog, query)
        if cached is not None:
            return cached

        try:
            data = await self._client.list_clips(
                int(catalog),
                offset=query.offset,
                limit=query.limit,
                q=query.text,
            )
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError:
            return await self._list_clips_from_cache(catalog, query)
        except CatdvError as exc:
            msg = str(exc)
            if msg.startswith("NOT_FOUND") or "not found" in msg.lower():
                raise NotFoundError(msg) from exc
            raise FatalProviderError(msg) from exc

        now = self._clock()
        raw_items = data.get("items") if isinstance(data, dict) else []
        items = tuple(from_catdv_clip(raw, fetched_at=now) for raw in (raw_items or []))
        total = int((data or {}).get("totalItems", len(items)))
        page = ClipPage(
            items=items,
            total=total,
            offset=query.offset,
            limit=query.limit,
        )
        await self._write_list_through(catalog, query, page, fetched_at=now)
        return page

    async def _list_clips_from_cache(self, catalog: str, query: ClipQuery) -> ClipPage:
        if not self._cache_enabled():
            return ClipPage(items=(), total=0, offset=query.offset, limit=query.limit)
        items, total = await self._clip_cache.list_by_catalog(
            self._db_provider(),
            provider_id=self.id,
            catalog_id=catalog,
            offset=query.offset,
            limit=query.limit,
            q=query.text,
            canonical=True,
        )
        return ClipPage(items=items, total=total, offset=query.offset, limit=query.limit)

    async def get_clip(self, clip: str) -> CanonicalClip:
        cached = await self._read_clip_from_cache(clip)
        if cached is not None:
            return cached

        if not self._is_online():
            stale = await self._read_clip_from_cache_stale(clip)
            if stale is not None:
                return stale
            raise FatalProviderError(f"clip {clip} not available offline")

        try:
            raw = await self._client.get_clip(int(clip))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            stale = await self._read_clip_from_cache_stale(clip)
            if stale is not None:
                return stale
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            msg = str(exc)
            if msg.startswith("NOT_FOUND") or "not found" in msg.lower():
                raise NotFoundError(msg) from exc
            raise FatalProviderError(msg) from exc

        canonical = from_catdv_clip(raw, fetched_at=self._clock())
        await self._write_clip_through(canonical, raw)
        return canonical

    async def list_field_definitions(self) -> list[FieldDef]:
        cached = await self._read_field_defs_from_cache()
        if cached is not None:
            return cached

        if not self._is_online():
            stale = await self._read_field_defs_from_cache_stale()
            return stale or []

        try:
            rows = await self._client.list_fields()
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            stale = await self._read_field_defs_from_cache_stale()
            if stale is not None:
                return stale
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            msg = str(exc)
            if msg.startswith("NOT_FOUND") or "not found" in msg.lower():
                raise NotFoundError(msg) from exc
            raise FatalProviderError(msg) from exc

        defs = [field_def_from_catdv(r) for r in rows]
        await self._write_field_defs_through(defs)
        return defs

    # --- write API ----------------------------------------------------

    async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
        provider_id, clip_id_str = change_set.clip_key
        if provider_id != self.id:
            raise FatalProviderError(
                f"ChangeSet for provider {provider_id!r} sent to catdv adapter"
            )
        if not self._is_online():
            raise RetryableError("offline — change queued")
        from backend.app.archive.providers.catdv.payload import build_put_payload

        try:
            current = await self._client.get_clip(int(clip_id_str))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            msg = str(exc)
            if msg.startswith("NOT_FOUND") or "not found" in msg.lower():
                raise NotFoundError(msg) from exc
            raise FatalProviderError(msg) from exc

        live_etag = self._etag_from_raw(current)
        if (
            change_set.expected_etag is not None
            and live_etag is not None
            and live_etag != change_set.expected_etag
        ):
            return WriteResult(
                status="conflict",
                upstream_response={},
                new_etag=live_etag,
                conflict_detail=ConflictDetail(
                    kind="modified",
                    expected_etag=change_set.expected_etag,
                    actual_etag=live_etag,
                ),
            )

        payload = build_put_payload(current=current, ops=list(change_set.ops))
        if not payload:
            return WriteResult(status="ok", upstream_response={}, new_etag=live_etag)

        try:
            response = await self._client.put_clip(int(clip_id_str), payload)
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            msg = str(exc)
            if msg.startswith("NOT_FOUND") or "not found" in msg.lower():
                raise NotFoundError(msg) from exc
            raise FatalProviderError(msg) from exc

        new_etag = self._etag_from_raw(response) or live_etag
        # Invalidate the cached clip: the PUT response is not a full clip
        # (just ID + modifyDate), so writing it through would cache a husk.
        # Deleting the row makes the next get_clip refetch live — otherwise
        # the Published view serves the pre-apply clip for up to
        # clip_cache_ttl_hours after a successful apply.
        await self._invalidate_clip_cache(clip_id_str)
        return WriteResult(status="ok", upstream_response=response, new_etag=new_etag)

    @staticmethod
    def _etag_from_raw(raw: dict[str, Any] | None) -> str | None:
        if not isinstance(raw, dict):
            return None
        v = raw.get("modifyDate")
        return str(v) if v is not None else None

    # --- cache helpers -----------------------------------------------

    def _cache_enabled(self) -> bool:
        return self._clip_cache is not None and self._db_provider is not None

    def _field_def_cache_enabled(self) -> bool:
        return self._field_def_cache is not None and self._db_provider is not None

    async def _read_clip_from_cache(self, clip_id: str) -> CanonicalClip | None:
        if not self._cache_enabled():
            return None
        db = self._db_provider()
        row = await self._clip_cache.get_row(db, provider_id=self.id, provider_clip_id=clip_id)
        if row is None:
            return None
        if self._is_expired(row.get("fetched_at")):
            return None
        return await self._clip_cache.get_by_key(db, provider_id=self.id, provider_clip_id=clip_id)

    async def _read_clip_from_cache_stale(self, clip_id: str) -> CanonicalClip | None:
        if not self._cache_enabled():
            return None
        return await self._clip_cache.get_by_key(
            self._db_provider(), provider_id=self.id, provider_clip_id=clip_id
        )

    async def _invalidate_clip_cache(self, clip_id: str) -> None:
        if not self._cache_enabled():
            return
        await self._clip_cache.delete_by_key(
            self._db_provider(), provider_id=self.id, provider_clip_id=clip_id
        )

    async def _read_field_defs_from_cache_stale(self) -> list[FieldDef] | None:
        if not self._field_def_cache_enabled():
            return None
        defs = await self._field_def_cache.list_for_provider(
            self._db_provider(), provider_id=self.id
        )
        return defs if defs else None

    async def _write_clip_through(self, canonical: CanonicalClip, raw: dict[str, Any]) -> None:
        if not self._cache_enabled():
            return
        catalog_id = self._catalog_id_for_clip(raw)
        await self._clip_cache.upsert(
            self._db_provider(),
            clip=canonical,
            catalog_id=catalog_id,
        )

    def _catalog_id_for_clip(self, raw: dict[str, Any]) -> str:
        # CatDV clip payloads embed catalogue ID under varying keys depending
        # on server version; fall back to the configured default.
        for key in ("catalogId", "catalogID", "catalog_id"):
            v = raw.get(key)
            if v is not None:
                return str(v)
        cat = raw.get("catalog")
        if isinstance(cat, dict):
            for key in ("ID", "id"):
                if key in cat:
                    return str(cat[key])
        return self._default_catalog_id

    async def _read_field_defs_from_cache(self) -> list[FieldDef] | None:
        if not self._field_def_cache_enabled():
            return None
        db = self._db_provider()
        latest = await self._field_def_cache.latest_fetched_at(db, provider_id=self.id)
        if latest is None or self._is_expired(latest):
            return None
        return await self._field_def_cache.list_for_provider(db, provider_id=self.id)

    async def _write_field_defs_through(self, defs: list[FieldDef]) -> None:
        if not self._field_def_cache_enabled():
            return
        await self._field_def_cache.replace_all_for_provider(
            self._db_provider(),
            provider_id=self.id,
            field_defs=defs,
        )

    def _list_cache_enabled(self) -> bool:
        return self._clip_list_cache is not None and self._db_provider is not None

    async def _read_list_from_cache(self, catalog: str, query: ClipQuery) -> ClipPage | None:
        if not self._list_cache_enabled():
            return None
        db = self._db_provider()
        entry = await self._clip_list_cache.get(
            db,
            provider_id=self.id,
            catalog_id=str(catalog),
            query_text=query.text,
            offset=query.offset,
            limit=query.limit,
        )
        if entry is None:
            return None
        if self._is_expired(entry.get("fetched_at"), ttl=self._list_ttl):
            return None
        return ClipPage(
            items=entry["items"],
            total=int(entry["total"]),
            offset=query.offset,
            limit=query.limit,
        )

    async def _write_list_through(
        self,
        catalog: str,
        query: ClipQuery,
        page: ClipPage,
        *,
        fetched_at: datetime,
    ) -> None:
        if not self._list_cache_enabled():
            return
        await self._clip_list_cache.upsert(
            self._db_provider(),
            provider_id=self.id,
            catalog_id=str(catalog),
            query_text=query.text,
            offset=query.offset,
            limit=query.limit,
            total=page.total,
            items=page.items,
            fetched_at_iso=fetched_at.isoformat(),
        )

    def _is_expired(self, fetched_at_iso: str | None, *, ttl: timedelta | None = None) -> bool:
        if fetched_at_iso is None:
            return True
        try:
            ts = datetime.fromisoformat(fetched_at_iso)
        except (TypeError, ValueError):
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (self._clock() - ts) > (ttl or self._ttl)
