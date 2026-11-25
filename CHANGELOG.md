# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Experimental control over the local network, as an option (**Control mode**, default
  *Cloud only*, so nothing changes unless it is enabled). The motors announce their
  position and acknowledge commands over UDP; with a local mode, *stop* and *move to
  position* go straight to the motor, and a command counts only when the motor
  acknowledges it. *Local network, cloud if the motor does not confirm* sends the command
  through the cloud when it does not; *Local network only* reports an error instead. The
  motor's own position replaces the cloud copy, and the motors keep working if the cloud
  goes down while Home Assistant is running.
  Tested on the UDIN-4208 (firmware 1.2.4.0) only.

### Fixed

- Deprecation warning on Home Assistant 2026.6+ ("has an update listener and should use
  it for scheduling a reload"), logged when re-authenticating or reconfiguring the
  account. Home Assistant announces that the old call stops working in 2026.12; the
  integration now saves the credentials and reloads the entry itself, which behaves the
  same on every supported Home Assistant version.
- Deprecation warning on Home Assistant 2026.9+ ("uses `device_registry.devices` as a
  mapping"), raised while migrating device identifiers. The integration no longer walks
  the device registry to find taken identifiers: Home Assistant rejects a colliding
  update and the device keeps its old identifier. The old access stops working in 2027.9.

## [1.0.5] - 2026-11-12

### Fixed

- Every device showing as offline and every cover command being refused since
  2026-11-11. The cloud API moved to `https://api.connectservices.assaabloy.com`; the old
  host still answers but serves a frozen copy of the account. The official app is
  redirected by a remote flag, the integration had the old host hardcoded.
  ([#18](https://github.com/henriquekraemer/udiconnect-plus-ha/issues/18))

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

[Unreleased]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.5...HEAD
[1.0.5]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/henriquekraemer/udiconnect-plus-ha/releases/tag/v1.0.0
