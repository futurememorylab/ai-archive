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

    monkeypatch.setattr("backend.app.services.gcs.storage.Client", lambda: FakeClient())
    svc = GcsService("default-bucket", "test-instance")
    url = svc.signed_url("gs://catdv-proxies/clips/42.mov", expires_s=3600)

    assert url == "https://signed.example/x"
    assert captured["bucket"] == "catdv-proxies"
    assert captured["blob"] == "clips/42.mov"
    assert captured["version"] == "v4"


def _fake_storage(monkeypatch, captured, blob_meta=None):
    class FakeBlob:
        def __init__(self, name):
            captured["blob"] = name
            self.size, self.content_type, self.md5_hash = blob_meta or (0, None, None)

        def generate_signed_url(self, **kwargs):
            captured.update(kwargs)
            return "https://signed.example/put"

    class FakeBucket:
        name = "catdv-proxies"

        def blob(self, name, **kwargs):
            return FakeBlob(name)

        def get_blob(self, name):
            return FakeBlob(name) if blob_meta is not None else None

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr("backend.app.services.gcs.storage.Client", lambda: FakeClient())
    return GcsService("catdv-proxies", "prod")


def test_signed_upload_url_is_a_put_bound_to_the_mime(monkeypatch):
    captured: dict = {}
    svc = _fake_storage(monkeypatch, captured)
    url = svc.signed_upload_url(1_000_000_007, "video/mp4", expires_s=900)

    assert url == "https://signed.example/put"
    # Uploaded clips land under the instance namespace, like server uploads.
    assert captured["blob"] == "instances/prod/uploads/1000000007.mov"
    assert captured["method"] == "PUT"
    assert captured["content_type"] == "video/mp4"
    assert captured["version"] == "v4"


def test_blob_meta_reads_size_type_and_md5(monkeypatch):
    svc = _fake_storage(monkeypatch, {}, blob_meta=(1234, "video/mp4", "bWQ1"))
    meta = svc.blob_meta(1_000_000_007)
    assert meta is not None
    assert (meta.size, meta.content_type, meta.md5) == (1234, "video/mp4", "bWQ1")


def test_blob_meta_is_none_when_absent(monkeypatch):
    svc = _fake_storage(monkeypatch, {})
    assert svc.blob_meta(1_000_000_007) is None
