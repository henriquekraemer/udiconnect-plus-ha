# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/henriquekraemer/udiconnect-plus-ha/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/henriquekraemer/udiconnect-plus-ha/releases/tag/v1.0.0
