# CatDV local-filesystem proxy resolver

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the annotator runs on the CatDV server host, resolve each clip's web proxy as a direct on-disk file path (`/Volumes/ARECA/CatDV_Proxy/...`) instead of downloading the proxy over HTTP and writing it to a local cache.

**Architecture:** Replace the current placeholder `FilesystemProxyResolver` (templated `{root}/{clip_id}.mov`, which never matched real CatDV layout) with a CatDV-aware resolver that (1) loads the server's Media Store config once at startup via `GET /catdv/api/9/mediastores`, (2) builds a hires-root → proxy-root mapping keyed by `pathOrder`, and (3) for each clip looks up `media.filePath` and rewrites the prefix to the corresponding proxy root. No download, no cache row written, no GCS upload from a cached file. Gemini still receives the small ~300 MB H.264 web proxy (not the 16 GB ProRes original). Same `PROXY_SOURCE=filesystem` env value selects the new behavior — no new settings.

**Tech Stack:** Python 3.14, FastAPI, async httpx (existing `CatdvClient`), pydantic-settings, pytest-asyncio. New module is pure logic over the existing `ArchiveProvider.get_clip` and `CatdvClient._call_json` interfaces.

---

## File structure

| Path | Responsibility |
|---|---|
| `backend/app/services/media_store_map.py` (create) | `MediaStoreMap` value object + `fetch_media_store_map(catdv_client)` async factory. Pure mapping logic: given the `/mediastores` JSON, expose `resolve_proxy(hires_path: str) -> Path \| None`. |
| `backend/app/services/proxy_resolver.py` (modify) | Replace `FilesystemProxyResolver` body. Drop the `path_template` parameter. Add archive-provider + media-store-map dependencies. Update `build_resolver()` accordingly. |
| `backend/app/settings.py` (modify) | Remove `proxy_fs_root` and `proxy_path_template` (no longer needed — mapping is fetched from server). Update `_validate_proxy`. |
| `backend/app/context.py` (modify) | Call `fetch_media_store_map` once when `PROXY_SOURCE=filesystem`. Pass the map + archive provider into `build_resolver`. |
| `.env.example` (modify) | Remove `PROXY_FS_ROOT` and `PROXY_PATH_TEMPLATE` examples; add a `PROXY_SOURCE=filesystem` comment block describing the local-host deploy. |
| `tests/unit/test_media_store_map.py` (create) | Unit tests for parsing + resolving against canned `/mediastores` JSON. |
| `tests/integration/test_proxy_resolver_fs.py` (rewrite) | Replace the templated test with one using a fake archive provider + a real on-disk proxy file. |
| `tests/unit/test_proxy_resolver_factory.py` (modify) | Update the `"filesystem"` factory test to pass the new deps. |
| `tests/unit/test_settings.py` (modify) | Drop the `PROXY_FS_ROOT` required-when-filesystem assertions. |
| `docs/decisions.md` (modify) | Decision entry — already written by the parent session. |
| `docs/DEPLOY.md` (modify) | Add a "Running on the CatDV host" section describing read-access requirements and `PROXY_SOURCE=filesystem`. |
| `backend/app/routes/debug.py` (delete) | Temporary passthrough route created during exploration — already removed by cleanup before this plan executes. |
| `backend/app/services/cache_inspector.py` (modify) | Accept `host_local_proxies: bool` in `__init__`; when True, the media-local `LayerStatus` reports `present=True, evictable=False` for every clip without reading `proxy_cache`. |
| `backend/app/services/clip_list_filters.py` (modify) | Accept the same flag in `resolve()`; short-circuit `cache=local` to "no filter" and `cache=none` to empty set. |
| `backend/app/templates/pages/clips.html` (modify) | Hide the "Cache locally" / "Remove from local cache" actions menu items and the "Cache:" filter dropdown when `host_local_proxies`. |
| `backend/app/templates/pages/clip_detail.html` (modify) | Hide the "Evict local" button when `host_local_proxies`. |
| `backend/app/templates/cache_popover.html` (modify) | Hide the per-layer Evict button on the `media-local` row when `host_local_proxies`. |
| `backend/app/routes/pages.py` and `backend/app/routes/cache.py` (modify) | Expose `host_local_proxies` to Jinja templates as a context variable (via `request.app.state.ctx`). |
| `tests/unit/test_cache_inspector_host_local.py` (create) | Unit tests for the new branch in `status_for_clips`. |
| `tests/unit/test_clip_list_filters_host_local.py` (create) | Unit tests for the new branch in `resolve()`. |

---

## Task 1: `MediaStoreMap` — parse the `/mediastores` response and resolve hires→proxy paths

**Files:**
- Create: `backend/app/services/media_store_map.py`
- Test: `tests/unit/test_media_store_map.py`

The CatDV server returned this shape during exploration (see decisions entry "2026-05-22 — Local-filesystem proxy resolution"):

```json
[{
  "ID": 361803, "name": "Pragafilm",
  "paths": [
    {"pathOrder": 2, "path": "/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
     "pathType": {"mediaType": "hires", "target": null}},
    {"pathOrder": 3, "path": "/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
     "pathType": {"mediaType": "hires", "target": null}},
    {"pathOrder": 2, "path": "/Volumes/ARECA/CatDV_Proxy",
     "pathType": {"mediaType": "proxy", "target": "web"}},
    {"pathOrder": 3, "path": "/Volumes/ARECA2/CatDV_Proxy",
     "pathType": {"mediaType": "proxy", "target": "web"}}
  ]
}]
```

Pairing rule: within a media store, pair the `mediaType=hires` and `mediaType=proxy` (with `target=web`) paths by matching `pathOrder`. That gives two prefix-swap rules: `…/ARECA/ARCHIV…` ↔ `…/ARECA/CatDV_Proxy`, and the ARECA2 equivalent.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_media_store_map.py
import json
from pathlib import Path

import pytest

from backend.app.services.media_store_map import MediaStoreMap


FIXTURE = [
    {
        "ID": 361803,
        "name": "Pragafilm",
        "paths": [
            {
                "pathOrder": 2,
                "path": "/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
                "pathType": {"mediaType": "hires", "target": None},
            },
            {
                "pathOrder": 3,
                "path": "/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
                "pathType": {"mediaType": "hires", "target": None},
            },
            {
                "pathOrder": 2,
                "path": "/Volumes/ARECA/CatDV_Proxy",
                "pathType": {"mediaType": "proxy", "target": "web"},
            },
            {
                "pathOrder": 3,
                "path": "/Volumes/ARECA2/CatDV_Proxy",
                "pathType": {"mediaType": "proxy", "target": "web"},
            },
            # Decoy: client-target proxy must be ignored — we only care
            # about "target": "web".
            {
                "pathOrder": 4,
                "path": "/Volumes/ARECA/CatDV_DesktopProxy",
                "pathType": {"mediaType": "proxy", "target": "client"},
            },
        ],
    }
]


def test_parses_two_prefix_rules_from_fixture():
    m = MediaStoreMap.from_json(FIXTURE)
    assert m.rules == [
        ("/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
         "/Volumes/ARECA/CatDV_Proxy"),
        ("/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
         "/Volumes/ARECA2/CatDV_Proxy"),
    ]


def test_resolve_swaps_prefix_keeps_relative_path():
    m = MediaStoreMap.from_json(FIXTURE)
    hires = "/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE/ABRAMCUKOVA Anna/ABRAMCUKOVA Anna 01.mov"
    assert m.resolve_proxy(hires) == Path(
        "/Volumes/ARECA/CatDV_Proxy/ABRAMCUKOVA Anna/ABRAMCUKOVA Anna 01.mov"
    )


def test_resolve_second_root():
    m = MediaStoreMap.from_json(FIXTURE)
    hires = "/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE/foo/bar.mov"
    assert m.resolve_proxy(hires) == Path("/Volumes/ARECA2/CatDV_Proxy/foo/bar.mov")


def test_resolve_returns_none_when_no_prefix_matches():
    m = MediaStoreMap.from_json(FIXTURE)
    assert m.resolve_proxy("/some/other/place/file.mov") is None


def test_unpaired_hires_root_is_dropped():
    # If a hires path has pathOrder 5 but no matching proxy path with
    # pathOrder 5, the rule is silently dropped — we only emit paired
    # rules.
    fixture = [
        {
            "ID": 1,
            "name": "X",
            "paths": [
                {"pathOrder": 5, "path": "/a", "pathType": {"mediaType": "hires", "target": None}},
                {"pathOrder": 6, "path": "/b", "pathType": {"mediaType": "proxy", "target": "web"}},
            ],
        }
    ]
    m = MediaStoreMap.from_json(fixture)
    assert m.rules == []


def test_empty_response_yields_empty_map():
    m = MediaStoreMap.from_json([])
    assert m.rules == []
    assert m.resolve_proxy("/anything") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/bin/pytest tests/unit/test_media_store_map.py -v
```
Expected: ImportError (module doesn't exist yet).

- [ ] **Step 3: Implement `MediaStoreMap`**

```python
# backend/app/services/media_store_map.py
"""Hires→proxy path mapping derived from CatDV's `/mediastores` config.

The CatDV server returns one Media Store per logical archive, and each
store contains a flat list of `paths`. We pair the `mediaType=hires`
paths with the `mediaType=proxy, target=web` paths by matching
`pathOrder` — that mirrors how CatDV's own web client resolves a
proxy URL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaStoreMap:
    """A list of (hires_root, proxy_root) prefix-rewrite rules."""

    rules: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_json(cls, stores: list[dict[str, Any]]) -> "MediaStoreMap":
        rules: list[tuple[str, str]] = []
        for store in stores:
            hires_by_order: dict[int, str] = {}
            proxy_by_order: dict[int, str] = {}
            for p in store.get("paths", []):
                ptype = p.get("pathType") or {}
                order = p.get("pathOrder")
                path = p.get("path")
                if order is None or not path:
                    continue
                if ptype.get("mediaType") == "hires":
                    hires_by_order[order] = path.rstrip("/")
                elif (
                    ptype.get("mediaType") == "proxy"
                    and ptype.get("target") == "web"
                ):
                    proxy_by_order[order] = path.rstrip("/")
            for order in sorted(hires_by_order):
                if order in proxy_by_order:
                    rules.append((hires_by_order[order], proxy_by_order[order]))
        return cls(rules=rules)

    def resolve_proxy(self, hires_path: str) -> Path | None:
        for hires_root, proxy_root in self.rules:
            if hires_path == hires_root:
                continue
            if hires_path.startswith(hires_root + "/"):
                rel = hires_path[len(hires_root) + 1 :]
                return Path(f"{proxy_root}/{rel}")
        return None


async def fetch_media_store_map(catdv_client) -> MediaStoreMap:
    """Call `/catdv/api/9/mediastores` once and build the map.

    Read-only; `klientAI` (non-admin) is allowed."""
    env = await catdv_client._call_json("GET", "/catdv/api/9/mediastores")
    return MediaStoreMap.from_json(env.data or [])
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/unit/test_media_store_map.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media_store_map.py tests/unit/test_media_store_map.py
git commit -m "feat(proxy): MediaStoreMap — parse /mediastores into hires→proxy rules"
```

---

## Task 2: Rewrite `FilesystemProxyResolver` to use MediaStoreMap + archive provider

**Files:**
- Modify: `backend/app/services/proxy_resolver.py:81-126`
- Rewrite: `tests/integration/test_proxy_resolver_fs.py`

The new resolver depends on an `ArchiveProvider` (so it can call `get_clip` to read `media.filePath`) and a `MediaStoreMap`. `path_for_clip_id(clip_id)` does:
1. `clip = await archive.get_clip(str(clip_id))`
2. `hires = clip.provider_data["media"]["filePath"]` — raise `ProxyNotFound` if missing/empty.
3. `proxy = media_store_map.resolve_proxy(hires)` — raise `ProxyNotFound` if `None`.
4. Verify `proxy.exists()` and `os.access(proxy, os.R_OK)` — raise `ProxyNotFound` otherwise.
5. Return `proxy`.

`is_managed(path)` returns `False` — these files are owned by CatDV, never written or evicted by us.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_proxy_resolver_fs.py — full rewrite
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.media_store_map import MediaStoreMap
from backend.app.services.proxy_resolver import (
    FilesystemProxyResolver,
    ProxyNotFound,
)


class _FakeArchive:
    def __init__(self, clip_by_id: dict[int, dict]):
        self._by_id = clip_by_id

    async def get_clip(self, clip_id_str: str):
        return SimpleNamespace(provider_data=self._by_id[int(clip_id_str)])


def _map_with(hires_root: Path, proxy_root: Path) -> MediaStoreMap:
    return MediaStoreMap(rules=[(str(hires_root), str(proxy_root))])


@pytest.mark.asyncio
async def test_returns_existing_proxy_path(tmp_path: Path):
    hires_root = tmp_path / "hires"
    proxy_root = tmp_path / "proxy"
    (proxy_root / "sub").mkdir(parents=True)
    proxy_file = proxy_root / "sub" / "clip.mov"
    proxy_file.write_bytes(b"x")

    archive = _FakeArchive({
        42: {"media": {"filePath": str(hires_root / "sub" / "clip.mov")}}
    })
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(hires_root, proxy_root),
    )

    assert await resolver.path_for_clip_id(42) == proxy_file
    assert resolver.is_managed(proxy_file) is False


@pytest.mark.asyncio
async def test_raises_when_clip_has_no_media_filepath(tmp_path: Path):
    archive = _FakeArchive({42: {"media": {}}})
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(tmp_path / "h", tmp_path / "p"),
    )
    with pytest.raises(ProxyNotFound, match="no media.filePath"):
        await resolver.path_for_clip_id(42)


@pytest.mark.asyncio
async def test_raises_when_hires_path_unknown_to_mediastore(tmp_path: Path):
    archive = _FakeArchive({
        42: {"media": {"filePath": "/some/unmapped/path.mov"}}
    })
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(tmp_path / "h", tmp_path / "p"),
    )
    with pytest.raises(ProxyNotFound, match="no mediastore rule"):
        await resolver.path_for_clip_id(42)


@pytest.mark.asyncio
async def test_raises_when_proxy_file_missing_on_disk(tmp_path: Path):
    hires_root = tmp_path / "hires"
    proxy_root = tmp_path / "proxy"
    proxy_root.mkdir()
    archive = _FakeArchive({
        42: {"media": {"filePath": str(hires_root / "sub" / "clip.mov")}}
    })
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(hires_root, proxy_root),
    )
    with pytest.raises(ProxyNotFound, match="not on disk"):
        await resolver.path_for_clip_id(42)


@pytest.mark.asyncio
async def test_raises_when_proxy_unreadable(tmp_path: Path):
    hires_root = tmp_path / "hires"
    proxy_root = tmp_path / "proxy"
    proxy_root.mkdir()
    proxy_file = proxy_root / "clip.mov"
    proxy_file.write_bytes(b"x")
    proxy_file.chmod(0)
    archive = _FakeArchive({
        42: {"media": {"filePath": str(hires_root / "clip.mov")}}
    })
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(hires_root, proxy_root),
    )
    try:
        with pytest.raises(ProxyNotFound, match="not readable"):
            await resolver.path_for_clip_id(42)
    finally:
        proxy_file.chmod(0o644)
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/integration/test_proxy_resolver_fs.py -v
```
Expected: ImportError (`media_store_map` not used by resolver yet) or signature mismatch.

- [ ] **Step 3: Rewrite `FilesystemProxyResolver`**

Replace the existing class (lines 81–98) and the `"filesystem"` branch of `build_resolver()` (lines 119–125) with:

```python
# backend/app/services/proxy_resolver.py — replace from line 81 onward

class ProxyNotFound(FileNotFoundError):
    """Raised when a proxy can't be located on the filesystem."""


class FilesystemProxyResolver:
    """Returns proxy paths from the CatDV server's local filesystem.

    No download. Uses `/mediastores` to map the clip's `media.filePath`
    (hires) to its on-disk web-proxy path. Intended for deployments
    running on the same host as the CatDV server.
    """

    def __init__(self, archive, media_store_map: "MediaStoreMap") -> None:
        self._archive = archive
        self._map = media_store_map

    async def path_for_clip_id(self, clip_id: int) -> Path:
        clip = await self._archive.get_clip(str(clip_id))
        media = (clip.provider_data or {}).get("media") or {}
        hires = media.get("filePath")
        if not hires:
            raise ProxyNotFound(f"clip {clip_id}: no media.filePath")
        proxy = self._map.resolve_proxy(hires)
        if proxy is None:
            raise ProxyNotFound(f"clip {clip_id}: no mediastore rule for {hires!r}")
        if not proxy.exists():
            raise ProxyNotFound(f"clip {clip_id}: proxy not on disk: {proxy}")
        if not os.access(proxy, os.R_OK):
            raise ProxyNotFound(f"clip {clip_id}: proxy not readable: {proxy}")
        return proxy

    def is_managed(self, path: Path) -> bool:
        return False
```

Update `build_resolver()` — replace the `"filesystem"` branch:

```python
    if source == "filesystem":
        if archive is None or media_store_map is None:
            raise ValueError(
                "filesystem source requires archive provider and media_store_map"
            )
        return FilesystemProxyResolver(archive=archive, media_store_map=media_store_map)
```

…and update the function signature accordingly (replace `fs_root`/`path_template` params with `archive` and `media_store_map`). Delete the `from .media_store_map import MediaStoreMap` re-export wherever needed; do `from backend.app.services.media_store_map import MediaStoreMap` at the top of the file inside a `TYPE_CHECKING` block for the annotation.

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/integration/test_proxy_resolver_fs.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/proxy_resolver.py tests/integration/test_proxy_resolver_fs.py
git commit -m "feat(proxy): rewrite FilesystemProxyResolver to use mediastore mapping"
```

---

## Task 3: Update settings — drop the old fs-template knobs

**Files:**
- Modify: `backend/app/settings.py:21-23, 57-62`
- Modify: `tests/unit/test_settings.py:25-50`
- Modify: `.env.example:14` and surrounding lines

- [ ] **Step 1: Update the failing settings tests**

In `tests/unit/test_settings.py`, delete `test_settings_rejects_filesystem_without_root` and `test_settings_rejects_filesystem_with_empty_root`. Add one new test that asserts `PROXY_SOURCE=filesystem` is accepted without `PROXY_FS_ROOT`:

```python
def test_settings_accepts_filesystem_without_fs_root(monkeypatch):
    monkeypatch.setenv("CATDV_BASE_URL", "http://x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "filesystem")
    from backend.app.settings import Settings
    s = Settings()
    assert s.proxy_source == "filesystem"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/unit/test_settings.py -v
```
Expected: deleted tests fail because they reference removed names — that's fine, you'll have deleted them. The new test fails because the validator still requires `PROXY_FS_ROOT`.

- [ ] **Step 3: Update `settings.py`**

Remove lines 22 and 23 (`proxy_fs_root`, `proxy_path_template`). Delete the `_validate_proxy` method (lines 57–62) entirely. Leave `proxy_source` and `proxy_cache_cap_gb` as-is.

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/unit/test_settings.py -v
```
Expected: all passing, including the new accept-filesystem-without-root case.

- [ ] **Step 5: Update `.env.example`**

Replace the block:

```
PROXY_SOURCE=rest
# PROXY_FS_ROOT=/path/to/proxies
# PROXY_PATH_TEMPLATE={root}/{clip_id}.mov
```

with:

```
PROXY_SOURCE=rest
# Set PROXY_SOURCE=filesystem when deploying on the same host as the
# CatDV server. The app then reads each clip's web-proxy directly from
# CatDV's media-store directory (e.g. /Volumes/ARECA/CatDV_Proxy/...),
# skipping the proxy download and the local cache entirely.
# No further config is required — the hires→proxy mapping is fetched
# from `GET /catdv/api/9/mediastores` at startup.
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings.py .env.example
git commit -m "refactor(settings): drop PROXY_FS_ROOT — mediastore mapping is auto-fetched"
```

---

## Task 4: Wire `MediaStoreMap` fetch + new resolver into `context.py`

**Files:**
- Modify: `backend/app/context.py:131-177`
- Modify: `tests/unit/test_proxy_resolver_factory.py`

- [ ] **Step 1: Update the factory tests**

In `tests/unit/test_proxy_resolver_factory.py`, replace the `test_factory_returns_filesystem_resolver` and `test_factory_rejects_filesystem_without_root` tests with:

```python
def test_factory_returns_filesystem_resolver():
    from backend.app.services.media_store_map import MediaStoreMap
    resolver = build_resolver(
        source="filesystem",
        catdv_client=None,
        cache_dir=None,
        archive=object(),
        media_store_map=MediaStoreMap(),
    )
    assert isinstance(resolver, FilesystemProxyResolver)


def test_factory_rejects_filesystem_without_archive_and_map():
    with pytest.raises(ValueError, match="archive provider and media_store_map"):
        build_resolver(
            source="filesystem",
            catdv_client=None,
            cache_dir=None,
            archive=None,
            media_store_map=None,
        )
```

Update the `test_factory_returns_rest_resolver` test to pass `archive=None, media_store_map=None` in its `build_resolver` call (the REST branch must ignore them).

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/unit/test_proxy_resolver_factory.py -v
```
Expected: TypeError — `build_resolver` doesn't take `archive` / `media_store_map` yet (Task 2 should already have added them; this verifies the wiring).

- [ ] **Step 3: Wire context.py**

In `backend/app/context.py`, replace lines 164–173 (the `if use_catdv: ctx.proxy_resolver = build_resolver(...)` block) with:

```python
            if use_catdv:
                media_store_map = None
                if settings.proxy_source == "filesystem":
                    from backend.app.services.media_store_map import (
                        fetch_media_store_map,
                    )
                    media_store_map = await fetch_media_store_map(ctx.catdv)
                ctx.proxy_resolver = build_resolver(
                    source=settings.proxy_source,
                    catdv_client=ctx.catdv,
                    cache_dir=settings.data_dir / "cache" / "proxies",
                    archive=ctx.archive,
                    media_store_map=media_store_map,
                    proxy_cache_repo=ctx.proxy_cache_repo,
                    db_provider=lambda c=ctx: c.db,
                )
```

- [ ] **Step 4: Run tests**

```
.venv/bin/pytest tests/unit/test_proxy_resolver_factory.py tests/integration/test_proxy_resolver_fs.py tests/unit/test_media_store_map.py -v
```
Expected: all passing.

- [ ] **Step 5: Smoke-test the running app with PROXY_SOURCE=filesystem**

This step is **manual** and only meaningful on the CatDV host (or with the `/Volumes/ARECA/CatDV_Proxy/...` paths reachable). Locally on a dev laptop the resolver will raise `ProxyNotFound` on every clip because the volumes aren't mounted — that's expected.

```
PROXY_SOURCE=filesystem .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
# in another shell:
curl -s http://127.0.0.1:8765/api/media/888700 -o /tmp/probe.mov -w "%{http_code}\n"
```

On the CatDV host: expect a 200 and a ~300 MB H.264 `.mov`. On a dev laptop: expect a 404 with body containing "proxy not on disk".

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py tests/unit/test_proxy_resolver_factory.py
git commit -m "feat(context): fetch media-store map at startup for filesystem resolver"
```

---

## Task 5: DEPLOY.md — instructions for running on the CatDV host

**Files:**
- Modify: `docs/DEPLOY.md`

- [ ] **Step 1: Add the section**

Append (or place near the existing deployment sections — match the file's style):

```markdown
## Running on the CatDV host (no proxy cache)

When the annotator runs on the same machine as the CatDV server, set:

```
PROXY_SOURCE=filesystem
```

…and ensure the OS user has **read access** to every directory listed
under `mediaType: proxy, target: web` in `GET /catdv/api/9/mediastores`.
For this installation that's `/Volumes/ARECA/CatDV_Proxy/` and
`/Volumes/ARECA2/CatDV_Proxy/`.

No other settings change. At startup the app fetches the media-store
config and builds the hires→proxy mapping. Per clip, it reads
`media.filePath` from CatDV, swaps the hires-root prefix for the
matching proxy root, and hands the resulting path to Gemini ingestion.

**What this turns off:** the `data/cache/proxies/` directory is no
longer written. `proxy_cache` rows are not recorded. CatDV doesn't get
hit for proxy bytes — only for clip metadata (which is light, already
cached).

**Failure modes:**

- `ProxyNotFound: ... no media.filePath` — the clip has no media
  attached upstream. Same outcome as the REST resolver would have had.
- `ProxyNotFound: ... no mediastore rule` — the clip's `media.filePath`
  prefix isn't in any media-store. Re-check `/mediastores` and confirm
  the volume mount you expect is present.
- `ProxyNotFound: ... not on disk` — the file is missing or the LTO
  archive has reclaimed it. CatDV's web client would show the same
  "media unavailable" state for that clip.

There is intentionally no automatic fallback to the REST resolver
when a proxy is missing on disk — failing loudly is better than
silently re-introducing the cache + VPN dependency.
```

- [ ] **Step 2: Commit**

```bash
git add docs/DEPLOY.md
git commit -m "docs(deploy): document PROXY_SOURCE=filesystem for on-host deploy"
```

---

## Task 6: Cache state + UI affordances in `host_local` mode

**Files:**
- Modify: `backend/app/services/proxy_resolver.py` (add `is_host_local` to Protocol + impls)
- Modify: `backend/app/services/cache_inspector.py:113-160`
- Modify: `backend/app/services/clip_list_filters.py:140-` (`resolve()` body)
- Modify: `backend/app/context.py:204-208` (CacheInspector construction)
- Modify: `backend/app/routes/pages.py`, `backend/app/routes/cache.py` (template context)
- Modify: `backend/app/templates/pages/clips.html:70-100`
- Modify: `backend/app/templates/pages/clip_detail.html:24`
- Modify: `backend/app/templates/cache_popover.html:29-38`
- Create: `tests/unit/test_cache_inspector_host_local.py`
- Create: `tests/unit/test_clip_list_filters_host_local.py`

**Invariant.** When the active resolver is host-local (i.e. `FilesystemProxyResolver`),
every clip's media-local cache layer is reported as `present=True, evictable=False`,
and the UI hides every control that would prompt a user to download or remove a
local proxy. The `proxy_cache` table is not read in this mode for media-local
status; in this deployment it stays empty and that's the correct steady state.

- [ ] **Step 1: Add `is_host_local` capability to the resolver Protocol**

In `backend/app/services/proxy_resolver.py`, extend the Protocol and both
implementations:

```python
@runtime_checkable
class ProxyResolver(Protocol):
    is_host_local: bool

    async def path_for_clip_id(self, clip_id: int) -> Path: ...
    def is_managed(self, path: Path) -> bool: ...


class RestProxyResolver:
    is_host_local = False
    # ... rest unchanged


class FilesystemProxyResolver:
    is_host_local = True
    # ... rest unchanged (the body Task 2 wrote)
```

- [ ] **Step 2: Failing test — CacheInspector reports synthetic media-local layer in host-local mode**

```python
# tests/unit/test_cache_inspector_host_local.py
import pytest

from backend.app.archive.model import ClipKey
from backend.app.services.cache_inspector import CacheInspector


@pytest.mark.asyncio
async def test_media_local_layer_synthetic_when_host_local(memdb):
    """In host-local mode, every clip reports media-local as present + non-evictable
    without any proxy_cache row."""
    inspector = CacheInspector(
        db_provider=lambda: memdb,
        media_cache_cap_bytes=0,
        provider=None,
        host_local_proxies=True,
    )
    rows = await inspector.status_for_clips([ClipKey(("catdv", "42"))])
    layer = next(layer for layer in rows[0].layers if layer.layer == "media-local")
    assert layer.present is True
    assert layer.evictable is False
    assert layer.location == "host:filesystem"


@pytest.mark.asyncio
async def test_media_local_layer_normal_when_not_host_local(memdb):
    """In rest mode, an absent proxy_cache row means present=False (existing behaviour)."""
    inspector = CacheInspector(
        db_provider=lambda: memdb,
        media_cache_cap_bytes=0,
        provider=None,
        host_local_proxies=False,
    )
    rows = await inspector.status_for_clips([ClipKey(("catdv", "42"))])
    layer = next(layer for layer in rows[0].layers if layer.layer == "media-local")
    assert layer.present is False
    assert layer.evictable is False
```

The `memdb` fixture should produce an `aiosqlite.Connection` against an
in-memory DB with the migrations applied — reuse the existing fixture in
`tests/conftest.py` (`db` or `memdb`; pick whichever the project already
exposes). If neither is parametrised to the inspector's signature, add a
local fixture in this test file that opens `aiosqlite.connect(":memory:")`
and runs `backend/app/migrations_runner.run_migrations` against it.

Run: `.venv/bin/pytest tests/unit/test_cache_inspector_host_local.py -v`
Expected: TypeError (`host_local_proxies` not accepted yet) or AssertionError
(synthetic layer not produced).

- [ ] **Step 3: Implement the inspector branch**

In `backend/app/services/cache_inspector.py`, extend `__init__`:

```python
class CacheInspector:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        media_cache_cap_bytes: int = 0,
        provider: Any | None = None,
        host_local_proxies: bool = False,
    ) -> None:
        self._db_provider = db_provider
        self._cap = media_cache_cap_bytes
        self._provider = provider
        self._host_local = host_local_proxies
```

In `status_for_clips`, replace the existing `ml_layer = LayerStatus(...)`
construction (the block that reads `ml_row = media_local.get(key)`) with:

```python
            if self._host_local:
                ml_layer = LayerStatus(
                    layer="media-local",
                    present=True,
                    size_bytes=None,
                    location="host:filesystem",
                    fetched_at=None,
                    last_used_at=None,
                    pinned_by_workspaces=ws_ids,
                    evictable=False,
                )
            else:
                ml_layer = LayerStatus(
                    layer="media-local",
                    present=ml_row is not None,
                    size_bytes=ml_row["size_bytes"] if ml_row else None,
                    location=ml_row["file_path"] if ml_row else None,
                    fetched_at=(
                        _parse_iso(ml_row["downloaded_at"]) if ml_row else None
                    ),
                    last_used_at=(
                        _parse_iso(ml_row["last_used_at"]) if ml_row else None
                    ),
                    pinned_by_workspaces=ws_ids,
                    evictable=(ml_row is not None and not ws_ids),
                )
```

In host-local mode, the `_load_media_local` SQL call is wasted; gate it:

```python
            media_local = (
                {} if self._host_local else await self._load_media_local(db, keys)
            )
```

Run: `.venv/bin/pytest tests/unit/test_cache_inspector_host_local.py -v`
Expected: 2 passed.

- [ ] **Step 4: Failing test — clip_list_filters short-circuits in host-local mode**

```python
# tests/unit/test_clip_list_filters_host_local.py
import pytest

from backend.app.services import clip_list_filters as f


@pytest.mark.asyncio
async def test_cache_local_short_circuits_to_no_filter(memdb):
    result = await f.resolve(
        memdb,
        provider_id="catdv",
        catalog_id="881507",
        cache="local",
        anno="any",
        host_local_proxies=True,
    )
    # `None` means "no filter active" — caller takes the standard
    # CatDV-paginated path, i.e. every clip is included.
    assert result is None


@pytest.mark.asyncio
async def test_cache_none_short_circuits_to_empty_set(memdb):
    result = await f.resolve(
        memdb,
        provider_id="catdv",
        catalog_id="881507",
        cache="none",
        anno="any",
        host_local_proxies=True,
    )
    assert result == set()


@pytest.mark.asyncio
async def test_cache_filter_ignored_in_host_local_when_anno_active(memdb):
    """When `anno` is active too, host-local just drops the cache predicate —
    the anno predicate still applies."""
    # Seed one review_item so the anno=for_review predicate returns {42}.
    await memdb.execute(
        "INSERT INTO review_items(catdv_clip_id, applied_at, ...) VALUES (42, NULL, ...)"
    )
    await memdb.commit()
    result = await f.resolve(
        memdb,
        provider_id="catdv",
        catalog_id="881507",
        cache="local",          # would normally restrict
        anno="for_review",
        host_local_proxies=True,
    )
    assert result == {42}
```

Note: the exact `INSERT INTO review_items` column list depends on the live
schema — use `PRAGMA table_info(review_items)` or copy from an existing test
fixture rather than the placeholder `...` above.

Run: `.venv/bin/pytest tests/unit/test_clip_list_filters_host_local.py -v`
Expected: TypeError (kwarg not accepted yet).

- [ ] **Step 5: Implement the filter branch**

In `backend/app/services/clip_list_filters.py`, add `host_local_proxies` as a
new kwarg to `resolve()` and adjust the predicate construction. Find the
lines that handle the `cache` filter (around the `_ids_with_media_local`
call site — confirm with `grep -n _ids_with_media_local clip_list_filters.py`)
and wrap them:

```python
async def resolve(
    db: aiosqlite.Connection,
    *,
    provider_id: str,
    catalog_id: str,
    cache: CacheFilter,
    anno: AnnoFilter,
    host_local_proxies: bool = False,
) -> set[int] | None:
    if host_local_proxies:
        # `local` matches every clip (so the cache predicate contributes
        # nothing) and `none` matches nothing (early-return empty set).
        if cache == "none":
            return set()
        cache = "any"
    # ... rest of function unchanged
```

- [ ] **Step 6: Run the new filter tests + the existing suite to confirm no regression**

```
.venv/bin/pytest tests/unit/test_clip_list_filters_host_local.py tests/unit -q
```

Expected: green. Any pre-existing call site of `resolve()` keeps working
because the new kwarg defaults to `False`.

- [ ] **Step 7: Wire the capability through `context.py` to the inspector**

In `backend/app/context.py`, locate the second `CacheInspector(...)`
construction (line ~204, in the `init_external` branch) and pass the flag:

```python
            ctx.cache_inspector = CacheInspector(
                db_provider=lambda c=ctx: c.db,
                media_cache_cap_bytes=cap_bytes,
                provider=ctx.archive,
                host_local_proxies=getattr(ctx.proxy_resolver, "is_host_local", False),
            )
```

Leave the earlier "always-wired" `CacheInspector` construction (the one
that runs when `init_external=False`, ~line 120) alone — that path doesn't
have a resolver yet, default `False` is correct.

- [ ] **Step 8: Hide the dead UI affordances**

The route that renders `pages/clips.html` needs to pass the capability into
the template. In `backend/app/routes/pages.py`, find the handler that
renders that template (search for `"pages/clips.html"`) and add:

```python
        "host_local_proxies": getattr(
            getattr(ctx, "proxy_resolver", None), "is_host_local", False
        ),
```

…to the context dict. Do the same in the handler that renders
`pages/clip_detail.html`. For `cache_popover.html`, it's rendered from
`backend/app/routes/cache.py` — search for that template name and inject the
same key.

In `backend/app/templates/pages/clips.html`, wrap the two action items and
the cache filter dropdown:

```html
{% if not host_local_proxies %}
  <button type="button"
          class="actions-item"
          @click="open = false; bulkPrefetch()">
    Cache locally
  </button>
  <button type="button"
          class="actions-item actions-item-danger"
          @click="open = false; bulkEvict()">
    Remove from local cache
  </button>
{% endif %}
```

And the cache filter dropdown (search `<select name="cache"` in the same
file or in `_clips_tbody.html`) gets wrapped with the same
`{% if not host_local_proxies %} … {% endif %}`. If the filter dropdown is
defined in a partial included from `clips.html`, edit it there.

In `backend/app/templates/pages/clip_detail.html` line 24:

```html
{% if not host_local_proxies %}
  <button type="button"
          class="cache-evict-btn"
          onclick="evictLocal({{ clip.id }})">Evict local</button>
{% endif %}
```

In `backend/app/templates/cache_popover.html` (the `media-local` row only —
not metadata or ai), wrap the Evict button with:

```html
{% if not (host_local_proxies and layer.layer == "media-local") %}
  <button …>Evict</button>
{% endif %}
```

- [ ] **Step 9: Manual smoke (skip if not on the CatDV host)**

This step requires the host filesystem and is the operator's responsibility
— mark N/A in the remote-agent report. Pattern to verify if you do run it:

1. Start with `PROXY_SOURCE=filesystem`.
2. `/clips` page: every row's middle (media-local) glyph is the
   `present-pinned` colour; no "Cache locally" / "Remove from local cache"
   in the Actions menu; no "Cache:" filter dropdown.
3. Per-clip cache popover: the `media-local` row has no Evict button.
4. Clip detail page: no "Evict local" button.
5. `cache=local` filter (if reached via a deep link): server returns the
   default catalog page (no filter applied).

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/proxy_resolver.py \
        backend/app/services/cache_inspector.py \
        backend/app/services/clip_list_filters.py \
        backend/app/context.py \
        backend/app/routes/pages.py \
        backend/app/routes/cache.py \
        backend/app/templates/pages/clips.html \
        backend/app/templates/pages/clip_detail.html \
        backend/app/templates/cache_popover.html \
        tests/unit/test_cache_inspector_host_local.py \
        tests/unit/test_clip_list_filters_host_local.py
git commit -m "feat(cache): host-local mode — synthetic media-local + hide dead controls"
```

---

## Self-review

1. **Spec coverage.** Resolver reads `media.filePath` (Task 2), mediastore mapping fetched once (Task 1 + 4), no cache row written (`FilesystemProxyResolver.is_managed` returns False, `path_for_clip_id` never touches the repo — Task 2), Gemini still receives the small proxy (downstream `ai_store.ensure_uploaded` is unchanged, sees the small file). ✓

2. **Placeholders.** No "TBD"/"similar to" placeholders. Every code step has the full code. ✓

3. **Type consistency.** `MediaStoreMap` constructor takes `rules=[(str,str)]`; `from_json` and tests both use that shape. `FilesystemProxyResolver(archive, media_store_map)` matches the factory call and both tests' construction. `resolve_proxy` returns `Path | None` in every reference. ✓

4. **Deletion sanity check.** Removing `proxy_fs_root`/`proxy_path_template` is safe — they're only referenced by `settings.py`, the factory `build_resolver` (which Task 2 also updates), `context.py` (Task 4 updates), and the tests we rewrite. `grep -rn "proxy_fs_root\|proxy_path_template" backend/ tests/` should return zero hits after Task 4. ✓

5. **Host-local invariant (Task 6).** In `PROXY_SOURCE=filesystem` mode, every clip's media-local layer reports `present=True, evictable=False, location="host:filesystem"` (Task 6 Step 3); the `cache=local` filter contributes nothing and `cache=none` returns the empty set (Step 5); the "Cache locally", "Remove from local cache", "Evict local", and per-layer media-local Evict controls are hidden from the templates (Step 8). The `proxy_cache` table is not read for media-local status in this mode, so its emptiness is the expected steady state. ✓
