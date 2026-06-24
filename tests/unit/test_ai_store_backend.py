import pytest

from backend.app.services.media_cache import AiStoreBackend
from backend.app.services.media_locator import RemoteUrl


class _Ref:
    handle = "gs://bucket/clips/5.mov"


class _AiStore:
    def __init__(self, status_ref=None):
        self._status_ref = status_ref
        self.uploaded = []

    async def status(self, key):
        return self._status_ref

    async def ensure_uploaded(self, key, path, mime):
        self.uploaded.append((key, path, mime))
        return _Ref()


class _Resolver:
    def __init__(self, path):
        self._path, self.calls = path, []

    async def path_for_clip_id(self, clip_id, progress_cb=None):
        self.calls.append(clip_id)
        return self._path


class _Gcs:
    def signed_url(self, handle, *, expires_s):
        return f"https://signed/{handle}"


class _ProxyCacheRepo:
    def __init__(self):
        self.deleted = []

    async def delete(self, db, clip_id):
        self.deleted.append(clip_id)


async def test_ensure_cached_status_hit_skips_download(tmp_path):
    r = _Resolver(tmp_path / "5.mov")
    b = AiStoreBackend(
        rest_resolver=r, ai_store=_AiStore(status_ref=_Ref()),
        gcs=_Gcs(), proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    await b.ensure_cached(5)
    assert r.calls == []  # dedup fast-path: no tunnel hit


async def test_ensure_cached_uploads_then_deletes_temp(tmp_path):
    p = tmp_path / "5.mov"
    p.write_bytes(b"video")
    store, repo, r = _AiStore(), _ProxyCacheRepo(), _Resolver(p)
    b = AiStoreBackend(
        rest_resolver=r, ai_store=store, gcs=_Gcs(),
        proxy_cache_repo=repo, db_provider=lambda: None,
    )
    await b.ensure_cached(5)
    assert store.uploaded and store.uploaded[0][0] == ("catdv", "5")
    assert not p.exists()           # temp deleted
    assert repo.deleted == [5]      # proxy_cache row removed


async def test_ensure_cached_deletes_temp_on_upload_failure(tmp_path):
    p = tmp_path / "5.mov"
    p.write_bytes(b"video")
    repo = _ProxyCacheRepo()

    class _Boom(_AiStore):
        async def ensure_uploaded(self, key, path, mime):
            raise RuntimeError("gcs down")

    b = AiStoreBackend(
        rest_resolver=_Resolver(p), ai_store=_Boom(), gcs=_Gcs(),
        proxy_cache_repo=repo, db_provider=lambda: None,
    )
    with pytest.raises(RuntimeError):
        await b.ensure_cached(5)
    assert not p.exists()           # temp still cleaned up
    assert repo.deleted == [5]      # proxy_cache row still removed


async def test_locate_returns_signed_url_on_status_hit():
    b = AiStoreBackend(
        rest_resolver=_Resolver(None), ai_store=_AiStore(status_ref=_Ref()),
        gcs=_Gcs(), proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    located = await b.locate(5)
    assert located == RemoteUrl("https://signed/gs://bucket/clips/5.mov")


async def test_locate_returns_none_on_miss():
    b = AiStoreBackend(
        rest_resolver=_Resolver(None), ai_store=_AiStore(status_ref=None),
        gcs=_Gcs(), proxy_cache_repo=_ProxyCacheRepo(), db_provider=lambda: None,
    )
    assert await b.locate(5) is None
