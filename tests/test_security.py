"""Security tests for the SmartPMS integration.

Tests marked ``xfail(strict=True)`` document a finding of the SPCWR security
review (epic SPCWR-141): they fail on the untouched default branch for the
reason given and are flipped to regular tests by the PR that fixes it.
The unmarked tests pin security properties that already hold.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import aiohttp
import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import TextSelector, TextSelectorType
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.smartpms.const import CONF_API_KEY, DOMAIN
from custom_components.smartpms.coordinator import SmartPMSApiClient

from .conftest import login_body
from .const import (
    API_KEY,
    EMAIL,
    LOGIN_URL,
    PASSWORD,
    PROPERTIES_URL,
    SECRETS,
    UNITS_URL,
    UPSTREAM_BODY_SENTINEL,
)
from .helpers import respond_in_sequence

UPSTREAM_BODY = json.dumps(
    {
        "message": f"{UPSTREAM_BODY_SENTINEL} SQLSTATE[HY000] at /var/www/app.php:42",
        "echo": {"email": EMAIL, "password": PASSWORD},
    }
)


async def _run_full_cycle(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry, units_body, freezer
) -> None:
    """Setup, one normal refresh and one refresh that needs a re-login."""
    aioclient_mock.post(LOGIN_URL, json=login_body())
    respond_in_sequence(
        aioclient_mock,
        "get",
        UNITS_URL,
        [(200, units_body), (200, units_body), (401, {"m": "x"}), (200, units_body)],
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(2):
        freezer.tick(timedelta(seconds=301))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


# --------------------------------------------------------------------------
# F1 - credentials / tokens / PII in Home Assistant logs  (security/sast)
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="F1: the login response body (access + refresh token) is logged at "
    "DEBUG; fixed by security/sast",
)
async def test_debug_logs_contain_no_credentials_or_tokens(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    units_body,
    caplog: pytest.LogCaptureFixture,
    freezer,
) -> None:
    """With debug logging enabled (what users paste into public GitHub issues)
    no password, API key, access token or refresh token is written."""
    caplog.set_level(logging.DEBUG)
    await _run_full_cycle(hass, aioclient_mock, config_entry, units_body, freezer)
    assert hass.states.get("sensor.test_hotel_alpha_room_1").state == "free"
    for name, value in SECRETS.items():
        assert value not in caplog.text, f"{name} written to the log"


@pytest.mark.xfail(
    strict=True,
    reason="F1: the account e-mail is logged at DEBUG on every login; fixed by "
    "security/sast",
)
async def test_debug_logs_contain_no_account_email(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    units_body,
    caplog: pytest.LogCaptureFixture,
    freezer,
) -> None:
    """The account e-mail (PII, also the SmartPMS username) is not logged."""
    caplog.set_level(logging.DEBUG, logger="custom_components.smartpms")
    await _run_full_cycle(hass, aioclient_mock, config_entry, units_body, freezer)
    smartpms_log = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.smartpms")
    )
    assert EMAIL not in smartpms_log


# --------------------------------------------------------------------------
# F2 - upstream response bodies in errors, UI and WARNING/ERROR logs
# --------------------------------------------------------------------------

SETUP_FAILURES = {
    "login-401": {"login": (401, UPSTREAM_BODY)},
    "login-422": {"login": (422, UPSTREAM_BODY)},
    "login-500": {"login": (500, UPSTREAM_BODY)},
    "units-403": {"units": (403, UPSTREAM_BODY)},
    "units-500": {"units": (500, UPSTREAM_BODY)},
}


def _visible_text(caplog: pytest.LogCaptureFixture, entry) -> str:
    """Everything a user sees without enabling debug logging."""
    visible = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    visible.append(str(entry.reason or ""))
    return "\n".join(visible)


@pytest.mark.xfail(
    strict=True,
    reason="F2: raw upstream response bodies are copied into exception messages "
    "(shown in the UI) and ERROR logs; fixed by security/sast",
)
@pytest.mark.parametrize("scenario", list(SETUP_FAILURES))
async def test_setup_errors_do_not_echo_upstream_bodies(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
) -> None:
    """Failed setup: the API's error body stays out of UI and INFO+ logs."""
    failure = SETUP_FAILURES[scenario]
    status, body = failure.get("login", (200, None))
    if body is None:
        aioclient_mock.post(LOGIN_URL, json=login_body())
    else:
        aioclient_mock.post(LOGIN_URL, status=status, text=body)
    if "units" in failure:
        status, body = failure["units"]
        aioclient_mock.get(UNITS_URL, status=status, text=body)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    visible = _visible_text(caplog, config_entry)
    assert UPSTREAM_BODY_SENTINEL not in visible
    assert PASSWORD not in visible


@pytest.mark.xfail(
    strict=True,
    reason="F2: config-flow errors log the upstream body at WARNING/ERROR; "
    "fixed by security/sast",
)
@pytest.mark.parametrize(
    ("url", "status"),
    [(LOGIN_URL, 401), (LOGIN_URL, 500), (PROPERTIES_URL, 500)],
)
async def test_config_flow_errors_do_not_log_upstream_bodies(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    url: str,
    status: int,
) -> None:
    """Credentials-form errors do not copy the API body into the log."""
    if url == LOGIN_URL:
        aioclient_mock.post(LOGIN_URL, status=status, text=UPSTREAM_BODY)
    else:
        aioclient_mock.post(LOGIN_URL, json=login_body())
        aioclient_mock.get(PROPERTIES_URL, status=status, text=UPSTREAM_BODY)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_API_KEY: API_KEY},
    )
    assert result["errors"]["base"] in {"auth_failed", "cannot_connect"}
    visible = "\n".join(
        r.getMessage() for r in caplog.records if r.levelno >= logging.INFO
    )
    assert UPSTREAM_BODY_SENTINEL not in visible
    assert PASSWORD not in visible


# --------------------------------------------------------------------------
# F3 - stored API key sent back to the browser in the reauth/reconfigure forms
# --------------------------------------------------------------------------


def _prefilled_values(result) -> list[str]:
    """Every value the form sends to the browser (defaults, suggestions)."""
    values = []
    for key in result["data_schema"].schema:
        if getattr(key, "default", vol.UNDEFINED) is not vol.UNDEFINED:
            values.append(str(key.default()))
        suggested = (getattr(key, "description", None) or {}).get("suggested_value")
        if suggested is not None:
            values.append(str(suggested))
    return values


def _validator(result, name: str):
    for key, validator in result["data_schema"].schema.items():
        if key == name:
            return validator
    raise AssertionError(f"{name} not in form")


@pytest.mark.xfail(
    strict=True,
    reason="F3: reauth/reconfigure forms pre-fill the stored API key in "
    "clear text; fixed by security/sast",
)
@pytest.mark.parametrize("flow", ["reauth", "reconfigure"])
async def test_forms_do_not_disclose_the_stored_api_key(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry, flow: str
) -> None:
    """The stored API key / password are never sent to the frontend."""
    if flow == "reauth":
        result = await config_entry.start_reauth_flow(hass)
    else:
        result = await config_entry.start_reconfigure_flow(hass)
    prefilled = _prefilled_values(result)
    assert API_KEY not in prefilled
    assert PASSWORD not in prefilled


@pytest.mark.xfail(
    strict=True,
    reason="F3: the API key field is a plain text field (typed in clear); "
    "fixed by security/sast",
)
@pytest.mark.parametrize("flow", ["user", "reauth", "reconfigure"])
async def test_api_key_field_is_masked(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry, flow: str
) -> None:
    """The API key input is rendered as a password field in every form."""
    if flow == "user":
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    elif flow == "reauth":
        result = await config_entry.start_reauth_flow(hass)
    else:
        result = await config_entry.start_reconfigure_flow(hass)
    validator = _validator(result, CONF_API_KEY)
    assert isinstance(validator, TextSelector)
    assert validator.config.get("type") == TextSelectorType.PASSWORD


# --------------------------------------------------------------------------
# F4 - no request timeout (security/dast-hardening)
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="F4: requests rely on aiohttp's 300 s default timeout; fixed by "
    "security/dast-hardening",
)
async def test_every_request_sets_an_explicit_timeout(
    hass: HomeAssistant, recording_session
) -> None:
    """Each API call is bounded (<= 60 s) so a stalled API cannot hang
    Home Assistant's setup or the coordinator for 5 minutes."""
    client = SmartPMSApiClient(
        session=recording_session,
        email=EMAIL,
        password=PASSWORD,
        api_key=API_KEY,
    )
    await client.get_properties()
    await client.get_units()
    assert len(recording_session.calls) == 3
    for method, url, kwargs in recording_session.calls:
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, aiohttp.ClientTimeout), (method, url)
        assert timeout.total is not None and timeout.total <= 60


@pytest.mark.xfail(
    strict=True,
    reason="F4: a request timeout escapes as a bare TimeoutError (config flow "
    "shows 'unknown'); fixed by security/dast-hardening",
)
async def test_request_timeout_is_reported_as_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timed-out login maps to UpdateFailed / cannot_connect like any other
    connection problem."""
    aioclient_mock.post(LOGIN_URL, exc=TimeoutError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_API_KEY: API_KEY},
    )
    assert result["errors"] == {"base": "cannot_connect"}


# --------------------------------------------------------------------------
# Properties that already hold (must stay green)
# --------------------------------------------------------------------------


async def test_password_is_only_sent_to_the_login_endpoint(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    units_body,
    freezer,
) -> None:
    """Data calls carry the token + API key, never the password."""
    await _run_full_cycle(hass, aioclient_mock, config_entry, units_body, freezer)
    for method, url, data, headers in aioclient_mock.mock_calls:
        if url.path.endswith("/login"):
            assert method.upper() == "POST"
            continue
        blob = json.dumps([str(url), data, dict(headers or {})])
        assert PASSWORD not in blob
        assert EMAIL not in blob


async def test_auth_failure_never_logs_the_password(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even at DEBUG, a rejected login does not write the password."""
    caplog.set_level(logging.DEBUG)
    aioclient_mock.post(LOGIN_URL, status=401, json={"message": "Unauthorized"})
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert PASSWORD not in caplog.text
    assert API_KEY not in caplog.text


async def test_non_https_or_foreign_hosts_are_never_called(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry,
    units_body,
    freezer,
) -> None:
    """All traffic goes to https://pms-api.smartness.com only."""
    await _run_full_cycle(hass, aioclient_mock, config_entry, units_body, freezer)
    hosts = {(c[1].scheme, c[1].host) for c in aioclient_mock.mock_calls}
    assert hosts == {("https", "pms-api.smartness.com")}
