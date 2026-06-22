"""Render a static gallery (index.html) of recorded walkthroughs.

Videos are grouped by topic (e.g. "Search page", "Clip page") with a sidebar
navigation menu that links to each topic and each individual walkthrough.
"""

from __future__ import annotations

import html
import re
from typing import TypedDict


class GalleryEntry(TypedDict):
    slug: str
    topic: str
    title: str
    description: str
    video: str  # path relative to the gallery file


def _anchor(topic: str) -> str:
    return "topic-" + re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-")


def _grouped(entries: list[GalleryEntry]) -> dict[str, list[GalleryEntry]]:
    """Group entries by topic, preserving first-seen topic order."""
    groups: dict[str, list[GalleryEntry]] = {}
    for e in entries:
        groups.setdefault(e.get("topic") or "Other", []).append(e)
    return groups


def render_gallery(entries: list[GalleryEntry]) -> str:
    groups = _grouped(entries)

    nav_items: list[str] = []
    sections: list[str] = []
    for topic, items in groups.items():
        t = html.escape(topic)
        anchor = _anchor(topic)
        sub_links = "\n".join(
            f'          <li><a href="#video-{html.escape(e["slug"], quote=True)}">'
            f'{html.escape(e["title"])}</a></li>'
            for e in items
        )
        nav_items.append(
            f'      <li class="nav-topic"><a href="#{anchor}">{t}</a>\n'
            f"        <ul>\n{sub_links}\n        </ul>\n      </li>"
        )

        cards = []
        for e in items:
            slug = html.escape(e["slug"], quote=True)
            title = html.escape(e["title"])
            desc = html.escape(e["description"])
            video = html.escape(e["video"], quote=True)
            cards.append(
                f'      <article class="card" id="video-{slug}">\n'
                f"        <h3>{title}</h3>\n"
                f"        <p>{desc}</p>\n"
                f'        <video controls preload="metadata" src="{video}"></video>\n'
                f"      </article>"
            )
        sections.append(
            f'    <section class="topic" id="{anchor}">\n'
            f"      <h2>{t}</h2>\n" + "\n".join(cards) + "\n    </section>"
        )

    nav = "\n".join(nav_items) if nav_items else "      <li>No recordings yet.</li>"
    body = "\n".join(sections) if sections else '    <p class="empty">No recordings yet.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Walkthrough gallery</title>
<style>
  :root {{ --line: #e3e3ea; --muted: #55555f; --accent: #2563eb; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0; color: #1a1a1f; }}
  .layout {{ display: flex; align-items: flex-start; max-width: 1200px; margin: 0 auto; }}
  nav.toc {{
    position: sticky; top: 0; align-self: flex-start;
    width: 260px; flex: 0 0 260px; height: 100vh; overflow-y: auto;
    padding: 1.5rem 1rem; border-right: 1px solid var(--line);
  }}
  nav.toc h1 {{ font-size: 1.05rem; margin: 0 0 1rem; }}
  nav.toc ul {{ list-style: none; margin: 0; padding: 0; }}
  nav.toc .nav-topic > a {{ display: block; font-weight: 600; margin-top: .9rem; color: #1a1a1f; text-decoration: none; }}
  nav.toc .nav-topic ul {{ margin: .25rem 0 0; }}
  nav.toc .nav-topic ul a {{ display: block; padding: .15rem 0 .15rem .6rem; color: var(--muted); text-decoration: none; font-size: .92rem; border-left: 2px solid var(--line); }}
  nav.toc a:hover {{ color: var(--accent); border-color: var(--accent); }}
  main {{ flex: 1 1 auto; padding: 1.5rem 2rem; max-width: 860px; }}
  main > h1 {{ font-size: 1.4rem; margin: .25rem 0 1.5rem; }}
  .topic {{ scroll-margin-top: 1rem; margin-bottom: 2.5rem; }}
  .topic > h2 {{ font-size: 1.2rem; margin: 0 0 1rem; padding-bottom: .4rem; border-bottom: 1px solid var(--line); }}
  .card {{ border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.25rem; margin: 1.25rem 0; scroll-margin-top: 1rem; }}
  .card h3 {{ font-size: 1.05rem; margin: 0 0 .25rem; }}
  .card p {{ color: var(--muted); margin: 0 0 .75rem; }}
  video {{ width: 100%; border-radius: 8px; background: #000; }}
</style>
</head>
<body>
  <div class="layout">
    <nav class="toc">
      <h1>Walkthroughs</h1>
      <ul>
{nav}
      </ul>
    </nav>
    <main>
      <h1>Annotated walkthroughs</h1>
{body}
    </main>
  </div>
</body>
</html>
"""
