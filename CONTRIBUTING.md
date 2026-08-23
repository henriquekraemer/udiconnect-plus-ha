# Contributing

Issues and pull requests are welcome. For anything bigger than a bug fix, open an issue
first so the approach can be discussed before you spend time on it.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt
```

Run the checks before pushing:

```bash
ruff check . && ruff format --check .
pytest
```

## Testing against the cloud

Tests use a fake cloud; nothing talks to the real API. To check behaviour against a real
account, copy `.env.example` to `.env`, fill in your own credentials and use
`scripts/probe_api.py`. `.env` is ignored by git; never commit credentials, tokens or
unredacted payloads (they contain e-mail, coordinates, MAC and Wi-Fi SSID).

Commands in `--set`/`--stop` move real motors. Keep that in mind when sharing logs from
someone else's account.

## Pull requests

- One topic per PR, linked to an issue.
- Add or adjust tests for behaviour changes.
- Update `CHANGELOG.md` under *Unreleased* for anything a user would notice.
- Do not bump the version; that happens in the release commit.
- Keep commit messages in the imperative mood with a short subject line.
- Code style follows Home Assistant core: short docstrings, `ruff` clean, comments only
  where the reason is not obvious.

CI runs hassfest, the HACS validation and the test suite. Workflows from first-time
contributors need approval before they run.

## Reporting API findings

The cloud API is undocumented. If you find a new endpoint, field or device type, open an
issue with the request/response (redacted) and the device model. That is how the
integration grows.
