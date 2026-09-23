"""Shared fixtures for the SmartPMS integration tests.

Every HTTP call goes through ``aioclient_mock`` (or the recording session
below); pytest-homeassistant-custom-component also enables pytest-socket, so
an accidental real request to the SmartPMS API fails the test instead of
reaching production.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.smartpms.const import (
    CONF_API_KEY,
    CONF_PROPERTY_ID,
    CONF_PROPERTY_NAME,
    DOMAIN,
)

from .const import (
    API_KEY,
    EMAIL,
    LOGIN_URL,
    PASSWORD,
    PROPERTIES_URL,
    PROPERTY_ID,
    PROPERTY_NAME,
    UNITS_URL,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Return a fresh copy of a JSON fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def login_body(expires_in: int | None = 86400) -> dict[str, Any]:
    """Login response as returned by POST /api/public/v2/login."""
    body = load_fixture("login.json")
    now = int(time.time())
    if expires_in is None:
        body["data"].pop("expiresAt")
        body["data"].pop("expiresIn")
    else:
        body["data"]["expiresIn"] = expires_in
        body["data"]["expiresAt"] = now + expires_in
    body["data"]["refreshExpiresAt"] = now + 2678400
    return body


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/smartpms in every test."""


@pytest.fixture
def units_body() -> dict[str, Any]:
    """Units response for today (mutable per test)."""
    return load_fixture("units.json")


@pytest.fixture
def properties_body() -> dict[str, Any]:
    """Properties response (mutable per test)."""
    return load_fixture("properties.json")


@pytest.fixture
def mock_api(
    aioclient_mock: AiohttpClientMocker,
    units_body: dict[str, Any],
    properties_body: dict[str, Any],
) -> AiohttpClientMocker:
    """Happy-path SmartPMS API: login, properties and units.

    Bodies are serialised at request time, so a test may still edit
    ``units_body`` / ``properties_body`` after this fixture ran.
    """

    def _lazy(body_factory: Callable[[], Any]):
        async def _side_effect(method, url, data):
            return AiohttpClientMockResponse(method, url, json=body_factory())

        return _side_effect

    aioclient_mock.post(LOGIN_URL, side_effect=_lazy(login_body))
    aioclient_mock.get(PROPERTIES_URL, side_effect=_lazy(lambda: properties_body))
    aioclient_mock.get(UNITS_URL, side_effect=_lazy(lambda: units_body))
    return aioclient_mock


def entry_data(**overrides: Any) -> dict[str, Any]:
    """Config entry data as created by the config flow."""
    data = {
        CONF_EMAIL: EMAIL,
        CONF_PASSWORD: PASSWORD,
        CONF_API_KEY: API_KEY,
        CONF_PROPERTY_ID: PROPERTY_ID,
        CONF_PROPERTY_NAME: PROPERTY_NAME,
    }
    data.update(overrides)
    return data


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A SmartPMS config entry for property 101, added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"SmartPMS - {PROPERTY_NAME}",
        unique_id=f"{EMAIL}_{PROPERTY_ID}",
        data=entry_data(),
    )
    entry.add_to_hass(hass)
    return entry


class _RecordedResponse:
    """Minimal aiohttp-like response used by RecordingSession."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return json.dumps(self._body)

    async def json(self, *args: Any, **kwargs: Any) -> Any:
        return copy.deepcopy(self._body)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def __aenter__(self) -> _RecordedResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class RecordingSession:
    """Session double that records every request with all keyword arguments.

    ``AiohttpClientMocker`` drops kwargs such as ``timeout`` and ``ssl``; this
    double keeps them so tests can assert on transport settings.
    """

    def __init__(self, router: Callable[[str, str], tuple[int, Any]]) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._router = router

    def _request(self, method: str, url: str, **kwargs: Any) -> _RecordedResponse:
        self.calls.append((method, str(url), kwargs))
        status, body = self._router(method, str(url))
        return _RecordedResponse(status, body)

    def get(self, url: str, **kwargs: Any) -> _RecordedResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _RecordedResponse:
        return self._request("POST", url, **kwargs)


@pytest.fixture
def recording_session(
    units_body: dict[str, Any], properties_body: dict[str, Any]
) -> RecordingSession:
    """RecordingSession serving the happy-path API."""

    def router(method: str, url: str) -> tuple[int, Any]:
        if url == LOGIN_URL:
            return 200, login_body()
        if url == PROPERTIES_URL:
            return 200, properties_body
        if url == UNITS_URL:
            return 200, units_body
        return 404, {"error": "not mocked"}

    return RecordingSession(router)
