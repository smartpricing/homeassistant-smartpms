"""Diagnostics: structure and redaction (characterization)."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .const import EMAIL, PROPERTY_ID, PROPERTY_NAME, SECRETS

REDACTED = "**REDACTED**"


async def test_diagnostics_redacts_credentials(
    hass: HomeAssistant,
    hass_client,
    mock_api: AiohttpClientMocker,
    config_entry,
) -> None:
    """email, password and api_key are redacted; the token never appears."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert diag["config_entry"] == {
        "email": REDACTED,
        "password": REDACTED,
        "api_key": REDACTED,
        "property_id": PROPERTY_ID,
        "property_name": PROPERTY_NAME,
    }
    assert diag["options"] == {}
    assert diag["coordinator"]["last_update_success"] is True
    assert diag["coordinator"]["update_interval"] == "0:05:00"
    assert diag["coordinator"]["unit_count"] == 3
    assert diag["coordinator"]["units"]["1001"] == {
        "name": "Room 1",
        "status": "free",
        "property_id": PROPERTY_ID,
    }

    dump = json.dumps(diag)
    for name, value in SECRETS.items():
        assert value not in dump, f"{name} leaked into diagnostics"
    assert EMAIL not in dump
