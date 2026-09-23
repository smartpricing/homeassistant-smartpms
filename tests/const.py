"""Synthetic values shared by the tests.

Nothing in this file is a real credential, account, property or unit. The
"SENTINEL" markers make any accidental leak of these values into logs,
exception messages, diagnostics or UI forms trivially detectable.
"""

API_BASE = "https://pms-api.smartness.com/api/public/v2"
LOGIN_URL = f"{API_BASE}/login"
PROPERTIES_URL = f"{API_BASE}/automations/properties"
UNITS_URL = f"{API_BASE}/automations/units"

EMAIL = "ha-test-user@example.invalid"
PASSWORD = "PW-SENTINEL-fake-password"
API_KEY = "APIKEY-SENTINEL-fake-key"
ACCESS_TOKEN = "ACCESS-SENTINEL-fake-token"
REFRESH_TOKEN = "REFRESH-SENTINEL-fake-token"

# Values that must never be written to logs, exception messages or diagnostics.
SECRETS = {
    "password": PASSWORD,
    "api_key": API_KEY,
    "access_token": ACCESS_TOKEN,
    "refresh_token": REFRESH_TOKEN,
}

PROPERTY_ID = 101
PROPERTY_NAME = "Test Hotel Alpha"
OTHER_PROPERTY_ID = 202

UPSTREAM_BODY_SENTINEL = "UPSTREAM-BODY-SENTINEL"
