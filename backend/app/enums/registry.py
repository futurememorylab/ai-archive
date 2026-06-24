"""Canonical declaration of every centralised enumeration.

Two kinds live here:
  * Fixed enums (editable=False) — code is the source of truth; values are
    served straight from this module (the DB is never consulted). They also
    keep their Literal type in models/ for static checking.
  * Editable enums (editable=True) — these `values` are the *seed*. The DB
    table `enum_values` stores the user's edits, reconciled against this seed
    at boot. See docs/superpowers/specs/2026-06-14-centralised-enumeration-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EnumValueSpec:
    value: str
    label: str | None = None
    default: bool = False  # editable enums: the seeded default pick
    metadata: dict | None = None  # forward-compat (region, rates, capabilities)


@dataclass(frozen=True)
class EnumSpec:
    key: str
    name: str
    description: str
    editable: bool
    values: tuple[EnumValueSpec, ...] = field(default_factory=tuple)


def _m(value: str, *, default: bool = False) -> EnumValueSpec:
    return EnumValueSpec(value=value, default=default)


ENUM_REGISTRY: dict[str, EnumSpec] = {
    "gemini_generation_model": EnumSpec(
        key="gemini_generation_model",
        name="Gemini generation models",
        description="Models offered when creating or editing a prompt.",
        editable=True,
        values=(
            _m("gemini-2.5-pro"),
            _m("gemini-2.5-flash"),
            _m("gemini-2.5-flash-lite", default=True),
            _m("gemini-3-flash-preview"),
            _m("gemini-3.1-pro-preview"),
            _m("gemini-3.1-flash-lite"),
            _m("gemini-3.1-flash-lite-preview"),
            _m("gemini-3.5-flash"),
        ),
    ),
    "toast_level": EnumSpec(
        key="toast_level",
        name="Toast levels",
        description="Severity levels for user-facing toast notifications.",
        editable=False,
        values=(
            EnumValueSpec("info"),
            EnumValueSpec("success"),
            EnumValueSpec("error"),
        ),
    ),
    "media_resolution": EnumSpec(
        key="media_resolution",
        name="Media resolutions",
        description="How much detail (and token cost) a clip's media gets in a Gemini call.",
        editable=False,
        values=(
            EnumValueSpec("low"),
            EnumValueSpec("medium"),
            EnumValueSpec("high"),
        ),
    ),
    "clip_publish_state": EnumSpec(
        key="clip_publish_state",
        name="Clip publish state",
        description="Headline status of a clip's annotation work versus CatDV.",
        editable=False,
        values=(
            EnumValueSpec("none"),
            EnumValueSpec("draft"),
            EnumValueSpec("publishing"),
            EnumValueSpec("live"),
            EnumValueSpec("failed"),
            EnumValueSpec("conflict"),
        ),
    ),
}
