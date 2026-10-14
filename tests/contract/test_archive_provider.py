"""Shared contract suite for ArchiveProvider implementations.

Spec §15 item 1 — the same parametrised tests run against every adapter
to guarantee the port boundary is real. Today: `catdv` and `fs`.

Each test resolves a `provider_case` fixture that exposes:

    provider_case.provider     -> ArchiveProvider (live)
    provider_case.seed(name)   -> ClipKey       (creates a fresh clip)
    provider_case.has_etag     -> bool
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.app.archive.model import (
    AI_OWNED_FIELDS,
    AppendNote,
    ChangeSet,
    ClipKey,
    ClipQuery,
    Marker,
    ReplaceAIMarkers,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.archive.providers.fs import media_probe
from backend.app.archive.providers.fs.adapter import FilesystemArchiveProvider
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@dataclass
class ProviderCase:
    name: str
    provider: Any
    seed: Callable[[str], ClipKey]
    has_etag: bool


# ----- catdv builder ------------------------------------------------------


class _CatdvCaseManager:
    def __init__(self) -> None:
        self._stack = ExitStack()
        self._counter = 0
        self._fake = None
        self._client: CatdvClient | None = None

    async def setup(self) -> ProviderCase:
        base_url, fake = self._stack.enter_context(running_fake_catdv())
        self._fake = fake
        self._client = await CatdvClient(base_url, "klientAI", "secret").__aenter__()
        adapter = CatdvArchiveAdapter(client=self._client)

        def seed(name: str) -> ClipKey:
            self._counter += 1
            cid = self._counter
            fake.clips[cid] = {
                "ID": cid,
                "name": name,
                "fps": 25.0,
                "markers": [],
                "fields": {},
                "modifyDate": "2026-05-19T00:00:00",
            }
            return ("catdv", str(cid))

        return ProviderCase(
            name="catdv",
            provider=adapter,
            seed=seed,
            has_etag=adapter.capabilities.supports_etag,
        )

    async def teardown(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
        self._stack.close()


# ----- fs builder ---------------------------------------------------------


class _FsCaseManager:
    def __init__(self, tmp_path: Path) -> None:
        self._root = tmp_path / "fs_root"
        self._root.mkdir()
        (self._root / "cat").mkdir()
        self._counter = 0

    async def setup(self) -> ProviderCase:
        # Force ffprobe-missing path so duration/fps are deterministic.
        media_probe.reset_warning_flag()
        media_probe.shutil.which = lambda _name: None  # type: ignore[assignment]
        provider = FilesystemArchiveProvider(fs_root=self._root)

        def seed(name: str) -> ClipKey:
            self._counter += 1
            slug = f"clip{self._counter:03d}"
            (self._root / "cat" / f"{slug}.mov").write_bytes(b"")
            return ("fs", f"cat/{slug}")

        return ProviderCase(
            name="fs",
            provider=provider,
            seed=seed,
            has_etag=provider.capabilities.supports_etag,
        )

    async def teardown(self) -> None:
        return None


# ----- fixture -----------------------------------------------------------


@pytest.fixture(params=["catdv", "fs"])
def provider_case_builder(request, tmp_path: Path):
    if request.param == "catdv":
        return _CatdvCaseManager()
    if request.param == "fs":
        return _FsCaseManager(tmp_path)
    raise AssertionError(request.param)


@pytest.fixture
async def provider_case(provider_case_builder):
    case = await provider_case_builder.setup()
    try:
        yield case
    finally:
        await provider_case_builder.teardown()


# ----- the contract -------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_shape(provider_case: ProviderCase):
    caps = provider_case.provider.capabilities
    assert isinstance(caps.supports_markers, bool)
    assert isinstance(caps.supports_etag, bool)
    assert isinstance(caps.supports_notes, frozenset)
    assert caps.write_atomicity in ("per-clip", "per-op")


@pytest.mark.asyncio
async def test_get_clip_round_trip(provider_case: ProviderCase):
    key = provider_case.seed("first")
    clip = await provider_case.provider.get_clip(key[1])
    assert clip.key == key
    assert isinstance(clip.name, str)
    assert clip.fps > 0


@pytest.mark.asyncio
async def test_set_field_round_trip(provider_case: ProviderCase):
    # Under the #123 ownership rule a provider writes ONLY the fields it owns,
    # so the one field that round-trips through apply_changes is the owned one
    # (AI_OWNED_FIELDS[0]). A write to any other field is rejected; see
    # test_non_owned_field_write_is_rejected.
    key = provider_case.seed("setfield")
    owned = AI_OWNED_FIELDS[0]
    cs = ChangeSet(
        clip_key=key,
        ops=(SetField(identifier=owned, value="HELLO"),),
    )
    result = await provider_case.provider.apply_changes(cs)
    assert result.status == "ok"
    # The CatDV fake stores `fields` as a dict that gets updated by the PUT
    # payload; the FS adapter persists to the sidecar; both surface through
    # `get_clip().fields`.
    clip = await provider_case.provider.get_clip(key[1])
    fv = clip.fields.get(owned)
    assert fv is not None
    assert fv.value == "HELLO"


@pytest.mark.asyncio
async def test_replace_ai_markers_adds_to_empty_clip(provider_case: ProviderCase):
    key = provider_case.seed("markers")
    initial = await provider_case.provider.get_clip(key[1])
    n_before = len(initial.markers)
    cs = ChangeSet(
        clip_key=key,
        ops=(
            ReplaceAIMarkers(
                desired=(Marker(name="addM", in_=Timecode(secs=0.5, fps=25.0), out=None),),
            ),
        ),
    )
    result = await provider_case.provider.apply_changes(cs)
    assert result.status == "ok"
    after = await provider_case.provider.get_clip(key[1])
    assert len(after.markers) == n_before + 1
    assert any(m.name == "addM" and m.category == "AI" for m in after.markers)


@pytest.mark.asyncio
async def test_replace_ai_markers_is_wholesale(provider_case: ProviderCase):
    """On every adapter, ReplaceAIMarkers replaces the AI set wholesale rather
    than accumulating: publishing again with a different marker leaves only the
    newest AI marker. (Preserving a pre-existing non-AI marker needs
    provider-specific seeding; it is covered in each adapter's own tests.)"""
    key = provider_case.seed("prov")
    await provider_case.provider.apply_changes(
        ChangeSet(
            clip_key=key,
            ops=(
                ReplaceAIMarkers(
                    desired=(Marker(name="a", in_=Timecode(secs=1.0, fps=25.0), out=None),)
                ),
            ),
        )
    )
    await provider_case.provider.apply_changes(
        ChangeSet(
            clip_key=key,
            ops=(
                ReplaceAIMarkers(
                    desired=(Marker(name="b", in_=Timecode(secs=2.0, fps=25.0), out=None),)
                ),
            ),
        )
    )
    after = await provider_case.provider.get_clip(key[1])
    ai_names = sorted(m.name for m in after.markers if m.category == "AI")
    assert ai_names == ["b"]


@pytest.mark.asyncio
async def test_non_owned_field_write_is_rejected(provider_case: ProviderCase):
    """Both adapters refuse to write a field the app does not own."""
    from backend.app.archive.errors import FatalProviderError

    key = provider_case.seed("guard")
    with pytest.raises(FatalProviderError):
        await provider_case.provider.apply_changes(
            ChangeSet(
                clip_key=key, ops=(SetField(identifier="pragafilm.dekáda.natočení", value="x"),)
            )
        )


@pytest.mark.asyncio
async def test_append_note_commutative(provider_case: ProviderCase):
    # CatDV stores notes under `fields.<id>` on the wire but the in-process
    # FakeCatdv doesn't reflect the PUT back into a readable canonical note, so
    # two AppendNote ops cannot be observed as concatenated through the
    # canonical shape for CatDV. The FS adapter — whose notes are first-class —
    # covers the canonical commutativity contract here, against the one owned
    # note target (#123).
    if provider_case.name == "catdv":
        pytest.skip("CatDV note round-trip requires a real CatDV server")
    key = provider_case.seed("notes")
    for text in ("first", "second"):
        result = await provider_case.provider.apply_changes(
            ChangeSet(
                clip_key=key,
                ops=(AppendNote(target=AI_OWNED_FIELDS[0], text=text),),
            )
        )
        assert result.status == "ok"
    clip = await provider_case.provider.get_clip(key[1])
    note = clip.notes.get(AI_OWNED_FIELDS[0], "")
    assert "first" in note and "second" in note
    assert note.index("first") < note.index("second")


@pytest.mark.asyncio
async def test_stale_etag_returns_conflict(provider_case: ProviderCase):
    if not provider_case.has_etag:
        pytest.skip(f"{provider_case.name} adapter does not support etags")
    key = provider_case.seed("etag")
    cs = ChangeSet(
        clip_key=key,
        ops=(SetField(identifier="contract.t", value="A"),),
        expected_etag="not-a-real-etag",
    )
    result = await provider_case.provider.apply_changes(cs)
    # FS adapter: sidecar is missing initially → live_etag is None and
    # expected_etag does not match → conflict.
    assert result.status == "conflict"
    assert result.conflict_detail is not None
    assert result.conflict_detail.expected_etag == "not-a-real-etag"


@pytest.mark.asyncio
async def test_list_clips_returns_seeded_clip(provider_case: ProviderCase):
    key = provider_case.seed("listable")
    if provider_case.name == "catdv":
        catalog = "881507"  # fake CatDV doesn't actually scope on catalog
    else:
        catalog = "cat"
    page = await provider_case.provider.list_clips(catalog, ClipQuery(limit=100))
    assert any(c.key == key for c in page.items)


@pytest.mark.asyncio
async def test_health_reports_ok(provider_case: ProviderCase):
    h = await provider_case.provider.health()
    assert h.ok is True


@pytest.mark.asyncio
async def test_list_field_definitions_returns_list(provider_case: ProviderCase):
    defs = await provider_case.provider.list_field_definitions()
    assert isinstance(defs, list)
