"""CatalogsRepo — persists / reads `catalogs`, the cached list of the archive
provider's catalogs (#124). Called by services/active_catalog."""

from __future__ import annotations

import aiosqlite

from backend.app.archive.model import CatalogInfo


class CatalogsRepo:
    async def list(self, conn: aiosqlite.Connection, *, provider_id: str) -> list[CatalogInfo]:
        cur = await conn.execute(
            "SELECT catalog_id, name, read_only FROM catalogs WHERE provider_id = ? "
            "ORDER BY name COLLATE NOCASE",
            (provider_id,),
        )
        return [CatalogInfo(cid, name, bool(ro)) for cid, name, ro in await cur.fetchall()]

    async def fetched_at(self, conn: aiosqlite.Connection, *, provider_id: str) -> str | None:
        cur = await conn.execute(
            "SELECT MIN(fetched_at) FROM catalogs WHERE provider_id = ?", (provider_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def replace_all(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalogs: list[CatalogInfo],
        fetched_at: str,
    ) -> None:
        await conn.execute("DELETE FROM catalogs WHERE provider_id = ?", (provider_id,))
        await conn.executemany(
            "INSERT INTO catalogs (provider_id, catalog_id, name, read_only, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(provider_id, c.id, c.name, int(c.read_only), fetched_at) for c in catalogs],
        )
        await conn.commit()
