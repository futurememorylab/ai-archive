from backend.app.ui.view_models import cache_status_view


class _Layer:
    def __init__(self, present, evictable, size_bytes=0, pinned=()):
        self.present = present
        self.evictable = evictable
        self.size_bytes = size_bytes
        self.pinned_by_workspaces = pinned

    def to_dict(self):
        return {
            "present": self.present,
            "evictable": self.evictable,
            "size_bytes": self.size_bytes,
            "pinned_by_workspaces": list(self.pinned_by_workspaces),
        }


class _Status:
    def __init__(self, ml_present, ml_pinned=()):
        self.clip_key = ("catdv", "1")
        self.layers = (
            _Layer(True, True),                              # metadata
            _Layer(ml_present, not ml_pinned, 1024 * 1024, ml_pinned),
            _Layer(False, False),                            # media-ai
        )


def test_cache_status_view_present_unpinned():
    view = cache_status_view(_Status(ml_present=True))
    assert view["media_local"]["present"] is True
    assert view["media_local"]["pinned"] is False
    assert view["media_local"]["size_mb"] == 1


def test_cache_status_view_present_pinned():
    view = cache_status_view(_Status(ml_present=True, ml_pinned=(3,)))
    assert view["media_local"]["present"] is True
    assert view["media_local"]["pinned"] is True


def test_cache_status_view_absent():
    view = cache_status_view(_Status(ml_present=False))
    assert view["media_local"]["present"] is False
