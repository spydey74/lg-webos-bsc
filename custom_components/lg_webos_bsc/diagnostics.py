"""Diagnostics for LG webOS (bscpylgtv)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LgWebosBscConfigEntry
from .const import CONF_CLIENT_KEY, CONF_MAC

TO_REDACT = {CONF_CLIENT_KEY, CONF_MAC, "serialNumber", "device_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LgWebosBscConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "unique_id_set": entry.unique_id is not None,
        },
        "runtime": {
            "push_mode": getattr(coordinator, "_push_mode", None),
            "last_update_success": coordinator.last_update_success,
            "connected": data.get("connected"),
            "power": data.get("power"),
            "current_app_id": data.get("current_app_id"),
            "volume": data.get("volume"),
            "muted": data.get("muted"),
            "sound_output": data.get("sound_output"),
            "source_count": len(data.get("apps") or []) + len(data.get("inputs") or []),
            "picture_settings": data.get("picture_settings"),
            "system_info": async_redact_data(dict(data.get("system_info") or {}), TO_REDACT),
            "last_game_genre": coordinator.last_game_genre,
            "last_picture_mode": coordinator.last_picture_mode,
        },
    }
