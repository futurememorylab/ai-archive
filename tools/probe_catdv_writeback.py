"""Live CatDV writeback probe (#123, Task 1).

Settles the rows still marked **unverified** in the spec's CatDV-9.3 facts
table, so later tasks can rely on them instead of guessing:

  * "Supplying some ``fields`` keys leaves the other keys untouched."
  * "Clearing a field (sending ``""`` / ``null``)."

and confirms, against the real server, the two we only *observed*:

  * a supplied ``markers`` array replaces the clip's whole marker list, and
  * a human (non-``AI``) marker on the clip survives when we PUT our own
    ``category == "AI"`` marker next to it.

THIS SCRIPT SPENDS THE CATDV LICENSE SEAT. It opens a session (``POST
/session``) and must be run by hand, against a disposable **scratch clip**,
never in CI and never automatically. It records every change it makes and
restores the clip's original markers and fields before it exits.

The seat is the one rule that bites (AGENTS.md): the probe therefore issues
``DELETE /session`` from a ``finally`` so the seat is released even when a
step raises half-way through. ``tests/unit/test_probe_catdv_writeback.py``
pins that contract with a mocked transport.

Usage::

    .venv/bin/python -m tools.probe_catdv_writeback --clip-id 12345

Creds come from the app ``Settings`` (env / ``.env``); pass ``--base-url`` /
``--username`` / ``--password`` to override.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from backend.app.services.catdv_client import CatdvClient

# The probe is a standalone, run-by-hand tool, so it carries its own literals
# rather than importing the canonical ownership constants (AI_MARKER_CATEGORY /
# AI_OWNED_FIELDS land in archive/model.py in Task 2). They are deliberately the
# same strings: the probe writes only the one field the app owns and tags only
# the one marker category it owns, then restores both.
AI_MARKER_CATEGORY = "AI"
PROBE_FIELD = "pragafilm.popis.ai"
PROBE_FIELD_VALUE = "probe-writeback-ai-value"


@dataclass
class ProbeResult:
    """What the probe learned. Each ``finding`` is a human-readable line for
    the spec's facts table; ``restored`` says whether the clip was returned to
    its original state."""

    findings: list[str] = field(default_factory=list)
    restored: bool = False
    clip_id: int | None = None

    def record(self, line: str) -> None:
        self.findings.append(line)


async def _get_clip(client: CatdvClient, clip_id: int) -> dict[str, Any]:
    """GET one clip including its markers and user fields.

    The bulk list endpoint omits both by default (see CatdvClient.list_clips),
    so the per-clip GET asks for them explicitly with the documented
    ``include`` parameter.
    """
    env = await client._call_json_with_params(  # noqa: SLF001 — probe-only tool
        "GET",
        f"/catdv/api/9/clips/{clip_id}",
        params={"include": "markers,clip.fields"},
    )
    return env.data or {}


def _human_markers(markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Markers the app does NOT own: anything whose category is not ``AI``."""
    return [m for m in markers if m.get("category") != AI_MARKER_CATEGORY]


async def run_probe(client: CatdvClient, clip_id: int) -> ProbeResult:
    """Open a session, probe the four facts on ``clip_id``, restore, and always
    release the seat.

    The ``finally`` is the load-bearing part: whatever happens in the body —
    a failed assertion, a dropped connection, a CatDV error — the session is
    deleted so the seat is not leaked. Everything the probe writes is captured
    first and written back before the normal return.
    """
    result = ProbeResult(clip_id=clip_id)
    await client.login()  # POST /session — takes the seat
    try:
        original = await _get_clip(client, clip_id)
        original_markers = list(original.get("markers") or [])
        original_fields = dict(original.get("fields") or {})
        human_before = _human_markers(original_markers)
        result.record(
            f"clip {clip_id}: {len(original_markers)} markers "
            f"({len(human_before)} non-AI), {len(original_fields)} fields"
        )

        # Fact: a supplied `markers` array replaces the whole list, and a human
        # marker survives when our AI marker is PUT next to it.
        ai_marker = {
            "name": "[probe] writeback probe marker",
            "category": AI_MARKER_CATEGORY,
            "in": (original_markers[0].get("in") if original_markers else {"frm": 0}),
            "description": "written by tools/probe_catdv_writeback.py",
        }
        await client.put_clip(clip_id, {"markers": human_before + [ai_marker]})
        after_markers = (await _get_clip(client, clip_id)).get("markers") or []
        human_after = _human_markers(after_markers)
        if human_after == human_before:
            result.record("markers: human markers are byte-identical after an AI-marker PUT")
        else:
            result.record(
                "markers: MISMATCH — human markers changed after PUT "
                f"(before={human_before!r} after={human_after!r})"
            )

        # Fact: supplying one `fields` key leaves the other keys untouched.
        await client.put_clip(clip_id, {"fields": {PROBE_FIELD: PROBE_FIELD_VALUE}})
        after_fields = (await _get_clip(client, clip_id)).get("fields") or {}
        other_before = {k: v for k, v in original_fields.items() if k != PROBE_FIELD}
        other_after = {k: v for k, v in after_fields.items() if k != PROBE_FIELD}
        if other_after == other_before:
            result.record(
                f"fields: the other keys are untouched after writing only {PROBE_FIELD!r} "
                "(per-key merge CONFIRMED)"
            )
        else:
            result.record(
                "fields: MISMATCH — other keys changed after a single-key PUT "
                f"(before={other_before!r} after={other_after!r})"
            )

        # Fact: clearing a field (send "").
        await client.put_clip(clip_id, {"fields": {PROBE_FIELD: ""}})
        cleared = (await _get_clip(client, clip_id)).get("fields") or {}
        result.record(
            f'clear: after PUT {PROBE_FIELD!r}="", GET returns {cleared.get(PROBE_FIELD)!r}'
        )

        # Restore the clip to exactly what we found.
        await client.put_clip(
            clip_id,
            {"markers": original_markers, "fields": original_fields},
        )
        result.restored = True
        result.record("restore: original markers and fields written back")
    finally:
        await client.logout()  # DELETE /session — releases the seat, always
    return result


async def _amain(args: argparse.Namespace) -> int:
    from backend.app.settings import Settings

    settings = Settings()  # type: ignore[call-arg]
    base_url = args.base_url or settings.catdv_base_url
    username = args.username or settings.catdv_username
    password = args.password or settings.catdv_password
    if not username or not password:
        print("CatDV username/password not configured; pass --username/--password", file=sys.stderr)
        return 2

    client = CatdvClient(base_url=base_url, username=username, password=password)
    async with client:
        result = await run_probe(client, args.clip_id)

    print("# CatDV writeback probe results")
    print(json.dumps({"clip_id": result.clip_id, "restored": result.restored}, indent=2))
    for line in result.findings:
        print(f"- {line}")
    if not result.restored:
        print("WARNING: clip was NOT restored — inspect it by hand", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip-id", type=int, required=True, help="scratch clip to probe")
    parser.add_argument("--base-url", default=None, help="override CATDV_BASE_URL")
    parser.add_argument("--username", default=None, help="override CATDV_USERNAME")
    parser.add_argument("--password", default=None, help="override CATDV_PASSWORD")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":  # pragma: no cover — manual entry point
    raise SystemExit(main())
