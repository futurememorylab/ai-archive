"""HTML page routers, split by feature (clips, prompts, studio, admin).

`page_routers` is consumed by main.py to register each feature router with
the FastAPI app. The admin area is split: `admin_router` is the enum console,
`admin_access_router` is the Access & Permissions (IAP roles) section, and
`access_router` serves the standalone access-denied page.
"""

from backend.app.routes.pages.access import router as access_router
from backend.app.routes.pages.admin import router as admin_router
from backend.app.routes.pages.admin_access import router as admin_access_router
from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router
from backend.app.routes.pages.studio import router as studio_router

page_routers = [
    clips_router,
    prompts_router,
    studio_router,
    access_router,
    admin_router,
    admin_access_router,
]

__all__ = [
    "page_routers",
    "access_router",
    "admin_router",
    "admin_access_router",
    "clips_router",
    "prompts_router",
    "studio_router",
]
