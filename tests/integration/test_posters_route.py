import importlib

from fastapi.testclient import TestClient


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


class _FakeCatdvClient:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def download_poster(self, clip_id: int) -> bytes:
        self.calls.append(clip_id)
        return b"\xff\xd8\xff\xe0JPEG-FROM-FAKE"

    async def __aexit__(self, *exc) -> None:
        # `AppContext.aclose()` calls __aexit__ on whatever it holds in
        # `ctx.catdv`; the fake is not a real async-context-manager.
        return None


def test_poster_route_serves_jpeg_and_caches(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = _FakeCatdvClient()
        client.app.state.ctx.catdv = fake

        r1 = client.get("/api/poster/42")
        assert r1.status_code == 200
        assert r1.headers["content-type"] == "image/jpeg"
        assert "immutable" in r1.headers.get("cache-control", "")
        assert r1.content.startswith(b"\xff\xd8")

        # Second call: must hit disk cache, not call the client again.
        r2 = client.get("/api/poster/42")
        assert r2.status_code == 200
        assert r2.content == r1.content
        assert fake.calls == [42], "second request must not re-fetch upstream"


def test_poster_route_ignores_v_query_string(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = _FakeCatdvClient()
        client.app.state.ctx.catdv = fake

        r1 = client.get("/api/poster/7?v=111")
        r2 = client.get("/api/poster/7?v=222")  # different version, same clip
        assert r1.status_code == r2.status_code == 200
        # `v` is purely client-side cache busting; server still caches by clip_id.
        assert fake.calls == [7]


def test_poster_route_returns_404_when_upstream_404s(monkeypatch, tmp_path):
    class NotFoundClient:
        async def download_poster(self, clip_id: int) -> bytes:
            import httpx
            raise httpx.HTTPStatusError(
                "404", request=httpx.Request("GET", "/"),
                response=httpx.Response(404),
            )

        async def __aexit__(self, *exc) -> None:
            return None

    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.catdv = NotFoundClient()
        r = client.get("/api/poster/999999")
        assert r.status_code == 404
