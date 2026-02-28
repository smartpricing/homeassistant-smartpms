# SmartPMS for Home Assistant

Custom integration for [Home Assistant](https://www.home-assistant.io/) that exposes room occupancy status from [SmartPMS](https://pms-api.smartness.com).

## Features

- One sensor per unit/room with state: `free`, `occupied`, `blocked`
- Dynamic icon based on status
- Configurable update interval (default: 5 minutes)
- UI-based configuration (Config Flow) with property selection
- HACS compatible

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations" → menu ⋮ → "Custom repositories"
3. Add this repository URL as type "Integration"
4. Search for "SmartPMS" and install it
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/smartpms/` folder to `/config/custom_components/`
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services → Add Integration
2. Search for "SmartPMS"
3. Enter your credentials:
   - **Email**: SmartPMS account email
   - **Password**: account password
4. If your account has multiple properties, select which one to monitor

## Options

After setup, you can change the update interval:
- Settings → Devices & Services → SmartPMS → Configure
- Minimum: 60 seconds, maximum: 3600 seconds

## Entities

For each unit a sensor `sensor.smartpms_<property_id>_<unit_id>` is created with:
- **State**: `free`, `occupied`, `blocked`
- **Attributes**: `unit_id`, `unit_name`, `property_id`
- **Icon**: changes based on status (`mdi:door-open`, `mdi:bed`, `mdi:wrench`)
