"""Guard: routes must never execute jobs themselves. Job execution belongs to
the lifespan-owned JobRunner (services/job_runner.py). Routes only insert
pending rows + (for cancel) call job_runner.cancel. See ADR 0125."""

from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "backend" / "app" / "routes"

BANNED = ("run_job", "_running_jobs", "start_job_in_background", "drain_running_jobs")


def test_routes_do_not_execute_jobs():
    offenders = []
    for py in ROUTES.rglob("*.py"):
        text = py.read_text()
        for token in BANNED:
            if token in text:
                offenders.append(f"{py.name}: {token}")
    assert not offenders, (
        "Routes must not execute jobs — use the lifespan JobRunner (ADR 0125). "
        f"Found: {offenders}"
    )
