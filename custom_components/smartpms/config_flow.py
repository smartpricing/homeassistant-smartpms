"""Config flow for the SmartPMS integration."""

import logging

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CONF_API_KEY,
    CONF_PROPERTY_ID,
    CONF_PROPERTY_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
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
    """Handle a config flow for SmartPMS."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict | None = None
        self._properties: dict[int, dict] = {}  # pid -> {count, name}

    async def async_step_user(self, user_input=None):
        """Handle step 1: credentials and API key."""
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
                properties = await client.get_properties()
            except ConfigEntryAuthFailed as err:
                _LOGGER.warning("SmartPMS config flow: auth failed: %s", err)
                errors["base"] = "auth_failed"
            except UpdateFailed as err:
                _LOGGER.error("SmartPMS config flow: API error: %s", err)
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError as err:
                _LOGGER.error("SmartPMS config flow: connection error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("SmartPMS config flow: unexpected error")
                errors["base"] = "unknown"
            else:
                property_info: dict[int, dict] = {}
                for prop in properties:
                    pid = prop.get("id")
                    if pid is not None:
                        property_info[pid] = {
                            "name": prop.get("name", ""),
                            "count": len(prop.get("units", [])),
                        }

                if not property_info:
                    errors["base"] = "no_properties"
                else:
                    self._user_input = user_input
                    self._properties = property_info
                    return await self.async_step_property()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_property(self, user_input=None):
        """Handle step 2: property selection."""
        if user_input is not None:
            pid = user_input[CONF_PROPERTY_ID]
            pname = user_input[CONF_PROPERTY_NAME]
            data = {
                **self._user_input,
                CONF_PROPERTY_ID: pid,
                CONF_PROPERTY_NAME: pname,
            }

            await self.async_set_unique_id(f"{self._user_input[CONF_EMAIL]}_{pid}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"SmartPMS - {pname}",
                data=data,
            )

        # Build property selector options
        property_options = {}
        for pid, info in self._properties.items():
            name = info.get("name")
            count = info["count"]
            if name:
                property_options[pid] = f"{name} ({count} units)"
            else:
                property_options[pid] = f"ID {pid} ({count} units)"

        default_pid = (
            next(iter(self._properties))
            if len(self._properties) == 1
            else vol.UNDEFINED
        )

        # Pre-fill property name if available from first property
        default_name = vol.UNDEFINED
        if len(self._properties) == 1:
            info = next(iter(self._properties.values()))
            if info.get("name"):
                default_name = info["name"]

        return self.async_show_form(
            step_id="property",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROPERTY_ID, default=default_pid): vol.In(
                        property_options
                    ),
                    vol.Required(CONF_PROPERTY_NAME, default=default_name): str,
                }
            ),
        )

    async def async_step_reauth(self, entry_data: dict):
        """Handle reauth trigger."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle reauth confirmation."""
        errors = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

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
                await client.get_units()
            except ConfigEntryAuthFailed:
                errors["base"] = "auth_failed"
            except (UpdateFailed, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("SmartPMS reauth: unexpected error")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_API_KEY: user_input[CONF_API_KEY],
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_API_KEY, default=entry.data.get(CONF_API_KEY, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SmartPMSOptionsFlow()


class SmartPMSOptionsFlow(config_entries.OptionsFlow):
    """Handle SmartPMS options."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
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
