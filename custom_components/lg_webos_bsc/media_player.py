"""Media player for LG webOS (bscpylgtv)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .const import (
    CONF_ENABLE_INPUT_SWITCHING,
    DEFAULT_ENABLE_INPUT_SWITCHING,
    HDMI_APP_PREFIX,
)
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity

_LOGGER = logging.getLogger(__name__)

VOLUME_STEP = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media player from a config entry."""
    async_add_entities([LgWebosBscMediaPlayer(entry.runtime_data, entry)])


class LgWebosBscMediaPlayer(LgWebosBscEntity, MediaPlayerEntity):
    """A webOS 26 TV as an HA media player, driven by bscpylgtv."""

    _attr_name = None  # use the device name
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="media_player")

    # ------------------------------------------------------------------ state

    @property
    def _connected(self) -> bool:
        return bool((self.coordinator.data or {}).get("connected"))

    @property
    def state(self) -> MediaPlayerState:
        data = self.coordinator.data or {}
        if not data.get("connected") or data.get("power") == "off":
            return MediaPlayerState.OFF
        return MediaPlayerState.ON

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = (
            MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )
        if self._wol_mac:
            features |= MediaPlayerEntityFeature.TURN_ON
        return features

    @property
    def _wol_mac(self) -> str | None:
        return self.coordinator.mac or None

    @property
    def volume_level(self) -> float | None:
        vol = (self.coordinator.data or {}).get("volume")
        if vol is None:
            return None
        try:
            return max(0.0, min(1.0, int(vol) / 100.0))
        except (TypeError, ValueError):
            return None

    @property
    def is_volume_muted(self) -> bool | None:
        return (self.coordinator.data or {}).get("muted")

    # --- source handling -------------------------------------------------

    @property
    def _input_switching_enabled(self) -> bool:
        return self.entry.options.get(
            CONF_ENABLE_INPUT_SWITCHING, DEFAULT_ENABLE_INPUT_SWITCHING
        )

    def _apps(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("apps") or []

    def _inputs(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("inputs") or []

    @property
    def source_list(self) -> list[str]:
        names: list[str] = []
        # Apps are always launchable. Skip HDMI pseudo-apps here; they belong to
        # the input list (guarded by the opt-in below).
        for app in self._apps():
            app_id = str(app.get("id", ""))
            title = app.get("title")
            if title and not app_id.startswith(HDMI_APP_PREFIX):
                names.append(str(title))
        # Physical inputs only appear when the user opts in (handover sec.8:
        # a network switchInput can poke the soundbar eARC/DAFC drift).
        if self._input_switching_enabled:
            for dev in self._inputs():
                label = dev.get("label") or dev.get("id")
                if label:
                    names.append(str(label))
        return sorted(dict.fromkeys(names))

    @property
    def source(self) -> str | None:
        current = (self.coordinator.data or {}).get("current_app_id")
        if not current:
            return None
        for app in self._apps():
            if str(app.get("id")) == str(current):
                return str(app.get("title") or current)
        for dev in self._inputs():
            if str(dev.get("appId")) == str(current):
                return str(dev.get("label") or dev.get("id") or current)
        return None

    @property
    def app_id(self) -> str | None:
        return (self.coordinator.data or {}).get("current_app_id")

    # --------------------------------------------------------------- commands

    async def async_turn_on(self) -> None:
        """Power on via Wake-on-LAN (TV needs 'Mobile TV On' enabled)."""
        mac = self._wol_mac
        if not mac:
            raise ServiceValidationError(
                "No MAC address configured; set one in the integration options to "
                "enable Wake-on-LAN power-on."
            )
        from wakeonlan import send_magic_packet

        await self.hass.async_add_executor_job(send_magic_packet, mac)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_power_off()

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_volume(int(round(volume * 100)))

    async def async_mute_volume(self, mute: bool) -> None:
        await self.coordinator.async_set_mute(mute)

    async def async_volume_up(self) -> None:
        await self._nudge_volume(+VOLUME_STEP)

    async def async_volume_down(self) -> None:
        await self._nudge_volume(-VOLUME_STEP)

    async def _nudge_volume(self, delta: int) -> None:
        cur = (self.coordinator.data or {}).get("volume")
        if cur is None:
            raise HomeAssistantError("Current volume unknown; cannot step.")
        await self.coordinator.async_set_volume(max(0, min(100, int(cur) + delta)))

    async def async_select_source(self, source: str) -> None:
        # App match first.
        for app in self._apps():
            if str(app.get("title")) == source:
                await self.coordinator.async_launch_app(str(app.get("id")))
                return
        # Then a physical input, only if the user opted in.
        for dev in self._inputs():
            label = dev.get("label") or dev.get("id")
            if str(label) == source:
                if not self._input_switching_enabled:
                    raise ServiceValidationError(
                        "Input switching is disabled (default). Enable it in the "
                        "integration options if you accept the soundbar-drift risk."
                    )
                await self.coordinator.async_set_input(str(dev.get("id")))
                return
        raise ServiceValidationError(f"Unknown source: {source}")
