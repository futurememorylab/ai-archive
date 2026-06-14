"""Auth adapters — the *only* place IAP/OAuth implementation specifics may
live (ADR 0078). Each adapter exposes ``current_user(request, settings) ->
CurrentUser``; the seam (``backend/app/auth/identity.py``) dispatches to one
based on ``settings.auth_backend``. The boundary is enforced by
``tests/unit/test_auth_seam_boundary.py``.
"""
