"""user_roles persistence — the app-side authorization layer.

Google IAP owns the gate; this table owns roles (spec
2026-06-14-iap-roles-admin-console-design.md). A leaf repo: raw SQL over
aiosqlite, no service imports. Email is the IAP-verified identity, stored and
compared lowercased so case can't defeat self-protection / dedupe.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

_COLS = ("email", "role", "status", "display_name", "granted_by", "granted_at")


def _norm(email: str) -> str:
    return email.strip().lower()


class UserRolesRepo:
    """DB-backed user_roles."""

    async def get(self, conn: aiosqlite.Connection, email: str) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM user_roles WHERE email = ?", (_norm(email),)
        )
        row = await cur.fetchone()
        return dict(zip(_COLS, row, strict=True)) if row else None

    async def get_active_role(self, conn: aiosqlite.Connection, email: str) -> str | None:
        """The role that ADMITS at the gate, or None. Only active/invited admit;
        requested (and absent) are denied."""
        cur = await conn.execute(
            "SELECT role FROM user_roles WHERE email = ? AND status IN ('active','invited')",
            (_norm(email),),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def list_members(
        self,
        conn: aiosqlite.Connection,
        *,
        role: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = [f"SELECT {', '.join(_COLS)} FROM user_roles"]
        where: list[str] = []
        args: list[Any] = []
        if role:
            where.append("role = ?")
            args.append(role)
        if status:
            where.append("status = ?")
            args.append(status)
        if query:
            where.append("(email LIKE ? OR lower(coalesce(display_name,'')) LIKE ?)")
            q = f"%{query.strip().lower()}%"
            args += [q, q]
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY (role='admin') DESC, email ASC")
        cur = await conn.execute(" ".join(sql), args)
        rows = await cur.fetchall()
        return [dict(zip(_COLS, r, strict=True)) for r in rows]

    async def upsert_role(
        self,
        conn: aiosqlite.Connection,
        email: str,
        role: str,
        *,
        status: str = "active",
        granted_by: str | None,
        display_name: str | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO user_roles(email, role, status, display_name, granted_by, granted_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(email) DO UPDATE SET
                role = excluded.role,
                status = excluded.status,
                display_name = COALESCE(excluded.display_name, user_roles.display_name),
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            """,
            (_norm(email), role, status, display_name, granted_by and _norm(granted_by)),
        )
        await conn.commit()

    async def record_request(
        self, conn: aiosqlite.Connection, email: str, *, display_name: str | None = None
    ) -> None:
        """Record an access request from the denial page. No-op if the user
        already has any row (an active/invited user shouldn't be downgraded; a
        repeat request stays a single pending row)."""
        existing = await self.get(conn, email)
        if existing is not None:
            return
        await conn.execute(
            "INSERT INTO user_roles(email, role, status, display_name, granted_by, granted_at) "
            "VALUES (?, 'member', 'requested', ?, NULL, datetime('now'))",
            (_norm(email), display_name),
        )
        await conn.commit()

    async def activate_on_first_sight(self, conn: aiosqlite.Connection, email: str) -> None:
        """Flip invited→active on first authenticated sight. The WHERE clause
        only matches an 'invited' row, so this writes exactly once (on first
        sign-in) and is a no-op thereafter — keeping Litestream write churn low
        (perf discipline). Last-seen tracking was removed; see ADR 0090."""
        await conn.execute(
            "UPDATE user_roles SET status = 'active' WHERE email = ? AND status = 'invited'",
            (_norm(email),),
        )
        await conn.commit()

    async def count_admins(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM user_roles WHERE role='admin' AND status IN ('active','invited')"
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def delete(self, conn: aiosqlite.Connection, email: str) -> int:
        cur = await conn.execute("DELETE FROM user_roles WHERE email = ?", (_norm(email),))
        await conn.commit()
        return cur.rowcount

    async def seed_admins(self, conn: aiosqlite.Connection, emails: list[str]) -> None:
        """Guarantee deploy-time admins are active/admin on every boot.

        ADMIN_EMAILS is the break-glass owner list: these accounts must always be
        able to reach the admin console, so seeding force-upserts them to
        active/admin even if a row already exists in another status (a prior
        'requested' from the denial page, or a console demotion). The WHERE guard
        makes it a no-op write when the row is already correct, so a steady-state
        boot writes nothing (keeps Litestream churn down). Human-set fields like
        display_name are left untouched."""
        for e in emails:
            await conn.execute(
                """
                INSERT INTO user_roles(email, role, status, granted_by, granted_at)
                VALUES (?, 'admin', 'active', 'bootstrap', datetime('now'))
                ON CONFLICT(email) DO UPDATE SET
                    role = 'admin',
                    status = 'active',
                    granted_by = 'bootstrap',
                    granted_at = datetime('now')
                WHERE user_roles.role != 'admin' OR user_roles.status != 'active'
                """,
                (_norm(e),),
            )
        await conn.commit()
