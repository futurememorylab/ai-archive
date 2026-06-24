"""PromptsRepo — CRUD + state-machine for prompts and their versions.

Invariants (enforced here + by partial unique index `idx_one_prod_per_prompt`):
  * At most one production version per prompt.
  * Editing body/target_map/output_schema/model only when state='draft'.
  * Promoting a draft demotes the previous production to 'archived'
    atomically.
"""

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.models.prompt import Prompt, PromptVersion
from backend.app.repositories._batch import chunked_in_clause


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class VersionImmutableError(RuntimeError):
    """Raised when caller tries to edit a non-draft version."""

    def __init__(self, version_id: int, state: str):
        super().__init__(f"version {version_id} is in state {state!r} and cannot be edited")
        self.version_id = version_id
        self.state = state


def _target_map_to_json(target_map: Any) -> str:
    """Accept dict OR a TargetMap model and produce a JSON string.

    Uses exclude_unset=True when serializing a TargetMap model so that
    optional TargetEntry fields (identifier, target, mode) are omitted
    when not explicitly set. This keeps the stored JSON compact and
    ensures exclude_unset=True works correctly on the round-trip read.
    """
    if hasattr(target_map, "model_dump_json"):
        return target_map.model_dump_json(exclude_unset=True)
    return json.dumps(target_map)


def _row_to_prompt(row) -> Prompt:
    return Prompt(
        id=row[0],
        name=row[1],
        description=row[2],
        archived=bool(row[3]),
        created_at=row[4],
        updated_at=row[5],
        media_kind=row[6],
    )


def _row_to_version(row) -> PromptVersion:
    return PromptVersion(
        id=row[0],
        prompt_id=row[1],
        version_num=row[2],
        state=row[3],
        body=row[4],
        target_map=json.loads(row[5]),
        output_schema=json.loads(row[6]),
        model=row[7],
        media_resolution=row[8],
        created_at=row[9],
        updated_at=row[10],
    )


_PROMPT_COLS = "id, name, description, archived, created_at, updated_at, media_kind"
_VERSION_COLS = (
    "id, prompt_id, version_num, state, body, target_map, "
    "output_schema, model, media_resolution, created_at, updated_at"
)


class PromptsRepo:
    # ── prompt-level ────────────────────────────────────────────────────────

    async def create_with_initial_version(
        self,
        conn: aiosqlite.Connection,
        *,
        name: str,
        description: str | None,
        body: str,
        target_map: Any,
        output_schema: Any,
        model: str,
        media_resolution: str | None = None,
        initial_state: str = "draft",
        media_kind: str = "any",
    ) -> tuple[int, int]:
        """Create prompt + v1. Returns (prompt_id, version_id)."""
        now = _now_iso()
        cur = await conn.execute(
            "INSERT INTO prompts(name, description, archived, media_kind, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (name, description, media_kind, now, now),
        )
        prompt_id = cur.lastrowid
        assert prompt_id is not None
        cur = await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, media_resolution, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prompt_id,
                initial_state,
                body,
                _target_map_to_json(target_map),
                json.dumps(output_schema),
                model,
                media_resolution,
                now,
                now,
            ),
        )
        version_id = cur.lastrowid
        assert version_id is not None
        await conn.commit()
        return prompt_id, version_id

    async def get_with_versions(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> tuple[Prompt, list[PromptVersion]]:
        cur = await conn.execute(f"SELECT {_PROMPT_COLS} FROM prompts WHERE id = ?", (prompt_id,))
        prow = await cur.fetchone()
        if prow is None:
            raise LookupError(f"prompt {prompt_id} not found")
        cur = await conn.execute(
            f"SELECT {_VERSION_COLS} FROM prompt_versions "
            "WHERE prompt_id = ? ORDER BY version_num DESC",
            (prompt_id,),
        )
        versions = [_row_to_version(r) for r in await cur.fetchall()]
        return _row_to_prompt(prow), versions

    async def list_active(self, conn: aiosqlite.Connection) -> list[Prompt]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE archived = 0 ORDER BY name"
        )
        return [_row_to_prompt(r) for r in await cur.fetchall()]

    async def versions_by_prompt_ids(
        self,
        conn: aiosqlite.Connection,
        prompt_ids: list[int],
    ) -> dict[int, list[PromptVersion]]:
        """Return all versions for the given prompts in ONE chunked query.

        Returns a dict keyed by prompt_id; each value is a list of
        PromptVersion ordered by version_num DESC (same as get_with_versions).
        Prompt IDs with no versions map to an empty list.
        """
        result: dict[int, list[PromptVersion]] = {pid: [] for pid in prompt_ids}
        if not prompt_ids:
            return result
        for frag, params in chunked_in_clause([(pid,) for pid in prompt_ids]):
            cur = await conn.execute(
                f"SELECT {_VERSION_COLS} FROM prompt_versions "
                f"WHERE prompt_id IN ({frag}) "
                "ORDER BY prompt_id, version_num DESC",
                params,
            )
            for row in await cur.fetchall():
                v = _row_to_version(row)
                result[v.prompt_id].append(v)
        return result

    async def list_archived(self, conn: aiosqlite.Connection) -> list[Prompt]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE archived = 1 ORDER BY name"
        )
        return [_row_to_prompt(r) for r in await cur.fetchall()]

    async def get_by_name(
        self,
        conn: aiosqlite.Connection,
        name: str,
    ) -> Prompt | None:
        """Fetch a single prompt by unique `name`, or None if missing."""
        cur = await conn.execute(f"SELECT {_PROMPT_COLS} FROM prompts WHERE name = ?", (name,))
        row = await cur.fetchone()
        return _row_to_prompt(row) if row else None

    async def get_production_version(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
    ) -> PromptVersion | None:
        """Return the current production version of a prompt, or None."""
        cur = await conn.execute(
            f"SELECT {_VERSION_COLS} FROM prompt_versions "
            "WHERE prompt_id = ? AND state = 'production' LIMIT 1",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return _row_to_version(row) if row else None

    async def update_metadata(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        media_kind: str | None = None,
    ) -> None:
        sets, args = [], []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if description is not None:
            sets.append("description = ?")
            args.append(description)
        if media_kind is not None:
            sets.append("media_kind = ?")
            args.append(media_kind)
        if not sets:
            return
        sets.append("updated_at = ?")
        args.append(_now_iso())
        args.append(prompt_id)
        await conn.execute(f"UPDATE prompts SET {', '.join(sets)} WHERE id = ?", args)
        await conn.commit()

    async def archive(self, conn: aiosqlite.Connection, prompt_id: int) -> None:
        await conn.execute(
            "UPDATE prompts SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), prompt_id),
        )
        await conn.commit()

    async def restore(self, conn: aiosqlite.Connection, prompt_id: int) -> None:
        await conn.execute(
            "UPDATE prompts SET archived = 0, updated_at = ? WHERE id = ?",
            (_now_iso(), prompt_id),
        )
        await conn.commit()

    # ── version-level ───────────────────────────────────────────────────────

    async def get_version(self, conn: aiosqlite.Connection, version_id: int) -> PromptVersion:
        cur = await conn.execute(
            f"SELECT {_VERSION_COLS} FROM prompt_versions WHERE id = ?",
            (version_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"prompt_version {version_id} not found")
        return _row_to_version(row)

    async def _current_production_id(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> int | None:
        cur = await conn.execute(
            "SELECT id FROM prompt_versions WHERE prompt_id = ? AND state = 'production' LIMIT 1",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def _latest_version_id(self, conn: aiosqlite.Connection, prompt_id: int) -> int | None:
        cur = await conn.execute(
            "SELECT id FROM prompt_versions WHERE prompt_id = ? ORDER BY version_num DESC LIMIT 1",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def _max_version_num(self, conn: aiosqlite.Connection, prompt_id: int) -> int:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(version_num), 0) FROM prompt_versions WHERE prompt_id = ?",
            (prompt_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def create_version(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        from_version_id: int | None = None,
    ) -> int:
        """Clone a source version into a new draft. Returns new version_id.

        Source selection: explicit from_version_id > current production > latest.
        """
        if from_version_id is None:
            from_version_id = await self._current_production_id(
                conn, prompt_id
            ) or await self._latest_version_id(conn, prompt_id)
        if from_version_id is None:
            raise LookupError(f"prompt {prompt_id} has no versions to clone from")
        src = await self.get_version(conn, from_version_id)
        if src.prompt_id != prompt_id:
            raise LookupError(f"version {from_version_id} does not belong to prompt {prompt_id}")
        next_num = (await self._max_version_num(conn, prompt_id)) + 1
        now = _now_iso()
        cur = await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, "
            "target_map, output_schema, model, media_resolution, created_at, updated_at) "
            "VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)",
            (
                prompt_id,
                next_num,
                src.body,
                _target_map_to_json(src.target_map),
                json.dumps(src.output_schema),
                src.model,
                src.media_resolution,
                now,
                now,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        await conn.commit()
        return new_id

    async def update_version(
        self,
        conn: aiosqlite.Connection,
        version_id: int,
        *,
        body: str,
        target_map: Any,
        output_schema: Any,
        model: str,
        media_resolution: str | None = None,
    ) -> None:
        v = await self.get_version(conn, version_id)
        if v.state != "draft":
            raise VersionImmutableError(version_id, v.state)
        await conn.execute(
            "UPDATE prompt_versions SET body = ?, target_map = ?, output_schema = ?, "
            "model = ?, media_resolution = ?, updated_at = ? WHERE id = ?",
            (
                body,
                _target_map_to_json(target_map),
                json.dumps(output_schema),
                model,
                media_resolution,
                _now_iso(),
                version_id,
            ),
        )
        await conn.commit()

    async def promote_version(
        self, conn: aiosqlite.Connection, prompt_id: int, version_id: int
    ) -> None:
        """Atomically demote current production -> 'archived', set target -> 'production'.

        Only draft versions can be promoted. Promoting a production version is
        a no-op. Promoting an archived version raises VersionImmutableError.
        """
        target = await self.get_version(conn, version_id)
        if target.state == "production":
            return  # idempotent no-op
        if target.state != "draft":
            raise VersionImmutableError(version_id, target.state)
        now = _now_iso()
        # The partial unique index forbids two production rows existing at the
        # same instant, so we MUST archive the old one before promoting the
        # new one. Single transaction.
        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "UPDATE prompt_versions SET state = 'archived', updated_at = ? "
                "WHERE prompt_id = ? AND state = 'production' AND id != ?",
                (now, prompt_id, version_id),
            )
            await conn.execute(
                "UPDATE prompt_versions SET state = 'production', updated_at = ? "
                "WHERE id = ? AND prompt_id = ?",
                (now, version_id, prompt_id),
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def duplicate(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> tuple[int, int]:
        """Create a new prompt with v1 cloned from source's current production
        (fallback: latest). When ``name`` is omitted, walks to the next available
        ``Copy of <name>`` / ``Copy of <name> (n)``. When ``name`` is provided,
        used as-is and a UNIQUE collision raises ``aiosqlite.IntegrityError``.
        When ``description`` is omitted, copies the source's description.
        Returns (new_prompt_id, new_version_id).
        """
        src_prompt, _ = await self.get_with_versions(conn, prompt_id)
        src_version_id = await self._current_production_id(
            conn, prompt_id
        ) or await self._latest_version_id(conn, prompt_id)
        assert src_version_id is not None  # invariant: every prompt has >=1 version
        src_version = await self.get_version(conn, src_version_id)
        new_name = name if name is not None else await self._next_copy_name(conn, src_prompt.name)
        new_desc = description if description is not None else src_prompt.description
        return await self.create_with_initial_version(
            conn,
            name=new_name,
            description=new_desc,
            body=src_version.body,
            target_map=src_version.target_map,
            output_schema=src_version.output_schema,
            model=src_version.model,
            media_resolution=src_version.media_resolution,
            initial_state="draft",
            media_kind=src_prompt.media_kind,
        )

    async def _next_copy_name(self, conn: aiosqlite.Connection, src_name: str) -> str:
        base = f"Copy of {src_name}"
        candidate = base
        n = 2
        while True:
            cur = await conn.execute("SELECT 1 FROM prompts WHERE name = ?", (candidate,))
            if (await cur.fetchone()) is None:
                return candidate
            candidate = f"{base} ({n})"
            n += 1
