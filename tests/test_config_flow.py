"""Characterization tests for the SmartPMS config, reauth, reconfigure and
options flows (behaviour of the untouched default branch)."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.smartpms.const import (
    CONF_API_KEY,
    CONF_PROPERTY_ID,
    CONF_PROPERTY_NAME,
    DOMAIN,
)

from .conftest import entry_data, login_body
from .const import (
    API_KEY,
    EMAIL,
    LOGIN_URL,
    OTHER_PROPERTY_ID,
    PASSWORD,
    PROPERTIES_URL,
    PROPERTY_ID,
    PROPERTY_NAME,
)
from .helpers import calls_to

USER_INPUT = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_API_KEY: API_KEY}


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Do not set up the entry created by the flow (flow tested in isolation)."""
    with patch(
        "custom_components.smartpms.async_setup_entry", return_value=True
    ) as mock:
        yield mock


def _schema_field(result, name: str):
    """Return the voluptuous marker for field ``name`` in a form result."""
    for key in result["data_schema"].schema:
        if key == name:
            return key
    raise AssertionError(f"{name} not in form schema")


async def _start_user_flow(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, mock_setup_entry
) -> None:
    """Credentials -> property selection -> entry with all fields."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    assert {str(k) for k in result["data_schema"].schema} == {
        CONF_EMAIL,
        CONF_PASSWORD,
        CONF_API_KEY,
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "property"
    prop_field = _schema_field(result, CONF_PROPERTY_ID)
    options = result["data_schema"].schema[prop_field].container
    assert options == {
        PROPERTY_ID: "Test Hotel Alpha (3 units)",
        OTHER_PROPERTY_ID: "Test Hotel Beta (1 units)",
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_ID: PROPERTY_ID, CONF_PROPERTY_NAME: "My Hotel"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SmartPMS - My Hotel"
    assert result["data"] == entry_data(**{CONF_PROPERTY_NAME: "My Hotel"})
    assert result["result"].unique_id == f"{EMAIL}_{PROPERTY_ID}"
    assert len(mock_setup_entry.mock_calls) == 1

    # Validation used exactly one login and one properties call.
    assert len(calls_to(mock_api, "POST", "/login")) == 1
    assert len(calls_to(mock_api, "GET", "/automations/properties")) == 1


async def test_single_property_is_preselected(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, properties_body
) -> None:
    """With one property, it is the default and its name prefills the name."""
    del properties_body["data"][1]
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert _schema_field(result, CONF_PROPERTY_ID).default() == PROPERTY_ID
    assert _schema_field(result, CONF_PROPERTY_NAME).default() == PROPERTY_NAME


@pytest.mark.parametrize(
    ("login_kwargs", "error"),
    [
        ({"status": 401, "json": {"message": "no"}}, "auth_failed"),
        ({"status": 422, "json": {"message": "no"}}, "auth_failed"),
        ({"status": 403, "json": {"message": "no"}}, "auth_failed"),
        ({"status": 500, "text": "boom"}, "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError("boom")}, "cannot_connect"),
    ],
)
async def test_user_flow_login_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    login_kwargs: dict,
    error: str,
) -> None:
    """Login failures are shown on the credentials form."""
    aioclient_mock.post(LOGIN_URL, **login_kwargs)
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_user_flow_unexpected_error(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """Unexpected exceptions map to 'unknown'."""
    with patch(
        "custom_components.smartpms.config_flow.SmartPMSApiClient.authenticate",
        side_effect=ValueError("unexpected"),
    ):
        result = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_no_properties(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, properties_body
) -> None:
    """An account without properties cannot be configured."""
    properties_body["data"] = []
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "no_properties"}


async def test_user_flow_recovers_after_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, properties_body
) -> None:
    """After a failed attempt the user can retry on the same flow."""
    aioclient_mock.post(LOGIN_URL, status=401, json={"message": "no"})
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "auth_failed"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(PROPERTIES_URL, json=properties_body)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "property"


async def test_user_flow_aborts_if_property_already_configured(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Same account + property twice -> abort already_configured."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_ID: PROPERTY_ID, CONF_PROPERTY_NAME: PROPERTY_NAME},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_account_other_property_is_a_new_entry(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """A second property of the same account gets its own entry."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_ID: OTHER_PROPERTY_ID, CONF_PROPERTY_NAME: "Beta"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == f"{EMAIL}_{OTHER_PROPERTY_ID}"


async def test_reauth_updates_credentials(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Reauth stores the new password and reloads the entry."""
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert _schema_field(result, CONF_EMAIL).default() == EMAIL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: "PW-SENTINEL-new", CONF_API_KEY: API_KEY},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "PW-SENTINEL-new"
    assert config_entry.data[CONF_API_KEY] == API_KEY
    assert config_entry.data[CONF_PROPERTY_ID] == PROPERTY_ID
    [login] = calls_to(mock_api, "POST", "/login")
    assert login[2] == {"email": EMAIL, "password": "PW-SENTINEL-new"}


async def test_reauth_with_invalid_credentials_shows_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry
) -> None:
    """Wrong credentials during reauth keep the form open with auth_failed."""
    aioclient_mock.post(LOGIN_URL, status=401, json={"message": "no"})
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong", CONF_API_KEY: API_KEY},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "auth_failed"}
    assert config_entry.data[CONF_PASSWORD] == PASSWORD


async def test_reconfigure_empty_password_keeps_stored_one(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Reconfigure with an empty password validates with the stored password
    and can move the entry to another property."""
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert _schema_field(result, CONF_PASSWORD).default() == ""

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: "", CONF_API_KEY: API_KEY},
    )
    assert result["step_id"] == "reconfigure_property"
    [login] = calls_to(mock_api, "POST", "/login")
    assert login[2] == {"email": EMAIL, "password": PASSWORD}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_ID: OTHER_PROPERTY_ID, CONF_PROPERTY_NAME: "Beta"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PASSWORD] == PASSWORD
    assert config_entry.data[CONF_PROPERTY_ID] == OTHER_PROPERTY_ID
    assert config_entry.data[CONF_PROPERTY_NAME] == "Beta"
    assert config_entry.unique_id == f"{EMAIL}_{OTHER_PROPERTY_ID}"
    assert config_entry.title == "SmartPMS - Beta"


async def test_reconfigure_new_password_and_api_key_are_stored(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """New password / API key entered during reconfigure replace the old ones."""
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: EMAIL,
            CONF_PASSWORD: "PW-SENTINEL-new",
            CONF_API_KEY: "APIKEY-SENTINEL-new",
        },
    )
    [login] = calls_to(mock_api, "POST", "/login")
    assert login[3]["X-API-KEY"] == "APIKEY-SENTINEL-new"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_ID: PROPERTY_ID, CONF_PROPERTY_NAME: PROPERTY_NAME},
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PASSWORD] == "PW-SENTINEL-new"
    assert config_entry.data[CONF_API_KEY] == "APIKEY-SENTINEL-new"


async def test_reconfigure_to_property_of_other_entry_aborts(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry
) -> None:
    """Reconfigure cannot create a duplicate of another entry."""
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{EMAIL}_{OTHER_PROPERTY_ID}",
        data=entry_data(**{CONF_PROPERTY_ID: OTHER_PROPERTY_ID}),
    )
    other.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: "", CONF_API_KEY: API_KEY},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROPERTY_ID: OTHER_PROPERTY_ID, CONF_PROPERTY_NAME: "Beta"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_PROPERTY_ID] == PROPERTY_ID


async def test_options_flow_sets_scan_interval(
    hass: HomeAssistant, config_entry
) -> None:
    """Options: scan interval, default 300 s."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert _schema_field(result, CONF_SCAN_INTERVAL).default() == 300
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 120}


@pytest.mark.parametrize("value", [0, 59, 3601, 86400])
async def test_options_flow_rejects_out_of_range_interval(
    hass: HomeAssistant, config_entry, value: int
) -> None:
    """Polling faster than 60 s (or slower than 1 h) is refused."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: value}
        )
