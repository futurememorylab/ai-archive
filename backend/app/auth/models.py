"""Identity value type shared by the seam and its adapters.

Lives in its own module so both ``identity`` (the dispatcher) and the
``adapters`` can import ``CurrentUser`` without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.auth.roles import ROLE_CAPS


@dataclass(frozen=True)
class CurrentUser:
    """Who is making the current request, and what they may do.

    ``role`` is populated by the authorization layer (the ``user_roles``
    lookup in the auth gate). ``permissions`` is derived from ``role`` via
    ``ROLE_CAPS`` so there is no stored permission state to drift.
    """

    email: str
    role: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.email)

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(ROLE_CAPS.get(self.role or "", set()))

    def has(self, cap: str) -> bool:
        return cap in self.permissions

    @property
    def is_admin(self) -> bool:
        return self.has("manage")
