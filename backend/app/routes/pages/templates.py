"""Shared Jinja2Templates instance for the page routers.

Registers the `smpte` global so feature routers under `routes/pages/` can
all reach the same configured `templates` without duplicating setup.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from backend.app.timecode import secs_to_smpte

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["smpte"] = secs_to_smpte
