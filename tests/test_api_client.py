"""Characterization tests for SmartPMSApiClient (the SmartPMS public v2 API).

These pin the wire contract the integration has with
https://pms-api.smartness.com/api/public/v2 on the untouched default branch:
endpoints, methods, headers, token caching/refresh and the mapping of HTTP
errors to Home Assistant exceptions (auth failure -> reauth, anything else ->
retry). They must stay green after every security PR.
"""

from __future__ import annotations

from datetime import datetime

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.smartpms.const import API_BASE_URL
from custom_components.smartpms.coordinator import SmartPMSApiClient

from .conftest import login_body
from .const import (
    ACCESS_TOKEN,
    API_BASE,
    API_KEY,
    EMAIL,
    LOGIN_URL,
    PASSWORD,
    PROPERTIES_URL,
    UNITS_URL,
)
from .helpers import calls_to, respond_in_sequence


def _client(hass: HomeAssistant) -> SmartPMSApiClient:
    return SmartPMSApiClient(
        session=async_get_clientsession(hass),
        email=EMAIL,
        password=PASSWORD,
        api_key=API_KEY,
    )


def test_api_base_url_is_the_public_v2_https_endpoint() -> None:
    """The integration talks to the production public v2 API over HTTPS."""
    assert API_BASE_URL == API_BASE
    assert API_BASE_URL.startswith("https://")


async def test_authenticate_posts_credentials_with_api_key(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """POST /login with email+password JSON and the X-API-KEY header."""
    client = _client(hass)
    await client.authenticate()

    [call] = calls_to(mock_api, "POST", "/login")
    _method, url, data, headers = call
    assert str(url) == LOGIN_URL
    assert data == {"email": EMAIL, "password": PASSWORD}
    assert headers["X-API-KEY"] == API_KEY
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


async def test_get_units_sends_bearer_token_api_key_and_date(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """GET /automations/units?date=<today> with Bearer token and API key."""
    client = _client(hass)
    units = await client.get_units()

    assert [u["id"] for u in units] == [1001, 1002, 1003, 2001]
    [call] = calls_to(mock_api, "GET", "/automations/units")
    _, url, _, headers = call
    assert url.query["date"] == datetime.now().strftime("%Y-%m-%d")
    assert headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert headers["X-API-KEY"] == API_KEY


async def test_get_units_with_explicit_date(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """An explicit date is passed through unchanged."""
    await _client(hass).get_units("2026-01-31")
    [call] = calls_to(mock_api, "GET", "/automations/units")
    assert call[1].query["date"] == "2026-01-31"


async def test_get_properties(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """GET /automations/properties returns the data list."""
    props = await _client(hass).get_properties()
    assert [p["id"] for p in props] == [101, 202]
    [call] = calls_to(mock_api, "GET", "/automations/properties")
    assert str(call[1]) == PROPERTIES_URL
    assert call[3]["Authorization"] == f"Bearer {ACCESS_TOKEN}"


async def test_token_is_cached_until_close_to_expiry(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """A valid token is reused: one login for several API calls."""
    client = _client(hass)
    await client.get_units()
    await client.get_units()
    await client.get_properties()
    assert len(calls_to(mock_api, "POST", "/login")) == 1
    assert len(calls_to(mock_api, "GET", "/automations/units")) == 2


async def test_token_is_renewed_within_five_minutes_of_expiry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, units_body
) -> None:
    """A token expiring in < 5 minutes triggers a new login on every call."""
    aioclient_mock.post(LOGIN_URL, json=login_body(expires_in=200))
    aioclient_mock.get(UNITS_URL, json=units_body)
    client = _client(hass)
    await client.get_units()
    await client.get_units()
    assert len(calls_to(aioclient_mock, "POST", "/login")) == 2


async def test_login_without_expiry_defaults_to_one_hour(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, units_body
) -> None:
    """No expiresAt in the login response: token assumed valid for 1 hour."""
    aioclient_mock.post(LOGIN_URL, json=login_body(expires_in=None))
    aioclient_mock.get(UNITS_URL, json=units_body)
    client = _client(hass)
    await client.get_units()
    await client.get_units()
    assert len(calls_to(aioclient_mock, "POST", "/login")) == 1


async def test_units_401_reauthenticates_once_and_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, units_body
) -> None:
    """HTTP 401 on units: drop the token, log in again, retry exactly once."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    respond_in_sequence(
        aioclient_mock, "get", UNITS_URL, [(401, {"message": "x"}), (200, units_body)]
    )
    units = await _client(hass).get_units()
    assert len(units) == 4
    assert len(calls_to(aioclient_mock, "POST", "/login")) == 2
    assert len(calls_to(aioclient_mock, "GET", "/automations/units")) == 2


async def test_units_401_after_retry_raises_auth_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Still 401 after re-login: ConfigEntryAuthFailed (starts reauth)."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(UNITS_URL, status=401, json={"message": "x"})
    with pytest.raises(ConfigEntryAuthFailed):
        await _client(hass).get_units()
    assert len(calls_to(aioclient_mock, "GET", "/automations/units")) == 2


@pytest.mark.parametrize("status", [401, 403, 422])
async def test_login_rejected_raises_auth_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """Login 401/403/422 -> ConfigEntryAuthFailed."""
    aioclient_mock.post(LOGIN_URL, status=status, json={"message": "no"})
    with pytest.raises(ConfigEntryAuthFailed):
        await _client(hass).authenticate()


@pytest.mark.parametrize("status", [400, 429, 500, 502, 503])
async def test_login_other_errors_raise_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """Any other non-200 login status -> UpdateFailed (retried later)."""
    aioclient_mock.post(LOGIN_URL, status=status, text="error")
    with pytest.raises(UpdateFailed):
        await _client(hass).authenticate()


async def test_login_non_json_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 with a non-JSON body -> UpdateFailed."""
    aioclient_mock.post(LOGIN_URL, text="<html>maintenance</html>")
    with pytest.raises(UpdateFailed):
        await _client(hass).authenticate()


async def test_login_without_token_raises_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 without data.token -> UpdateFailed."""
    aioclient_mock.post(LOGIN_URL, json={"success": True, "data": {}})
    with pytest.raises(UpdateFailed):
        await _client(hass).authenticate()


@pytest.mark.parametrize(
    ("url", "call"),
    [
        (UNITS_URL, "get_units"),
        (PROPERTIES_URL, "get_properties"),
    ],
)
async def test_forbidden_raises_auth_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, url: str, call: str
) -> None:
    """HTTP 403 (API key / user mismatch) -> ConfigEntryAuthFailed."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(url, status=403, json={"message": "forbidden"})
    with pytest.raises(ConfigEntryAuthFailed):
        await getattr(_client(hass), call)()


async def test_properties_401_raises_auth_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """HTTP 401 on properties -> ConfigEntryAuthFailed (no retry)."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(PROPERTIES_URL, status=401, json={"message": "x"})
    with pytest.raises(ConfigEntryAuthFailed):
        await _client(hass).get_properties()
    assert len(calls_to(aioclient_mock, "GET", "/automations/properties")) == 1


@pytest.mark.parametrize(
    ("url", "call"), [(UNITS_URL, "get_units"), (PROPERTIES_URL, "get_properties")]
)
@pytest.mark.parametrize("status", [404, 429, 500, 503])
async def test_server_errors_raise_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    url: str,
    call: str,
    status: int,
) -> None:
    """Other HTTP errors on data endpoints -> UpdateFailed."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    aioclient_mock.get(url, status=status, text="error")
    with pytest.raises(UpdateFailed):
        await getattr(_client(hass), call)()


@pytest.mark.parametrize(
    "exc", [aiohttp.ClientConnectionError("boom"), aiohttp.ClientPayloadError("x")]
)
@pytest.mark.parametrize("failing", [LOGIN_URL, UNITS_URL])
async def test_connection_errors_raise_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    units_body,
    exc: Exception,
    failing: str,
) -> None:
    """aiohttp client errors (DNS, TLS, reset...) -> UpdateFailed."""
    if failing == LOGIN_URL:
        aioclient_mock.post(LOGIN_URL, exc=exc)
    else:
        aioclient_mock.post(LOGIN_URL, json=login_body())
        aioclient_mock.get(UNITS_URL, exc=exc)
    with pytest.raises(UpdateFailed):
        await _client(hass).get_units()


async def test_only_login_is_a_write_request(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """The integration only reads data: the sole non-GET call is POST /login."""
    client = _client(hass)
    await client.get_properties()
    await client.get_units()
    methods = {(c[0].upper(), c[1].path) for c in mock_api.mock_calls}
    assert methods == {
        ("POST", "/api/public/v2/login"),
        ("GET", "/api/public/v2/automations/properties"),
        ("GET", "/api/public/v2/automations/units"),
    }


async def test_requests_do_not_disable_tls_verification(
    hass: HomeAssistant, recording_session
) -> None:
    """No request passes ssl=False / verify_ssl=False (TLS stays verified)."""
    client = SmartPMSApiClient(
        session=recording_session,
        email=EMAIL,
        password=PASSWORD,
        api_key=API_KEY,
    )
    await client.get_properties()
    await client.get_units()
    assert recording_session.calls
    for _method, url, kwargs in recording_session.calls:
        assert url.startswith("https://")
        assert kwargs.get("ssl", True) is not False
        assert "verify_ssl" not in kwargs
