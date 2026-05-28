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

def test_textarea_field_cls_appends_single_class():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.textarea_field('Map', 'm', cls='json-editor', input_attrs='x-model=\"d.m\"') }}"
    )
    out = t.render()
    assert 'class="txt-area json-editor"' in out
    # textarea carries exactly one merged class attribute (no double class=)
    textarea = out[out.index('<textarea'):out.index('</textarea>')]
    assert textarea.count('class=') == 1
    assert 'x-model="d.m"' in out

def test_field_cls_appends_single_class():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.field('Name', 'n', cls='wide') }}"
    )
    out = t.render()
    assert 'class="txt wide"' in out

def test_field_help_renders_markup():
    env = _env()
    t = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{{ ui.field('Map', 'm', help='see <span class=\"code-inline\">x</span>') }}"
    )
    out = t.render()
    assert '<span class="code-inline">x</span>' in out

def test_page_header_renders_with_and_without_caller():
    env = _env()
    plain = env.from_string(
        "{% import 'components/_ui.html' as ui %}{{ ui.page_header('Cache', meta='3 items') }}"
    ).render()
    assert 'class="page-hdr"' in plain and '<h1>Cache</h1>' in plain and '3 items' in plain
    with_actions = env.from_string(
        "{% import 'components/_ui.html' as ui %}"
        "{% call ui.page_header('Cache') %}<button>Refresh</button>{% endcall %}"
    ).render()
    assert '<h1>Cache</h1>' in with_actions and '<button>Refresh</button>' in with_actions
