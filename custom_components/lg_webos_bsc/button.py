"""Button entities for LG webOS (bscpylgtv).

Phase 2, Tier 1: safe one-shot actions that do NOT need the input/pointer socket
(screen on/off, screensaver, reboot). Remote key presses (which DO need the input
socket and can 401 on this firmware) are a separate, later platform.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity


@dataclass(frozen=True, kw_only=True)
class LgWebosBscButtonDescription(ButtonEntityDescription):
    """Describes an LG webOS button; client_method is a no-arg client coroutine."""

    client_method: str


BUTTONS: tuple[LgWebosBscButtonDescription, ...] = (
    LgWebosBscButtonDescription(
        key="screen_off",
        translation_key="screen_off",
        icon="mdi:monitor-off",
        client_method="turn_screen_off",
    ),
    LgWebosBscButtonDescription(
        key="screen_on",
        translation_key="screen_on",
        icon="mdi:monitor",
        client_method="turn_screen_on",
    ),
    LgWebosBscButtonDescription(
        key="screensaver",
        translation_key="screensaver",
        icon="mdi:television-shimmer",
        client_method="show_screen_saver",
    ),
    LgWebosBscButtonDescription(
        key="reboot",
        translation_key="reboot",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
        client_method="reboot",
    ),
    LgWebosBscButtonDescription(
        key="reboot_soft",
        translation_key="reboot_soft",
        icon="mdi:restart-alert",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        client_method="reboot_soft",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(LgWebosBscButton(coordinator, entry, desc) for desc in BUTTONS)


class LgWebosBscButton(LgWebosBscEntity, ButtonEntity):
    """A one-shot webOS action."""

    entity_description: LgWebosBscButtonDescription

    def __init__(
        self,
        coordinator: LgWebosBscCoordinator,
        entry: LgWebosBscConfigEntry,
        description: LgWebosBscButtonDescription,
    ) -> None:
        super().__init__(coordinator, entry, key=description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    async def async_press(self) -> None:
        await self.coordinator.async_button(self.entity_description.client_method)
