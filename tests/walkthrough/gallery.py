"""Render a static gallery (index.html) of recorded walkthroughs."""

from __future__ import annotations

import html
from typing import TypedDict


class GalleryEntry(TypedDict):
    slug: str
    title: str
    description: str
    video: str  # path relative to the gallery file


def render_gallery(entries: list[GalleryEntry]) -> str:
    cards = []
    for e in entries:
        title = html.escape(e["title"])
        desc = html.escape(e["description"])
        video = html.escape(e["video"], quote=True)
        cards.append(
            f"""    <section class="card">
      <h2>{title}</h2>
      <p>{desc}</p>
      <video controls preload="metadata" width="720" src="{video}"></video>
    </section>"""
        )
    body = "\n".join(cards) if cards else "    <p>No recordings yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Walkthrough gallery</title>
<style>
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 820px; color: #1a1a1f; }}
  .card {{ border: 1px solid #e3e3ea; border-radius: 12px; padding: 1rem 1.25rem; margin: 1.25rem 0; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin: 0 0 .25rem; }}
  p {{ color: #55555f; margin: 0 0 .75rem; }}
  video {{ width: 100%; border-radius: 8px; background: #000; }}
</style>
</head>
<body>
  <h1>Annotated walkthroughs</h1>
{body}
</body>
</html>
"""
