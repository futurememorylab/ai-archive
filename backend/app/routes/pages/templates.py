"""Shared Jinja2Templates instance for the page routers.

Registers the `smpte` global and the `bytes_human` / `comma` filters so
all route modules reach the same configured `templates` without duplicating
setup.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from backend.app.timecode import secs_to_smpte

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["smpte"] = secs_to_smpte


def _bytes_human(n: int | None) -> str:
    if not n:
        return "0 B"
    n = int(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _comma(n: int | None) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}"


templates.env.filters["bytes_human"] = _bytes_human
templates.env.filters["comma"] = _comma
