"""Adapter: (StudioRun, PromptVersion) → panels dict for _anno_panels.html.

Maps:
  output_json["scenes"][]            → panels["markers"]
  other top-level output_json keys   → panels["fields"] (identifier via target_map)
  no notes / big_notes in v1         → both None
"""

from __future__ import annotations

from typing import Any

from backend.app.models.prompt import PromptVersion
from backend.app.models.studio import StudioRun

EMPTY_PANELS: dict[str, Any] = {
    "markers": [],
    "fields": [],
    "notes": None,
    "big_notes": None,
}


def panels_from_studio_run(
    run: StudioRun | None,
    version: PromptVersion | None,
    fps: float,
) -> dict[str, Any]:
    """Return the panels dict consumed by templates/pages/_anno_panels.html.

    Defensive: when run or version is None (or the run hasn't completed
    successfully), returns empty panels — the surrounding template handles
    the empty-state copy.
    """
    if run is None or version is None or not run.output_json:
        return {**EMPTY_PANELS, "fps": fps}

    out = run.output_json
    scenes = out.get("scenes") or []
    markers = [
        {
            "in_secs": s.get("in_secs"),
            "out_secs": s.get("out_secs"),
            "name": s.get("name") or "",
            "description": s.get("description"),
            "category": s.get("category"),
        }
        for s in scenes
        if s.get("in_secs") is not None
    ]

    # target_map is a TargetMap RootModel — its .root is dict[str, TargetEntry].
    tmap = version.target_map.root if version.target_map else {}

    fields: list[dict[str, Any]] = []
    for key, value in out.items():
        if key == "scenes":
            continue
        entry = tmap.get(key)
        identifier = entry.identifier if (entry and entry.identifier) else key
        fields.append({"identifier": identifier, "value": value})

    return {
        "markers": markers,
        "fields": fields,
        "notes": None,
        "big_notes": None,
        "fps": fps,
    }
