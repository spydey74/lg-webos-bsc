"""Connection + polling coordinator for the LG webOS (bscpylgtv) integration.

Centralizes client creation/connection (handover sec.4/sec.5):
  * canonical manifest applied BEFORE connect() on every (re)connect (sec.2),
  * states=[] so the unguarded static-state fetch (get_software_info) in
    bscpylgtv's connect_handler cannot abort setup (sec.5),
  * an in-memory key store so bscpylgtv never writes its sqlite file into the
    HA config dir, and never persists a *different* key over the configured one
    (sec.3),
  * a bounded connect-retry loop mirroring lg_webos_net.py.

Polls a minimal set (current app, volume, mute, power) rather than subscribing,
because on this firmware some subscriptions are silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from bscpylgtv import WebOsClient

from .const import (
    CONNECT_RETRY_INTERVAL,
    CONNECT_WAIT_SECONDS,
    GAME_GENRE_CATEGORY,
    GAME_GENRE_KEY,
)
from .patch import apply_manifest

_LOGGER = logging.getLogger(__name__)


class _MemoryKeyStore:
    """In-memory bscpylgtv StorageProto.

    Keeps bscpylgtv from creating/reading its sqlite key store on disk. The
    config entry remains the single source of truth for the client key; we seed
    this store from it and read any freshly-paired key back out after connect().
    """

    def __init__(self, ip: str, key: str | None) -> None:
        self._data: dict[str, str] = {}
        if key:
            self._data[ip] = key

    async def get_key(self, key: str) -> str | None:  # key == ip
        return self._data.get(key)

    async def set_key(self, key: str, val: str) -> None:  # key == ip, val == client key
        self._data[key] = val

    async def list_keys(self) -> dict[str, str]:
        return dict(self._data)


class LgWebosBscCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the bscpylgtv client and polls the TV."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        client_key: str | None,
        mac: str | None,
        scan_interval,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"lg_webos_bsc {host}",
            update_interval=scan_interval,
            config_entry=entry,
        )
        self.entry = entry
        self.host = host
        self.client_key = client_key
        self.mac = mac
        self._client: WebOsClient | None = None
        self._connect_lock = asyncio.Lock()
        # We cannot reliably read gameGenre back (getSystemSettings 500s on this
        # firmware), so track the last value we set for optimistic select state.
        self.last_game_genre: str | None = None

    # ------------------------------------------------------------------ client

    async def _build_client(self) -> WebOsClient:
        """Create a bscpylgtv client with the manifest patched, but not connected."""
        client = await WebOsClient.create(
            self.host,
            client_key=self.client_key,     # None -> fresh pair; else reuse configured key
            key_file_path=None,             # do not touch the sqlite key store
            storage=_MemoryKeyStore(self.host, self.client_key),
            states=[],                      # sec.5: avoid the subscription/static-state cascade
        )
        apply_manifest(client)              # sec.2: MUST be before connect()
        return client

    async def _connect_with_retry(self) -> WebOsClient:
        """Bounded connect loop that tolerates a cold/booting TV (sec.4)."""
        deadline = time.monotonic() + CONNECT_WAIT_SECONDS
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            client = await self._build_client()
            try:
                await client.connect()
            except Exception as exc:  # noqa: BLE001 -- tolerate a booting TV, keep retrying
                last_exc = exc
                await self._safe_disconnect(client)
                await asyncio.sleep(CONNECT_RETRY_INTERVAL)
                continue
            # A freshly-paired key surfaces on the client; capture it so callers
            # can persist it to the config entry.
            if client.client_key and client.client_key != self.client_key:
                self.client_key = client.client_key
            return client
        raise ConnectionError(
            f"could not connect to {self.host} within {CONNECT_WAIT_SECONDS:.0f}s: {last_exc}"
        )

    async def async_ensure_connected(self) -> WebOsClient:
        """Return a live client, (re)connecting under a lock if needed."""
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected():
                return self._client
            if self._client is not None:
                await self._safe_disconnect(self._client)
                self._client = None
            self._client = await self._connect_with_retry()
            return self._client

    @staticmethod
    async def _safe_disconnect(client: WebOsClient) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 -- best effort
            pass

    async def async_shutdown_client(self) -> None:
        if self._client is not None:
            await self._safe_disconnect(self._client)
            self._client = None

    # ------------------------------------------------------------------ poll

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll a minimal, individually-tolerant state set.

        A TV that is merely off/unreachable is reported as power=off (entities
        stay available), not as an update failure.
        """
        try:
            client = await self.async_ensure_connected()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("TV %s not reachable: %s", self.host, exc)
            return self._offline_data()

        data: dict[str, Any] = {"connected": True, "power": "on"}

        data["current_app_id"] = await self._safe_call(client.get_current_app)
        data["volume"] = await self._safe_call(client.get_volume)
        data["muted"] = await self._safe_call(client.get_muted)

        power = await self._safe_call(client.get_power_state)
        data["power"] = self._interpret_power(power)

        # Apps/inputs change rarely and each read can cost a full silent-drop
        # timeout, so fetch them only once (when the cache is empty) and reuse
        # thereafter. A reconnect re-empties the cache and re-fetches.
        prev = self.data or {}
        apps = prev.get("apps") or []
        inputs = prev.get("inputs") or []
        if not apps:
            apps = await self._safe_call(client.get_apps) or []
        if not inputs:
            inputs = await self._safe_call(client.get_inputs) or []
        data["apps"] = apps
        data["inputs"] = inputs

        data["game_genre"] = self.last_game_genre
        return data

    def _offline_data(self) -> dict[str, Any]:
        prev = self.data or {}
        return {
            "connected": False,
            "power": "off",
            "current_app_id": None,
            "volume": None,
            "muted": None,
            "apps": prev.get("apps") or [],
            "inputs": prev.get("inputs") or [],
            "game_genre": self.last_game_genre,
        }

    async def _safe_call(self, func, *args):
        """Call a client getter, returning None on any failure or silent drop."""
        try:
            return await func(*args)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("poll %s failed: %s", getattr(func, "__name__", func), exc)
            return None

    @staticmethod
    def _interpret_power(power: Any) -> str:
        if not isinstance(power, dict):
            return "on"  # connected but no power info -> assume on
        payload = power.get("payload") if "payload" in power else power
        state = str((payload or {}).get("state", "")).lower()
        if not state:
            return "on"
        if "active" in state or "screen off" in state:
            # 'Active' and 'Active Standby'/'Screen Off' still mean powered.
            return "on"
        if "suspend" in state or "power off" in state or "off" in state:
            return "off"
        return "on"

    # --------------------------------------------------------------- commands

    async def async_command(self, coro_factory) -> Any:
        """Run a client command with a connection guarantee, then refresh."""
        client = await self.async_ensure_connected()
        result = await coro_factory(client)
        await self.async_request_refresh()
        return result

    async def async_launch_app(self, app_id: str) -> None:
        await self.async_command(lambda c: c.launch_app(app_id))

    async def async_set_input(self, input_id: str) -> None:
        await self.async_command(lambda c: c.set_input(input_id))

    async def async_set_volume(self, volume: int) -> None:
        await self.async_command(lambda c: c.set_volume(volume))

    async def async_set_mute(self, mute: bool) -> None:
        await self.async_command(lambda c: c.set_mute(mute))

    async def async_power_off(self) -> None:
        await self.async_command(lambda c: c.power_off())

    async def async_set_game_genre(self, genre: str) -> None:
        """Set Game Optimizer genre via bscpylgtv.set_settings (alert bridge)."""
        await self.async_command(
            lambda c: c.set_settings(GAME_GENRE_CATEGORY, {GAME_GENRE_KEY: genre})
        )
        self.last_game_genre = genre
        self.async_update_listeners()
