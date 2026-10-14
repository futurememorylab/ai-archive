"""The seeded prompt templates route the Czech summary to the one field the app
owns, `pragafilm.popis.ai`, in replace mode (#123).

Before this change the summary was appended to `pragafilm.popis.materialu`, a
human field — every re-publish stacked another block onto it. The summary now
replaces the AI-owned field instead, and the shared decade/year fields keep
their real targets (the ownership rule turns them into suggestions at write
time, not here).
"""

import json
from pathlib import Path

import pytest

from backend.app.archive.model import AI_OWNED_FIELDS

SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"
TEMPLATES = ["default_template.json", "image_template.json"]


def _target_map(name: str) -> dict:
    return json.loads((SEEDS / name).read_text())["target_map"]


@pytest.mark.parametrize("name", TEMPLATES)
def test_summary_routes_to_owned_field_in_replace_mode(name):
    tm = _target_map(name)
    summary = tm["summary_cz"]
    assert summary["kind"] == "note"
    assert summary["target"] == AI_OWNED_FIELDS[0] == "pragafilm.popis.ai"
    assert summary["mode"] == "replace"


@pytest.mark.parametrize("name", TEMPLATES)
def test_summary_no_longer_appends_to_human_materialu(name):
    tm = _target_map(name)
    assert tm["summary_cz"]["target"] != "pragafilm.popis.materialu"


@pytest.mark.parametrize("name", TEMPLATES)
def test_shared_fields_keep_their_real_targets(name):
    # decade/year stay mapped to their real shared fields; the ownership rule
    # (not the seed) demotes them to suggestions at write time.
    tm = _target_map(name)
    assert tm["decade"] == {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}
    assert tm["years"] == {"kind": "field", "identifier": "pragafilm.rok.natočení"}
