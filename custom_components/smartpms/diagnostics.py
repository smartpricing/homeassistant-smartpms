"""Diagnostics support for SmartPMS."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACT_CONFIG = {"email", "password", "api_key"}
REDACT_COORDINATOR = {"token", "email", "password"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    unit_summary = {}
    if coordinator.data:
        for unit_id, unit_data in coordinator.data.items():
            unit_summary[str(unit_id)] = {
                "name": unit_data.get("name"),
                "status": unit_data.get("status"),
                "property_id": unit_data.get("property_id"),
            }

    return {
        "config_entry": async_redact_data(dict(entry.data), REDACT_CONFIG),
        "options": dict(entry.options),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "unit_count": len(coordinator.data) if coordinator.data else 0,
            "units": unit_summary,
        },
    }
