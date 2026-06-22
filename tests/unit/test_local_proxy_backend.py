from backend.app.services.media_cache import LocalProxyBackend
from backend.app.services.media_locator import LocalFile, RemoteUrl


class _Resolver:
    def __init__(self, path=None, raise_exc=None):
        self._path, self._raise, self.calls = path, raise_exc, []

    async def path_for_clip_id(self, clip_id, progress_cb=None):
        self.calls.append(clip_id)
        if self._raise:
            raise self._raise
        return self._path


class _AiStore:
    def __init__(self, ref=None):
        self._ref = ref

    async def status(self, key):
        return self._ref


class _Gcs:
    def signed_url(self, handle, *, expires_s):
        return f"https://signed/{handle}"


async def test_ensure_cached_downloads_via_resolver(tmp_path):
    r = _Resolver(path=tmp_path / "1.mov")
    b = LocalProxyBackend(resolver=r, ai_store=_AiStore(), gcs=_Gcs())
    await b.ensure_cached(1)
    assert r.calls == [1]


async def test_locate_prefers_local_file(tmp_path):
    p = tmp_path / "1.mov"
    p.write_bytes(b"x")
    b = LocalProxyBackend(resolver=_Resolver(path=p), ai_store=_AiStore(), gcs=_Gcs())
    located = await b.locate(1)
    assert located == LocalFile(p)


async def test_locate_falls_back_to_gcs(tmp_path):
    class _Ref:
        handle = "gs://bucket/clips/1.mov"
    b = LocalProxyBackend(
        resolver=_Resolver(raise_exc=RuntimeError("not on disk")),
        ai_store=_AiStore(ref=_Ref()),
        gcs=_Gcs(),
    )
    located = await b.locate(1)
    assert isinstance(located, RemoteUrl)
    assert located.url == "https://signed/gs://bucket/clips/1.mov"
