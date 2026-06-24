"""CLI to run/record walkthrough scenarios.

  python -m tests.walkthrough.run --assert            # headless, no video, pass/fail
  python -m tests.walkthrough.run --record [slug...]  # headed, annotated webm + gallery
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import webbrowser
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.walkthrough.app_server import WalkthroughApp
from tests.walkthrough.gallery import render_gallery
from tests.walkthrough.harness import Walkthrough
from tests.walkthrough.scenarios import get_scenario, load_scenarios

ARTIFACTS = Path(__file__).parent / "artifacts"


def run_scenarios(slugs: list[str], *, record: bool) -> list[dict]:
    scenarios = (
        [get_scenario(s) for s in slugs] if slugs else load_scenarios()
    )
    results: list[dict] = []
    if record:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as data_dir:
        app = WalkthroughApp(data_dir=Path(data_dir), port=8766)
        app.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not record)
                for mod in scenarios:
                    video = str(ARTIFACTS / f"{mod.SLUG}.webm") if record else None
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    page.goto(app.base_url)
                    wt = Walkthrough(page, record=record, video_path=video)
                    ok, err = True, None
                    try:
                        wt.start(mod.TITLE, mod.DESCRIPTION)
                        mod.run(wt)
                        wt.finish()
                    except Exception as exc:  # noqa: BLE001 - report per-scenario
                        ok, err = False, f"{type(exc).__name__}: {exc}"
                    finally:
                        page.close()
                    results.append(
                        {
                            "slug": mod.SLUG,
                            "topic": getattr(mod, "TOPIC", "Other"),
                            "title": mod.TITLE,
                            "description": mod.DESCRIPTION,
                            "video": f"{mod.SLUG}.webm",
                            "ok": ok,
                            "error": err,
                        }
                    )
                browser.close()
        finally:
            app.stop()
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="walkthrough")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--assert", dest="assert_", action="store_true")
    mode.add_argument("--record", action="store_true")
    ap.add_argument("slugs", nargs="*", help="scenario slugs (default: all)")
    args = ap.parse_args(argv)

    results = run_scenarios(args.slugs, record=args.record)

    for r in results:
        status = "PASS" if r["ok"] else f"FAIL ({r['error']})"
        print(f"  [{status}] {r['slug']} — {r['title']}")

    if args.record:
        gallery = ARTIFACTS / "index.html"
        gallery.write_text(render_gallery(results), encoding="utf-8")
        print(f"\nGallery: {gallery}")
        # Don't try to pop a browser in CI (no display / spawns xdg-open).
        if not os.environ.get("CI"):
            webbrowser.open(gallery.as_uri())

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
