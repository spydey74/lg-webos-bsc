"""Shared base entity: groups all entities under one HA device."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LgWebosBscConfigEntry
from .const import CONF_HOST, DOMAIN
from .coordinator import LgWebosBscCoordinator


class LgWebosBscEntity(CoordinatorEntity[LgWebosBscCoordinator]):
    """Base entity tying everything to a single LG webOS device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LgWebosBscCoordinator,
        entry: LgWebosBscConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        device_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.title,
            manufacturer="LG Electronics",
            model="webOS TV (bscpylgtv)",
            configuration_url=f"http://{entry.data.get(CONF_HOST)}",
        )
