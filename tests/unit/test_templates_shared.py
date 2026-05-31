"""Tier-3 guardrail: exactly ONE Jinja2Templates construction in the codebase,
and all filters/globals are registered on that shared instance.

T3-A3: Consolidate four Jinja2Templates environments into one.
"""

from __future__ import annotations

from pathlib import Path


def test_bytes_human_comma_and_smpte_on_shared_env():
    """All three helpers resolve on the single shared templates env."""
    from backend.app.routes.pages.templates import templates

    result = templates.env.from_string(
        "{{ 1536|bytes_human }} {{ 1234|comma }} {{ smpte(1.0, 25.0) }}"
    ).render()

    assert result == "1.5 KB 1,234 00:00:01:00"


def test_exactly_one_jinja2templates_construction():
    """Guardrail: only one Jinja2Templates(...) instantiation under backend/app/."""
    root = Path(__file__).resolve().parents[2] / "backend" / "app"
    count = 0
    for py_file in root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        count += text.count("Jinja2Templates(")
    assert count == 1, (
        f"Expected exactly 1 Jinja2Templates(...) construction under backend/app/, "
        f"found {count}. Consolidate all instances into "
        f"backend/app/routes/pages/templates.py (T3-A3)."
    )
