"""Config flow per l'integrazione SmartPMS."""

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_API_KEY, CONF_PROPERTY_ID, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import SmartPMSApiClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_API_KEY): str,
    }
)


class SmartPMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Gestione config flow per SmartPMS."""

    VERSION = 1

    def __init__(self) -> None:
        """Inizializza il config flow."""
        self._user_input: dict | None = None
        self._client: SmartPMSApiClient | None = None
        self._properties: dict[int, str] = {}

    async def async_step_user(self, user_input=None):
        """Step 1: credenziali e API key."""
        errors = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = SmartPMSApiClient(
                session=session,
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                api_key=user_input[CONF_API_KEY],
            )

            try:
                await client.authenticate()
                units = await client.get_units()
            except Exception:
                _LOGGER.exception("Errore durante la validazione delle credenziali")
                errors["base"] = "auth_failed"
            else:
                # Estrai proprietà uniche dalle unità, conta camere per label
                property_counts: dict[int, int] = {}
                for unit in units:
                    pid = unit.get("property_id")
                    if pid is not None:
                        property_counts[pid] = property_counts.get(pid, 0) + 1
                properties = {
                    pid: f"Proprietà {pid} ({count} camere)"
                    for pid, count in property_counts.items()
                }

                if not properties:
                    errors["base"] = "no_properties"
                elif len(properties) == 1:
                    # Una sola proprietà, salta la selezione
                    pid = next(iter(properties))
                    user_input[CONF_PROPERTY_ID] = pid

                    await self.async_set_unique_id(
                        f"{user_input[CONF_EMAIL]}_{pid}"
                    )
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"SmartPMS - {properties[pid]}",
                        data=user_input,
                    )
                else:
                    # Più proprietà, vai allo step di selezione
                    self._user_input = user_input
                    self._properties = properties
                    return await self.async_step_property()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_property(self, user_input=None):
        """Step 2: selezione proprietà."""
        if user_input is not None:
            pid = user_input[CONF_PROPERTY_ID]
            data = {**self._user_input, CONF_PROPERTY_ID: pid}

            await self.async_set_unique_id(
                f"{self._user_input[CONF_EMAIL]}_{pid}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"SmartPMS - {self._properties.get(pid, pid)}",
                data=data,
            )

        # Costruisci le opzioni del selettore
        property_options = {
            pid: pname for pid, pname in self._properties.items()
        }

        return self.async_show_form(
            step_id="property",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROPERTY_ID): vol.In(property_options),
                }
            ),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Restituisci l'options flow handler."""
        return SmartPMSOptionsFlow(config_entry)


class SmartPMSOptionsFlow(config_entries.OptionsFlow):
    """Gestione opzioni per SmartPMS."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Inizializza options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Gestisci le opzioni."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                }
            ),
        )
