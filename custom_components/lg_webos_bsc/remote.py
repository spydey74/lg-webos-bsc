"""Remote entity for LG webOS (bscpylgtv).

Phase 2, Tier 3: D-pad / key presses via bscpylgtv.button(<NAME>). These go over
the input/pointer socket, which can 401 on this firmware -- so a failed key press
raises a clear error but never brings the entity or the config entry down.

Common command names (pass to remote.send_command): HOME, MENU, BACK, EXIT,
UP, DOWN, LEFT, RIGHT, ENTER, INFO, PLAY, PAUSE, STOP, FASTFORWARD, REWIND,
VOLUMEUP, VOLUMEDOWN, MUTE, CHANNELUP, CHANNELDOWN, 0-9, RED, GREEN, YELLOW, BLUE.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the remote from a config entry."""
    async_add_entities([LgWebosBscRemote(entry.runtime_data, entry)])


class LgWebosBscRemote(LgWebosBscEntity, RemoteEntity):
    """webOS remote key sender."""

    _attr_translation_key = "remote"
    _attr_icon = "mdi:remote-tv"
    _attr_entity_registry_enabled_default = False  # input socket may 401; opt-in

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="remote")

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        return bool(data.get("connected")) and data.get("power") != "off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_wake()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_power_off()

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send one or more webOS button names over the input socket."""
        for cmd in command:
            name = str(cmd).strip().upper()
            try:
                await self.coordinator.async_remote_button(name)
            except Exception as exc:  # noqa: BLE001
                # Most likely the input/pointer socket 401'd on this firmware.
                _LOGGER.warning("Remote key %s failed: %s", name, exc)
                raise HomeAssistantError(
                    f"Remote key '{name}' failed (the input socket may be "
                    f"unavailable on this firmware): {exc}"
                ) from exc
