"""Cloud video stays readable and playable across origins.

On Cloud Run `/api/media/{id}` redirects to a signed URL on the media
bucket, another site. The player must load it with `crossorigin="anonymous"`
or the browser won't let the page read the video's pixels (the clip chat's
frame hung on a tainted canvas, 2026-10-18). With the attribute, the bucket's
CORS policy must allow every app URL or the video doesn't play at all. Local
runs serve media same-origin, so neither shows up there. These tests pin the
attribute and the checked-in policy; `deploy/check-media-cors.sh` checks the
live bucket."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYER = ROOT / "backend" / "app" / "templates" / "pages" / "_player.html"
CORS = ROOT / "deploy" / "gcs-cors.json"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def _services() -> list[str]:
    text = WORKFLOW.read_text()
    found = re.findall(r"^\s+(?:STAGING|PROD)_SERVICE:\s*\"?([\w-]+)\"?", text, re.M)
    assert len(found) == 2, f"STAGING_SERVICE / PROD_SERVICE not found in {WORKFLOW.name}"
    return found


def _rule() -> dict:
    rules = json.loads(CORS.read_text())
    assert len(rules) == 1, "gcs-cors.json should hold one rule"
    return rules[0]


def test_player_video_is_fetched_with_cors():
    videos = re.findall(r"<video\s[^>]*>", PLAYER.read_text())
    assert videos, "no <video> in _player.html"
    for tag in videos:
        assert 'crossorigin="anonymous"' in tag, tag


def test_cors_allows_both_urls_of_each_service():
    origins = _rule()["origin"]
    for service in _services():
        mine = [
            o
            for o in origins
            if re.fullmatch(
                rf"https://{service}-(\d+\.[\w-]+|[a-z0-9]+-[a-z]{{2}}\.a)\.run\.app", o
            )
        ]
        # A service answers on its project-number URL and its hash URL.
        assert len(mine) == 2, (service, origins)


def test_cors_allows_reading_and_seeking():
    rule = _rule()
    assert {"GET", "HEAD"} <= set(rule["method"])
    # Seeking reads byte ranges, so the browser must see these headers.
    assert {"Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"} <= set(
        rule["responseHeader"]
    )
