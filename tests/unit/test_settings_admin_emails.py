"""ADMIN_EMAILS is a comma-separated env string parsed into a normalized list
(lowercased, trimmed, de-duped) — the deploy-time root of trust for the first
admins (spec 2026-06-14-iap-roles-admin-console-design.md)."""
import pytest
from pydantic import ValidationError

from backend.app.settings import Settings


def _settings(**over) -> Settings:
    base = dict(
        catdv_base_url="http://localhost:0",
        catdv_catalog_id=881507,
        gcp_project_id="p",
        gcs_bucket_name="b",
    )
    base.update(over)
    return Settings(**base)


def test_admin_email_list_empty_by_default():
    assert _settings().admin_email_list == []


def test_admin_email_list_parses_and_normalizes():
    s = _settings(admin_emails="  Maya@X.com , elena@x.com ,maya@x.com")
    assert s.admin_email_list == ["maya@x.com", "elena@x.com"]


def test_prod_refuses_non_iap_backend():
    """Cloud (app_env=prod) must run gated: refuse to boot with the dev backend,
    which would treat every IAP-admitted user as implicit admin."""
    with pytest.raises(ValidationError):
        _settings(app_env="prod", auth_backend="dev")
