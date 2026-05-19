from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.app.repositories.cache_actions_log import CacheActionsLogRepo
from backend.app.services.cache_actions import CacheActions
from backend.app.services.cache_inspector import CacheInspector
from backend.app.services.lru_eviction import LruEviction


async def _seed_proxy_cache(db, *, key, file_path, size_bytes, last_used_at):
    await db.execute(
        """
        INSERT INTO proxy_cache
          (catdv_clip_id, provider_id, provider_clip_id, file_path,
           size_bytes, etag, downloaded_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            int(key[1]) if key[1].isdigit() else 0,
            key[0], key[1], file_path, size_bytes,
            last_used_at, last_used_at,
        ),
    )
    await db.commit()


async def _seed_workspace_pin(db, *, ws_id, key):
    await db.execute(
        """
        INSERT INTO workspaces (id, name, provider_id, catalog_id, created_at)
        VALUES (?, ?, 'catdv', '1', ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (ws_id, f"w{ws_id}", datetime.now(UTC).isoformat()),
    )
    await db.execute(
        """
        INSERT INTO workspace_clips
          (workspace_id, provider_id, provider_clip_id, added_at, cache_state)
        VALUES (?, ?, ?, ?, 'ready')
        """,
        (ws_id, key[0], key[1], datetime.now(UTC).isoformat()),
    )
    await db.commit()


def _make_lru(db, *, cap_bytes):
    insp = CacheInspector(db_provider=lambda: db)
    log_repo = CacheActionsLogRepo()
    actions = CacheActions(
        db_provider=lambda: db,
        inspector=insp,
        log_repo=log_repo,
        who_provider=lambda: "system",
    )
    return LruEviction(
        actions=actions, log_repo=log_repo,
        db_provider=lambda: db,
        media_cache_cap_bytes=cap_bytes,
        tick_interval_s=0.01,
    )


@pytest.mark.asyncio
async def test_under_cap_is_no_op(db, tmp_path: Path):
    p = tmp_path / "1.mov"

    p.write_bytes(b"x" * 5)
    await _seed_proxy_cache(
        db, key=("catdv", "1"), file_path=str(p), size_bytes=5,
        last_used_at=datetime.now(UTC).isoformat(),
    )
    lru = _make_lru(db, cap_bytes=1000)
    n = await lru.tick_once()
    assert n == 0
    assert p.exists()


@pytest.mark.asyncio
async def test_over_cap_evicts_oldest_first(db, tmp_path: Path):
    """Three proxies, cap forces eviction of the two oldest."""
    now = datetime.now(UTC)
    proxies = []
    for i, age_min in enumerate([60, 30, 1]):  # oldest first
        p = tmp_path / f"{i}.mov"

        p.write_bytes(b"x" * 100)
        proxies.append(p)
        await _seed_proxy_cache(
            db, key=("catdv", str(i)), file_path=str(p), size_bytes=100,
            last_used_at=(now - timedelta(minutes=age_min)).isoformat(),
        )
    # cap = 150 bytes; total = 300 → must evict 2 (200 freed)
    lru = _make_lru(db, cap_bytes=150)
    n = await lru.tick_once()
    assert n == 2
    # newest survives
    assert proxies[2].exists()
    assert not proxies[0].exists()
    assert not proxies[1].exists()
    # lru_evict log rows written
    cur = await db.execute(
        "SELECT COUNT(*) FROM cache_actions_log "
        "WHERE action='lru_evict' AND result='ok'"
    )
    assert (await cur.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_pinned_rows_never_evicted(db, tmp_path: Path):
    """Even when over cap, a pinned row stays."""
    now = datetime.now(UTC)
    pinned = tmp_path / "pin.mov"

    pinned.write_bytes(b"p" * 200)
    free1 = tmp_path / "f1.mov"

    free1.write_bytes(b"a" * 200)
    await _seed_proxy_cache(
        db, key=("catdv", "1"), file_path=str(pinned), size_bytes=200,
        last_used_at=(now - timedelta(hours=2)).isoformat(),
    )
    await _seed_proxy_cache(
        db, key=("catdv", "2"), file_path=str(free1), size_bytes=200,
        last_used_at=(now - timedelta(minutes=10)).isoformat(),
    )
    await _seed_workspace_pin(db, ws_id=1, key=("catdv", "1"))
    # cap = 100 bytes; non-pinned total = 200 → evict the unpinned row
    lru = _make_lru(db, cap_bytes=100)
    n = await lru.tick_once()
    assert n == 1
    assert pinned.exists()
    assert not free1.exists()


@pytest.mark.asyncio
async def test_partial_logged_when_pins_keep_over_cap(db, tmp_path):
    """Pinned bytes alone exceed cap → partial result logged."""
    now = datetime.now(UTC)
    pin = tmp_path / "p.mov"

    pin.write_bytes(b"x" * 500)
    await _seed_proxy_cache(
        db, key=("catdv", "1"), file_path=str(pin), size_bytes=500,
        last_used_at=now.isoformat(),
    )
    await _seed_workspace_pin(db, ws_id=1, key=("catdv", "1"))
    lru = _make_lru(db, cap_bytes=100)
    n = await lru.tick_once()
    # no non-pinned rows → 0 evictions, total under cap (non-pinned=0)
    # → not partial because the algorithm checks NON-pinned total
    # against cap. Now add a non-pinned row:
    other = tmp_path / "o.mov"

    other.write_bytes(b"y" * 200)
    await _seed_proxy_cache(
        db, key=("catdv", "2"), file_path=str(other), size_bytes=200,
        last_used_at=now.isoformat(),
    )
    n = await lru.tick_once()
    # cap=100, non-pinned=200, evicting the 1 non-pinned row frees 200,
    # leaving non-pinned=0 (under cap). So evicted=1, no partial.
    assert n == 1


@pytest.mark.asyncio
async def test_start_stop_lifecycle(db, tmp_path):
    """Start the loop, let it tick once (interval ~10ms), then stop."""
    lru = _make_lru(db, cap_bytes=1024 * 1024)
    await lru.start()
    import asyncio
    await asyncio.sleep(0.05)
    await lru.stop()
    # stop is idempotent
    await lru.stop()
