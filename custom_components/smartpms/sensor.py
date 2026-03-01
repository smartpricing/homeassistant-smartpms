"""Sensor platform for SmartPMS."""

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PROPERTY_NAME,
    DOMAIN,
    STATUS_BLOCKED,
    STATUS_FREE,
    STATUS_OCCUPIED,
)
from .coordinator import SmartPMSCoordinator

_LOGGER = logging.getLogger(__name__)

ICON_MAP = {
    STATUS_FREE: "mdi:door-open",
    STATUS_OCCUPIED: "mdi:bed",
    STATUS_BLOCKED: "mdi:wrench",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartPMS sensors from a config entry."""
    coordinator: SmartPMSCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for unit_id, unit_data in coordinator.data.items():
        entities.append(SmartPMSUnitSensor(coordinator, entry, unit_id, unit_data))

    async_add_entities(entities, update_before_add=False)


class SmartPMSUnitSensor(CoordinatorEntity, SensorEntity):
    """Sensor representing a single SmartPMS unit."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartPMSCoordinator,
        entry: ConfigEntry,
        unit_id: int,
        unit_data: dict,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._unit_id = unit_id
        self._property_id = unit_data.get("property_id", 0)
        self._unit_name = unit_data.get("name", f"Unit {unit_id}")

        self._attr_unique_id = f"smartpms_{self._property_id}_{self._unit_id}"
        self._attr_name = self._unit_name
        self._attr_translation_key = "unit_status"

        property_name = entry.data.get(
            CONF_PROPERTY_NAME, f"Property {self._property_id}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"property_{self._property_id}")},
            name=property_name,
            manufacturer="Smartness",
            model="SmartPMS",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> str | None:
        """Return the current unit status."""
        if self.coordinator.data and self._unit_id in self.coordinator.data:
            return self.coordinator.data[self._unit_id].get("status")
        return None

    @property
    def icon(self) -> str:
        """Return icon based on status."""
        return ICON_MAP.get(self.native_value, "mdi:help-circle")

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        return {
            "unit_id": self._unit_id,
            "unit_name": self._unit_name,
            "property_id": self._property_id,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
