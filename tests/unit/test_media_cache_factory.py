from backend.app.services.media_cache import (
    AiStoreBackend,
    LocalProxyBackend,
    build_media_cache_backend,
)


class _R:
    async def path_for_clip_id(self, c): ...
class _S:
    async def status(self, k): ...
class _G:
    def signed_url(self, h, *, expires_s): ...
class _Repo:
    async def delete(self, db, c): ...


def _mk(mode):
    return build_media_cache_backend(
        media_cache=mode, resolver=_R(), ai_store=_S(), gcs=_G(),
        proxy_cache_repo=_Repo(), db_provider=lambda: None,
    )


def test_local_mode_builds_local_backend():
    assert isinstance(_mk("local"), LocalProxyBackend)


def test_ai_store_mode_builds_ai_store_backend():
    assert isinstance(_mk("ai_store"), AiStoreBackend)
