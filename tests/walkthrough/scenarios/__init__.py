"""Scenario discovery — every module here with a SLUG/TITLE/run is a scenario."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def load_scenarios() -> list[ModuleType]:
    """Import every sibling module exposing SLUG + run(), sorted by SLUG."""
    mods: list[ModuleType] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "SLUG") and hasattr(mod, "run"):
            mods.append(mod)
    return sorted(mods, key=lambda m: m.SLUG)


def get_scenario(slug: str) -> ModuleType:
    for m in load_scenarios():
        if m.SLUG == slug:
            return m
    raise KeyError(f"no scenario with slug {slug!r}")
