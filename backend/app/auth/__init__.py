"""Auth seam — identity is established behind one boundary. See identity.py
and ADR 0078. Import ``CurrentUser`` / ``get_current_user`` from here.
"""

from backend.app.auth.identity import CurrentUser, get_current_user, resolve_user

__all__ = ["CurrentUser", "get_current_user", "resolve_user"]
