"""StudioRunsRepo — runs + run items + crash-recovery sweep."""
from datetime import UTC, datetime

import aiosqlite

from backend.app.models.studio import StudioRun, StudioRunItem


def _now() -> str:
    return datetime.now(UTC).isoformat()


_RUN_COLS = "id, testbench_id, prompt_version_id, status, created_at, started_at, finished_at, notes"
_ITEM_COLS = (
    "id, run_id, testbench_item_id, status, error, unacceptable_reason, "
    "structured_json, raw_text, prompt_used, model, latency_ms, started_at, finished_at"
)


def _run(row) -> StudioRun:
    return StudioRun(
        id=row[0], testbench_id=row[1], prompt_version_id=row[2],
        status=row[3], created_at=row[4], started_at=row[5],
        finished_at=row[6], notes=row[7],
    )


def _ri(row) -> StudioRunItem:
    return StudioRunItem(
        id=row[0], run_id=row[1], testbench_item_id=row[2], status=row[3],
        error=row[4], unacceptable_reason=row[5], structured_json=row[6],
        raw_text=row[7], prompt_used=row[8], model=row[9], latency_ms=row[10],
        started_at=row[11], finished_at=row[12],
    )


class StudioRunsRepo:
    async def create(
        self, conn: aiosqlite.Connection,
        *, testbench_id: int, prompt_version_id: int,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO studio_runs (testbench_id, prompt_version_id, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (testbench_id, prompt_version_id, _now()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, id: int) -> StudioRun:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_runs WHERE id=?", (id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"studio_run {id} not found")
        return _run(row)

    async def list_for_testbench(
        self, conn: aiosqlite.Connection, testbench_id: int
    ) -> list[StudioRun]:
        cur = await conn.execute(
            f"SELECT {_RUN_COLS} FROM studio_runs WHERE testbench_id=? "
            "ORDER BY created_at DESC, id DESC",
            (testbench_id,),
        )
        return [_run(r) for r in await cur.fetchall()]

    async def update_status(
        self, conn: aiosqlite.Connection, id: int, status: str,
        *, started: bool = False, finished: bool = False,
    ) -> None:
        fields = ["status=?"]
        vals: list = [status]
        if started:
            fields.append("started_at=?")
            vals.append(_now())
        if finished:
            fields.append("finished_at=?")
            vals.append(_now())
        vals.append(id)
        await conn.execute(
            f"UPDATE studio_runs SET {', '.join(fields)} WHERE id=?", vals
        )
        await conn.commit()

    async def upsert_item(
        self, conn: aiosqlite.Connection,
        *, run_id: int, testbench_item_id: int,
    ) -> int:
        cur = await conn.execute(
            "SELECT id FROM studio_run_items WHERE run_id=? AND testbench_item_id=?",
            (run_id, testbench_item_id),
        )
        row = await cur.fetchone()
        if row:
            return row[0]
        cur = await conn.execute(
            "INSERT INTO studio_run_items (run_id, testbench_item_id, status, started_at) "
            "VALUES (?, ?, 'pending', ?)",
            (run_id, testbench_item_id, _now()),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def update_item_status(
        self, conn: aiosqlite.Connection, id: int, status: str,
        *, error: str | None = None, unacceptable_reason: str | None = None,
    ) -> None:
        fields = ["status=?"]
        vals: list = [status]
        if error is not None:
            fields.append("error=?")
            vals.append(error)
        if unacceptable_reason is not None:
            fields.append("unacceptable_reason=?")
            vals.append(unacceptable_reason)
        if status in ("done", "error", "unacceptable"):
            fields.append("finished_at=?")
            vals.append(_now())
        vals.append(id)
        await conn.execute(
            f"UPDATE studio_run_items SET {', '.join(fields)} WHERE id=?", vals,
        )
        await conn.commit()

    async def attach_output(
        self, conn: aiosqlite.Connection, id: int,
        *, structured_json: str | None, raw_text: str,
        prompt_used: str, model: str, latency_ms: int,
    ) -> None:
        await conn.execute(
            "UPDATE studio_run_items SET "
            "  structured_json=?, raw_text=?, prompt_used=?, model=?, latency_ms=?, "
            "  status='done', finished_at=? WHERE id=?",
            (structured_json, raw_text, prompt_used, model, latency_ms, _now(), id),
        )
        await conn.commit()

    async def list_items(
        self, conn: aiosqlite.Connection, run_id: int
    ) -> list[StudioRunItem]:
        cur = await conn.execute(
            f"SELECT {_ITEM_COLS} FROM studio_run_items WHERE run_id=? ORDER BY id",
            (run_id,),
        )
        return [_ri(r) for r in await cur.fetchall()]

    async def reset_transient(self, conn: aiosqlite.Connection) -> int:
        """Sweep runs left mid-flight by a crash: running → failed; transient
        item states + pending items inside failed runs → error('interrupted')."""
        cur = await conn.execute(
            "UPDATE studio_runs SET status='failed', finished_at=? "
            "WHERE status='running'",
            (_now(),),
        )
        n = cur.rowcount
        await conn.execute(
            "UPDATE studio_run_items SET status='error', "
            "error='interrupted by restart', finished_at=? "
            "WHERE status IN ('resolving','uploading','prompting','pending') "
            "AND run_id IN (SELECT id FROM studio_runs WHERE status='failed')",
            (_now(),),
        )
        await conn.commit()
        return n
