from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    RetryableError,
)
from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    FieldDef,
    WriteResult,
)
from backend.app.archive.provider import ProviderCapabilities
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
        client: CatdvClient,
        clip_cache_repo: Any = None,
        field_def_cache_repo: Any = None,
        db_provider: Callable[[], Any] | None = None,
        clip_cache_ttl_hours: int = 168,
        clock: Callable[[], datetime] | None = None,
        default_catalog_id: str = "",
    ) -> None:
        self._client = client
        self._clip_cache = clip_cache_repo
        self._field_def_cache = field_def_cache_repo
        self._db_provider = db_provider
        self._ttl = timedelta(hours=clip_cache_ttl_hours)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._default_catalog_id = default_catalog_id

    # --- read API -----------------------------------------------------

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        try:
            data = await self._client.list_clips(
                int(catalog),
                offset=query.offset,
                limit=query.limit,
                q=query.text,
            )
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        now = self._clock()
        raw_items = data.get("clips") if isinstance(data, dict) else []
        items = tuple(from_catdv_clip(raw, fetched_at=now) for raw in (raw_items or []))
        return ClipPage(
            items=items,
            total=int((data or {}).get("total", len(items))),
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip: str) -> CanonicalClip:
        cached = await self._read_clip_from_cache(clip)
        if cached is not None:
            return cached

        try:
            raw = await self._client.get_clip(int(clip))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        canonical = from_catdv_clip(raw, fetched_at=self._clock())
        await self._write_clip_through(canonical, raw)
        return canonical

    async def list_field_definitions(self) -> list[FieldDef]:
        cached = await self._read_field_defs_from_cache()
        if cached is not None:
            return cached

        try:
            rows = await self._client.list_fields()
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

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
        from backend.app.archive.providers.catdv.payload import build_put_payload

        try:
            current = await self._client.get_clip(int(clip_id_str))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        payload = build_put_payload(current=current, ops=list(change_set.ops))
        if not payload:
            return WriteResult(status="ok", upstream_response={}, detail="no-op")

        try:
            response = await self._client.put_clip(int(clip_id_str), payload)
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        return WriteResult(status="ok", upstream_response=response)

    # --- cache helpers -----------------------------------------------

    def _cache_enabled(self) -> bool:
        return self._clip_cache is not None and self._db_provider is not None

    def _field_def_cache_enabled(self) -> bool:
        return self._field_def_cache is not None and self._db_provider is not None

    async def _read_clip_from_cache(self, clip_id: str) -> CanonicalClip | None:
        if not self._cache_enabled():
            return None
        db = self._db_provider()
        row = await self._clip_cache.get_row(
            db, provider_id=self.id, provider_clip_id=clip_id
        )
        if row is None:
            return None
        if self._is_expired(row.get("fetched_at")):
            return None
        return await self._clip_cache.get_by_key(
            db, provider_id=self.id, provider_clip_id=clip_id
        )

    async def _write_clip_through(
        self, canonical: CanonicalClip, raw: dict[str, Any]
    ) -> None:
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
        latest = await self._field_def_cache.latest_fetched_at(
            db, provider_id=self.id
        )
        if latest is None or self._is_expired(latest):
            return None
        return await self._field_def_cache.list_for_provider(
            db, provider_id=self.id
        )

    async def _write_field_defs_through(self, defs: list[FieldDef]) -> None:
        if not self._field_def_cache_enabled():
            return
        await self._field_def_cache.replace_all_for_provider(
            self._db_provider(),
            provider_id=self.id,
            field_defs=defs,
        )

    def _is_expired(self, fetched_at_iso: str | None) -> bool:
        if fetched_at_iso is None:
            return True
        try:
            ts = datetime.fromisoformat(fetched_at_iso)
        except (TypeError, ValueError):
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (self._clock() - ts) > self._ttl
