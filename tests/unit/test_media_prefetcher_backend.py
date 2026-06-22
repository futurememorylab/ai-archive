from backend.app.services.media_prefetcher import MediaPrefetcher


class _Queue:
    def __init__(self, row):
        self._row, self.done = row, []

    async def claim_next(self, db):
        r, self._row = self._row, None
        return r

    async def mark_done(self, db, rid, bytes_downloaded):
        self.done.append((rid, bytes_downloaded))

    async def mark_error(self, db, rid, msg):
        self.done.append((rid, "error", msg))


class _Backend:
    def __init__(self, raise_exc=None):
        self.cached, self._raise = [], raise_exc

    async def ensure_cached(self, clip_id, progress_cb=None):
        self.cached.append(clip_id)
        if self._raise:
            raise self._raise


async def test_tick_calls_backend_ensure_cached():
    q = _Queue({"id": 1, "provider_clip_id": "42"})
    backend = _Backend()
    pf = MediaPrefetcher(queue_repo=q, backend=backend, db_provider=lambda: None)
    cid = await pf.tick_once()
    assert cid == 42
    assert backend.cached == [42]
    assert q.done == [(1, 0)]


async def test_tick_marks_error_on_backend_failure():
    q = _Queue({"id": 2, "provider_clip_id": "7"})
    pf = MediaPrefetcher(
        queue_repo=q, backend=_Backend(raise_exc=RuntimeError("tunnel down")),
        db_provider=lambda: None,
    )
    cid = await pf.tick_once()
    assert cid == 7
    assert q.done == [(2, "error", "tunnel down")]
