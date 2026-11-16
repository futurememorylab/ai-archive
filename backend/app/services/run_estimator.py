"""Pre-run cost estimator (spec §6).

Input tokens are deterministic arithmetic per media kind, seeded with
documented constants and self-calibrated from local history (median of
actual per-second token ratios). Output tokens are the uncertain part:
a p50/p90 distribution from history with a fallback chain —
(prompt_hash, model, kind) → (model, kind) → seed. All statistics use
BILLABLE output (candidates + thinking) and exclude MAX_TOKENS rows
(enforced in RunTelemetryRepo). Fully offline: aggregate SQL only,
never a network call. Query count is per media-kind group, never per
clip (ADR 0046).
"""

from dataclasses import dataclass
from functools import partial

from backend.app.media_kind import classify_media_kind
from backend.app.services.pricing import compute_cost, rate_cards
from backend.app.services.resolution import resolution_valid_for_kind
from backend.app.services.telemetry_capture import TokenUsage
from backend.app.services.telemetry_capture import prompt_hash as _prompt_hash

# Seed constants — sanity-check against one real run's usageMetadata
# during implementation (spec §6). Calibration replaces them as soon as
# 3+ runs of the same (model, kind) exist.
SEED_INPUT_TOKENS_PER_SEC = {
    "video+audio": 300.0,
    "video": 258.0,
    "audio": 32.0,
}
IMAGE_TILE_TOKENS = 258
SEED_OUTPUT_TOKENS_PER_SEC = 15.0
SEED_OUTPUT_TOKENS_PER_IMAGE = 700.0
_MIN_SAMPLES = 3
_GOOD_SAMPLES = 10
CHARS_PER_TOKEN = 4.0


@dataclass(frozen=True)
class ClipEstimateInput:
    clip_id: int
    media_kind: str  # image | audio | video | video+audio
    duration_secs: float | None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class RunEstimate:
    tokens_in: int
    tokens_out_p50: int
    tokens_out_p90: int
    cost_usd_p50: float | None
    cost_usd_p90: float | None
    confidence: str  # good | fair | rough
    n_samples: int
    n_clips: int


@dataclass(frozen=True)
class _KindStats:
    """Output statistics for one media kind, resolved once per estimate.

    ``p50``/``p90`` are None when no history reached _MIN_SAMPLES at any
    fallback level (level 3) — callers fall back to seed constants.
    """

    p50: float | None
    p90: float | None
    n: int  # sample count behind the chosen level
    level: int  # 1 = prompt-level history, 2 = model-level, 3 = seeds
    input_ratio: float | None = None  # calibrated tokens/sec; None → seed


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    # half-up rounding — avoids banker's-rounding surprises at even n
    idx = min(len(s) - 1, max(0, int(q * (len(s) - 1) + 0.5)))
    return s[idx]


def _image_tiles(width: int | None, height: int | None) -> int:
    if not width or not height:
        return 1
    return max(1, -(-width // 768)) * max(1, -(-height // 768))


_EMPTY_ESTIMATE = RunEstimate(
    tokens_in=0,
    tokens_out_p50=0,
    tokens_out_p90=0,
    cost_usd_p50=None,
    cost_usd_p90=None,
    confidence="rough",
    n_samples=0,
    n_clips=0,
)


def _resolution_for_kind(media_resolution: str | None, kind: str) -> str | None:
    """Resolution a clip of ``kind`` actually runs at under ``media_resolution``.

    Mirror the run's per-clip downgrade (services/resolution.py is the
    single source of truth): HIGH is image-only, so a video/audio clip
    under a HIGH-resolution prompt actually RUNS at medium. Read the
    matching-resolution history so the estimate doesn't drift from the
    charge. media_resolution=None (estimator caller didn't supply one)
    passes through and matches the repo's "any resolution" lookup.
    """
    if media_resolution is not None and not resolution_valid_for_kind(media_resolution, kind):
        return "medium"
    return media_resolution


async def _kind_history(
    conn,
    repo,
    *,
    kind: str,
    model: str,
    p_hash: str,
    media_resolution: str | None,
) -> _KindStats:
    """Output stats and calibrated input ratio (None when too little history)
    for one media kind: prompt-level history first, then model-level."""
    kind_resolution = _resolution_for_kind(media_resolution, kind)
    input_ratio = None
    if kind != "image":
        ratios = await repo.recent_input_ratios(
            conn, model=model, media_kind=kind, media_resolution=kind_resolution
        )
        if len(ratios) >= _MIN_SAMPLES:
            input_ratio = _pct(ratios, 0.5)
    rates = await repo.recent_output_rates(
        conn,
        model=model,
        media_kind=kind,
        prompt_hash=p_hash,
        media_resolution=kind_resolution,
    )
    level = 1
    if len(rates) < _MIN_SAMPLES:
        rates = await repo.recent_output_rates(
            conn, model=model, media_kind=kind, media_resolution=kind_resolution
        )
        level = 2
    if len(rates) < _MIN_SAMPLES:
        return _KindStats(p50=None, p90=None, n=0, level=3, input_ratio=input_ratio)
    return _KindStats(
        p50=_pct(rates, 0.5),
        p90=_pct(rates, 0.9),
        n=len(rates),
        level=level,
        input_ratio=input_ratio,
    )


def _clip_tokens(c: ClipEstimateInput, st: _KindStats) -> tuple[float, float, float, float]:
    """(media input tokens, audio share of them, output p50, output p90) for one clip."""
    if c.media_kind == "image":
        media_in = float(_image_tiles(c.width, c.height) * IMAGE_TILE_TOKENS)
        scale, seed = 1.0, SEED_OUTPUT_TOKENS_PER_IMAGE
    else:
        scale, seed = float(c.duration_secs or 0.0), SEED_OUTPUT_TOKENS_PER_SEC
        k = st.input_ratio
        if k is None:
            k = SEED_INPUT_TOKENS_PER_SEC.get(c.media_kind, 300.0)
        media_in = scale * k
    audio_in = media_in if c.media_kind == "audio" else 0.0
    out_p50 = scale * (st.p50 if st.p50 is not None else seed)
    out_p90 = scale * (st.p90 if st.p90 is not None else seed * 2)
    return media_in, audio_in, out_p50, out_p90


def _confidence(stats: dict[str, _KindStats]) -> tuple[str, int]:
    """Confidence label and sample count; reflects the weakest kind in the batch."""
    worst_level = max(st.level for st in stats.values())
    n_samples = min(st.n for st in stats.values())
    if worst_level == 1 and n_samples >= _GOOD_SAMPLES:
        return "good", n_samples
    if worst_level <= 2 and n_samples >= _MIN_SAMPLES:
        return "fair", n_samples
    return "rough", n_samples


def _estimate_cost(
    model: str,
    *,
    tokens_in: float,
    text_tokens: float,
    audio_media_tokens: float,
    out_tokens: float,
) -> float | None:
    if model not in rate_cards():
        return None
    # Approximate the modality split: media tokens at the video rate
    # bucket (correct for video/image; audio clips are billed higher —
    # route their share through the audio bucket).
    # audio_media_tokens was accumulated using the calibrated rate during
    # the clip loop — using it here avoids a negative video bucket when
    # the calibrated rate is below the seed.
    usage = TokenUsage(
        tokens_in=int(tokens_in),
        tokens_in_text=int(text_tokens),
        tokens_in_video=max(0, int(tokens_in - text_tokens - audio_media_tokens)),
        tokens_in_audio=int(audio_media_tokens),
        tokens_out=int(out_tokens),
    )
    cost, _version = compute_cost(usage, model)
    return cost


async def estimate_clips(
    conn,
    repo,
    clips: list[ClipEstimateInput],
    *,
    prompt_body: str,
    schema: dict,
    model: str,
    prompt_hash_override: str | None = None,
    media_resolution: str | None = None,
) -> RunEstimate:
    if not clips:
        return _EMPTY_ESTIMATE

    p_hash = prompt_hash_override or _prompt_hash(prompt_body)
    prompt_tokens = (len(prompt_body) + len(str(schema))) / CHARS_PER_TOKEN

    # One repo round per distinct media kind, NOT per clip. Percentiles are
    # resolved here once per kind — the clip loop only does arithmetic.
    stats: dict[str, _KindStats] = {}
    for kind in {c.media_kind for c in clips}:
        stats[kind] = await _kind_history(
            conn, repo, kind=kind, model=model, p_hash=p_hash, media_resolution=media_resolution
        )

    text_tokens = prompt_tokens * len(clips)
    tokens_in = text_tokens
    out_p50 = 0.0
    out_p90 = 0.0
    audio_media_tokens = 0.0
    for c in clips:
        media_in, audio_in, p50, p90 = _clip_tokens(c, stats[c.media_kind])
        tokens_in += media_in
        audio_media_tokens += audio_in
        out_p50 += p50
        out_p90 += p90

    confidence, n_samples = _confidence(stats)

    cost = partial(
        _estimate_cost,
        model,
        tokens_in=tokens_in,
        text_tokens=text_tokens,
        audio_media_tokens=audio_media_tokens,
    )
    return RunEstimate(
        tokens_in=int(tokens_in),
        tokens_out_p50=int(out_p50),
        tokens_out_p90=int(out_p90),
        cost_usd_p50=cost(out_tokens=out_p50),
        cost_usd_p90=cost(out_tokens=out_p90),
        confidence=confidence,
        n_samples=n_samples,
        n_clips=len(clips),
    )


def _classify_cached_row(row) -> str:
    """Media kind for one clip_cache row. name is last-ditch: clip titles
    sometimes carry an extension (fs provider). Misclassification falls
    through to 'video+audio', the safe non-image default for estimation."""
    cj = row["canonical_json"] or {}
    media = cj.get("media") or {}
    path = media.get("cached_path") or media.get("upstream_handle") or cj.get("name")
    return classify_media_kind(str(path) if path else None)


def _dimensions_from_cached_row(row) -> tuple[int | None, int | None]:
    """Extract pixel width/height from a clip_cache row's provider_data.media dict.

    CatDV stores image/video dimensions in the raw provider_data under the
    "media" key (e.g. provider_data["media"]["width"]). The canonical MediaRef
    sub-dict does NOT carry dimensions, so we reach into provider_data.

    Returns (None, None) for any missing/non-integer value — callers must
    handle None gracefully (used only for the image-tile estimate, where
    None → 1-tile fallback in _image_tiles).
    """
    cj = row["canonical_json"] or {}
    provider_media = (cj.get("provider_data") or {}).get("media") or {}
    return _coerce_dim(provider_media.get("width")), _coerce_dim(provider_media.get("height"))


def _coerce_dim(v) -> int | None:
    """Coerce a provider dimension to a positive int, or None.

    CatDV sometimes serialises width/height as numeric strings ("4000") rather
    than ints; accept those too so large images aren't silently under-estimated
    as a single tile. Non-numeric / non-positive / missing values → None.
    """
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def media_kinds_for_clip_ids(conn, *, clip_cache_repo, provider_id, clip_ids):
    """{clip_id: media_kind} DB-first (offline-safe). Uncached clips default to
    'video+audio' — the safe non-image default (never offered HIGH)."""
    cached = await clip_cache_repo.get_many_by_ids(conn, provider_id, clip_ids)
    out: dict[int, str] = {}
    for cid in clip_ids:
        row = cached.get(cid)
        out[cid] = "video+audio" if row is None else _classify_cached_row(row)
    return out


_UNKNOWN_CLIP_DURATION_SECS = 60.0  # conservative default for uncached clips


async def estimate_for_clip_ids(
    conn,
    *,
    clip_cache_repo,
    run_telemetry_repo,
    prompts_repo,
    model_config_repo,
    provider_id: str,
    clip_ids: list[int],
    prompt_version_id: int,
) -> dict:
    """DB-first estimate for the UI: durations/kinds from clip_cache
    (offline-safe), history from run_telemetry. Uncached clips get a
    conservative default duration rather than failing the whole estimate.
    Returns n_unknown: count of clip_ids not present in the local cache."""
    version = await prompts_repo.get_version(conn, prompt_version_id)

    from backend.app.services.resolution import resolve_media_resolution

    _mc = await model_config_repo.get(conn, version.model)
    _model_default = _mc.default_media_resolution if _mc and not _mc.removed else None
    media_resolution = resolve_media_resolution(version.media_resolution, _model_default)

    cached = await clip_cache_repo.get_many_by_ids(conn, provider_id, clip_ids)
    clips: list[ClipEstimateInput] = []
    n_unknown = 0
    for cid in clip_ids:
        row = cached.get(cid)
        if row is None:
            n_unknown += 1
            clips.append(
                ClipEstimateInput(
                    clip_id=cid,
                    media_kind="video+audio",
                    duration_secs=_UNKNOWN_CLIP_DURATION_SECS,
                )
            )
            continue
        w, h = _dimensions_from_cached_row(row)
        clips.append(
            ClipEstimateInput(
                clip_id=cid,
                media_kind=_classify_cached_row(row),
                duration_secs=row["duration_secs"],
                width=w,
                height=h,
            )
        )
    est = await estimate_clips(
        conn,
        run_telemetry_repo,
        clips,
        prompt_body=version.body,
        schema=version.output_schema,
        model=version.model,
        media_resolution=media_resolution,
    )
    return {
        "tokens_in": est.tokens_in,
        "tokens_out_p50": est.tokens_out_p50,
        "tokens_out_p90": est.tokens_out_p90,
        "cost_usd_p50": est.cost_usd_p50,
        "cost_usd_p90": est.cost_usd_p90,
        "pricing_missing": est.cost_usd_p50 is None,
        "confidence": est.confidence,
        "n_samples": est.n_samples,
        "n_clips": est.n_clips,
        "n_unknown": n_unknown,
        "media_resolution": media_resolution,
    }
