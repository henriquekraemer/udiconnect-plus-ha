# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- A curtain whose limits were never set showed as fully open. The cloud reports such a
  curtain with state `255`, which the app shows as "configure"; the cover is now
  unavailable until the limits are set in the app.
- Devices with category `Curtain` or `Blind` are always given a cover entity, as in the
  app, whatever their `controllerType`.
- The position falls back to `deviceParameters.state`, the field the app reads, when the
  top-level `state` is missing.

### Changed

- Talk to the cloud at `api.connectservices.assaabloy.com`, the server the current app
  uses. The previous server, `AA-Brands.yaleconnect-services.com`, stopped working.
- Log in as app version 5.11.11, the current Udiconnect Plus release.

## [1.0.4] - 2026-10-23

### Changed

- The integration is in the HACS default store; the README no longer asks to add it as a
  custom repository.

## [1.0.3] - 2026-10-09

### Fixed

- Every entity showing up twice, with the original set left unavailable, after the
  cloud assigned a new `deviceId` to a device. Devices and entities are now identified
  by `deviceUS`, which does not change, and the existing ones are migrated on the first
  start. Duplicates created before this update are not merged: delete the unavailable
  entities and rename the remaining ones if automations or dashboards use the original
  entity ids. ([#12](https://github.com/henriquekraemer/udiconnect-plus-ha/issues/12))

### Changed

- Config entries move to version 3. Going back to 1.0.2 or older requires removing the
  integration and adding it again.

## [1.0.2] - 2026-08-23

### Added

- Reconfigure flow to update the account password from the integration entry.
- Devices no longer reported by the cloud can be deleted from the device page.
- Cover device class follows the cloud `category` (blind, curtain, shutter, shade, awning).
- Brand icon bundled with the integration.
- Field descriptions in the configuration dialogs.
- Portuguese (Portugal) and Spanish translations.

### Changed

- Default polling interval is now 60 s (minimum 10 s). Existing installations keep the
  interval saved in their options.

## [1.0.1] - 2026-08-22

### Fixed

- Cover commands failing with `CurtainSetPosition refused ... no message` when the cloud
  answers `result: false`. The command is now retried once after 1.5 s, and the error
  message carries the full response body. ([#1](https://github.com/henriquekraemer/udiconnect-plus-ha/issues/1))
- Account e-mail no longer appears in debug logs through the coordinator name.

## [1.0.0] - 2026-08-22

### Added

- Cover entity for Udiconnect Plus blinds and curtains with open, close, stop and
  position, showing opening/closing while the motor moves.
- Connectivity and low battery binary sensors and a firmware update entity per device.
- Config flow with reauth and a polling interval option.
- Automatic re-login when the access token expires.
- Diagnostics with the redacted raw `SyncAccount` payload.
- English and Brazilian Portuguese translations.
- Migration of config entries and entity ids from `rezendeneto/udiconnect-plus-ha`.
- `scripts/probe_api.py` for inspecting an account without Home Assistant.

[Unreleased]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.4...HEAD
[1.0.4]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/henriquekraemer/udiconnect-plus-ha/releases/tag/v1.0.0
