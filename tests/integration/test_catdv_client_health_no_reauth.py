"""health() must NOT trigger a re-login on AUTH envelope. Otherwise the
probe itself can take the CatDV seat that the probe was looking for —
the very thing CLAUDE.md's 'CatDV session discipline' warns about."""

import pytest

from backend.app.services.catdv_client import CatdvAuthError, CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_health_raises_authentication_required_without_relogin():
    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Track login call count after the initial login.
            login_calls_before = fake.login_call_count
            # Force AUTH envelope on the next request.
            import time
            fake.force_auth_until = time.time() + 60

            with pytest.raises(CatdvAuthError):
                await client.health()

            # The health probe must NOT have triggered a re-login.
            assert fake.login_call_count == login_calls_before, (
                f"login_calls before={login_calls_before} after={fake.login_call_count}; "
                "health() should not relogin"
            )
