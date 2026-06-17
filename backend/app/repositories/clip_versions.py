"""ClipVersionsRepo — the publish (commit) history for a clip. One immutable
row per publish; only publish_state / synced_at / failed_reason transition
(performed by the SyncEngine). Leaf repository — no service imports."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.annotation import ClipVersion
from backend.app.repositories._batch import chunked_in_clause

_COLS = (
    "id",
    "provider_id",
    "catdv_clip_id",
    "version_num",
    "parent_version_id",
    "snapshot",
    "diff",
    "origin",
    "model",
    "prompt_version_id",
    "annotation_id",
    "author",
    "publish_state",
    "expected_etag",
    "failed_reason",
    "synced_at",
    "created_at",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ClipVersionsRepo:
    async def insert(self, conn: aiosqlite.Connection, v: ClipVersion) -> int:
        cur = await conn.execute(
            """
            INSERT INTO clip_versions
              (provider_id, catdv_clip_id, version_num, parent_version_id,
               snapshot, diff, origin, model, prompt_version_id, annotation_id,
               author, publish_state, expected_etag, failed_reason, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                v.provider_id,
                v.catdv_clip_id,
                v.version_num,
                v.parent_version_id,
                json.dumps(v.snapshot, ensure_ascii=False),
                json.dumps(v.diff, ensure_ascii=False) if v.diff is not None else None,
                v.origin,
                v.model,
                v.prompt_version_id,
                v.annotation_id,
                v.author,
                v.publish_state,
                v.expected_etag,
                v.failed_reason,
                v.synced_at,
            ),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, version_id: int) -> ClipVersion:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM clip_versions WHERE id = ?", (version_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"clip_version {version_id} not found")
        return self._row(row)

    async def next_version_num(
        self, conn: aiosqlite.Connection, clip_id: int, *, provider_id: str = "catdv"
    ) -> int:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(version_num), 0) + 1 FROM clip_versions "
            "WHERE provider_id = ? AND catdv_clip_id = ?",
            (provider_id, clip_id),
        )
        row = await cur.fetchone()
        return int(row[0])

    async def list_by_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, provider_id: str = "catdv"
    ) -> list[ClipVersion]:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM clip_versions "
            "WHERE provider_id = ? AND catdv_clip_id = ? ORDER BY version_num DESC",
            (provider_id, clip_id),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def live_for_clip(
        self, conn: aiosqlite.Connection, clip_id: int, *, provider_id: str = "catdv"
    ) -> ClipVersion | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_COLS)} FROM clip_versions "
            "WHERE provider_id = ? AND catdv_clip_id = ? AND publish_state = 'live' "
            "ORDER BY version_num DESC LIMIT 1",
            (provider_id, clip_id),
        )
        row = await cur.fetchone()
        return self._row(row) if row is not None else None

    async def mark_publishing(self, conn: aiosqlite.Connection, version_id: int) -> None:
        """Move a version (back) into 'publishing' — used when re-activating an
        older version: it goes publishing → live once its re-write lands."""
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'publishing', failed_reason = NULL "
            "WHERE id = ?",
            (version_id,),
        )
        await conn.commit()

    async def mark_live(self, conn: aiosqlite.Connection, version_id: int) -> None:
        """Flip a version live and supersede the prior live for the same clip.

        Also supersedes any *other* rows still stuck in 'publishing' for the
        clip: when several publishes for one clip merge into a single PUT, only
        the freshest version is flipped live, so the older 'publishing' rows
        would otherwise orphan forever. Exactly one row per clip ends 'live'.
        See the publishing-logic audit, anomaly A4.
        """
        cur = await conn.execute(
            "SELECT provider_id, catdv_clip_id FROM clip_versions WHERE id = ?",
            (version_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return
        provider_id, clip_id = row[0], row[1]
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'superseded' "
            "WHERE provider_id = ? AND catdv_clip_id = ? "
            "AND publish_state IN ('live', 'publishing') AND id != ?",
            (provider_id, clip_id, version_id),
        )
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'live', synced_at = ? WHERE id = ?",
            (_now_iso(), version_id),
        )
        await conn.commit()

    async def mark_failed(
        self, conn: aiosqlite.Connection, version_id: int, *, reason: str
    ) -> None:
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'failed', failed_reason = ? WHERE id = ?",
            (reason, version_id),
        )
        await conn.commit()

    async def mark_conflict(
        self, conn: aiosqlite.Connection, version_id: int, *, reason: str | None = None
    ) -> None:
        await conn.execute(
            "UPDATE clip_versions SET publish_state = 'conflict', failed_reason = ? WHERE id = ?",
            (reason, version_id),
        )
        await conn.commit()

    async def newest_state_by_clip(
        self, conn: aiosqlite.Connection, clip_ids: list[int], *, provider_id: str = "catdv"
    ) -> dict[int, tuple[str, int]]:
        """Batched: {clip_id: (publish_state, version_num)} for the NEWEST
        version per clip. Backs the clips-list status badge without N+1."""
        out: dict[int, tuple[str, int]] = {}
        if not clip_ids:
            return out
        for fragment, params in chunked_in_clause((cid,) for cid in clip_ids):
            cur = await conn.execute(
                f"""
                SELECT cv.catdv_clip_id, cv.publish_state, cv.version_num
                  FROM clip_versions cv
                  JOIN (
                    SELECT catdv_clip_id, MAX(version_num) AS mx
                      FROM clip_versions
                     WHERE provider_id = ? AND catdv_clip_id IN ({fragment})
                     GROUP BY catdv_clip_id
                  ) m ON m.catdv_clip_id = cv.catdv_clip_id AND m.mx = cv.version_num
                 WHERE cv.provider_id = ?
                """,
                (provider_id, *params, provider_id),
            )
            for clip_id, state, num in await cur.fetchall():
                out[int(clip_id)] = (state, int(num))
        return out

    async def live_version_num_by_clip(
        self, conn: aiosqlite.Connection, clip_ids: list[int], *, provider_id: str = "catdv"
    ) -> dict[int, int]:
        """Batched: {clip_id: version_num} for the LIVE version per clip (clips
        with no live version are absent). Backs the 'Live vN' label without N+1;
        the publishing/failed signal comes from pending_operations, not here."""
        out: dict[int, int] = {}
        if not clip_ids:
            return out
        for fragment, params in chunked_in_clause((cid,) for cid in clip_ids):
            cur = await conn.execute(
                f"""
                SELECT catdv_clip_id, MAX(version_num)
                  FROM clip_versions
                 WHERE provider_id = ? AND publish_state = 'live'
                   AND catdv_clip_id IN ({fragment})
                 GROUP BY catdv_clip_id
                """,
                (provider_id, *params),
            )
            for clip_id, num in await cur.fetchall():
                out[int(clip_id)] = int(num)
        return out

    @staticmethod
    def _row(row) -> ClipVersion:
        return ClipVersion(
            id=row[0],
            provider_id=row[1],
            catdv_clip_id=row[2],
            version_num=row[3],
            parent_version_id=row[4],
            snapshot=json.loads(row[5]),
            diff=json.loads(row[6]) if row[6] is not None else None,
            origin=row[7],
            model=row[8],
            prompt_version_id=row[9],
            annotation_id=row[10],
            author=row[11],
            publish_state=row[12],
            expected_etag=row[13],
            failed_reason=row[14],
            synced_at=row[15],
            created_at=row[16],
        )
