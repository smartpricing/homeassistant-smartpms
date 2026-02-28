"""Coordinator e API client per SmartPMS."""

import logging
from datetime import datetime, timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import API_BASE_URL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SmartPMSApiClient:
    """Client per le API REST di SmartPMS."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
    ) -> None:
        """Inizializza il client API."""
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def authenticate(self) -> None:
        """Autentica con le API SmartPMS e ottieni il token JWT."""
        url = f"{API_BASE_URL}/login"
        payload = {"email": self._email, "password": self._password}
        headers = {"Content-Type": "application/json"}

        try:
            async with self._session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 401:
                    raise ConfigEntryAuthFailed("Credenziali non valide")
                if resp.status != 200:
                    raise UpdateFailed(
                        f"Errore autenticazione SmartPMS: HTTP {resp.status}"
                    )
                body = await resp.json()
                data = body.get("data", {})
                self._token = data.get("token")
                expires_at = data.get("expiresAt")
                if expires_at:
                    self._token_expires_at = datetime.fromtimestamp(expires_at)
                else:
                    self._token_expires_at = datetime.now() + timedelta(hours=1)

                if not self._token:
                    raise UpdateFailed("Token non ricevuto dalla risposta login")

                _LOGGER.debug("SmartPMS: autenticazione riuscita")
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Errore di connessione a SmartPMS: {err}") from err

    async def _ensure_auth(self) -> None:
        """Ri-autentica se il token è scaduto o mancante."""
        if (
            self._token is None
            or self._token_expires_at is None
            or datetime.now() >= self._token_expires_at - timedelta(minutes=5)
        ):
            await self.authenticate()

    async def get_units(self, date: str | None = None) -> list[dict]:
        """Ottieni lista unità con stato occupazione."""
        await self._ensure_auth()

        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        url = f"{API_BASE_URL}/automations/units"
        params = {"date": date}
        headers = {
            "Authorization": f"Bearer {self._token}",
        }

        try:
            async with self._session.get(
                url, params=params, headers=headers
            ) as resp:
                if resp.status == 401:
                    self._token = None
                    await self._ensure_auth()
                    headers["Authorization"] = f"Bearer {self._token}"
                    async with self._session.get(
                        url, params=params, headers=headers
                    ) as retry_resp:
                        if retry_resp.status == 401:
                            raise ConfigEntryAuthFailed(
                                "Autenticazione SmartPMS fallita"
                            )
                        retry_resp.raise_for_status()
                        body = await retry_resp.json()
                        return body.get("data", [])

                if resp.status != 200:
                    raise UpdateFailed(
                        f"Errore API SmartPMS: HTTP {resp.status}"
                    )
                body = await resp.json()
                return body.get("data", [])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Errore di connessione a SmartPMS: {err}") from err


class SmartPMSCoordinator(DataUpdateCoordinator):
    """Coordinator per aggiornare i dati SmartPMS."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SmartPMSApiClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        property_id: int | None = None,
    ) -> None:
        """Inizializza il coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._property_id = property_id

    async def _async_update_data(self) -> dict[int, dict]:
        """Recupera dati aggiornati da SmartPMS."""
        units = await self.client.get_units()
        if self._property_id is not None:
            units = [u for u in units if u.get("property_id") == self._property_id]
        return {unit["id"]: unit for unit in units}
