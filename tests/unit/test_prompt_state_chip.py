"""One shared prompt-state chip (draft / production / archived) used by
both the prompt editor and Studio. Production and archived are
read-only, so the chip carries a lock icon; draft (editable) does not.
Guards the macro's output and that both call sites use it."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path("backend/app/templates")


def _render_chip(state: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    tmpl = env.from_string(
        "{% import 'components/_ui.html' as ui %}{{ ui.prompt_state_chip(state) }}"
    )
    return tmpl.render(state=state)


def test_draft_chip_is_accent_and_has_no_lock():
    html = _render_chip("draft")
    assert "tag accent" in html
    assert "draft" in html
    assert "prompt-state-lock" not in html, "draft is editable — no lock"


def test_production_chip_is_good_and_locked():
    html = _render_chip("production")
    assert "tag good" in html
    assert "production" in html
    assert "prompt-state-lock" in html, "production is read-only — show lock"


def test_archived_chip_is_muted_and_locked():
    html = _render_chip("archived")
    assert "tag muted" in html
    assert "archived" in html
    assert "prompt-state-lock" in html, "archived is read-only — show lock"


def test_prompt_editor_uses_shared_chip():
    t = (TEMPLATES / "pages/_prompt_detail.html").read_text()
    assert "prompt_state_chip" in t, "prompt editor must use the shared chip"
    assert '<span class="tag good"><span class="dot"></span>production' not in t, (
        "inline production chip must be replaced by the macro"
    )


def test_studio_version_picker_uses_shared_chip():
    t = (TEMPLATES / "pages/_studio_version_picker.html").read_text()
    assert "prompt_state_chip" in t, "studio version picker must use the shared chip"
    assert "pc-status" not in t, "dead .pc-status chip must be removed"
