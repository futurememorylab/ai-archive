from datetime import datetime, timezone

from backend.app.models.prompt import Prompt, PromptVersion, TargetMap
from backend.app.routes.prompts import _prompt_envelope


def _now():
    return datetime.now(timezone.utc).isoformat()


def _prompt():
    return Prompt(
        id=1,
        name="P",
        description=None,
        archived=False,
        created_at=_now(),
        updated_at=_now(),
    )


def _version(state, n):
    return PromptVersion(
        id=10 + n,
        prompt_id=1,
        version_num=n,
        state=state,
        body="b",
        target_map=TargetMap({}),
        output_schema={},
        model="gemini-2.5-pro",
        created_at=_now(),
        updated_at=_now(),
    )


def test_envelope_exposes_production_version_when_one_exists():
    env = _prompt_envelope(_prompt(), [_version("draft", 2), _version("production", 1)])
    assert env["current_production_version_id"] == 11
    assert env["current_production_version_num"] == 1


def test_envelope_exposes_none_when_no_production_version():
    env = _prompt_envelope(_prompt(), [_version("draft", 1)])
    assert env["current_production_version_id"] is None
    assert env["current_production_version_num"] is None
