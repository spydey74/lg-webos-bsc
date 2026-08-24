"""Connection + polling coordinator for the LG webOS (bscpylgtv) integration.

Centralizes client creation/connection (handover sec.4/sec.5):
  * canonical manifest applied BEFORE connect() on every (re)connect (sec.2),
  * states=[] so the unguarded static-state fetch (get_software_info) in
    bscpylgtv's connect_handler cannot abort setup (sec.5),
  * an in-memory key store so bscpylgtv never writes its sqlite file into the
    HA config dir, and never persists a *different* key over the configured one
    (sec.3),
  * a single fast connect attempt on the poll path so an off/unreachable TV is
    reported off within ~2s instead of blocking the poll.

Polls a minimal set (current app, volume, mute, power, sound output) rather than
subscribing, because on this firmware some subscriptions are silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from bscpylgtv import WebOsClient

from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_WOL_BROADCAST,
    GAME_GENRE_CATEGORY,
    GAME_GENRE_KEY,
    PICTURE_CATEGORY,
    SOUND_CATEGORY,
    SOUND_SETTINGS_KEYS,
)
from .patch import apply_manifest

# Fast connect tuning for routine polling: a shut-down/unreachable TV should be
# detected in a couple of seconds, not block the poll. (~timeout_connect *
# connect_retry_attempts worst case.) The config flow keeps the library defaults.
_POLL_TIMEOUT_CONNECT = 2
_POLL_CONNECT_ATTEMPTS = 1

# Push (hybrid) mode: subscribe to the fast-changing scalars so the UI updates
# instantly (confirmed to fire on webOS 26 via tools/subscription_probe.py).
# Deliberately EXCLUDES apps/inputs (their subscribed property is a differently
# shaped dict -- we keep reading those via the list getters) and
# system_info/software_info (static; software_info 401s). power/current_app/
# muted/volume/sound_output update client.power_state/current_appId/muted/volume/
# sound_output.
_SUBSCRIBE_STATES = ["power", "current_app", "muted", "volume", "sound_output"]
# bscpylgtv awaits subscription setup with no timeout; if a subscription silently
# drops, connect() can hang. Bound it, and fall back to pure polling on timeout.
_PUSH_CONNECT_TIMEOUT = 20.0

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


async def async_make_client(
    hass: HomeAssistant,
    host: str,
    client_key: str | None,
    storage: _MemoryKeyStore,
    *,
    states: list[str] | None = None,
    timeout_connect: int | None = None,
    connect_retry_attempts: int | None = None,
) -> WebOsClient:
    """Build a bscpylgtv client with the canonical manifest, but NOT connected.

    bscpylgtv.WebOsClient.__init__ builds its SSL context synchronously
    (ssl.load_default_certs / set_default_verify_paths) which is blocking file
    I/O; HA forbids that in the event loop. So construct the object in an
    executor, then run the async storage init and apply the manifest in the loop.
    Mirrors WebOsClient.create() (which is __init__ + async_init) but off-loop.

    timeout_connect / connect_retry_attempts override the library defaults; the
    coordinator passes tight values for snappy offline detection while the config
    flow leaves them at the (known-good) defaults.
    """
    extra: dict[str, Any] = {}
    if timeout_connect is not None:
        extra["timeout_connect"] = timeout_connect
    if connect_retry_attempts is not None:
        extra["connect_retry_attempts"] = connect_retry_attempts

    def _construct() -> WebOsClient:
        return WebOsClient(
            host,
            client_key=client_key,     # None -> fresh pair; else reuse configured key
            key_file_path=None,         # do not touch the sqlite key store
            storage=storage,
            states=states or [],        # [] = poll only; a subset = push subscriptions
            **extra,
        )

    client = await hass.async_add_executor_job(_construct)
    await client.async_init()
    apply_manifest(client)              # sec.2: MUST be before connect()
    return client


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
        # Start in push (hybrid) mode; downgrade to pure polling if a subscription
        # ever hangs connect().
        self._push_mode = True
        # We cannot reliably read some settings back (getSystemSettings 500s on
        # this firmware), so track the last value we set for optimistic state.
        self.last_game_genre: str | None = None
        self.last_picture_mode: str | None = None
        # Resolved readable subset of the sound category (None = not yet probed).
        self._sound_keys: list[str] | None = None

    # ------------------------------------------------------------------ client

    async def _build_client(self) -> WebOsClient:
        """Create a bscpylgtv client with the manifest patched, but not connected.

        In push mode, subscribe to the scalar state subset and register the push
        callback so the UI updates instantly.
        """
        client = await async_make_client(
            self.hass,
            self.host,
            self.client_key,
            _MemoryKeyStore(self.host, self.client_key),
            states=_SUBSCRIBE_STATES if self._push_mode else [],
            timeout_connect=_POLL_TIMEOUT_CONNECT,
            connect_retry_attempts=_POLL_CONNECT_ATTEMPTS,
        )
        if self._push_mode:
            await client.register_state_update_callback(self._on_push)
        return client

    async def async_ensure_connected(self) -> WebOsClient:
        """Return a live client, (re)connecting under a lock if needed.

        A single fast attempt: if the TV is off/unreachable this raises within a
        couple of seconds and the poll reports the TV as off, rather than blocking.
        In push mode connect() is bounded by a timeout; if a subscription hangs we
        permanently downgrade to pure polling and reconnect.
        """
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected():
                return self._client
            if self._client is not None:
                await self._safe_disconnect(self._client)
                self._client = None

            client = await self._build_client()
            try:
                if self._push_mode:
                    await asyncio.wait_for(client.connect(), timeout=_PUSH_CONNECT_TIMEOUT)
                else:
                    await client.connect()
            except asyncio.TimeoutError:
                await self._safe_disconnect(client)
                if self._push_mode:
                    _LOGGER.warning(
                        "Push subscriptions hung connect() on %s; downgrading to "
                        "polling for this session",
                        self.host,
                    )
                    self._push_mode = False
                    client = await self._build_client()
                    await client.connect()
                else:
                    raise

            # A freshly-paired key surfaces on the client; capture it so callers
            # can persist it to the config entry.
            if client.client_key and client.client_key != self.client_key:
                self.client_key = client.client_key
            self._client = client
            return client

    async def _on_push(self, client: WebOsClient) -> None:
        """bscpylgtv push callback: reflect the new scalar state immediately."""
        prev = self.data or {}
        self.async_set_updated_data(
            {
                "connected": True,
                "power": self._interpret_power(getattr(client, "power_state", None)),
                "current_app_id": getattr(client, "current_appId", None),
                "volume": getattr(client, "volume", None),
                "muted": getattr(client, "muted", None),
                "sound_output": getattr(client, "sound_output", None),
                # Slow-changing bits keep their last polled snapshot.
                "apps": prev.get("apps") or [],
                "inputs": prev.get("inputs") or [],
                "system_info": prev.get("system_info") or {},
                "picture_settings": prev.get("picture_settings") or {},
                "sound_settings": prev.get("sound_settings") or {},
                "game_genre": self.last_game_genre,
                "picture_mode": self.last_picture_mode,
            }
        )

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

        if self._push_mode:
            # Scalars come from the subscription-updated properties (no network);
            # power is re-read explicitly so standby detection stays reliable even
            # if the power subscription is quiet.
            data["current_app_id"] = getattr(client, "current_appId", None)
            data["volume"] = getattr(client, "volume", None)
            data["muted"] = getattr(client, "muted", None)
            data["sound_output"] = getattr(client, "sound_output", None)
        else:
            data["current_app_id"] = await self._safe_call(client.get_current_app)
            data["volume"] = await self._safe_call(client.get_volume)
            data["muted"] = await self._safe_call(client.get_muted)
            data["sound_output"] = await self._safe_call(client.get_sound_output)

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

        # system_info reads on this firmware (software_info 401s); cache it once
        # for the model name / diagnostics.
        system_info = prev.get("system_info") or {}
        if not system_info:
            system_info = await self._safe_call(client.get_system_info) or {}
        data["system_info"] = system_info

        # Picture settings DO read back here (the 4 default keys); poll them so the
        # number sliders show real values. Reuse the previous snapshot on failure.
        pic = await self._safe_call(client.get_picture_settings)
        data["picture_settings"] = pic if pic is not None else (prev.get("picture_settings") or {})

        sound = await self._read_sound_settings(client)
        data["sound_settings"] = sound if sound else (prev.get("sound_settings") or {})

        data["game_genre"] = self.last_game_genre
        data["picture_mode"] = self.last_picture_mode
        return data

    async def _read_sound_settings(self, client: WebOsClient) -> dict[str, Any]:
        """Read the readable subset of the sound category.

        A single unsupported key 500s a batch read (as with picture
        sharpness/oled_light), so on first use we resolve the readable subset --
        try the full batch, and on failure fall back to per-key discovery -- then
        batch just that subset on every subsequent poll.
        """
        if self._sound_keys is None:
            try:
                res = await client.get_system_settings(SOUND_CATEGORY, list(SOUND_SETTINGS_KEYS))
                self._sound_keys = list(SOUND_SETTINGS_KEYS)
                return (res or {}).get("settings") or {}
            except Exception:  # noqa: BLE001 -- discover the good subset one by one
                good: dict[str, Any] = {}
                for key in SOUND_SETTINGS_KEYS:
                    try:
                        res = await client.get_system_settings(SOUND_CATEGORY, [key])
                        settings = (res or {}).get("settings") or {}
                        if key in settings:
                            good[key] = settings[key]
                    except Exception:  # noqa: BLE001
                        pass
                self._sound_keys = list(good.keys())
                if good:
                    _LOGGER.debug("Readable sound settings: %s", self._sound_keys)
                else:
                    _LOGGER.debug("No sound-category settings are readable on this TV")
                return good

        if not self._sound_keys:
            return {}
        try:
            res = await client.get_system_settings(SOUND_CATEGORY, list(self._sound_keys))
            return (res or {}).get("settings") or {}
        except Exception:  # noqa: BLE001 -- transient; caller reuses the last snapshot
            return {}

    def _offline_data(self) -> dict[str, Any]:
        prev = self.data or {}
        return {
            "connected": False,
            "power": "off",
            "current_app_id": None,
            "volume": None,
            "muted": None,
            "sound_output": None,
            "apps": prev.get("apps") or [],
            "inputs": prev.get("inputs") or [],
            "system_info": prev.get("system_info") or {},
            "picture_settings": prev.get("picture_settings") or {},
            "sound_settings": prev.get("sound_settings") or {},
            "game_genre": self.last_game_genre,
            "picture_mode": self.last_picture_mode,
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
        """Map a webOS power state to on/off.

        Order matters: 'Active Standby' contains 'active' but is a low-power
        standby (what a Quick-Start TV reports shortly after power-off while it
        stays network-reachable), so standby/suspend/off are checked first.
        'Screen Off' means the panel is off but the system is on (audio may still
        play via eARC), so it counts as on.
        """
        if not isinstance(power, dict):
            return "on"  # connected but no power info -> assume on
        state = str(power.get("state", "")).strip().lower()
        if not state:
            return "on"
        if "standby" in state or "suspend" in state or "power off" in state or state == "off":
            return "off"
        if "screen off" in state:
            return "on"
        if "active" in state:
            return "on"
        # 'Unknown' or unexpected -> off, so a shut-down TV never reads as on.
        return "off"

    # --------------------------------------------------------------- commands

    async def async_command(self, coro_factory) -> Any:
        """Run a client command with a connection guarantee, then refresh now.

        Uses async_refresh() (immediate) rather than async_request_refresh()
        (debounced ~10s) so volume/mute/source changes reflect in the UI right
        away instead of lagging up to the debounce cooldown.
        """
        client = await self.async_ensure_connected()
        result = await coro_factory(client)
        await self.async_refresh()
        return result

    async def async_launch_app(self, app_id: str) -> None:
        await self.async_command(lambda c: c.launch_app(app_id))

    async def async_set_input(self, input_id: str) -> None:
        await self.async_command(lambda c: c.set_input(input_id))

    async def async_set_sound_output(self, output: str) -> None:
        """Change the TV's sound output (bscpylgtv.change_sound_output).

        Used by the reconcile engine to re-assert e.g. external_arc when the TV
        overrides it. Touches the audio path -- callers should honour the
        Bluetooth-headphones guard.
        """
        await self.async_command(lambda c: c.change_sound_output(output))

    async def async_set_volume(self, volume: int) -> None:
        await self.async_command(lambda c: c.set_volume(volume))

    async def async_set_mute(self, mute: bool) -> None:
        await self.async_command(lambda c: c.set_mute(mute))

    async def async_power_off(self) -> None:
        await self.async_command(lambda c: c.power_off())

    async def async_set_picture_mode(self, mode: str) -> None:
        """Set picture mode (webOS v9+ set_system_picture_mode)."""
        await self.async_command(lambda c: c.set_system_picture_mode(mode))
        self.last_picture_mode = mode
        base = dict(self.data) if self.data else {}
        base["picture_mode"] = mode
        self.async_set_updated_data(base)

    async def async_set_picture_setting(self, key: str, value: int) -> None:
        """Write one picture setting and optimistically reflect it.

        The immediate refresh in async_command re-reads get_picture_settings (which
        works here), so the slider confirms the real value; the optimistic write
        just covers the brief window before that read lands.
        """
        await self.async_command(
            lambda c: c.set_settings(PICTURE_CATEGORY, {key: value})
        )
        base = dict(self.data) if self.data else {}
        ps = dict(base.get("picture_settings") or {})
        ps[key] = str(value)
        base["picture_settings"] = ps
        self.async_set_updated_data(base)

    async def async_set_settings(self, category: str, settings: dict) -> Any:
        """Raw setSystemSettings via the alert bridge (bscpylgtv.set_settings)."""
        return await self.async_command(lambda c: c.set_settings(category, settings))

    async def async_raw_request(self, uri: str, payload: dict | None = None) -> Any:
        """Raw SSAP request passthrough (e.g. 'audio/getVolume')."""
        return await self.async_command(lambda c: c.request(uri, payload or {}))

    async def async_luna(self, uri: str, params: dict) -> Any:
        """Raw protected luna call via the alert bridge (bscpylgtv.luna_request)."""
        return await self.async_command(lambda c: c.luna_request(uri, params))

    async def async_button(self, method_name: str) -> None:
        """Call a no-arg client method by name (used by button entities)."""
        await self.async_command(lambda c: getattr(c, method_name)())

    async def async_remote_button(self, name: str) -> None:
        """Send a remote key via the input/pointer socket (may 401 on this firmware)."""
        client = await self.async_ensure_connected()
        await client.button(name)

    async def async_send_message(self, message: str) -> None:
        """Show an on-screen toast (bscpylgtv.send_message)."""
        client = await self.async_ensure_connected()
        await client.send_message(message)

    async def async_wake(self) -> None:
        """Power on via Wake-on-LAN.

        Sends to (in order) an optional user-configured broadcast address, the
        TV's directed subnet broadcast, and the global broadcast -- ports 9 and 7.
        Note: whether this reaches the TV depends on HA's network egress; on some
        setups HA cannot emit a LAN broadcast at all.
        """
        mac = self.mac
        if not mac:
            raise HomeAssistantError(
                "No MAC address configured; set one in the integration options."
            )
        from wakeonlan import send_magic_packet

        targets: list[str] = []
        override = (self.entry.options.get(CONF_WOL_BROADCAST) or "").strip()
        if override:
            targets.append(override)
        parts = self.host.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            targets.append(".".join(parts[:3] + ["255"]))  # directed subnet broadcast
        targets.append("255.255.255.255")                   # global broadcast fallback

        def _send() -> None:
            for target in dict.fromkeys(targets):  # de-dup, keep order
                send_magic_packet(mac, ip_address=target, port=9)
                send_magic_packet(mac, ip_address=target, port=7)

        await self.hass.async_add_executor_job(_send)

    async def async_set_game_genre(self, genre: str) -> None:
        """Set Game Optimizer genre via bscpylgtv.set_settings (alert bridge).

        gameGenre cannot be read back (getSystemSettings 500s), so we do NOT
        poll after: we push the new value straight into the cached data and
        notify listeners, which makes the select reflect it immediately.
        """
        client = await self.async_ensure_connected()
        await client.set_settings(GAME_GENRE_CATEGORY, {GAME_GENRE_KEY: genre})
        self.last_game_genre = genre
        base = dict(self.data) if self.data else {}
        base["game_genre"] = genre
        self.async_set_updated_data(base)
