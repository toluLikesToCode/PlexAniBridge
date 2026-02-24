"""Webhook route aggregator."""

from fastapi.routing import APIRouter

from .jellyfin import router as jellyfin_router
from .plex import router as plex_router

__all__ = ["router"]

router = APIRouter()
router.include_router(plex_router, prefix="/plex", tags=["plex"])
router.include_router(jellyfin_router, prefix="/jellyfin", tags=["jellyfin"])
