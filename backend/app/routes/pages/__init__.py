"""HTML page routers, split by feature (clips, prompts, studio, review).

`page_routers` is consumed by main.py to register each feature router with
the FastAPI app.
"""

from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router
from backend.app.routes.pages.review import router as review_router
from backend.app.routes.pages.studio import router as studio_router

page_routers = [clips_router, prompts_router, studio_router, review_router]

__all__ = ["page_routers", "clips_router", "prompts_router", "studio_router", "review_router"]
