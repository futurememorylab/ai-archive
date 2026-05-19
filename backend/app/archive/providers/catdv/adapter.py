from __future__ import annotations

from datetime import datetime, timezone

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
    WriteResult,
)
from backend.app.archive.provider import ProviderCapabilities
from backend.app.archive.providers.catdv.mapping import from_catdv_clip
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

    def __init__(self, *, client: CatdvClient) -> None:
        self._client = client

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

        now = datetime.now(timezone.utc)
        raw_items = data.get("clips") if isinstance(data, dict) else []
        items = tuple(from_catdv_clip(raw, fetched_at=now) for raw in (raw_items or []))
        return ClipPage(
            items=items,
            total=int((data or {}).get("total", len(items))),
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip: str) -> CanonicalClip:
        try:
            raw = await self._client.get_clip(int(clip))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc
        return from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))

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
