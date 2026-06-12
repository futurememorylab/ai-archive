"""Guard: the Cloud Run deploy must scale to zero (min=0) but never run more
than one instance (max=1) — one CatDV seat, one Litestream writer, one
in-process write queue. See ADR 0077 and the scale-to-zero spec."""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml"


def test_deploy_scales_to_zero_and_caps_at_one():
    text = WORKFLOW.read_text()
    assert "--min-instances=0" in text, "scale-to-zero requires min-instances=0"
    assert "--max-instances=1" in text, "max-instances must stay 1 (single seat/writer/queue)"
    assert "--min-instances=1" not in text, "min-instances=1 re-pins the instance floor"
    # CPU-always-allocated is required so Litestream + background loops run
    # between requests and the SIGTERM teardown reliably gets CPU.
    assert "--no-cpu-throttling" in text
