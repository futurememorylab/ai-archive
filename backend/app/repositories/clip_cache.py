from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _tc_to_dict(tc: Timecode) -> dict[str, Any]:
    return {"secs": tc.secs, "fps": tc.fps, "frm": tc.frm, "txt": tc.txt}


def _marker_to_dict(m: Marker) -> dict[str, Any]:
    return {
        "name": m.name,
        "in_": _tc_to_dict(m.in_),
        "out": _tc_to_dict(m.out) if m.out is not None else None,
        "description": m.description,
        "category": m.category,
        "color": m.color,
    }


def _clip_to_json(clip: CanonicalClip) -> str:
    payload = {
        "key": list(clip.key),
        "name": clip.name,
        "duration_secs": clip.duration_secs,
        "fps": clip.fps,
        "markers": [_marker_to_dict(m) for m in clip.markers],
        "fields": {
            k: {"identifier": v.identifier, "value": v.value, "is_multi": v.is_multi}
            for k, v in clip.fields.items()
        },
        "notes": dict(clip.notes),
        "media": {
            "mime_type": clip.media.mime_type,
            "size_bytes": clip.media.size_bytes,
            "cached_path": str(clip.media.cached_path) if clip.media.cached_path else None,
            "upstream_handle": clip.media.upstream_handle,
        },
        "provider_data": clip.provider_data,
        "fetched_at": clip.fetched_at.isoformat(),
    }
    return json.dumps(payload, default=_json_default)


def _tc_from_dict(d: dict[str, Any]) -> Timecode:
    return Timecode(
        secs=float(d["secs"]),
        fps=float(d["fps"]),
        frm=d.get("frm"),
        txt=d.get("txt"),
    )


def _marker_from_dict(d: dict[str, Any]) -> Marker:
    return Marker(
        name=d["name"],
        in_=_tc_from_dict(d["in_"]),
        out=_tc_from_dict(d["out"]) if d.get("out") else None,
        description=d.get("description"),
        category=d.get("category"),
        color=d.get("color"),
    )


def _clip_from_json(raw: str) -> CanonicalClip:
    p = json.loads(raw)
    media = p["media"]
    return CanonicalClip(
        key=(p["key"][0], p["key"][1]),
        name=p["name"],
        duration_secs=float(p["duration_secs"]),
        fps=float(p["fps"]),
        markers=tuple(_marker_from_dict(m) for m in p["markers"]),
        fields={
            k: FieldValue(
                identifier=v["identifier"],
                value=v["value"],
                is_multi=bool(v.get("is_multi", False)),
            )
            for k, v in p["fields"].items()
        },
        notes=dict(p["notes"]),
        media=MediaRef(
            mime_type=media["mime_type"],
            size_bytes=media["size_bytes"],
            cached_path=Path(media["cached_path"]) if media["cached_path"] else None,
            upstream_handle=media["upstream_handle"],
        ),
        provider_data=p["provider_data"],
        fetched_at=datetime.fromisoformat(p["fetched_at"]),
    )


_ROW_COLS = (
    "provider_id",
    "provider_clip_id",
    "name",
    "catalog_id",
    "duration_secs",
    "fps",
    "canonical_json",
    "provider_etag",
    "fetched_at",
    "pinned_to_workspace_id",
)


class ClipCacheRepo:
    """DB-backed local mirror of upstream clip state."""

    async def upsert(
        self,
        conn: aiosqlite.Connection,
        *,
        clip: CanonicalClip,
        catalog_id: str,
        provider_etag: str | None = None,
    ) -> None:
        provider_id, provider_clip_id = clip.key
        await conn.execute(
            """
            INSERT INTO clip_cache
              (provider_id, provider_clip_id, name, catalog_id,
               duration_secs, fps, canonical_json, provider_etag, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, provider_clip_id) DO UPDATE SET
              name           = excluded.name,
              catalog_id     = excluded.catalog_id,
              duration_secs  = excluded.duration_secs,
              fps            = excluded.fps,
              canonical_json = excluded.canonical_json,
              provider_etag  = excluded.provider_etag,
              fetched_at     = excluded.fetched_at
            """,
            (
                provider_id,
                provider_clip_id,
                clip.name,
                catalog_id,
                clip.duration_secs,
                clip.fps,
                _clip_to_json(clip),
                provider_etag,
                clip.fetched_at.isoformat(),
            ),
        )
        await conn.commit()

    async def get_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> CanonicalClip | None:
        cur = await conn.execute(
            "SELECT canonical_json FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return _clip_from_json(row[0])

    async def get_row(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> dict[str, Any] | None:
        cur = await conn.execute(
            f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(_ROW_COLS, row))

    async def list_by_catalog(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        catalog_id: str,
        offset: int | None = None,
        limit: int | None = None,
        q: str | None = None,
        canonical: bool = False,
    ):
        """Two modes:

        - Legacy (no offset/limit/canonical): returns ``list[dict]`` of raw
          rows for callers like ``CacheInspector.deep_orphans``.
        - Paginated/canonical (kwargs provided): returns
          ``(tuple[CanonicalClip, ...], total: int)`` filtered by an
          optional substring ``q`` against ``name`` and the cached blob's
          ``notes`` map.
        """
        if not canonical and offset is None and limit is None and q is None:
            cur = await conn.execute(
                f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache "
                "WHERE provider_id = ? AND catalog_id = ?",
                (provider_id, catalog_id),
            )
            return [dict(zip(_ROW_COLS, row)) for row in await cur.fetchall()]

        params: list = [provider_id, catalog_id]
        where = "provider_id = ? AND catalog_id = ?"
        if q:
            where += " AND (LOWER(name) LIKE ? OR LOWER(canonical_json) LIKE ?)"
            needle = f"%{q.lower()}%"
            params.extend([needle, needle])

        count_cur = await conn.execute(
            f"SELECT COUNT(*) FROM clip_cache WHERE {where}", tuple(params)
        )
        total_row = await count_cur.fetchone()
        total = int(total_row[0]) if total_row else 0

        page_sql = (
            f"SELECT {', '.join(_ROW_COLS)} FROM clip_cache WHERE {where} "
            "ORDER BY provider_clip_id ASC LIMIT ? OFFSET ?"
        )
        page_params = tuple(params) + (int(limit or 50), int(offset or 0))
        cur = await conn.execute(page_sql, page_params)
        rows = await cur.fetchall()

        items: list[CanonicalClip] = []
        for row in rows:
            row_dict = dict(zip(_ROW_COLS, row))
            blob = row_dict.get("canonical_json")
            if blob:
                items.append(_clip_from_json(blob))
        return tuple(items), total

    async def delete_by_key(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> None:
        await conn.execute(
            "DELETE FROM clip_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        )
        await conn.commit()
