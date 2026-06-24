# 0014. Local-filesystem proxy resolution (deploy on the CatDV host)

- **Date:** 2026-05-22
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

The current `RestProxyResolver` downloads each clip's web
proxy (~300 MB H.264) from `GET /catdv/api/9/clips/{id}/media` over
the WireGuard VPN (~370 KB/s sustained) into `data/cache/proxies/`,
then hands that file to Gemini ingestion. When the annotator runs on
the same machine as the CatDV server, both the download and the local
cache are pure overhead — the proxy already exists on the host's
filesystem, written there by CatDV's worker pipeline.

The blocker was simply not knowing where on disk. The clip JSON
exposes `media.filePath` (the **hires** ProRes path,
`/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE/...`), but nothing in
the per-clip JSON tells us where the matching proxy lives. Probing
`GET /catdv/api/9/mediastores` answered that:

```
Hires: /Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE
       /Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE
Proxy: /Volumes/ARECA/CatDV_Proxy            (pathType: proxy/web)
       /Volumes/ARECA2/CatDV_Proxy
```

Pairing is by `pathOrder` within a media store. The proxy file mirrors
the hires file's relative path under the swapped root (CatDV
convention; `extensions: null` on the proxy pathType confirms no
filename rewriting). `klientAI` (non-admin) is allowed to read
`/mediastores` — verified via a temporary debug passthrough route in
the running backend (since removed).

**Alternatives considered.**

1. *Same code, loopback CatDV.* Deploy as-is with
   `CATDV_BASE_URL=http://localhost:8080`. The existing `/clips/{id}/media`
   stream now runs at disk speed instead of VPN speed; cache still
   exists but fills in seconds. **Rejected (as the destination, kept
   as a fallback option):** still maintains a cache subsystem we
   wanted to eliminate, still burns a CatDV session seat for media
   bytes, still couples Gemini ingestion to CatDV uptime. Trivially
   simple (env-var flip) so it remains a viable rollback path.
2. *Stream proxy → Gemini without writing to disk.* Pipe the
   `/clips/{id}/media` response body straight into
   `ai_store.ensure_uploaded`. **Rejected:** `ai_store` is built
   around `Path` input; refactoring it to accept an async iterator
   touches the whole AI-store layer including the GCS-files repo,
   for a smaller upside than option 3.
3. *Read `media.filePath` directly and ingest the 16 GB ProRes
   original.* **Rejected:** Gemini upload time and token cost would
   balloon ~50×, and we'd be re-doing the transcode CatDV already
   performed. Only viable with an on-the-fly ffmpeg transcode, which
   is essentially rebuilding the proxy CatDV already has.
4. *Read the proxy file from disk via `/mediastores` mapping*
   (chosen). Map hires-root → proxy-root once at startup, swap
   prefixes per clip, hand Gemini the small H.264 directly.

## Decision

Option 4. Implementation is a rewrite of the existing
`FilesystemProxyResolver` (whose previous `{root}/{clip_id}.mov`
template never matched any real CatDV deployment — it was speculative
scaffolding from PR 7) plus a new `MediaStoreMap` value object that
parses the `/mediastores` JSON. The `PROXY_SOURCE=filesystem` env
value already exists and remains the selector; `PROXY_FS_ROOT` and
`PROXY_PATH_TEMPLATE` are removed because the mapping is fetched from
the server. The hires→proxy pairing rule is "same `pathOrder` inside
the same media store"; `proxy` paths must have `target: "web"` (we
ignore the desktop-client proxy variant).

## Consequences

- *Eliminates the cache subsystem on this deployment.* No writes to
  `data/cache/proxies/`, no `proxy_cache` row recording, no LRU
  eviction pressure from media bytes. The cache code path stays in
  place for `PROXY_SOURCE=rest` (off-site dev, the VPN-bound mode);
  on-host deploys simply don't exercise it.
- *No CatDV media seat.* Metadata calls (lightweight, already
  per-clip cached) are the only CatDV traffic. The 2-seat limit
  stops being a concern when the human web client is also running.
- *Gemini ingest stays small.* Resolver returns the existing web
  proxy file — ~25–50× smaller than the ProRes original, same
  bytes Gemini was already receiving via the REST path.
- *Authoritative config.* Fetching `/mediastores` at startup keeps
  the mapping in sync if the admin reshapes storage; no `PROXY_FS_*`
  env vars to drift out of date.
- *Failure mode is loud.* If a proxy is missing on disk we raise
  `ProxyNotFound` rather than silently falling back to REST — that
  would re-introduce the cache + VPN dependency the deploy was
  designed to eliminate. Operationally, missing proxies match
  CatDV's own "media unavailable" state for that clip.

**Pairing details for `MediaStoreMap`.**

- Group `paths` by `mediaStoreID`.
- Within a store: collect `mediaType=hires` entries into a
  `pathOrder -> path` dict; collect `mediaType=proxy` AND
  `target=web` entries similarly. Emit one rule per `pathOrder`
  present in both dicts. Drop unpaired orders silently — an
  orphan hires root (no matching proxy) is operationally identical
  to "we can't serve those clips locally", and we'd rather skip
  the rule than fabricate one.
- Resolution: linear scan of rules, first `startswith(hires_root + "/")`
  wins. Linear is fine — CatDV deployments rarely have more than a
  handful of media-store paths.

**Out of scope (explicit non-goals).**

- *Automatic detection that we're "on the CatDV host."* The deploy
  artifact selects `PROXY_SOURCE` explicitly. We don't probe whether
  `/Volumes/ARECA/CatDV_Proxy` is reachable before choosing the
  resolver — that's a deploy-time concern, not a runtime one.
- *Cache eviction of any pre-existing `data/cache/proxies/`
  contents on the on-host deploy.* They're stale once we stop
  writing to that directory; cleanup is a one-time manual `rm` if
  the operator cares.
- *Falling back to REST when a proxy is missing on disk.* Explicitly
  rejected — see "Why / Failure mode is loud" above.

**Cache-state UI invariant in host-local mode.**

The `proxy_cache` table is the source of truth for "have we
downloaded a copy of this proxy?" In `PROXY_SOURCE=filesystem` mode
no rows are ever written there, which would naively render every
clip's media-local glyph as `absent` and leave the "Cache locally"
and "Evict local" controls live (and useless). The deploy-side
truth is the opposite: every clip the catalog exposes is already on
the host's disk via the media-store mount, and the user has no
business "caching" or "evicting" anything.

The resolver Protocol therefore carries an `is_host_local: bool`
capability flag (False on `RestProxyResolver`, True on
`FilesystemProxyResolver`). The CacheInspector and clip-list filter
resolver branch on it: in host-local mode the media-local
`LayerStatus` is synthesised as `present=True, evictable=False,
location="host:filesystem"` without reading `proxy_cache`; the
`cache=local` filter contributes nothing and `cache=none` returns
the empty set. Templates hide the controls entirely (per "hide vs
disable" we chose hide — a disabled-with-tooltip variant was
considered and rejected as visual clutter, since the controls
genuinely do not apply, not just "not right now"). The cache page
itself stays unmodified — it lists `proxy_cache` rows, which is
correct: there aren't any in this mode, and "empty cache" is the
accurate state to show.

The chosen seam is the resolver capability rather than a global
`settings.proxy_source` check because the capability travels with
the object that has the most authoritative view of what "having a
proxy locally" means for a given clip — a future resolver that, say,
mirrors proxies into a per-tenant FUSE mount would also set
`is_host_local=True` and inherit the same UI behaviour without
touching the inspector or filter code.
