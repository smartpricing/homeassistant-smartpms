# SmartPMS for Home Assistant

[![HACS Validation](https://github.com/smartpricing/homeassistant-smartpms/actions/workflows/hacs.yaml/badge.svg)](https://github.com/smartpricing/homeassistant-smartpms/actions/workflows/hacs.yaml)
[![Hassfest Validation](https://github.com/smartpricing/homeassistant-smartpms/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/smartpricing/homeassistant-smartpms/actions/workflows/hassfest.yaml)
[![License: MIT](https://img.shields.io/github/license/smartpricing/homeassistant-smartpms)](LICENSE)

Custom integration for [Home Assistant](https://www.home-assistant.io/) that exposes room occupancy status from [SmartPMS](https://smartpricing.it/) (by Smartness).

## Prerequisites

- A SmartPMS account with API access
- An **API key** provided by Smartness for the public v2 API
- At least one property configured in your SmartPMS account

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → three-dot menu → **Custom repositories**
3. Add `https://github.com/smartpricing/homeassistant-smartpms` as type **Integration**
4. Search for **SmartPMS** and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/smartpms/` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **SmartPMS**
3. Enter your credentials:
   - **Email** – your SmartPMS account email
   - **Password** – account password
   - **API Key** – the v2 API key from Smartness
4. Select the property to monitor (if your account has more than one)
5. Give the property a friendly name

## Options

After setup, go to **Settings → Devices & Services → SmartPMS → Configure** to change:

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Update interval | 300 s | 60–3600 s | How often unit status is polled |

## Entities

For each unit in the selected property a sensor is created:

| Attribute | Value |
|-----------|-------|
| Entity ID | `sensor.smartpms_<property_id>_<unit_id>` |
| State | `free`, `occupied`, or `blocked` |
| Icon | `mdi:door-open` / `mdi:bed` / `mdi:wrench` |
| Extra attributes | `unit_id`, `unit_name`, `property_id` |

All sensors are grouped under a single **device** named after your property.

## Diagnostics

Download integration diagnostics from **Settings → Devices & Services → SmartPMS → three-dot menu → Download diagnostics**. Sensitive fields (email, password, API key) are automatically redacted.

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `auth_failed` | Invalid email, password, or API key | Double-check credentials; ensure the API key matches the account |
| `cannot_connect` | Network issue or API downtime | Verify internet connectivity; retry later |
| `no_properties` | Account has no properties | Ensure at least one property is configured in SmartPMS |
| Reauth notification | Token expired or credentials changed | Click the notification and re-enter valid credentials |

## License

[MIT](LICENSE) – Copyright Smartpricing
