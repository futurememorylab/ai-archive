# ADR 0131 — Multiple CatDV catalogs: a per-browser active catalog

Date: 2026-11-12 · Status: accepted · Lifespan: Invariant (19a) ·
Recorded after the fact for PR #126 (issue #124, merged 2026-10-14) ·
Spec: `docs/specs/2026-10-14-multiple-catalogs-design.md`

## Context

Until #126 the app worked against exactly one CatDV catalog, fixed at boot
by `CATDV_CATALOG_ID`. The service account now sees more than one
(`881507 AI katalog`, `913722 vyber pro AI`), and people need to see which
one they are in and switch. PR #126 shipped the change with a spec but no
ADR. It added a migration and a cookie contract, both expensive to reverse,
so it needed one; this ADR records it after the fact (SDLC audit,
2026-11-12).

## Decision

- **The active catalog is per browser.** It lives in the `active_catalog`
  cookie (365 days, `httponly`, `samesite=lax`), read through
  `services/active_catalog.py::active_catalog_id`. A missing or malformed
  cookie falls back to `settings.catdv_catalog_id`. Two people on the one
  Cloud Run instance can work in different catalogs at the same time.
- **The catalog list is a small cache layer.** Migration
  `0030_catalogs.sql` adds `catalogs(provider_id, catalog_id, name,
  read_only, fetched_at)`. It is refreshed from `GET /catdv/api/9/catalogs`
  only when online and older than an hour; any miss or failure degrades to
  the stale rows, so the topbar picker works offline (invariant 5). CatDV's
  own permissions are the filter; there is no app allowlist.
- **Clip ids are server-wide, so only list surfaces are catalog-scoped.**
  A live probe (2026-10-14) showed CatDV clip ids don't overlap between
  catalogs and `GET /clips/{id}` needs no catalog. Clip detail, Studio,
  drafts and write-back stay keyed by clip id alone. The clips list,
  `/api/catdv/clips`, new batches and new workspaces read the active
  catalog. `archive/registry.py`'s `default_catalog_id` is unrelated (the
  fallback the adapter stamps on payloads with no catalog) and stays.

## Consequences

- Any new list surface must take the catalog from `active_catalog_id`, never
  from `settings.catdv_catalog_id`. A future list that reads the setting
  directly silently shows the default catalog to someone working in another.
- The same media in two catalogs is two clips with two ids, annotated and
  written back independently. No cross-catalog dedup.
- If CatDV ever makes clip ids per catalog, the write path needs the catalog
  too; the probe above is what this ADR rests on.
- The fs provider has a single implicit catalog; the picker shows just that.

## Alternatives considered

1. **Per-instance active catalog (a setting or an `app_meta` row).** One
   person switching would switch everyone on the shared Cloud Run instance.
   Rejected.
2. **Per-user (stored against the IAP identity).** Survives a browser
   change, but needs a schema and the roles layer for little gain over a
   cookie. Possible later.
