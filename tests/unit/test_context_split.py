"""T3-A1: AppContext is split into CoreCtx + LiveCtx.

These tests pin the type-level contract of the split:

* ``AppContext`` no longer exists on ``backend.app.context``.
* ``CoreCtx`` carries everything always-present — no Optional service
  fields (settings, db, repos, write_queue, event_bus, cache services).
* ``LiveCtx`` exposes the genuinely-external services; ``archive`` /
  ``ai_store`` / ``gemini`` are non-Optional (always built when
  ``init_external=True``). ``catdv`` is legitimately Optional (the app
  can boot "live" but with CatDV offline / auth-failed).
"""

from __future__ import annotations

import dataclasses

import backend.app.context as context_mod


def _raw_annotations(cls) -> dict[str, str]:
    """Raw annotation strings for a class.

    ``backend.app.context`` uses ``from __future__ import annotations`` plus
    TYPE_CHECKING-only imports for the heavy service types, so
    ``typing.get_type_hints`` can't evaluate them at runtime. We assert on the
    textual annotation instead — which is exactly what basedpyright reads for
    the offline/online contract.
    """
    return dict(getattr(cls, "__annotations__", {}))


def _is_optional(annotation: str) -> bool:
    """True if the (string) annotation declares ``X | None`` / Optional[X]."""
    text = annotation.replace(" ", "")
    return "|None" in text or "None|" in text or text.startswith("Optional[")


def test_appcontext_is_gone() -> None:
    assert not hasattr(context_mod, "AppContext")


def test_core_and_live_ctx_exist() -> None:
    assert hasattr(context_mod, "CoreCtx")
    assert hasattr(context_mod, "LiveCtx")


def test_core_ctx_has_no_optional_service_fields() -> None:
    CoreCtx = context_mod.CoreCtx
    anns = _raw_annotations(CoreCtx)
    fields = {f.name for f in dataclasses.fields(CoreCtx)}

    # Every always-present field that the offline boot guarantees.
    always_present = {
        "settings",
        "db",
        "db_cm",
        "prompts_repo",
        "jobs_repo",
        "annotations_repo",
        "review_items_repo",
        "write_log_repo",
        "proxy_cache_repo",
        "ai_store_files_repo",
        "clip_cache_repo",
        "clip_list_cache_repo",
        "field_def_cache_repo",
        "pending_ops_repo",
        "workspaces_repo",
        "cache_actions_log_repo",
        "prefetch_queue_repo",
        "studio_folders_repo",
        "studio_runs_repo",
        "event_bus",
        "write_queue",
        "cache_inspector",
        "cache_actions",
    }
    missing = always_present - fields
    assert not missing, f"CoreCtx missing always-present fields: {missing}"

    for name in always_present:
        assert not _is_optional(anns[name]), f"CoreCtx.{name} must NOT be Optional"


def test_live_ctx_core_services_are_non_optional() -> None:
    LiveCtx = context_mod.LiveCtx
    anns = _raw_annotations(LiveCtx)
    for name in ("archive", "ai_store", "gemini"):
        assert name in anns, f"LiveCtx must declare {name}"
        assert not _is_optional(anns[name]), f"LiveCtx.{name} must be non-Optional"
    # catdv exists but is legitimately Optional (offline-but-booted).
    assert "catdv" in anns
    assert _is_optional(anns["catdv"]), "LiveCtx.catdv must stay Optional"
