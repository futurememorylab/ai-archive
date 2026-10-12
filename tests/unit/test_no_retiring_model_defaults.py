"""No shipped default may point at a Gemini model Google is retiring.

The Gemini 2.5 family (including the old default gemini-2.5-flash-lite)
retires on Vertex not before 2026-10-16. A default that names it makes every
new prompt, fresh DB and Live summary fail once Google switches it off. The
2.5 models stay selectable (old prompt versions reference them and their rate
cards price historical runs); they just can't be what we hand out by default.
"""

import json
from pathlib import Path

from backend.app.enums.registry import ENUM_REGISTRY
from backend.app.settings import Settings

RETIRING_PREFIXES = ("gemini-2.5-",)
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"


def _retiring(model: str) -> bool:
    return model.startswith(RETIRING_PREFIXES)


def test_registry_default_generation_model_is_not_retiring():
    spec = ENUM_REGISTRY["gemini_generation_model"]
    (default,) = [v.value for v in spec.values if v.default]
    assert not _retiring(default), default


def test_settings_default_gemini_model_is_not_retiring():
    default = Settings.model_fields["gemini_model"].default
    assert not _retiring(default), default


def test_seed_prompts_do_not_use_retiring_models():
    offenders = {
        p.name: json.loads(p.read_text())["model"]
        for p in SEEDS.glob("*.json")
        if "model" in json.loads(p.read_text()) and _retiring(json.loads(p.read_text())["model"])
    }
    assert not offenders, offenders
