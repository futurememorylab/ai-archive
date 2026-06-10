from backend.app.settings import Settings


def _minimal(**over):
    base = dict(
        catdv_base_url="http://x",
        catdv_catalog_id=1,
        gcp_project_id="p",
        gcs_bucket_name="b",
    )
    base.update(over)
    return Settings(**base)


def test_media_cache_defaults_to_local():
    assert _minimal().media_cache == "local"


def test_media_cache_accepts_ai_store():
    assert _minimal(media_cache="ai_store").media_cache == "ai_store"


def test_playback_source_is_removed():
    assert not hasattr(_minimal(), "playback_source")
