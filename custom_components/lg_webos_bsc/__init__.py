"""The LG webOS (bscpylgtv) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIENT_KEY,
    CONF_HOST,
    CONF_MAC,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import LgWebosBscCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BUTTON,
]

type LgWebosBscConfigEntry = ConfigEntry[LgWebosBscCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LgWebosBscConfigEntry) -> bool:
    """Set up LG webOS (bscpylgtv) from a config entry."""
    host = entry.data[CONF_HOST]
    client_key = entry.data.get(CONF_CLIENT_KEY)
    mac = entry.options.get(CONF_MAC) or entry.data.get(CONF_MAC)
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    coordinator = LgWebosBscCoordinator(
        hass,
        entry,
        host=host,
        client_key=client_key,
        mac=mac,
        scan_interval=timedelta(seconds=poll_interval),
    )

    # Never raises for a merely-off/unreachable TV (returns an offline snapshot),
    # so the entry loads and the device appears even if the TV is currently off.
    await coordinator.async_config_entry_first_refresh()

    # If a fresh pairing produced a new key, persist it as the source of truth.
    if coordinator.client_key and coordinator.client_key != client_key:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_CLIENT_KEY: coordinator.client_key}
        )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LgWebosBscConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and entry.runtime_data is not None:
        await entry.runtime_data.async_shutdown_client()
    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: LgWebosBscConfigEntry) -> None:
    """Reload the entry when options change (poll interval, input switching)."""
    await hass.config_entries.async_reload(entry.entry_id)
