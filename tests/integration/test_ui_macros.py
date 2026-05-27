from jinja2 import Environment, FileSystemLoader
from pathlib import Path

TPL = Path(__file__).resolve().parents[2] / "backend" / "app" / "templates"

def _env():
    return Environment(loader=FileSystemLoader(str(TPL)), autoescape=True)

def test_button_renders_anchor_and_button():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.button('Save', variant='primary') }}|"
        "{{ ui.button('Go', href='/x', variant='ghost', size='sm') }}"
    )
    out = t.render()
    assert '<button' in out and 'class="btn primary"' in out and 'Save' in out
    assert '<a ' in out and 'href="/x"' in out and 'class="btn ghost sm"' in out

def test_textarea_field_passes_input_attrs():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.textarea_field('Body', 'body', value='hi', input_attrs='x-model=\"d.body\"') }}"
    )
    out = t.render()
    assert 'class="field-label"' in out and 'Body' in out
    assert 'class="txt-area' in out and 'x-model="d.body"' in out and '>hi</textarea>' in out
