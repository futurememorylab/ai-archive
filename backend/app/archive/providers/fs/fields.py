"""Read FS_ROOT/.archive/fields.json → list[FieldDef].

Missing file returns []. Malformed JSON returns [] and logs a warning.
Unknown JSON keys go into the `provider_data` blob on each FieldDef so
adapters that want them later can read them without a schema change.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.app.archive.model import FieldDef

log = logging.getLogger(__name__)


_KNOWN_KEYS = {
    "identifier",
    "name",
    "type",
    "is_multi",
    "is_editable",
    "picklist_values",
    "provider_data",
}

_VALID_TYPES = {
    "text",
    "integer",
    "decimal",
    "date",
    "picklist",
    "multi-picklist",
    "bool",
}


def load_field_defs(fs_root: Path) -> list[FieldDef]:
    path = Path(fs_root) / ".archive" / "fields.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("FS field defs unreadable at %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        log.warning("FS field defs at %s is not a JSON array", path)
        return []

    out: list[FieldDef] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("identifier") or entry.get("name") or "")
        if not identifier:
            continue
        name = str(entry.get("name") or identifier)
        raw_type = str(entry.get("type") or "text").lower()
        mapped_type = raw_type if raw_type in _VALID_TYPES else "text"
        is_multi = bool(entry.get("is_multi", mapped_type == "multi-picklist"))
        is_editable = bool(entry.get("is_editable", True))
        pv_raw = entry.get("picklist_values")
        pv_tuple: tuple[str, ...] | None
        if isinstance(pv_raw, list):
            pv_tuple = tuple(str(v) for v in pv_raw)
        else:
            pv_tuple = None

        provider_data: dict[str, Any] = {}
        pd = entry.get("provider_data")
        if isinstance(pd, dict):
            provider_data.update(pd)
        for k, v in entry.items():
            if k not in _KNOWN_KEYS:
                provider_data.setdefault(k, v)

        out.append(
            FieldDef(
                identifier=identifier,
                name=name,
                type=mapped_type,  # type: ignore[arg-type]
                is_multi=is_multi,
                is_editable=is_editable,
                picklist_values=pv_tuple,
                provider_data=provider_data,
            )
        )
    return out
