# Udiconnect Plus for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/henriquekraemer/udiconnect-plus-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/henriquekraemer/udiconnect-plus-ha/actions/workflows/validate.yml)

Unofficial Home Assistant integration for Udinese blind and curtain motors that use the
**Udiconnect Plus** app. The motors only officially integrate with Alexa and Google Home;
this integration talks to the same cloud API the app uses.

## Features

- Discovers every blind/curtain on the account, across all homes
- `cover` entity with open, close, stop and position (0-100%), showing opening/closing
  while the motor moves
- Diagnostic entities per device: connectivity, low battery and a firmware `update` entity
- UI configuration with reauth and reconfigure; polling interval in the options
- Automatic re-login when the token expires; tolerant of the cloud's 503s, timeouts and
  incomplete responses
- Diagnostics download with the redacted raw payload, for bug reports
- Migrates config entries and entities from
  [rezendeneto/udiconnect-plus-ha](https://github.com/rezendeneto/udiconnect-plus-ha)

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Repository `https://github.com/henriquekraemer/udiconnect-plus-ha`, category **Integration**
3. Search for **Udiconnect Plus**, download it and restart Home Assistant

### Manual

Copy `custom_components/udiconnect_plus` into `<config>/custom_components/` and restart.

## Configuration

Settings → Devices & services → Add integration → **Udiconnect Plus**, then enter the
e-mail and password of your Udiconnect Plus account.

Each motor becomes a device with:

| Entity | Description |
|---|---|
| `cover.<name>` | Position control (0 = closed, 100 = open), open/close/stop; device class follows the cloud `category` (blind, curtain, shutter) |
| `binary_sensor.<name>_connectivity` | Device online |
| `binary_sensor.<name>_low_battery` | `lowBattery` flag from the cloud |
| `update.<name>_firmware` | Installed vs. latest firmware (read-only; update from the app) |

Cover attributes: `device_id`, `home_id`, `home_name`, `category`, `is_calibrating`.
Devices whose `controllerType` is not `Curtain` (locks, for example) only get the
diagnostic entities.

### Options

**Polling interval** (default 60 s, minimum 10 s). After a command the integration polls
every 5 s on its own until the motor reaches the target position, so a longer interval
only delays changes made from the app, Alexa or a remote.

To change the password without waiting for a reauthentication prompt, use **Reconfigure**
on the integration entry. Devices that disappear from the account can be deleted from the
device page.

### Coming from the original integration

Remove the old integration in HACS, install this one and restart. The config entry and
the `cover.*` entities are migrated in place, keeping entity ids and history.

## Troubleshooting

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.udiconnect_plus: debug
```

Download diagnostics (Settings → Devices & services → Udiconnect Plus → three-dot menu →
Download diagnostics) and attach it to the issue. It contains the raw item for each device
with credentials, tokens, MAC, SSID and coordinates redacted.

Entities show up as *unavailable* when the cloud reports the device offline or the last
poll failed. The cloud has periodic hiccups (503, connection reset, timeouts); the
integration recovers on the next successful poll.

## API notes

The cloud is the Yale Connect platform (ASSA ABLOY) with the Udinese brand tenant.
Everything below comes from observing the Android app.

- Base URL: `https://AA-Brands.yaleconnect-services.com/api/YaleConnect`
- `POST /Account/Login` → `accessToken`, sent back as the `access-token` header
- `POST /App/SyncAccount` → `status.code`, `account.maintenanceMode`,
  `account.homeList[]` (`homeId`, `description`) → `deviceList[]` with `deviceId` (int),
  `description`, `controllerType` (`Curtain`), `category` (`Blind`),
  `deviceModelDescription` (e.g. `UDIN-4205`), `currentFirmwareVersion`,
  `lastestFirmwareVersion` (sic), `state` (position 0-100), `isOnline`, `isCalibrating`,
  `lowBattery`, `newFirmwareAvailable`, `deviceParameters.physicalAddress` (MAC)
- `POST /Curtain/SetPositionCurtain` handles every command, selected by `action`:
  - `{"action": "CurtainSetPosition", "deviceId": 123, "position": 50}`; always send
    `position`; when it is missing the cloud uses 0
  - `{"action": "CurtainStop", "deviceId": 123}`
  - response: `{"result": true|false}`

Observed on a UDIN-4205: commands are acknowledged in about a second; `state` keeps the
old value until the motor arrives (full travel ~19 s) and then jumps to the new position,
with no intermediate values. After a stop the resting position shows up within ~2 s.

### Probing the API

`scripts/probe_api.py` logs in and prints the (redacted) `SyncAccount` payload without
Home Assistant:

```bash
cp .env.example .env   # fill in UDI_EMAIL and UDI_PASSWORD
pip install aiohttp
python3 scripts/probe_api.py
python3 scripts/probe_api.py --raw payload.json
```

`--set DEVICE_ID POSITION` and `--stop DEVICE_ID` send commands to a real motor.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt
ruff check . && ruff format --check .
pytest
```

Tests run against a fake cloud (`tests/conftest.py`) built on
`pytest-homeassistant-custom-component`.

## Credits and disclaimer

Written from scratch. The endpoints and payload fields were first documented by
[rezendeneto/udiconnect-plus-ha](https://github.com/rezendeneto/udiconnect-plus-ha) and its
contributors.

Not affiliated with Udinese, ASSA ABLOY or Yale. Provided as is, for personal use; using
it may be against the manufacturer's terms of service. [MIT license](LICENSE).
