"""Walkthrough-local archive/resolver/thumbnail doubles.

Mirrors tests/integration/test_clip_detail_draft.py: the web UI requires a
numeric clip key (ui/view_models.py does int(clip.key[1])), which the real fs
provider cannot supply. These are injected via install_live_ctx.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from backend.app.archive.errors import NotFoundError
from backend.app.archive.model import (
    CanonicalClip,
    CatalogInfo,
    ClipPage,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)

CLIP_ID = 101
CLIP_NAME = "archive_30s"
DECADE_IDENT = "pragafilm.dekáda.natočení"
PUBLISHED_DECADE = "30.léta"

# The catalog the app lists against. Must match CATDV_CATALOG_ID in the app's
# boot env (app_server) and the catalog_id the seed writes into clip_list_cache,
# so the annotation-status filters resolve against the same clip universe.
CATALOG_ID = "881507"

# A clip that keeps a pending AI draft for the whole run — no scenario publishes
# it — so the annotation-status filter has a STABLE "awaiting review" member,
# independent of the review→publish scenario's mutations to clip 101.
REVIEW_FIXTURE_CLIP_ID = 110
REVIEW_FIXTURE_CLIP_NAME = "Reel awaiting human review"

# Search catalog: extra clips (beyond the canonical 101) so the search page can
# be driven end-to-end. The names are the single source of truth shared with the
# search scenarios — a search term, the clips that match it, and one that does
# not, so a scenario can assert "matches shown, non-match gone".
SEARCH_TERM = "Prague"
SEARCH_MATCH_NAMES = (
    "Prague rooftops at dawn",
    "Prague tram on Wenceslas Square",
)
SEARCH_NONMATCH_NAME = "Brno textile factory"
NO_RESULTS_TERM = "zzqxnomatch"

# Clips that NO search/review assertion touches — safe to drive a real annotate
# job against (bulk-annotate-start, cancel-running-batch) without perturbing the
# other scenarios. The single source of truth shared with those scenarios.
ANNOTATE_SAFE_NAMES = (
    "Wartime newsreel, reel 12",  # clip 105
    "Studio interview (unedited)",  # clip 106
)

# A clip only the batch-review-apply scenario annotates, reviews and publishes,
# so its batch row's status is not perturbed by the other job scenarios.
BATCH_REVIEW_CLIP_ID = 107
BATCH_REVIEW_CLIP_NAME = "Harbour cranes, reel 3"

# (clip_id, name) for every extra clip. 101 is the canonical clip (build_clip)
# and is added separately; none of these names contain SEARCH_TERM by accident.
_EXTRA_CLIPS = (
    (102, SEARCH_MATCH_NAMES[0]),
    (103, SEARCH_MATCH_NAMES[1]),
    (104, SEARCH_NONMATCH_NAME),
    (105, ANNOTATE_SAFE_NAMES[0]),
    (106, ANNOTATE_SAFE_NAMES[1]),
    (BATCH_REVIEW_CLIP_ID, BATCH_REVIEW_CLIP_NAME),
    (REVIEW_FIXTURE_CLIP_ID, REVIEW_FIXTURE_CLIP_NAME),
)
# A catalog clip that is never annotated — used by the "not annotated" filter
# scenario as the clip that should remain visible.
NOT_ANNOTATED_CLIP_NAME = SEARCH_NONMATCH_NAME

# A prompt with a PRODUCTION version is seeded (seed.seed_production_prompt) so
# the bulk-annotate modal and the New-batch picker have a selectable prompt —
# both only list prompts whose current_production_version_id is non-null. Its
# media_kind matches the seeded video clips so it shows up for them.
PRODUCTION_PROMPT_NAME = "decade-tagger (production)"


def build_clip(video_path: Path, duration_secs: float = 8.0, fps: float = 25.0) -> CanonicalClip:
    """The single clip the walkthrough renders. Published state lives here."""
    return CanonicalClip(
        key=("catdv", str(CLIP_ID)),
        name=CLIP_NAME,
        duration_secs=duration_secs,
        fps=fps,
        # Four markers on whole-second frame boundaries (2 scenes with in+out, 2
        # point "shots" with no out) so the player's prev/next-marker transport
        # has something to step through — drives the player walkthrough scenarios.
        markers=(
            Marker(
                name="Opening titles",
                in_=Timecode(secs=0.0, fps=fps, frm=0),
                out=Timecode(secs=1.5, fps=fps, frm=38),
                description="Opening title card",
            ),
            Marker(
                name="Establishing shot",
                in_=Timecode(secs=2.0, fps=fps, frm=50),
                out=None,
                description="Wide establishing shot",
            ),
            # A LEGACY app marker (#123): written before the redesign, so it
            # carries a Gemini category (not "AI"). It is treated as human —
            # kept and never touched on a publish.
            Marker(
                name="Interview",
                in_=Timecode(secs=4.0, fps=fps, frm=100),
                out=Timecode(secs=5.5, fps=fps, frm=138),
                description="Talking-head interview",
                category="Rozhovor",
            ),
            Marker(
                name="Closing shot",
                in_=Timecode(secs=6.0, fps=fps, frm=150),
                out=None,
                description="Closing cutaway",
            ),
        ),
        fields={DECADE_IDENT: FieldValue(identifier=DECADE_IDENT, value=PUBLISHED_DECADE)},
        notes={"notes": "Test clip"},
        media=MediaRef(
            mime_type="video/mp4",
            size_bytes=video_path.stat().st_size,
            cached_path=video_path,
            upstream_handle=str(CLIP_ID),
        ),
        provider_data={"ID": CLIP_ID, "name": CLIP_NAME},
        fetched_at=datetime.now(UTC),
    )


def _list_clip(
    clip_id: int, name: str, video_path: Path, duration_secs: float = 8.0, fps: float = 25.0
) -> CanonicalClip:
    """A minimal clip for the list/search surface (no markers/draft needed)."""
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=duration_secs,
        fps=fps,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/mp4",
            size_bytes=video_path.stat().st_size,
            cached_path=video_path,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": name},
        fetched_at=datetime.now(UTC),
    )


# Multiple catalogs (#124): the default catalog holds every clip above; a
# second catalog holds two clips of its own so switching visibly changes the list.
SECOND_CATALOG_ID = "913722"
CATALOGS = (
    CatalogInfo(CATALOG_ID, "AI katalog"),
    CatalogInfo(SECOND_CATALOG_ID, "vyber pro AI"),
)
SECOND_CATALOG_CLIP_NAMES = ("ARNOLD Bogdan Sis 02", "BENES Jana 01")
_SECOND_CATALOG_CLIPS = ((201, SECOND_CATALOG_CLIP_NAMES[0]), (202, SECOND_CATALOG_CLIP_NAMES[1]))


def build_second_catalog_clips(video_path: Path) -> tuple[CanonicalClip, ...]:
    return tuple(_list_clip(i, n, video_path) for i, n in _SECOND_CATALOG_CLIPS)


def build_clips(video_path: Path) -> tuple[CanonicalClip, ...]:
    """The full in-memory catalog: the canonical clip 101 + the search extras."""
    return (build_clip(video_path), *(_list_clip(i, n, video_path) for i, n in _EXTRA_CLIPS))


class FakeArchive:
    """Numeric-keyed archive serving an in-memory clip catalog.

    `list_clips` filters by `query.text` (case-insensitive substring of the clip
    name, matching the real list's Python-side name filter) and paginates with
    offset/limit, so the search page is fully driveable with no real provider.
    Accepts a single clip or a sequence. Records apply_changes.
    """

    def __init__(
        self,
        clips: CanonicalClip | tuple[CanonicalClip, ...],
        *,
        catalogs: tuple[CatalogInfo, ...] = (),
        clip_catalogs: dict[str, str] | None = None,
        default_catalog: str = CATALOG_ID,
    ) -> None:
        self._clips: tuple[CanonicalClip, ...] = (
            (clips,) if isinstance(clips, CanonicalClip) else tuple(clips)
        )
        self._catalogs = catalogs
        self._clip_catalogs = clip_catalogs or {}
        self._default_catalog = default_catalog
        self.applied: list = []

    async def list_catalogs(self) -> list[CatalogInfo]:
        return list(self._catalogs)

    async def list_clips(self, catalog, query):
        # catalog None = no catalog filter (unit tests); unmapped clips belong
        # to the default catalog.
        pool = [
            c
            for c in self._clips
            if catalog is None
            or self._clip_catalogs.get(c.key[1], self._default_catalog) == str(catalog)
        ]
        needle = (query.text or "").strip().casefold()
        matches = [c for c in pool if needle in c.name.casefold()] if needle else pool
        page = matches[query.offset : query.offset + query.limit]
        return ClipPage(
            items=tuple(page), total=len(matches), offset=query.offset, limit=query.limit
        )

    async def get_clip(self, clip_id_str: str):
        for clip in self._clips:
            if clip_id_str == clip.key[1]:
                return clip
        raise NotFoundError(f"clip not found: {clip_id_str}")

    async def apply_changes(self, change_set):
        # Record the attempt AND apply the #123 ownership rule to the in-memory
        # clip, so the post-publish refresh (/clips/{id}/published + the player
        # island) shows the AI set as published (orange) while human/legacy
        # markers survive — the walkthrough asserts exactly that.
        self.applied.append(change_set)
        from backend.app.archive.model import WriteResult

        self._apply_ownership(change_set)
        return WriteResult(status="ok", upstream_response={}, new_etag="fake-etag")

    def _apply_ownership(self, change_set) -> None:
        from dataclasses import replace

        from backend.app.archive.model import (
            AI_MARKER_CATEGORY,
            AI_OWNED_FIELDS,
            AddMarkers,
            AppendNote,
            Marker,
            ReconcileMarkers,
            ReplaceAIMarkers,
            ReplaceNote,
            SetField,
            ai_marker_name,
        )

        cid = change_set.clip_key[1]
        idx = next((i for i, c in enumerate(self._clips) if c.key[1] == cid), None)
        if idx is None:
            return
        clip = self._clips[idx]
        markers = list(clip.markers)
        fields = dict(clip.fields)

        marker_ops = [
            o
            for o in change_set.ops
            if isinstance(o, (ReplaceAIMarkers, AddMarkers, ReconcileMarkers))
        ]
        if marker_ops:
            kept = [m for m in markers if m.category != AI_MARKER_CATEGORY]
            last = marker_ops[-1]
            desired = (
                last.desired
                if isinstance(last, (ReplaceAIMarkers, ReconcileMarkers))
                else last.markers
            )
            ai = [
                Marker(
                    name=ai_marker_name(d.category, d.name),
                    in_=d.in_,
                    out=d.out,
                    description=d.description,
                    category=AI_MARKER_CATEGORY,
                    color=None,
                )
                for d in desired
            ]
            markers = kept + ai

        for op in change_set.ops:
            if isinstance(op, SetField) and op.identifier in AI_OWNED_FIELDS:
                fields[op.identifier] = FieldValue(identifier=op.identifier, value=op.value)
            elif isinstance(op, ReplaceNote) and op.target in AI_OWNED_FIELDS:
                fields[op.target] = FieldValue(identifier=op.target, value=op.text)
            elif isinstance(op, AppendNote) and op.target in AI_OWNED_FIELDS:
                prev = fields.get(op.target)
                prior = prev.value if prev is not None else ""
                text = (str(prior) + "\n\n---\n\n" + op.text) if prior else op.text
                fields[op.target] = FieldValue(identifier=op.target, value=text)

        self._clips = tuple(
            replace(clip, markers=tuple(markers), fields=fields) if i == idx else c
            for i, c in enumerate(self._clips)
        )


class LocalFileResolver:
    """Returns a real on-disk video so /api/media/{id} streams a playable file."""

    is_host_local = False

    def __init__(self, video_path: Path) -> None:
        self._video = video_path

    async def path_for_clip_id(self, clip_id: int) -> Path:
        return self._video

    def is_managed(self, path: Path) -> bool:
        return True


class StubThumbnailService:
    """Serves one real JPEG poster for every clip so rows show a thumbnail on
    camera instead of a broken-image placeholder. Offline (never hits network).
    Falls back to a cache miss (None) when no poster path was supplied."""

    is_online_provider = False

    def __init__(self, thumb_path: Path | None = None) -> None:
        self._thumb = thumb_path

    async def get_or_fetch(self, clip_id: int):
        return self._thumb


class FakeAIStore:
    """Offline AI-input store: reports every clip as already uploaded.

    `status()` returning non-None makes the annotator take its fast path
    (skip the local-proxy resolve + upload entirely — see
    services/clip_media.py::ai_input_for_clip), so a job can run with no network
    and no CatDV seat. Mirrors the GCS store's DB-only, offline-safe
    `status()` contract (CLAUDE.md, cache layers)."""

    id = "fake-ai-store"

    async def status(self, clip_key):
        return {"key": tuple(clip_key)}

    async def ensure_uploaded(self, clip_key, local_path, mime):
        return {"key": tuple(clip_key)}

    async def reference_for_gemini(self, upload):
        return {"uri": "fake://" + "/".join(upload["key"])}


# Default release timeout: if a scenario forgets to release() a held annotate,
# the executor thread unblocks after this many seconds rather than wedging the
# app. Generous so a slow CI box never trips it during a legitimate hold.
_HOLD_TIMEOUT_SECS = 30.0


class FakeGemini:
    """Offline Gemini double for the job-start / cancel walkthroughs.

    `annotate` is invoked by the annotator via ``asyncio.to_thread`` (Vertex's
    client is synchronous), so it runs on the app's executor thread. By default
    the gate is *released*: annotate returns immediately with a deterministic
    payload, so any scenario that just needs a job to run gets instant
    completion.

    A scenario can call ``hold()`` (from the Playwright / main thread — the
    walkthrough app runs in-process) to make every subsequent annotate block on
    a thread-safe gate until ``release()`` is called. That keeps a batch in
    ``running`` / ``prompting`` long enough to observe progress and click
    Cancel, then lets the run finish cleanly. Fully offline: no network, no
    CatDV seat (ADR 0111)."""

    id = "fake-gemini"

    def __init__(self) -> None:
        self._gate = threading.Event()
        self._gate.set()  # released by default → instant
        self._entered = threading.Event()

    def hold(self) -> None:
        """Make the next annotate() block until release() is called."""
        self._entered.clear()
        self._gate.clear()

    def release(self) -> None:
        """Unblock a held annotate() so the run ends (or cancels) cleanly."""
        self._gate.set()

    def wait_until_prompting(self, timeout: float = 10.0) -> bool:
        """Block until a held annotate() has actually started — i.e. the batch
        has reached ``prompting`` — so a caller asserts 'running' without a
        race. Returns False on timeout."""
        return self._entered.wait(timeout)

    def annotate(self, *, file_ref, prompt, schema, model, media_resolution=None):
        self._entered.set()
        # No-op when the gate is already set (default); blocks while held.
        self._gate.wait(timeout=_HOLD_TIMEOUT_SECS)
        # A shared-field suggestion (decade) plus one AI marker, so a run makes a
        # draft with both an owned write and a copy-only suggestion (#123).
        payload = {
            "decade": PUBLISHED_DECADE,
            "scenes": [{"name": "Harbour wide", "in": {"secs": 0.0}, "out": {"secs": 2.0}}],
        }
        return {"text": json.dumps(payload), "raw": {}}


# Process-wide singleton: app_server injects THIS instance into the LiveCtx, and
# scenarios import it via gemini_fake() to hold/release the fake while Playwright
# drives the browser. One walkthrough app instance per run, so one shared fake.
_GEMINI = FakeGemini()


def gemini_fake() -> FakeGemini:
    """The FakeGemini the walkthrough app injects — shared with scenarios."""
    return _GEMINI


class ToggleMonitor:
    """Connection-monitor stand-in a scenario can flip offline/online (#124).

    Same surface as tests/_helpers/live_ctx._OnlineMonitor. The real UI
    Disconnect needs a real CatDV client, so the offline scenario flips this
    directly from the Playwright thread (like gemini_fake().hold())."""

    is_forced = False
    _forced_offline = False

    def __init__(self) -> None:
        from backend.app.services.connection_monitor import ConnectionState

        self._online, self._offline = ConnectionState.online, ConnectionState.offline
        self._state = self._online

    def current_state(self):
        return self._state

    def set_online(self, online: bool) -> None:
        self._state = self._online if online else self._offline

    async def probe_once(self) -> None:
        return None


_MONITOR = ToggleMonitor()


def monitor_fake() -> ToggleMonitor:
    return _MONITOR
