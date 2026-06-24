"""H4 + prompt-count N+1: pin the Admin → Prompts tab against N+1 query regressions.

`_prompts_view` once called `stats_by_resolution(...)` AND `model_config.get(...)`
inside the per-version loop → O(versions) queries. It now collects every
version first, then resolves calibration stats (stats_by_resolution_many),
usage totals (totals_by_prompt_version), and model rate-cards (all_live) with a
fixed number of batched/prefetched reads. This test pins that the rendered
page's query count does NOT scale with the number of prompt versions.

A second test (test_prompts_tab_query_count_does_not_scale_with_prompts) pins
that the query count also does NOT scale with the number of PROMPTS — it guards
against the original per-prompt `get_with_versions` loop being reintroduced.
"""

import asyncio
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed_versions(db_path, n_versions: int) -> None:
    """Insert ONE prompt with N versions. Keeping it a single prompt holds the
    per-prompt list_active/get_with_versions loop constant, so the render's
    query-count delta isolates the per-VERSION reads (the ones the H4 bug
    added) — those must stay batched/flat."""
    import aiosqlite

    from backend.app.repositories.prompts import PromptsRepo

    async with aiosqlite.connect(db_path) as conn:
        repo = PromptsRepo()
        pid, vid = await repo.create_with_initial_version(
            conn,
            name="PerfPrompt",
            description="perf prompt",
            body="Identify scenes.",
            target_map={"scenes": {"kind": "markers"}},
            output_schema={"type": "object"},
            model="gemini-2.5-flash-lite",
            media_resolution="low",
        )
        for _ in range(n_versions - 1):
            await repo.create_version(conn, pid, from_version_id=vid)
        await conn.commit()


async def _seed_prompts(db_path, n_prompts: int) -> None:
    """Insert N prompts each with 1 version. Varying prompt count with a
    constant version-per-prompt ratio isolates per-PROMPT scaling — the
    per-prompt `get_with_versions` N+1 that versions_by_prompt_ids replaces."""
    import aiosqlite

    from backend.app.repositories.prompts import PromptsRepo

    async with aiosqlite.connect(db_path) as conn:
        repo = PromptsRepo()
        for i in range(n_prompts):
            await repo.create_with_initial_version(
                conn,
                name=f"PerfPrompt{i}",
                description=f"perf prompt {i}",
                body="Identify scenes.",
                target_map={"scenes": {"kind": "markers"}},
                output_schema={"type": "object"},
                model="gemini-2.5-flash-lite",
                media_resolution="low",
            )
        await conn.commit()


def _count_render(monkeypatch, tmp_path, n: int) -> int:
    """Boot a fresh app, seed 1 prompt with N versions, render /admin/prompts,
    return the SQL statement count for that render."""
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_versions(tmp_path / "app.db", n))
        ctx = client.app.state.core_ctx

        db = ctx.db
        orig_execute = db.execute
        orig_executemany = db.executemany
        stmt_count = 0

        async def _count_execute(*args, **kwargs):
            nonlocal stmt_count
            stmt_count += 1
            return await orig_execute(*args, **kwargs)

        async def _count_executemany(*args, **kwargs):
            nonlocal stmt_count
            stmt_count += 1
            return await orig_executemany(*args, **kwargs)

        db.execute = _count_execute  # type: ignore[method-assign]
        db.executemany = _count_executemany  # type: ignore[method-assign]
        try:
            r = client.get("/admin/prompts")
        finally:
            db.execute = orig_execute  # type: ignore[method-assign]
            db.executemany = orig_executemany  # type: ignore[method-assign]

        assert r.status_code == 200, r.text
    return stmt_count


def _count_render_n_prompts(monkeypatch, tmp_path, n: int) -> int:
    """Boot a fresh app, seed N prompts (1 version each), render /admin/prompts,
    return the SQL statement count for that render."""
    with _client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_prompts(tmp_path / "app.db", n))
        ctx = client.app.state.core_ctx

        db = ctx.db
        orig_execute = db.execute
        orig_executemany = db.executemany
        stmt_count = 0

        async def _count_execute(*args, **kwargs):
            nonlocal stmt_count
            stmt_count += 1
            return await orig_execute(*args, **kwargs)

        async def _count_executemany(*args, **kwargs):
            nonlocal stmt_count
            stmt_count += 1
            return await orig_executemany(*args, **kwargs)

        db.execute = _count_execute  # type: ignore[method-assign]
        db.executemany = _count_executemany  # type: ignore[method-assign]
        try:
            r = client.get("/admin/prompts")
        finally:
            db.execute = orig_execute  # type: ignore[method-assign]
            db.executemany = orig_executemany  # type: ignore[method-assign]

        assert r.status_code == 200, r.text
    return stmt_count


def test_prompts_tab_query_count_does_not_scale(monkeypatch, tmp_path):
    """The /admin/prompts render issues the SAME number of SQL statements for a
    prompt with 5 versions as for one with 25 — proving the per-version
    calibration-stats / model-config N+1 is gone (H4). With a single prompt the
    per-prompt list/get loop is constant, so equality isolates the per-version
    reads: any that scale would break this.
    """
    sub5 = tmp_path / "n5"
    sub5.mkdir()
    sub25 = tmp_path / "n25"
    sub25.mkdir()
    c5 = _count_render(monkeypatch, sub5, 5)
    c25 = _count_render(monkeypatch, sub25, 25)

    assert c5 == c25, (
        f"prompts-tab query count differs across version count "
        f"(5→{c5}, 25→{c25}); the render is no longer O(1) in prompt versions. "
        "A per-version calibration/cost/model-config N+1 may be back. "
        "See ADR 0046."
    )


def test_prompts_tab_query_count_does_not_scale_with_prompts(monkeypatch, tmp_path):
    """The /admin/prompts render issues the SAME number of SQL statements for
    5 prompts (1 version each) as for 25 prompts (1 version each) — proving
    the per-prompt `get_with_versions` N+1 is gone. Equality of statement
    counts with varying prompt count proves versions_by_prompt_ids batches
    the fetch into a constant number of queries regardless of prompt count.
    See ADR 0046.
    """
    sub5 = tmp_path / "p5"
    sub5.mkdir()
    sub25 = tmp_path / "p25"
    sub25.mkdir()
    c5 = _count_render_n_prompts(monkeypatch, sub5, 5)
    c25 = _count_render_n_prompts(monkeypatch, sub25, 25)

    assert c5 == c25, (
        f"prompts-tab query count differs across prompt count "
        f"(5 prompts→{c5}, 25 prompts→{c25}); the render is no longer O(1) in "
        "prompt count. The per-prompt get_with_versions N+1 may be back. "
        "See ADR 0046."
    )
