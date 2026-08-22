"""Notify entity for LG webOS (bscpylgtv): on-screen toast messages."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the notify entity from a config entry."""
    async_add_entities([LgWebosBscNotify(entry.runtime_data, entry)])


class LgWebosBscNotify(LgWebosBscEntity, NotifyEntity):
    """Sends a floating toast message to the TV screen."""

    _attr_translation_key = "toast"
    _attr_icon = "mdi:message-text"

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="toast")

    @property
    def available(self) -> bool:
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        text = f"{title}: {message}" if title else message
        await self.coordinator.async_send_message(text)
