"""Secret accessor — reads from env in dev, from GCP Secret Manager in
prod. Cached per process."""

import os
from functools import lru_cache


@lru_cache(maxsize=64)
def get_secret(name: str, *, app_env: str, project_id: str | None = None) -> str:
    """Return secret value. In dev reads from env; in prod reads from Secret Manager."""
    if app_env != "prod":
        value = os.environ.get(name)
        if value is None:
            raise KeyError(f"Secret {name} not found in environment (APP_ENV=dev)")
        return value

    from google.cloud import secretmanager  # type: ignore[import-not-found]

    if not project_id:
        raise RuntimeError("project_id required to fetch secrets in prod")

    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{project_id}/secrets/{name}/versions/latest"
    response = client.access_secret_version(request={"name": resource})
    return response.payload.data.decode("utf-8")
