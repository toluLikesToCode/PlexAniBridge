"""Tests for Tailscale service behavior."""

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from anibridge.app.config.database import db
from anibridge.app.models.db.housekeeping import Housekeeping
from anibridge.app.web.services.tailscale_service import TailscaleService


def _set_housekeeping(key: str, value: str | None) -> None:
    with db() as ctx:
        existing = ctx.session.get(Housekeeping, key)
        if value is None:
            if existing is not None:
                ctx.session.delete(existing)
        else:
            ctx.session.merge(Housekeeping(key=key, value=value))
        ctx.session.commit()


def _get_housekeeping(key: str) -> str | None:
    with db() as ctx:
        row = ctx.session.get(Housekeeping, key)
        return row.value if row else None


@pytest.fixture(autouse=True)
def _clean_tailscale_housekeeping() -> None:
    keys = (
        TailscaleService._ENABLED_KEY,
        TailscaleService._HOSTNAME_KEY,
        TailscaleService._STATE_KEY,
    )
    for key in keys:
        _set_housekeeping(key, None)
    yield
    for key in keys:
        _set_housekeeping(key, None)


@pytest.mark.asyncio
async def test_enable_persists_hostname_and_enabled_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enabling Tailscale should persist settings to housekeeping."""
    service = TailscaleService(data_path=tmp_path)

    async def fake_start() -> None:
        service._daemon = SimpleNamespace(returncode=None)

    async def fake_apply(_hostname: str) -> None:
        return None

    monkeypatch.setattr(service, "_start_daemon_locked", fake_start)
    monkeypatch.setattr(service, "_ensure_health_task_locked", lambda: None)
    monkeypatch.setattr(service, "_apply_hostname_locked", fake_apply)

    payload = await service.enable("Ani-Node")

    assert payload["enabled"] is True
    assert payload["hostname"] == "ani-node"
    assert _get_housekeeping(TailscaleService._ENABLED_KEY) == "1"
    assert _get_housekeeping(TailscaleService._HOSTNAME_KEY) == "ani-node"


@pytest.mark.asyncio
async def test_enable_does_not_persist_enabled_flag_when_start_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failed daemon start should not leave enabled state persisted."""
    service = TailscaleService(data_path=tmp_path)
    monkeypatch.setattr(
        service,
        "_start_daemon_locked",
        AsyncMock(side_effect=FileNotFoundError("tailscaled not found")),
    )

    with pytest.raises(FileNotFoundError):
        await service.enable("ani-node")

    assert service._enabled is False
    assert _get_housekeeping(TailscaleService._ENABLED_KEY) is None


@pytest.mark.asyncio
async def test_initialize_restores_and_persists_state_blob(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Persisted state blobs should be restored to and re-read from the state file."""
    encoded = base64.b64encode(b"seed-state").decode("ascii")
    _set_housekeeping(TailscaleService._STATE_KEY, encoded)
    _set_housekeeping(TailscaleService._ENABLED_KEY, "0")

    service = TailscaleService(data_path=tmp_path)
    monkeypatch.setattr(service, "_start_daemon_locked", lambda: None)

    await service.initialize()
    assert service._state_file.read_bytes() == b"seed-state"

    service._state_file.write_bytes(b"updated-state")
    async with service._lock:
        await service._persist_state_blob_locked()

    assert _get_housekeeping(TailscaleService._STATE_KEY) == base64.b64encode(
        b"updated-state"
    ).decode("ascii")


@pytest.mark.asyncio
async def test_login_updates_auth_url_and_enabled_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Login flow should keep Tailscale enabled and expose auth URL."""
    service = TailscaleService(data_path=tmp_path)

    async def fake_start() -> None:
        service._daemon = SimpleNamespace(returncode=None)

    async def fake_status() -> dict[str, str]:
        return {"BackendState": "NeedsLogin"}

    async def fake_run_up() -> str:
        return "https://login.tailscale.com/a/example"

    async def fake_persist() -> None:
        return None

    monkeypatch.setattr(service, "_start_daemon_locked", fake_start)
    monkeypatch.setattr(service, "_ensure_health_task_locked", lambda: None)
    monkeypatch.setattr(service, "_status_json_locked", fake_status)
    monkeypatch.setattr(service, "_run_up_locked", fake_run_up)
    monkeypatch.setattr(service, "_persist_state_blob_locked", fake_persist)

    payload = await service.login()

    assert payload["enabled"] is True
    assert payload["auth_url"] == "https://login.tailscale.com/a/example"
    assert _get_housekeeping(TailscaleService._ENABLED_KEY) == "1"


@pytest.mark.asyncio
async def test_stop_up_process_force_kills_when_terminate_times_out(
    tmp_path: Path,
) -> None:
    """Timed out `tailscale up` termination should escalate to kill."""
    service = TailscaleService(data_path=tmp_path)
    process = SimpleNamespace(
        returncode=None,
        terminate=Mock(),
        kill=Mock(),
        wait=AsyncMock(side_effect=[TimeoutError(), None]),
    )
    service._up_process = process

    async with service._lock:
        await service._stop_up_process_locked()

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2
    assert service._up_process is None
