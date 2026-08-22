"""Number entities for LG webOS (bscpylgtv).

Phase 2, Tier 2: picture sliders (backlight/contrast/brightness/color). These
read back on this firmware via get_picture_settings, so they show real current
values; writes go through set_settings("picture", {...}) via the alert bridge.
(sharpness/oled_light are intentionally excluded -- including them 500s the whole
read on this TV.)
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .const import PICTURE_NUMBERS
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up picture number entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        LgWebosBscPictureNumber(coordinator, entry, key, lo, hi)
        for key, (lo, hi) in PICTURE_NUMBERS.items()
    )


class LgWebosBscPictureNumber(LgWebosBscEntity, NumberEntity):
    """A single picture setting slider."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: LgWebosBscCoordinator,
        entry: LgWebosBscConfigEntry,
        setting_key: str,
        lo: int,
        hi: int,
    ) -> None:
        super().__init__(coordinator, entry, key=f"picture_{setting_key}")
        self._setting_key = setting_key
        self._attr_translation_key = f"picture_{setting_key}"
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi

    @property
    def available(self) -> bool:
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    @property
    def native_value(self) -> float | None:
        raw = ((self.coordinator.data or {}).get("picture_settings") or {}).get(self._setting_key)
        if raw is None:
            return None
        try:
            return float(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_picture_setting(self._setting_key, int(value))
