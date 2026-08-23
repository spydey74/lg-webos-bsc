"""Sensors for LG webOS (bscpylgtv).

Phase 2: an audio-output sensor so you can see whether sound is going out over
HDMI ARC/eARC, Bluetooth, or the TV speakers.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LgWebosBscConfigEntry
from .const import SOUND_OUTPUT_NAMES, SOUND_SETTINGS_KEYS, SOUND_SETTINGS_NAMES
from .coordinator import LgWebosBscCoordinator
from .entity import LgWebosBscEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgWebosBscConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    entities: list = [
        LgWebosBscSoundOutputSensor(entry.runtime_data, entry),
        LgWebosBscModelSensor(entry.runtime_data, entry),
        # Summary of the TV's reported audio settings (all keys as attributes) --
        # the main surface for comparing against the soundbar's desired state.
        LgWebosBscAudioSettingsSensor(entry.runtime_data, entry),
    ]
    # Optional per-setting sensors (disabled by default) for history/automation.
    entities.extend(
        LgWebosBscSoundSettingSensor(entry.runtime_data, entry, key)
        for key in SOUND_SETTINGS_KEYS
    )
    async_add_entities(entities)


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


class LgWebosBscModelSensor(LgWebosBscEntity, SensorEntity):
    """TV model name (from get_system_info; software_info 401s on this firmware)."""

    _attr_translation_key = "model"
    _attr_icon = "mdi:television"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="model")

    @property
    def native_value(self) -> str | None:
        return ((self.coordinator.data or {}).get("system_info") or {}).get("modelName")

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        info = (self.coordinator.data or {}).get("system_info") or {}
        attrs = {}
        if info.get("serialNumber"):
            attrs["serial_number"] = info["serialNumber"]
        if info.get("receiverType"):
            attrs["receiver_type"] = info["receiverType"]
        return attrs


class LgWebosBscAudioSettingsSensor(LgWebosBscEntity, SensorEntity):
    """The TV's reported sound-category settings (all keys as attributes).

    State is the current sound output setting; every readable sound key is an
    attribute (e.g. soundMode, soundOutputDigital, aiSound), so an automation can
    compare the TV's actual audio state to the desired state set on the soundbar.
    """

    _attr_translation_key = "audio_settings"
    _attr_icon = "mdi:tune-vertical"

    def __init__(
        self, coordinator: LgWebosBscCoordinator, entry: LgWebosBscConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, key="audio_settings")

    @property
    def available(self) -> bool:
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    @property
    def native_value(self) -> str | None:
        settings = (self.coordinator.data or {}).get("sound_settings") or {}
        return settings.get("soundOutput") or settings.get("soundMode")

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return dict((self.coordinator.data or {}).get("sound_settings") or {})


class LgWebosBscSoundSettingSensor(LgWebosBscEntity, SensorEntity):
    """One TV sound-category setting as its own sensor (disabled by default)."""

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: LgWebosBscCoordinator,
        entry: LgWebosBscConfigEntry,
        setting_key: str,
    ) -> None:
        super().__init__(coordinator, entry, key=f"sound_{setting_key}")
        self._setting_key = setting_key
        self._attr_name = SOUND_SETTINGS_NAMES.get(setting_key, setting_key)

    @property
    def available(self) -> bool:
        if not (super().available and bool((self.coordinator.data or {}).get("connected"))):
            return False
        # Unavailable if this key isn't readable on this TV.
        return self._setting_key in ((self.coordinator.data or {}).get("sound_settings") or {})

    @property
    def native_value(self) -> str | None:
        val = ((self.coordinator.data or {}).get("sound_settings") or {}).get(self._setting_key)
        return None if val is None else str(val)
