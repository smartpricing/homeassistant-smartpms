"""Piattaforma sensore per SmartPMS."""

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATUS_BLOCKED, STATUS_FREE, STATUS_OCCUPIED
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
    """Configura i sensori SmartPMS da un config entry."""
    coordinator: SmartPMSCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for unit_id, unit_data in coordinator.data.items():
        entities.append(SmartPMSUnitSensor(coordinator, entry, unit_id, unit_data))

    async_add_entities(entities, update_before_add=False)


class SmartPMSUnitSensor(CoordinatorEntity, SensorEntity):
    """Sensore per una singola unità SmartPMS."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartPMSCoordinator,
        entry: ConfigEntry,
        unit_id: int,
        unit_data: dict,
    ) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self._unit_id = unit_id
        self._property_id = unit_data.get("property_id", 0)
        self._unit_name = unit_data.get("name", f"Unità {unit_id}")
        self._entry = entry

        self._attr_unique_id = f"smartpms_{self._property_id}_{self._unit_id}"
        self._attr_name = self._unit_name
        self._attr_translation_key = "unit_status"

    @property
    def native_value(self) -> str | None:
        """Restituisci lo stato corrente dell'unità."""
        if self.coordinator.data and self._unit_id in self.coordinator.data:
            return self.coordinator.data[self._unit_id].get("status")
        return None

    @property
    def icon(self) -> str:
        """Restituisci l'icona in base allo stato."""
        return ICON_MAP.get(self.native_value, "mdi:help-circle")

    @property
    def extra_state_attributes(self) -> dict:
        """Restituisci attributi aggiuntivi."""
        return {
            "unit_id": self._unit_id,
            "unit_name": self._unit_name,
            "property_id": self._property_id,
        }

    @property
    def device_info(self):
        """Restituisci informazioni sul dispositivo."""
        return {
            "identifiers": {(DOMAIN, f"property_{self._property_id}")},
            "name": f"SmartPMS Proprietà {self._property_id}",
            "manufacturer": "Smartness",
            "model": "SmartPMS",
            "entry_type": "service",
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Gestisci aggiornamento dati dal coordinator."""
        self.async_write_ha_state()
