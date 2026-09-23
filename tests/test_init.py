"""Characterization tests for setup/unload, the coordinator and the sensors."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import (
    async_get_clientsession as real_get_session,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.smartpms.const import DOMAIN

from .conftest import login_body
from .const import LOGIN_URL, PROPERTY_ID, PROPERTY_NAME, UNITS_URL
from .helpers import calls_to

ROOM_1 = "sensor.test_hotel_alpha_room_1"
ROOM_2 = "sensor.test_hotel_alpha_room_2"
ROOM_3 = "sensor.test_hotel_alpha_room_3"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_one_sensor_per_unit_of_the_property(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Only units of the configured property become sensors."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    assert set(states) == {ROOM_1, ROOM_2, ROOM_3}
    assert states[ROOM_1].state == "free"
    assert states[ROOM_2].state == "occupied"
    assert states[ROOM_3].state == "blocked"
    assert states[ROOM_1].attributes["icon"] == "mdi:door-open"
    assert states[ROOM_2].attributes["icon"] == "mdi:bed"
    assert states[ROOM_3].attributes["icon"] == "mdi:wrench"
    assert states[ROOM_1].attributes["unit_id"] == 1001
    assert states[ROOM_1].attributes["unit_name"] == "Room 1"
    assert states[ROOM_1].attributes["property_id"] == PROPERTY_ID

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(ROOM_1).unique_id == "smartpms_101_1001"
    dev_reg = dr.async_get(hass)
    [device] = dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)
    assert device.identifiers == {(DOMAIN, "property_101")}
    assert device.name == PROPERTY_NAME
    assert device.manufacturer == "Smartness"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_setup_uses_the_shared_tls_verifying_session(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """The API client uses HA's shared session with TLS verification on."""
    with patch(
        "custom_components.smartpms.async_get_clientsession",
        side_effect=real_get_session,
    ) as get_session:
        await _setup(hass, config_entry)
    get_session.assert_called_once_with(hass)


async def test_default_and_custom_poll_interval(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Default polling is 5 minutes; the option overrides it."""
    await _setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    assert coordinator.update_interval == timedelta(seconds=300)

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_SCAN_INTERVAL: 120}
    )
    await hass.async_block_till_done()  # update listener reloads the entry
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    assert coordinator.update_interval == timedelta(seconds=120)


async def test_periodic_refresh_updates_states_without_new_login(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry,
    units_body,
    freezer,
) -> None:
    """Every interval one GET /automations/units; the token is reused."""
    await _setup(hass, config_entry)
    assert hass.states.get(ROOM_1).state == "free"

    units_body["data"][0]["status"] = "occupied"
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ROOM_1).state == "occupied"
    assert len(calls_to(mock_api, "GET", "/automations/units")) == 2
    assert len(calls_to(mock_api, "POST", "/login")) == 1


async def test_unload_entry(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Unloading removes the coordinator from hass.data."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert config_entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.parametrize("status", [401, 403, 422])
async def test_setup_with_rejected_login_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    status: int,
) -> None:
    """Rejected credentials: entry in SETUP_ERROR and a reauth flow starts;
    the integration does not keep retrying the login (no lockout loop)."""
    aioclient_mock.post(LOGIN_URL, status=status, json={"message": "no"})
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert flows[0]["context"]["entry_id"] == config_entry.entry_id
    assert len(calls_to(aioclient_mock, "POST", "/login")) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_setup_with_api_error_is_retried(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    status: int,
) -> None:
    """Transient API errors: SETUP_RETRY (HA retries with back-off)."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(UNITS_URL, status=status, text="error")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_refresh_failure_marks_entities_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    units_body,
    freezer,
) -> None:
    """A failed refresh after setup makes the sensors unavailable."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(UNITS_URL, json=units_body)
    await _setup(hass, config_entry)
    assert hass.states.get(ROOM_1).state == "free"

    aioclient_mock.clear_requests()
    aioclient_mock.get(UNITS_URL, status=500, text="error")
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ROOM_1).state == "unavailable"
