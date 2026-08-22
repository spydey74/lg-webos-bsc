"""Sensors for LG webOS (bscpylgtv).

Phase 2: an audio-output sensor so you can see whether sound is going out over
HDMI ARC/eARC, Bluetooth, or the TV speakers.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .const import SOUND_OUTPUT_NAMES
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    async_add_entities([LgWebosBscSoundOutputSensor(entry.runtime_data, entry)])


class LgWebosBscSoundOutputSensor(LgWebosBscEntity, SensorEntity):
    """Current audio output (eARC / Bluetooth / TV speakers / ...)."""

    _attr_translation_key = "sound_output"
    _attr_icon = "mdi:speaker"

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="sound_output")

    @property
    def available(self) -> bool:
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    @property
    def native_value(self) -> str | None:
        raw = (self.coordinator.data or {}).get("sound_output")
        if not raw:
            return None
        return SOUND_OUTPUT_NAMES.get(raw, raw)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        raw = (self.coordinator.data or {}).get("sound_output")
        return {"raw_sound_output": raw} if raw else {}
