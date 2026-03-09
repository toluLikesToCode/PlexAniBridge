"""Tests for Tailscale API endpoints."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from anibridge.app.web.routes.api import config as config_api_module
from anibridge.app.web.routes.api import tailscale as tailscale_api_module

_BASE_STATUS = {
    "enabled": False,
    "daemon_running": False,
    "authenticated": False,
    "backend_state": None,
    "hostname": None,
    "advertised_hostname": None,
    "dns_name": None,
    "tailnet": None,
    "ips": [],
    "online": False,
    "health": [],
    "auth_url": None,
    "last_error": None,
    "exit_nodes_supported": False,
}


class _DummyTailscaleService:
    async def status(self):
        return dict(_BASE_STATUS)

    async def enable(self, hostname: str | None = None):
        payload = dict(_BASE_STATUS)
        payload.update({"enabled": True, "hostname": hostname})
        return payload

    async def disable(self):
        return dict(_BASE_STATUS)

    async def login(self):
        payload = dict(_BASE_STATUS)
        payload.update(
            {
                "enabled": True,
                "daemon_running": True,
                "auth_url": "https://login.tailscale.com/a/example",
            }
        )
        return payload

    async def logout(self):
        return dict(_BASE_STATUS)

    async def set_hostname(self, hostname: str | None):
        payload = dict(_BASE_STATUS)
        payload.update({"hostname": hostname})
        return payload


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(tailscale_api_module.router, prefix="/api/tailscale")
    return app


def _allow_config_api(monkeypatch: MonkeyPatch, *, allowed: bool) -> None:
    monkeypatch.setattr(
        config_api_module,
        "runtime_config",
        SimpleNamespace(
            web=SimpleNamespace(
                has_auth=False,
                allow_config_without_auth=allowed,
            )
        ),
        raising=False,
    )


def test_tailscale_api_blocked_when_config_api_is_blocked(
    monkeypatch: MonkeyPatch,
) -> None:
    """Tailscale API follows config API access policy."""
    _allow_config_api(monkeypatch, allowed=False)

    state = SimpleNamespace(tailscale_service=_DummyTailscaleService())
    monkeypatch.setattr(tailscale_api_module, "get_app_state", lambda: state)

    client = TestClient(_build_app())
    response = client.get("/api/tailscale/status")

    assert response.status_code == 403
    assert "Configuration API is disabled" in response.json()["detail"]


def test_tailscale_api_endpoints(monkeypatch: MonkeyPatch) -> None:
    """Tailscale API should expose status and mutation routes."""
    _allow_config_api(monkeypatch, allowed=True)

    state = SimpleNamespace(tailscale_service=_DummyTailscaleService())
    monkeypatch.setattr(tailscale_api_module, "get_app_state", lambda: state)

    client = TestClient(_build_app())

    status_response = client.get("/api/tailscale/status")
    assert status_response.status_code == 200
    assert status_response.json()["enabled"] is False

    enable_response = client.post(
        "/api/tailscale/enable",
        json={"hostname": "anime-node"},
    )
    assert enable_response.status_code == 200
    assert enable_response.json()["status"]["enabled"] is True
    assert enable_response.json()["status"]["hostname"] == "anime-node"

    login_response = client.post("/api/tailscale/login")
    assert login_response.status_code == 200
    assert login_response.json()["auth_url"] == "https://login.tailscale.com/a/example"

    hostname_response = client.post(
        "/api/tailscale/hostname",
        json={"hostname": "renamed-node"},
    )
    assert hostname_response.status_code == 200
    assert hostname_response.json()["status"]["hostname"] == "renamed-node"

    logout_response = client.post("/api/tailscale/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["ok"] is True

    disable_response = client.post("/api/tailscale/disable")
    assert disable_response.status_code == 200
    assert disable_response.json()["status"]["enabled"] is False
