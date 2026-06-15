"""signed_url must parse any gs:// handle (not assume the default
bucket) and request a V4 URL. The IAM-signBlob fallback path needs real
ADC and is covered by manual acceptance flow 5, not unit tests."""

from backend.app.services.gcs import GcsService


def test_signed_url_parses_gs_uri(monkeypatch):
    captured: dict = {}

    class FakeBlob:
        def __init__(self, name):
            captured["blob"] = name

        def generate_signed_url(self, **kwargs):
            captured.update(kwargs)
            return "https://signed.example/x"

    class FakeBucket:
        def __init__(self, name):
            captured["bucket"] = name

        def blob(self, name, **kwargs):
            return FakeBlob(name)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket(name)

    monkeypatch.setattr(
        "backend.app.services.gcs.storage.Client", lambda: FakeClient()
    )
    svc = GcsService("default-bucket", "test-instance")
    url = svc.signed_url("gs://catdv-proxies/clips/42.mov", expires_s=3600)

    assert url == "https://signed.example/x"
    assert captured["bucket"] == "catdv-proxies"
    assert captured["blob"] == "clips/42.mov"
    assert captured["version"] == "v4"
