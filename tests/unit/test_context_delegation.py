"""Drift guard: every public ``CoreCtx`` field is reachable on ``LiveCtx``.

T3-A1 split the old monolithic ``AppContext`` into ``CoreCtx`` (always
present) + ``LiveCtx`` (composes a ``CoreCtx`` and adds live-only
services). ``LiveCtx`` re-exposes every ``CoreCtx`` field via a thin
``@property`` delegator so live-route handlers read both core and live
state off one object.

The hazard that delegation introduces: a NEW field added to ``CoreCtx``
without a matching ``LiveCtx`` delegator. Nothing fails at import time —
it only blows up when a live route (or the annotator's
``_run_in_bg(live, …)`` background path) reads ``live.<new_core_field>``
and hits ``AttributeError`` at runtime.

This test locks the invariant: the set of ``CoreCtx`` dataclass field
names must be a subset of the names reachable on ``LiveCtx`` (its own
dataclass fields + its ``@property`` delegators). Add a field to
``CoreCtx`` without a delegator and this goes red.
"""

import dataclasses

from backend.app.context import CoreCtx, LiveCtx


def _livectx_accessible_names() -> set[str]:
    field_names = {f.name for f in dataclasses.fields(LiveCtx)}
    property_names = {
        name for name, value in vars(LiveCtx).items() if isinstance(value, property)
    }
    return field_names | property_names


def test_core_fields_subset_of_live_accessible_names():
    core_fields = {f.name for f in dataclasses.fields(CoreCtx)}
    live_accessible = _livectx_accessible_names()

    missing = core_fields - live_accessible
    assert not missing, (
        "CoreCtx field(s) not reachable on LiveCtx — add a @property "
        f"delegator on LiveCtx for each: {sorted(missing)}. Without it, a "
        "live route or the annotator's _run_in_bg(live, …) path that reads "
        "ctx.<field> raises AttributeError at runtime (T3-A1)."
    )
