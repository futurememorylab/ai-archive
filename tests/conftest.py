"""Test-suite-wide configuration and shared fixtures.

Why the env autouse fixture below exists
----------------------------------------
Many tests assume a developer-style ``.env`` file is present with the required
Settings fields (CATDV_BASE_URL, CATDV_CATALOG_ID, GCP_PROJECT_ID,
GCS_BUCKET_NAME) populated. In a clean environment (CI, fresh sandbox,
new contributor) there is no ``.env``, so ``Settings()`` raises and unrelated
tests fail at import time.

We also need to keep the suite from making any real network calls. The startup
lifespan only initialises external clients when
``_real_external_enabled(settings)`` is True, which requires *all* of
CATDV_USERNAME / CATDV_PASSWORD / GCP_* to be truthy. Setting the credentials
to empty strings is enough to flip that flag off, so the FastAPI app boots
in "offline" mode and never tries to log in to CatDV (which would take a
license seat and, against a stub URL, hang). We also force
``CATDV_OFFLINE=true`` as a belt-and-braces default — individual tests that
exercise the auto-fallback path override it via monkeypatch.

The fixture only sets variables that are NOT already set, so a real
``.env`` (or shell exports) still wins for local development.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_TEST_ENV_DEFAULTS = {
    "APP_ENV": "dev",
    "CATDV_OFFLINE": "true",
    "CATDV_BASE_URL": "http://localhost:0",
    "CATDV_USERNAME": "",
    "CATDV_PASSWORD": "",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "test-project",
    "GCS_BUCKET_NAME": "test-bucket",
    "INSTANCE_ID": "test-instance",
}


@pytest.fixture(autouse=True, scope="session")
def _safe_test_env_defaults():
    """Populate required Settings env vars only when not already set.

    See module docstring for rationale.
    """
    for key, value in _TEST_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)
    yield
