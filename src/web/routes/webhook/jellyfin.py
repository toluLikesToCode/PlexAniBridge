"""Jellyfin Webhook endpoint."""

from typing import Any

from fastapi.param_functions import Depends
from fastapi.routing import APIRouter
from starlette.requests import Request

from src import log
from src.config.settings import MediaServerProvider, SyncMode
from src.exceptions import (
    InvalidWebhookPayloadError,
    ProfileNotFoundError,
    SchedulerNotInitializedError,
    WebhookModeDisabledError,
)
from src.models.schemas.jellyfin import JellyfinWebhook
from src.web.state import get_app_state

__all__ = ["router"]

router = APIRouter()


async def parse_webhook_request(request: Request) -> JellyfinWebhook:
    """Parse incoming Jellyfin webhook request body as JSON."""
    try:
        data = await request.json()
    except Exception as e:
        raise InvalidWebhookPayloadError(f"Invalid JSON body: {e}") from e
    try:
        return JellyfinWebhook.model_validate(data)
    except Exception as e:
        raise InvalidWebhookPayloadError(f"Invalid payload structure: {e}") from e


@router.post("")
async def jellyfin_webhook(
    payload: JellyfinWebhook = Depends(parse_webhook_request),
) -> dict[str, Any]:
    """Receive Jellyfin webhook and trigger a targeted sync."""
    scheduler = get_app_state().scheduler
    if not scheduler:
        log.warning("Webhook: Scheduler not available")
        raise SchedulerNotInitializedError("Scheduler not available")

    if not payload.account_id:
        raise InvalidWebhookPayloadError("No account ID found in webhook payload")
    if not payload.top_level_rating_key:
        raise InvalidWebhookPayloadError("No item ID found in webhook payload")

    try:
        profiles = [
            p
            for p in scheduler.get_profiles_for_server_account(
                MediaServerProvider.JELLYFIN, payload.account_id
            )
            if SyncMode.WEBHOOK in p[1].sync_modes
        ]
    except KeyError as e:
        raise ProfileNotFoundError("Profile not found") from e

    if not profiles:
        raise WebhookModeDisabledError(
            "Webhook sync mode is not enabled for this profile"
        )

    success = False
    for profile_name, _ in profiles:
        try:
            await scheduler.trigger_sync(
                profile_name=profile_name,
                poll=False,
                rating_keys=[payload.top_level_rating_key],
            )
            success = True
        except KeyError:
            continue

    return {
        "ok": success,
        "processed_rating_key": payload.top_level_rating_key,
        "event": payload.event,
    }
