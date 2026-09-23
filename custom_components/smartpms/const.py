"""Constants for the SmartPMS integration."""

DOMAIN = "smartpms"

DEFAULT_SCAN_INTERVAL = 300  # 5 minutes

API_BASE_URL = "https://pms-api.smartness.com/api/public/v2"

# Upper bound for one SmartPMS API request (connect + response). Without it
# aiohttp's 5-minute default applies and a stalled API blocks setup/refresh.
REQUEST_TIMEOUT = 30  # seconds

CONF_API_KEY = "api_key"
CONF_PROPERTY_ID = "property_id"
CONF_PROPERTY_NAME = "property_name"

STATUS_FREE = "free"
STATUS_OCCUPIED = "occupied"
STATUS_BLOCKED = "blocked"
