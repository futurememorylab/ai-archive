"""Calibration helpers. The confidence a resolution has 'unlocked' mirrors the
estimator's sample thresholds (run_estimator._MIN_SAMPLES / _GOOD_SAMPLES)."""

from backend.app.services.run_estimator import _GOOD_SAMPLES, _MIN_SAMPLES


def confidence_for_samples(n: int) -> str:
    if n >= _GOOD_SAMPLES:
        return "good"
    if n >= _MIN_SAMPLES:
        return "fair"
    return "rough"
