"""HTML page routers, split by feature (clips, prompts).

`page_routers` is consumed by main.py to register each feature router with
the FastAPI app.
"""

from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router

page_routers = [clips_router, prompts_router]

__all__ = ["page_routers", "clips_router", "prompts_router"]
