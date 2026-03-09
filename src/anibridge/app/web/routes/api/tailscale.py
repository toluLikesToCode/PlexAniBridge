"""Tailscale management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from anibridge.app.web.routes.api.config import require_config_api_access
from anibridge.app.web.services.tailscale_service import (
    TailscaleService,
    get_tailscale_service,
)
from anibridge.app.web.state import get_app_state

__all__ = ["router"]


class TailscaleStatusResponse(BaseModel):
    enabled: bool
    daemon_running: bool
    authenticated: bool
    status: str = "disabled"
    backend_state: str | None = None
    hostname: str | None = None
    advertised_hostname: str | None = None
    dns_name: str | None = None
    tailnet: str | None = None
    ips: list[str] = Field(default_factory=list)
    online: bool = False
    health: list[str] = Field(default_factory=list)
    auth_url: str | None = None
    tailnet_url: str | None = None
    last_error: str | None = None
    exit_nodes_supported: bool = False


class TailscaleEnableRequest(BaseModel):
    hostname: str | None = None


class TailscaleHostnameRequest(BaseModel):
    hostname: str | None = None


class TailscaleActionResponse(BaseModel):
    ok: bool
    status: TailscaleStatusResponse


class TailscaleLoginResponse(TailscaleActionResponse):
    auth_url: str | None = None


router = APIRouter(dependencies=[Depends(require_config_api_access)])


def _service() -> TailscaleService:
    app_state = get_app_state()
    service = app_state.tailscale_service
    if service is None:
        service = get_tailscale_service()
        app_state.set_tailscale_service(service)
    return service


def _handle_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc) or "Tailscale operation failed",
    )


@router.get("/status", response_model=TailscaleStatusResponse)
async def get_status() -> TailscaleStatusResponse:
    """Return current Tailscale service status."""
    try:
        payload = await _service().status()
    except Exception as exc:
        raise _handle_service_error(exc) from exc
    return TailscaleStatusResponse(**payload)


@router.post("/enable", response_model=TailscaleActionResponse)
async def enable_tailscale(
    request: TailscaleEnableRequest,
) -> TailscaleActionResponse:
    """Enable Tailscale and start tailscaled."""
    try:
        payload = await _service().enable(hostname=request.hostname)
    except Exception as exc:
        raise _handle_service_error(exc) from exc
    return TailscaleActionResponse(ok=True, status=TailscaleStatusResponse(**payload))


@router.post("/disable", response_model=TailscaleActionResponse)
async def disable_tailscale() -> TailscaleActionResponse:
    """Disable Tailscale and stop tailscaled."""
    try:
        payload = await _service().disable()
    except Exception as exc:
        raise _handle_service_error(exc) from exc
    return TailscaleActionResponse(ok=True, status=TailscaleStatusResponse(**payload))


@router.post("/login", response_model=TailscaleLoginResponse)
async def login_tailscale() -> TailscaleLoginResponse:
    """Trigger Tailscale authentication flow."""
    try:
        payload = await _service().login()
    except Exception as exc:
        raise _handle_service_error(exc) from exc
    status_model = TailscaleStatusResponse(**payload)
    return TailscaleLoginResponse(
        ok=True,
        auth_url=status_model.auth_url,
        status=status_model,
    )


@router.post("/logout", response_model=TailscaleActionResponse)
async def logout_tailscale() -> TailscaleActionResponse:
    """Logout the current Tailscale session."""
    try:
        payload = await _service().logout()
    except Exception as exc:
        raise _handle_service_error(exc) from exc
    return TailscaleActionResponse(ok=True, status=TailscaleStatusResponse(**payload))


@router.post("/hostname", response_model=TailscaleActionResponse)
async def update_hostname(
    request: TailscaleHostnameRequest,
) -> TailscaleActionResponse:
    """Update persisted hostname and apply when connected."""
    try:
        payload = await _service().set_hostname(request.hostname)
    except Exception as exc:
        raise _handle_service_error(exc) from exc
    return TailscaleActionResponse(ok=True, status=TailscaleStatusResponse(**payload))
