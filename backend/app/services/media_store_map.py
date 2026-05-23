"""Hires→proxy path mapping derived from CatDV's `/mediastores` config.

The CatDV server returns one Media Store per logical archive, and each
store contains a flat list of `paths`. We pair the `mediaType=hires`
paths with the `mediaType=proxy, target=web` paths by matching
`pathOrder` — that mirrors how CatDV's own web client resolves a
proxy URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaStoreMap:
    """A list of (hires_root, proxy_root) prefix-rewrite rules."""

    rules: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_json(cls, stores: list[dict[str, Any]]) -> MediaStoreMap:
        rules: list[tuple[str, str]] = []
        for store in stores:
            hires_by_order: dict[int, str] = {}
            proxy_by_order: dict[int, str] = {}
            for p in store.get("paths", []):
                ptype = p.get("pathType") or {}
                order = p.get("pathOrder")
                path = p.get("path")
                if order is None or not path:
                    continue
                if ptype.get("mediaType") == "hires":
                    hires_by_order[order] = path.rstrip("/")
                elif ptype.get("mediaType") == "proxy" and ptype.get("target") == "web":
                    proxy_by_order[order] = path.rstrip("/")
            for order in sorted(hires_by_order):
                if order in proxy_by_order:
                    rules.append((hires_by_order[order], proxy_by_order[order]))
        return cls(rules=rules)

    def resolve_proxy(self, hires_path: str) -> Path | None:
        for hires_root, proxy_root in self.rules:
            if hires_path == hires_root:
                continue
            if hires_path.startswith(hires_root + "/"):
                rel = hires_path[len(hires_root) + 1 :]
                return Path(f"{proxy_root}/{rel}")
        return None


async def fetch_media_store_map(catdv_client) -> MediaStoreMap:
    """Call `/catdv/api/9/mediastores` once and build the map.

    Read-only; `klientAI` (non-admin) is allowed."""
    env = await catdv_client._call_json("GET", "/catdv/api/9/mediastores")
    return MediaStoreMap.from_json(env.data or [])
