"""Guard: staging and prod SHARE the proxy/AI-store bucket (uploads are
namespaced by INSTANCE_ID, not by bucket — see staging.env.yaml header and
issue #55). So `GCS_BUCKET_NAME` MUST be identical in both deploy env files.

A typo in either (e.g. `catdv-proxies` instead of `catdav-proxies`) points an
instance at a non-existent bucket and breaks ALL media caching there — every
`ensure_uploaded` fails — while the other instance keeps working, which makes
it look like a code bug rather than a config typo. This test pins the two
values together so the divergence fails CI instead of staging."""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
STAGING = DEPLOY / "staging.env.yaml"
PROD = DEPLOY / "cloudrun.env.yaml"


def _bucket(env_file: Path) -> str:
    m = re.search(r'^GCS_BUCKET_NAME:\s*"?([^"\n]+)"?', env_file.read_text(), re.M)
    assert m, f"GCS_BUCKET_NAME not found in {env_file.name}"
    return m.group(1).strip()


def test_staging_and_prod_share_the_same_proxy_bucket():
    staging_bucket = _bucket(STAGING)
    prod_bucket = _bucket(PROD)
    assert staging_bucket == prod_bucket, (
        f"staging GCS_BUCKET_NAME={staging_bucket!r} != prod {prod_bucket!r}; "
        "they must point at the SAME bucket (uploads namespaced by INSTANCE_ID)"
    )
