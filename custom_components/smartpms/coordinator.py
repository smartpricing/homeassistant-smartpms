"""Coordinator and API client for SmartPMS."""

import json
import logging
from datetime import datetime, timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import API_BASE_URL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SmartPMSApiClient:
    """Client for the SmartPMS REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        api_key: str,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._email = email
        self._password = password
        self._api_key = api_key
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def authenticate(self) -> None:
        """Authenticate with SmartPMS API and obtain a JWT token."""
        url = f"{API_BASE_URL}/login"
        payload = {"email": self._email, "password": self._password}
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": self._api_key,
        }

        try:
            _LOGGER.debug(
                "Authenticating with SmartPMS: POST %s, email=%s",
                url,
                self._email,
            )
            async with self._session.post(url, json=payload, headers=headers) as resp:
                resp_text = await resp.text()
                _LOGGER.debug(
                    "SmartPMS auth response: HTTP %s, body: %.500s",
                    resp.status,
                    resp_text,
                )

                if resp.status in (401, 422):
                    raise ConfigEntryAuthFailed(
                        f"Invalid credentials (HTTP {resp.status}): {resp_text[:200]}"
                    )
                if resp.status == 403:
                    raise ConfigEntryAuthFailed(
                        f"Forbidden (HTTP 403): {resp_text[:200]}"
                    )
                if resp.status != 200:
                    raise UpdateFailed(
                        f"SmartPMS auth error: HTTP {resp.status} - {resp_text[:200]}"
                    )

                try:
                    body = json.loads(resp_text)
                except (json.JSONDecodeError, ValueError) as err:
                    raise UpdateFailed(
                        f"Non-JSON response from SmartPMS: {resp_text[:200]}"
                    ) from err

                data = body.get("data", {})
                self._token = data.get("token")
                expires_at = data.get("expiresAt")
                if expires_at:
                    self._token_expires_at = datetime.fromtimestamp(expires_at)
                else:
                    self._token_expires_at = datetime.now() + timedelta(hours=1)

                if not self._token:
                    raise UpdateFailed(f"No token in login response. Body: {body}")

                _LOGGER.debug("SmartPMS authentication successful")
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Connection error to SmartPMS: {err}") from err

    async def _ensure_auth(self) -> None:
        """Re-authenticate if the token is expired or missing."""
        if (
            self._token is None
            or self._token_expires_at is None
            or datetime.now() >= self._token_expires_at - timedelta(minutes=5)
        ):
            await self.authenticate()

    def _auth_headers(self) -> dict[str, str]:
        """Return headers with current auth token."""
        return {
            "Authorization": f"Bearer {self._token}",
            "X-API-KEY": self._api_key,
        }

    async def get_properties(self) -> list[dict]:
        """Get list of properties with their units."""
        await self._ensure_auth()

        url = f"{API_BASE_URL}/automations/properties"

        try:
            async with self._session.get(url, headers=self._auth_headers()) as resp:
                if resp.status in (401, 403):
                    resp_text = await resp.text()
                    raise ConfigEntryAuthFailed(
                        f"Properties request failed (HTTP {resp.status}): "
                        f"{resp_text[:200]}"
                    )
                if resp.status != 200:
                    resp_text = await resp.text()
                    raise UpdateFailed(
                        f"SmartPMS API error: HTTP {resp.status} - {resp_text[:200]}"
                    )
                body = await resp.json()
                return body.get("data", [])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Connection error to SmartPMS: {err}") from err

    async def get_units(self, date: str | None = None) -> list[dict]:
        """Get list of units with occupancy status."""
        await self._ensure_auth()

        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        url = f"{API_BASE_URL}/automations/units"
        params = {"date": date}

        try:
            async with self._session.get(
                url, params=params, headers=self._auth_headers()
            ) as resp:
                if resp.status == 401:
                    # Token expired, re-authenticate and retry once
                    self._token = None
                    await self._ensure_auth()
                    async with self._session.get(
                        url, params=params, headers=self._auth_headers()
                    ) as retry_resp:
                        if retry_resp.status == 401:
                            raise ConfigEntryAuthFailed(
                                "SmartPMS authentication failed after retry"
                            )
                        retry_resp.raise_for_status()
                        body = await retry_resp.json()
                        return body.get("data", [])

                if resp.status == 403:
                    resp_text = await resp.text()
                    _LOGGER.error(
                        "SmartPMS get_units: HTTP 403 (Forbidden), body: %.500s",
                        resp_text,
                    )
                    raise ConfigEntryAuthFailed(
                        f"API key/user mismatch (HTTP 403): {resp_text[:200]}"
                    )

                if resp.status != 200:
                    resp_text = await resp.text()
                    _LOGGER.error(
                        "SmartPMS get_units: HTTP %s, body: %.500s",
                        resp.status,
                        resp_text,
                    )
                    raise UpdateFailed(
                        f"SmartPMS API error: HTTP {resp.status} - {resp_text[:200]}"
                    )
                body = await resp.json()
                return body.get("data", [])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Connection error to SmartPMS: {err}") from err


class SmartPMSCoordinator(DataUpdateCoordinator):
    """Coordinator to update SmartPMS data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SmartPMSApiClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        property_id: int | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._property_id = property_id

    async def _async_update_data(self) -> dict[int, dict]:
        """Fetch updated data from SmartPMS."""
        units = await self.client.get_units()
        if self._property_id is not None:
            units = [u for u in units if u.get("property_id") == self._property_id]
        return {unit["id"]: unit for unit in units}
