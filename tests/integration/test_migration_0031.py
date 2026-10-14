"""0031 moves the AI summary off the human field (#123): every production prompt
version whose target_map writes to pragafilm.popis.materialu gets a NEW
production version targeting pragafilm.popis.ai (replace); the old one is
archived, exactly like Create new version + Promote in the UI."""

import json
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SQL = (MIGRATIONS / "0031_summary_to_popis_ai.sql").read_text()

OLD_MAP = {
    "scenes": {"kind": "markers", "identifier": None, "target": None, "mode": "append"},
    "summary_cz": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"},
    "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
}


async def _prompt(conn, name, versions):
    cur = await conn.execute(
        "INSERT INTO prompts(name, description, archived, created_at, updated_at) "
        "VALUES (?, NULL, 0, '2026-01-01', '2026-01-01')",
        (name,),
    )
    pid = cur.lastrowid
    for num, state, tmap in versions:
        await conn.execute(
            "INSERT INTO prompt_versions(prompt_id, version_num, state, body, target_map, "
            "output_schema, model, media_resolution, created_at, updated_at) "
            "VALUES (?, ?, ?, 'body', ?, '{\"x\":1}', 'gemini-x', 'low', "
            "'2026-01-01', '2026-01-01')",
            (pid, num, state, json.dumps(tmap, ensure_ascii=False)),
        )
    await conn.commit()
    return pid


async def _versions(conn, pid):
    cur = await conn.execute(
        "SELECT version_num, state, target_map, body, output_schema, model, media_resolution "
        "FROM prompt_versions WHERE prompt_id = ? ORDER BY version_num",
        (pid,),
    )
    return [
        {
            "num": r[0],
            "state": r[1],
            "map": json.loads(r[2]),
            "body": r[3],
            "schema": r[4],
            "model": r[5],
            "res": r[6],
        }
        for r in await cur.fetchall()
    ]


@pytest.mark.asyncio
async def test_promotes_a_popis_ai_version_and_leaves_others(tmp_path):
    async with open_db(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)  # 0031 runs on an empty DB: no-op
        affected = await _prompt(conn, "a", [(1, "production", OLD_MAP), (2, "draft", OLD_MAP)])
        clean = await _prompt(conn, "b", [(1, "production", {"scenes": {"kind": "markers"}})])

        await conn.executescript(SQL)

        v = await _versions(conn, affected)
        assert [(x["num"], x["state"]) for x in v] == [
            (1, "archived"),
            (2, "draft"),  # an existing draft is left alone
            (3, "production"),
        ]
        new = v[2]
        assert new["map"]["summary_cz"] == {
            "kind": "note",
            "target": "pragafilm.popis.ai",
            "mode": "replace",
        }
        assert new["map"]["scenes"] == OLD_MAP["scenes"]
        assert new["map"]["decade"] == OLD_MAP["decade"]  # non-ASCII key value intact
        assert (new["body"], new["schema"], new["model"], new["res"]) == (
            "body",
            '{"x":1}',
            "gemini-x",
            "low",
        )

        assert [(x["num"], x["state"]) for x in await _versions(conn, clean)] == [(1, "production")]

        await conn.executescript(SQL)  # idempotent: nothing left to migrate
        assert len(await _versions(conn, affected)) == 3
