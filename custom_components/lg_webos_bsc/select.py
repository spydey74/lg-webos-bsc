"""Select entities for LG webOS (bscpylgtv).

Phase 1 headline feature: the LG Game Optimizer genre, which core webostv does
not expose. Set via bscpylgtv.set_settings("other", {"gameGenre": <v>}), which
uses the createAlert->closeAlert alert bridge under the hood (handover sec.2).
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .const import GAME_GENRE_OPTIONS
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities from a config entry."""
    async_add_entities([LgWebosBscGameGenreSelect(entry.runtime_data, entry)])


class LgWebosBscGameGenreSelect(LgWebosBscEntity, SelectEntity):
    """Game Optimizer genre selector."""

    _attr_translation_key = "game_genre"
    _attr_icon = "mdi:gamepad-variant"
    _attr_options = GAME_GENRE_OPTIONS

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="game_genre")

    @property
    def available(self) -> bool:
        # Only actionable while the TV is reachable.
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    @property
    def current_option(self) -> str | None:
        # gameGenre cannot be read back reliably (getSystemSettings 500s on this
        # firmware), so we report the last value we set this session, if any.
        genre = (self.coordinator.data or {}).get("game_genre")
        if genre in self._attr_options:
            return genre
        return None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_game_genre(option)
