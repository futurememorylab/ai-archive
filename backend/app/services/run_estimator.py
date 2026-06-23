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

from backend.app.media_kind import classify_media_kind
from backend.app.services.pricing import compute_cost, rate_cards
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


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    # half-up rounding — avoids banker's-rounding surprises at even n
    idx = min(len(s) - 1, max(0, int(q * (len(s) - 1) + 0.5)))
    return s[idx]


def _image_tiles(width: int | None, height: int | None) -> int:
    if not width or not height:
        return 1
    return max(1, -(-width // 768)) * max(1, -(-height // 768))


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
        return RunEstimate(
            tokens_in=0,
            tokens_out_p50=0,
            tokens_out_p90=0,
            cost_usd_p50=None,
            cost_usd_p90=None,
            confidence="rough",
            n_samples=0,
            n_clips=0,
        )

    p_hash = prompt_hash_override or _prompt_hash(prompt_body)
    prompt_tokens = (len(prompt_body) + len(str(schema))) / CHARS_PER_TOKEN

    # One repo round per distinct media kind, NOT per clip. Percentiles are
    # resolved here once per kind — the clip loop only does arithmetic.
    kinds = {c.media_kind for c in clips}
    input_ratio: dict[str, float] = {}
    stats: dict[str, _KindStats] = {}
    for kind in kinds:
        if kind != "image":
            ratios = await repo.recent_input_ratios(
                conn, model=model, media_kind=kind, media_resolution=media_resolution
            )
            if len(ratios) >= _MIN_SAMPLES:
                input_ratio[kind] = _pct(ratios, 0.5)
        rates = await repo.recent_output_rates(
            conn,
            model=model,
            media_kind=kind,
            prompt_hash=p_hash,
            media_resolution=media_resolution,
        )
        level = 1
        if len(rates) < _MIN_SAMPLES:
            rates = await repo.recent_output_rates(
                conn, model=model, media_kind=kind, media_resolution=media_resolution
            )
            level = 2
        if len(rates) >= _MIN_SAMPLES:
            stats[kind] = _KindStats(
                p50=_pct(rates, 0.5), p90=_pct(rates, 0.9), n=len(rates), level=level
            )
        else:
            stats[kind] = _KindStats(p50=None, p90=None, n=0, level=3)

    tokens_in = prompt_tokens * len(clips)
    out_p50 = 0.0
    out_p90 = 0.0
    audio_media_tokens = 0.0
    for c in clips:
        st = stats[c.media_kind]
        if c.media_kind == "image":
            tokens_in += _image_tiles(c.width, c.height) * IMAGE_TILE_TOKENS
            out_p50 += st.p50 if st.p50 is not None else SEED_OUTPUT_TOKENS_PER_IMAGE
            out_p90 += st.p90 if st.p90 is not None else SEED_OUTPUT_TOKENS_PER_IMAGE * 2
        else:
            dur = float(c.duration_secs or 0.0)
            k = input_ratio.get(
                c.media_kind,
                SEED_INPUT_TOKENS_PER_SEC.get(c.media_kind, 300.0),
            )
            tokens_in += dur * k
            if c.media_kind == "audio":
                audio_media_tokens += dur * k
            out_p50 += dur * (st.p50 if st.p50 is not None else SEED_OUTPUT_TOKENS_PER_SEC)
            out_p90 += dur * (st.p90 if st.p90 is not None else SEED_OUTPUT_TOKENS_PER_SEC * 2)

    # Confidence reflects the weakest kind in the batch.
    worst_level = max(st.level for st in stats.values())
    n_samples = min(st.n for st in stats.values())

    if worst_level == 1 and n_samples >= _GOOD_SAMPLES:
        confidence = "good"
    elif worst_level <= 2 and n_samples >= _MIN_SAMPLES:
        confidence = "fair"
    else:
        confidence = "rough"

    def _cost(out_tokens: float) -> float | None:
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
            tokens_in_text=int(prompt_tokens * len(clips)),
            tokens_in_video=max(
                0, int(tokens_in - prompt_tokens * len(clips) - audio_media_tokens)
            ),
            tokens_in_audio=int(audio_media_tokens),
            tokens_out=int(out_tokens),
        )
        cost, _version = compute_cost(usage, model)
        return cost

    return RunEstimate(
        tokens_in=int(tokens_in),
        tokens_out_p50=int(out_p50),
        tokens_out_p90=int(out_p90),
        cost_usd_p50=_cost(out_p50),
        cost_usd_p90=_cost(out_p90),
        confidence=confidence,
        n_samples=n_samples,
        n_clips=len(clips),
    )


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
        cj = row["canonical_json"] or {}
        media = cj.get("media") or {}
        # name is last-ditch: clip titles sometimes carry an extension
        # (fs provider). Misclassification falls through to "video+audio",
        # the safe default for estimation.
        path = media.get("cached_path") or media.get("upstream_handle") or cj.get("name")
        clips.append(
            ClipEstimateInput(
                clip_id=cid,
                media_kind=classify_media_kind(str(path) if path else None),
                duration_secs=row["duration_secs"],
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
        "confidence": est.confidence,
        "n_samples": est.n_samples,
        "n_clips": est.n_clips,
        "n_unknown": n_unknown,
        "media_resolution": media_resolution,
    }
