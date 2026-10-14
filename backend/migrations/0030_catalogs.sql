-- Cached list of the archive provider's catalogs (#124) so the topbar catalog
-- picker works offline. Refreshed by services/active_catalog when online + stale.
CREATE TABLE catalogs (
  provider_id TEXT NOT NULL,
  catalog_id  TEXT NOT NULL,
  name        TEXT NOT NULL,
  read_only   INTEGER NOT NULL DEFAULT 0,
  fetched_at  TEXT NOT NULL,
  PRIMARY KEY (provider_id, catalog_id)
);
