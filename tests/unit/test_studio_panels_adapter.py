"""Unit tests for panels_from_studio_run — the adapter that converts a
studio run's output_json + the prompt version's target_map into the
panels dict shape _anno_panels.html expects.
"""

from backend.app.models.prompt import PromptVersion, TargetMap
from backend.app.models.studio import StudioRun


def _version(target_map_dict: dict) -> PromptVersion:
    return PromptVersion(
        id=1, prompt_id=1, version_num=1, state="draft",
        body="x", target_map=TargetMap(target_map_dict),
        output_schema={}, model="gemini-2.5-pro",
        created_at="2026-05-26T00:00:00Z", updated_at="2026-05-26T00:00:00Z",
    )


def _ok_run(output_json: dict) -> StudioRun:
    return StudioRun(
        id=1, prompt_version_id=1, clip_id=12041, job_id=None,
        status="ok", output_json=output_json,
        duration_s=1.0, tokens_in=10, tokens_out=20, cost_usd=0.01,
        model="gemini-2.5-pro", error=None,
        started_at=None, finished_at="2026-05-27T00:00:00Z",
    )


def test_returns_empty_panels_when_run_is_none():
    from backend.app.services.studio_panels import panels_from_studio_run
    p = panels_from_studio_run(None, _version({}), fps=25.0)
    assert p == {"markers": [], "fields": [], "notes": None, "big_notes": None, "fps": 25.0}


def test_returns_empty_panels_when_version_is_none():
    from backend.app.services.studio_panels import panels_from_studio_run
    p = panels_from_studio_run(_ok_run({"scenes": []}), None, fps=25.0)
    assert p == {"markers": [], "fields": [], "notes": None, "big_notes": None, "fps": 25.0}


def test_scenes_become_markers():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [
        {"in_secs": 1.0, "out_secs": 2.5, "name": "a", "description": "d", "category": "c"},
        {"in_secs": 3.0, "out_secs": 4.0, "name": "b"},
    ]})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    assert len(p["markers"]) == 2
    m0, m1 = p["markers"]
    assert m0["in_secs"] == 1.0 and m0["out_secs"] == 2.5
    assert m0["name"] == "a" and m0["description"] == "d" and m0["category"] == "c"
    assert m1["in_secs"] == 3.0 and m1["out_secs"] == 4.0 and m1["name"] == "b"
    assert m1.get("description") in (None, "")
    assert m1.get("category") in (None, "")


def test_non_scenes_become_fields_via_target_map():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({
        "scenes": [],
        "summary_cz": "krátký souhrn",
        "decade": "1970s",
    })
    v = _version({
        "summary_cz": {"kind": "field", "identifier": "pragafilm.popis.materialu"},
        "decade":     {"kind": "field", "identifier": "pragafilm.dekada"},
    })
    p = panels_from_studio_run(run, v, fps=25.0)
    fields_by_identifier = {f["identifier"]: f["value"] for f in p["fields"]}
    assert fields_by_identifier == {
        "pragafilm.popis.materialu": "krátký souhrn",
        "pragafilm.dekada": "1970s",
    }


def test_missing_target_map_entry_falls_through_to_raw_key():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [], "leftover": "value"})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    assert p["fields"] == [{"identifier": "leftover", "value": "value"}]


def test_non_string_field_values_pass_through():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [], "count": 5, "tags": ["a", "b"]})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    values_by_key = {f["identifier"]: f["value"] for f in p["fields"]}
    assert values_by_key["count"] == 5
    assert values_by_key["tags"] == ["a", "b"]


def test_scene_with_no_out_secs():
    from backend.app.services.studio_panels import panels_from_studio_run
    run = _ok_run({"scenes": [{"in_secs": 1.0, "name": "a"}]})
    p = panels_from_studio_run(run, _version({}), fps=25.0)
    assert p["markers"][0]["in_secs"] == 1.0
    assert p["markers"][0]["out_secs"] is None
