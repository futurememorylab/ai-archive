"""PromptsRepo — CRUD + state-machine for prompts and their versions.

Invariants (enforced here + by partial unique index `idx_one_prod_per_prompt`):
  * At most one production version per prompt.
  * Editing body/target_map/output_schema/model only when state='draft'.
  * Promoting a draft demotes the previous production to 'archived'
    atomically.
"""
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.app.models.prompt import Prompt, PromptVersion


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionImmutableError(RuntimeError):
    """Raised when caller tries to edit a non-draft version."""

    def __init__(self, version_id: int, state: str):
        super().__init__(f"version {version_id} is in state {state!r} and cannot be edited")
        self.version_id = version_id
        self.state = state


def _target_map_to_json(target_map: Any) -> str:
    """Accept dict OR a TargetMap model and produce a JSON string."""
    if hasattr(target_map, "model_dump_json"):
        return target_map.model_dump_json()
    return json.dumps(target_map)


def _row_to_prompt(row) -> Prompt:
    return Prompt(
        id=row[0], name=row[1], description=row[2],
        archived=bool(row[3]), created_at=row[4], updated_at=row[5],
    )


def _row_to_version(row) -> PromptVersion:
    return PromptVersion(
        id=row[0], prompt_id=row[1], version_num=row[2], state=row[3],
        body=row[4], target_map=json.loads(row[5]),
        output_schema=json.loads(row[6]), model=row[7],
        created_at=row[8], updated_at=row[9],
    )


_PROMPT_COLS = "id, name, description, archived, created_at, updated_at"
_VERSION_COLS = (
    "id, prompt_id, version_num, state, body, target_map, "
    "output_schema, model, created_at, updated_at"
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
        initial_state: str = "draft",
    ) -> tuple[int, int]:
        """Create prompt + v1. Returns (prompt_id, version_id)."""
        now = _now_iso()
        cur = await conn.execute(
            "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (name, description, now, now),
        )
        prompt_id = cur.lastrowid
        assert prompt_id is not None
        cur = await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
            (
                prompt_id, initial_state, body,
                _target_map_to_json(target_map), json.dumps(output_schema),
                model, now, now,
            ),
        )
        version_id = cur.lastrowid
        assert version_id is not None
        await conn.commit()
        return prompt_id, version_id

    async def get_with_versions(
        self, conn: aiosqlite.Connection, prompt_id: int
    ) -> tuple[Prompt, list[PromptVersion]]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE id = ?", (prompt_id,)
        )
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

    async def list_archived(self, conn: aiosqlite.Connection) -> list[Prompt]:
        cur = await conn.execute(
            f"SELECT {_PROMPT_COLS} FROM prompts WHERE archived = 1 ORDER BY name"
        )
        return [_row_to_prompt(r) for r in await cur.fetchall()]

    async def update_metadata(
        self,
        conn: aiosqlite.Connection,
        prompt_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        sets, args = [], []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if description is not None:
            sets.append("description = ?")
            args.append(description)
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

    # ── version-level (Task 4 fills in create_version, update_version, promote, duplicate) ──

    async def get_version(
        self, conn: aiosqlite.Connection, version_id: int
    ) -> PromptVersion:
        cur = await conn.execute(
            f"SELECT {_VERSION_COLS} FROM prompt_versions WHERE id = ?",
            (version_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"prompt_version {version_id} not found")
        return _row_to_version(row)
