"""build_context_text — render Czech context blocks for Gemini Live.

Two clearly-labeled sections so the model never conflates committed data
with the operator's working hypothesis:

    === Publikované anotace (z CatDV) ===   ← clip metadata as currently in CatDV
    === Rozpracované anotace (můj draft, ještě neuložené do CatDV) ===

Either block may be omitted entirely when nothing of substance is in it.
All Czech free-text fields are run through `view_models._fix` to repair
mojibake (see catdv-mojibake-display-fix memory + ui/view_models.py).
"""

from typing import Any

from backend.app.ui.view_models import _fix


def _ne(value) -> bool:
    """non-empty: trims strings, treats None / [] / {} / '' as empty."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _v(value: Any) -> str:
    """Stringify a field value for display — list -> comma joined, else str()."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(_fix(str(v))) for v in value if _ne(v))
    if isinstance(value, str):
        return _fix(value) or ""
    return str(value)


def _render_marker(m: dict) -> str:
    name = _fix(m.get("name", "") or "") or ""
    desc = _fix(m.get("description", "") or "") or ""
    range_ = f"{m.get('in_smpte', '')} – {m.get('out_smpte', '')}"
    label = f'„{name}"' if name else ""
    suffix = f" — {desc}" if desc else ""
    return f"- {range_}  {label}{suffix}".rstrip()


def _render_published(clip: dict) -> str:
    lines = ["=== Publikované anotace (z CatDV) ==="]
    lines.append(
        f"Název klipu: {_fix(clip.get('name', '') or '') or ''}\n"
        f"Formát: {_fix(clip.get('format', '') or '') or ''}   "
        f"FPS: {clip.get('fps', '')}   "
        f"Délka: {clip.get('duration_smpte', '')}"
    )
    notes = clip.get("notes")
    if _ne(notes):
        lines.append("Poznámky:")
        lines.append(_fix(notes) or "")
    big_notes = clip.get("big_notes")
    if _ne(big_notes):
        lines.append("Rozšířené poznámky:")
        lines.append(_fix(big_notes) or "")
    markers = clip.get("markers") or []
    if markers:
        lines.append("Markery (čas → popis):")
        lines.extend(_render_marker(m) for m in markers)
    fields = {k: v for k, v in (clip.get("fields") or {}).items() if _ne(v)}
    if fields:
        lines.append("Vlastní pole (pragafilm.*):")
        for k, v in fields.items():
            lines.append(f"- {k}: {_v(v)}")
    return "\n".join(lines)


def _render_draft(draft: dict) -> str | None:
    markers = draft.get("markers") or []
    fields = {k: v for k, v in (draft.get("fields") or {}).items() if _ne(v)}
    notes = draft.get("notes")
    if not markers and not fields and not _ne(notes):
        return None
    lines = ["=== Rozpracované anotace (můj draft, ještě neuložené do CatDV) ==="]
    if markers:
        lines.append("Draft markery:")
        lines.extend(_render_marker(m) for m in markers)
    if fields:
        lines.append("Draft pole:")
        for k, v in fields.items():
            lines.append(f"- {k}: {_v(v)}")
    if _ne(notes):
        lines.append("Draft poznámky:")
        lines.append(_fix(notes) or "")
    return "\n".join(lines)


def build_context_text(clip: dict, draft: dict) -> str:
    blocks = [b for b in (_render_published(clip), _render_draft(draft)) if b]
    blocks.append("(Konec kontextu. Následuje aktuální snímek a moje otázka.)")
    return "\n\n".join(blocks)
