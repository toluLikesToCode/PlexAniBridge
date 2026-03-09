"""Tailscale runtime management service for the web layer."""

import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from anibridge.utils.cache import cache

from anibridge.app import config, log
from anibridge.app.config.database import db
from anibridge.app.models.db.housekeeping import Housekeeping

__all__ = ["TailscaleService", "get_tailscale_service"]


class TailscaleService:
    """Manage a userspace Tailscale daemon and its persisted runtime state."""

    _ENABLED_KEY = "tailscale_enabled"
    _STATE_KEY = "tailscale_state"
    _HOSTNAME_KEY = "tailscale_hostname"
    _AUTH_URL_RE = re.compile(r"https://\S+")
    _HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

    def __init__(
        self,
        *,
        data_path: Path | None = None,
        tailscale_bin: str | None = None,
        tailscaled_bin: str | None = None,
        health_interval_seconds: float = 30.0,
    ) -> None:
        """Initialize service configuration and runtime state."""
        root = (data_path or config.data_path).resolve()
        self._runtime_dir = root / "tailscale"
        self._state_file = self._runtime_dir / "tailscaled.state"
        self._socket_file = self._runtime_dir / "tailscaled.sock"

        self._tailscale_bin = tailscale_bin or os.getenv(
            "AB_TAILSCALE_BIN",
            "tailscale",
        )
        self._tailscaled_bin = tailscaled_bin or os.getenv(
            "AB_TAILSCALED_BIN", "tailscaled"
        )

        self._health_interval = max(5.0, health_interval_seconds)
        self._lock = asyncio.Lock()
        self._daemon: asyncio.subprocess.Process | None = None
        self._up_process: asyncio.subprocess.Process | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._up_monitor_task: asyncio.Task[None] | None = None

        self._initialized = False
        self._enabled = False
        self._hostname: str | None = None
        self._last_state_blob: str | None = None
        self._last_error: str | None = None
        self._last_auth_url: str | None = None

    async def initialize(self) -> None:
        """Load persisted config/state and start daemon when enabled."""
        async with self._lock:
            if self._initialized:
                return

            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            self._load_persisted_settings_locked()
            self._restore_state_blob_locked()
            self._initialized = True

            if self._enabled:
                try:
                    await self._start_daemon_locked()
                    self._ensure_health_task_locked()
                except Exception as exc:
                    self._last_error = str(exc)
                    log.exception("Web - Failed to initialize Tailscale service")

    async def shutdown(self) -> None:
        """Stop background checks and terminate the daemon."""
        await self.initialize()
        async with self._lock:
            await self._stop_health_task_locked()
            await self._persist_state_blob_locked()
            await self._stop_daemon_locked()

    async def status(self) -> dict[str, Any]:
        """Return runtime status exposed by the API."""
        await self.initialize()
        async with self._lock:
            return await self._status_locked(refresh_daemon=True)

    async def enable(self, hostname: str | None = None) -> dict[str, Any]:
        """Enable Tailscale and ensure the daemon is running."""
        await self.initialize()
        async with self._lock:
            if hostname is not None:
                self._hostname = self._normalize_hostname(hostname)

            self._enabled = True
            self._save_enabled_locked(self._enabled)
            self._save_hostname_locked(self._hostname)

            await self._start_daemon_locked()
            self._ensure_health_task_locked()

            return await self._status_locked(refresh_daemon=True)

    async def disable(self) -> dict[str, Any]:
        """Disable Tailscale and stop the daemon."""
        await self.initialize()
        async with self._lock:
            self._enabled = False
            self._last_auth_url = None
            self._save_enabled_locked(self._enabled)
            await self._stop_health_task_locked()
            await self._persist_state_blob_locked()
            await self._stop_daemon_locked()
            return await self._status_locked(refresh_daemon=False)

    async def login(self) -> dict[str, Any]:
        """Run `tailscale up` and return updated status."""
        await self.initialize()
        async with self._lock:
            self._enabled = True
            self._save_enabled_locked(self._enabled)
            await self._start_daemon_locked()
            self._ensure_health_task_locked()
            auth_url = await self._run_up_locked()
            if auth_url:
                self._last_auth_url = auth_url
            await self._persist_state_blob_locked()
            return await self._status_locked(refresh_daemon=True)

    async def logout(self) -> dict[str, Any]:
        """Run `tailscale logout` and return updated status."""
        await self.initialize()
        async with self._lock:
            if self._daemon and self._daemon.returncode is None:
                with contextlib.suppress(Exception):
                    await self._run_cli_locked("logout", check=False)
            self._last_auth_url = None
            # Delete state so the next login generates a fresh node key,
            # avoiding the "Duplicate node key" error on re-authentication.
            self._state_file.unlink(missing_ok=True)
            self._save_kv_locked(self._STATE_KEY, None)
            self._last_state_blob = None
            return await self._status_locked(refresh_daemon=True)

    async def set_hostname(self, hostname: str | None) -> dict[str, Any]:
        """Persist and (when possible) apply the requested hostname."""
        await self.initialize()
        async with self._lock:
            self._hostname = self._normalize_hostname(hostname)
            self._save_hostname_locked(self._hostname)

            if self._hostname and self._daemon and self._daemon.returncode is None:
                status_json = await self._status_json_locked()
                if self._as_str(status_json, "BackendState") == "Running":
                    await self._apply_hostname_locked(self._hostname)

            return await self._status_locked(refresh_daemon=True)

    async def _status_locked(self, *, refresh_daemon: bool) -> dict[str, Any]:
        status_json: dict[str, Any] | None = None
        daemon_running = bool(self._daemon and self._daemon.returncode is None)

        if daemon_running and refresh_daemon:
            try:
                status_json = await self._status_json_locked()
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)

        backend_state = self._as_str(status_json, "BackendState")
        auth_url = self._extract_auth_url(self._as_str(status_json, "AuthURL"))
        if auth_url:
            self._last_auth_url = auth_url

        self_node = status_json.get("Self") if isinstance(status_json, dict) else {}
        if not isinstance(self_node, dict):
            self_node = {}

        dns_name = self._as_str(self_node, "DNSName")
        host_name = self._as_str(self_node, "HostName")
        ips = self._extract_ips(status_json, self_node)
        health = self._extract_health(status_json)
        online = bool(self_node.get("Online")) if self_node else False
        tailnet = self._tailnet_from_dns(dns_name)

        effective_hostname = self._hostname or self._compute_default_hostname()
        tailnet_url = (
            f"http://{dns_name.rstrip('.')}:{config.web.port}/" if dns_name else None
        )

        if not self._enabled:
            ts_status = "disabled"
        elif not daemon_running:
            ts_status = "error" if self._last_error else "starting"
        elif backend_state == "Running":
            ts_status = "connected"
        elif self._last_auth_url or backend_state in ("NeedsLogin", "NoState"):
            ts_status = "awaiting_auth"
        elif self._last_error:
            ts_status = "error"
        else:
            ts_status = "starting"

        return {
            "enabled": self._enabled,
            "daemon_running": daemon_running,
            "authenticated": backend_state == "Running",
            "backend_state": backend_state,
            "status": ts_status,
            "hostname": self._hostname,
            "advertised_hostname": host_name or effective_hostname,
            "dns_name": dns_name,
            "tailnet": tailnet,
            "ips": ips,
            "online": online,
            "health": health,
            "auth_url": auth_url or self._last_auth_url,
            "tailnet_url": tailnet_url,
            "last_error": self._last_error,
            "exit_nodes_supported": False,
        }

    def _load_persisted_settings_locked(self) -> None:
        with db() as ctx:
            enabled_row = ctx.session.get(Housekeeping, self._ENABLED_KEY)
            hostname_row = ctx.session.get(Housekeeping, self._HOSTNAME_KEY)
            state_row = ctx.session.get(Housekeeping, self._STATE_KEY)

        self._enabled = (enabled_row.value if enabled_row else "0") == "1"
        self._hostname = self._normalize_hostname(
            hostname_row.value if hostname_row else None
        )
        self._last_state_blob = state_row.value if state_row else None

    def _restore_state_blob_locked(self) -> None:
        if not self._last_state_blob:
            return
        try:
            raw = base64.b64decode(self._last_state_blob.encode("ascii"), validate=True)
        except Exception:
            raw = self._last_state_blob.encode("utf-8")
        self._state_file.write_bytes(raw)

    async def _persist_state_blob_locked(self) -> None:
        if not self._state_file.exists():
            if self._last_state_blob is not None:
                self._save_kv_locked(self._STATE_KEY, None)
                self._last_state_blob = None
            return

        raw = self._state_file.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        if encoded == self._last_state_blob:
            return
        self._save_kv_locked(self._STATE_KEY, encoded)
        self._last_state_blob = encoded

    async def _start_daemon_locked(self) -> None:
        if self._daemon and self._daemon.returncode is None:
            return

        tailscaled = self._resolve_binary(self._tailscaled_bin)
        self._socket_file.unlink(missing_ok=True)

        self._daemon = await asyncio.create_subprocess_exec(
            tailscaled,
            "--tun=userspace-networking",
            f"--socket={self._socket_file}",
            f"--state={self._state_file}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        await self._wait_for_daemon_locked()
        log.info("Web - Tailscale daemon started (userspace mode)")

    async def _stop_daemon_locked(self) -> None:
        await self._stop_up_process_locked()
        daemon = self._daemon
        self._daemon = None
        self._socket_file.unlink(missing_ok=True)
        if daemon is None:
            return

        if daemon.returncode is None:
            daemon.terminate()
            try:
                await asyncio.wait_for(daemon.wait(), timeout=5)
            except TimeoutError:
                daemon.kill()
                await daemon.wait()
        log.info("Web - Tailscale daemon stopped")

    async def _wait_for_daemon_locked(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 15.0
        while loop.time() < deadline:
            if self._daemon is None:
                raise RuntimeError("Tailscale daemon not available")
            if self._daemon.returncode is not None:
                raise RuntimeError(
                    f"Tailscale daemon exited early with code {self._daemon.returncode}"
                )
            if self._socket_file.exists():
                with contextlib.suppress(Exception):
                    await self._status_json_locked()
                    return
            await asyncio.sleep(0.2)
        raise TimeoutError("Timed out waiting for tailscaled socket readiness")

    async def _status_json_locked(self) -> dict[str, Any]:
        stdout, stderr, returncode = await self._run_cli_locked(
            "status",
            "--json",
            check=False,
        )

        if stdout:
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(stdout)

        if returncode != 0:
            raise RuntimeError(stderr or "tailscale status failed")
        return {}

    async def _run_up_locked(self) -> str | None:
        """Start `tailscale up --json` and return auth URL immediately if needed.

        The process continues running in the background after this method
        returns. ``_monitor_up_completion`` detects when the user finishes
        authenticating and updates state accordingly.
        """
        if self._daemon is None or self._daemon.returncode is not None:
            raise RuntimeError("Tailscale daemon is not running")

        tailscale = self._resolve_binary(self._tailscale_bin)
        effective_hostname = self._hostname or self._compute_default_hostname()

        await self._stop_up_process_locked()

        process = await asyncio.create_subprocess_exec(
            tailscale,
            f"--socket={self._socket_file}",
            "up",
            "--json",
            f"--hostname={effective_hostname}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._up_process = process

        # Read the first output line (auth URL JSON or "already running") quickly.
        # `tailscale up --json` writes one JSON line immediately when auth is
        # needed, then blocks until auth completes.  We must not await the full
        # process here — just grab that first line and return.
        auth_url: str | None = None
        try:
            assert process.stdout is not None
            raw = await asyncio.wait_for(process.stdout.readline(), timeout=15.0)
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    data = json.loads(line)
                    if url := data.get("AuthURL"):
                        auth_url = url
                    elif data.get("BackendState") == "Running":
                        self._up_process = None
                        return None  # Already authenticated
                if not auth_url:
                    auth_url = self._extract_auth_url(line)
        except TimeoutError:
            pass

        # If the process exited already (error, or already connected)
        if process.returncode is not None:
            if process.returncode == 0:
                self._up_process = None
                return None
            assert process.stderr is not None
            stderr_bytes = await process.stderr.read()
            err = stderr_bytes.decode("utf-8", errors="replace").strip()
            self._up_process = None
            raise RuntimeError(err or "tailscale up failed")

        if auth_url:
            self._last_auth_url = auth_url

        # Regardless of whether we got a URL, monitor the background process.
        self._up_monitor_task = asyncio.create_task(
            self._monitor_up_completion(),
            name="tailscale-up-completion",
        )
        return auth_url

    async def _stop_up_process_locked(self) -> None:
        """Terminate the background `tailscale up` process if running."""
        proc = self._up_process
        self._up_process = None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=3.0)

    async def _monitor_up_completion(self) -> None:
        """Background task: detect when `tailscale up` finishes (auth complete)."""
        process = self._up_process
        if process is None:
            return
        try:
            await process.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        async with self._lock:
            if self._up_process is process:
                self._up_process = None
            if process.returncode == 0:
                self._last_auth_url = None
                with contextlib.suppress(Exception):
                    await self._status_json_locked()
                log.info("Web - Tailscale authenticated successfully")

    async def _apply_hostname_locked(self, hostname: str) -> None:
        _, stderr, returncode = await self._run_cli_locked(
            "set",
            f"--hostname={hostname}",
            check=False,
        )
        if returncode != 0:
            raise RuntimeError(stderr or "failed to apply hostname")

    async def _run_cli_locked(
        self,
        *args: str,
        check: bool = True,
        timeout: float = 20.0,
    ) -> tuple[str, str, int]:
        if self._daemon is None or self._daemon.returncode is not None:
            raise RuntimeError("Tailscale daemon is not running")

        tailscale = self._resolve_binary(self._tailscale_bin)
        process = await asyncio.create_subprocess_exec(
            tailscale,
            f"--socket={self._socket_file}",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError(
                f"tailscale command timed out: {' '.join(args)}"
            ) from None

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if check and process.returncode != 0:
            raise RuntimeError(stderr or stdout or "tailscale command failed")

        return stdout, stderr, process.returncode or 0

    def _ensure_health_task_locked(self) -> None:
        if self._health_task and not self._health_task.done():
            return
        self._health_task = asyncio.create_task(
            self._health_loop(),
            name="tailscale-health",
        )

    async def _stop_health_task_locked(self) -> None:
        task = self._health_task
        self._health_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(self._health_interval)
            async with self._lock:
                if not self._enabled:
                    return
                try:
                    if self._daemon is None or self._daemon.returncode is not None:
                        log.warning(
                            "Web - Tailscale daemon not running; attempting restart"
                        )
                        await self._start_daemon_locked()
                    else:
                        await self._status_json_locked()
                    await self._persist_state_blob_locked()
                    self._last_error = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_error = str(exc)
                    log.debug(
                        "Web - Tailscale health check failed: %s",
                        exc,
                        exc_info=True,
                    )

    def _save_enabled_locked(self, enabled: bool) -> None:
        self._save_kv_locked(self._ENABLED_KEY, "1" if enabled else "0")

    def _save_hostname_locked(self, hostname: str | None) -> None:
        self._save_kv_locked(self._HOSTNAME_KEY, hostname)

    def _save_kv_locked(self, key: str, value: str | None) -> None:
        with db() as ctx:
            existing = ctx.session.get(Housekeeping, key)
            if value is None:
                if existing is not None:
                    ctx.session.delete(existing)
            else:
                ctx.session.merge(Housekeeping(key=key, value=value))
            ctx.session.commit()

    def _resolve_binary(self, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or "/" in value:
            if not path.exists():
                raise FileNotFoundError(f"Binary not found: {value}")
            return value
        found = shutil.which(value)
        if not found:
            raise FileNotFoundError(f"Binary not found in PATH: {value}")
        return found

    def _compute_default_hostname(self) -> str:
        """Return the default hostname used when no custom one is configured."""
        return "anibridge"

    def _normalize_hostname(self, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip().lower()
        if not stripped:
            return None
        if not self._HOSTNAME_RE.fullmatch(stripped):
            raise ValueError(
                "Hostname must be 1-63 chars using lowercase letters, numbers, and '-'"
            )
        return stripped

    def _extract_auth_url(self, text: str | None) -> str | None:
        if not text:
            return None
        match = self._AUTH_URL_RE.search(text)
        return match.group(0).rstrip(").,") if match else None

    def _extract_ips(
        self, status_json: dict[str, Any] | None, self_node: dict[str, Any]
    ) -> list[str]:
        candidates: Any = self_node.get("TailscaleIPs")
        if not isinstance(candidates, list):
            candidates = status_json.get("TailscaleIPs") if status_json else []
        if not isinstance(candidates, list):
            return []
        return [str(ip) for ip in candidates if isinstance(ip, (str, int, float))]

    # Health warnings that are always present in userspace/container mode and
    # are never actionable by the user.  Suppress them to reduce noise.
    _IGNORED_HEALTH_PATTERNS = (
        "getting OS base config is not supported",
        "failed to fetch the DNS configuration",
        "--accept-routes is false",
    )

    def _extract_health(self, status_json: dict[str, Any] | None) -> list[str]:
        if not isinstance(status_json, dict):
            return []
        health = status_json.get("Health", [])
        if not isinstance(health, list):
            return []
        return [
            str(item)
            for item in health
            if item is not None
            and not any(p in str(item) for p in self._IGNORED_HEALTH_PATTERNS)
        ]

    def _as_str(self, data: dict[str, Any] | None, key: str) -> str | None:
        if not isinstance(data, dict):
            return None
        value = data.get(key)
        return str(value) if isinstance(value, (str, int, float)) else None

    def _tailnet_from_dns(self, dns_name: str | None) -> str | None:
        if not dns_name:
            return None
        parts = dns_name.rstrip(".").split(".", 1)
        return parts[1] if len(parts) == 2 else None


@cache
def get_tailscale_service() -> TailscaleService:
    """Return the cached Tailscale service instance."""
    return TailscaleService()
