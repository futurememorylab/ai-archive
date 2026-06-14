"""Access-control pages — the app-rendered states of the IAP login design
(spec 2026-06-13-iap-access-control-design.md): state 3 "Access not granted"
(a signed-in user who passed the IAP gate but has no role in `user_roles`)
and state 4 "Error". Google/IAP owns the sign-in + redirect states upstream,
so they are not rendered here.

Today this is a plain render endpoint so the page is viewable and reviewable
before the auth gate lands. When `get_current_user` is wired (PR2), it will
render `pages/access.html` directly with a 403 for an authenticated-but-unroled
user, and this route must be on the auth gate's allow-list (it is the page the
gate redirects to — gating it would loop).
"""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app.routes.pages.templates import templates

router = APIRouter()


@router.get("/access", response_class=HTMLResponse)
async def access(
    request: Request,
    state: Literal["denied", "error"] = "denied",
    email: str | None = None,
):
    # Anything other than the explicit "error" state shows the denial card.
    view = "error" if state == "error" else "denied"
    return templates.TemplateResponse(
        request,
        "pages/access.html",
        {"state": view, "email": email},
    )
