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
- New user-facing strings go into every file in `translations/` (`en`, `pt-BR`, `pt`,
  `es`). If you do not speak a language, copy the English text; a test checks that all
  files have the same keys.
- Code style follows Home Assistant core: short docstrings, `ruff` clean, comments only
  where the reason is not obvious.

CI runs hassfest, the HACS validation and the test suite. Workflows on pull requests
from forks only run after a maintainer approves them, every time. Merging needs a
passing CI and a maintainer review.

## Reporting API findings

The cloud API is undocumented. If you find a new endpoint, field or device type, open an
issue with the request/response (redacted) and the device model. That is how the
integration grows.

## Maintainer notes

Pull requests are merged locally, not with the GitHub merge button, so the commit
identity stays the one configured in this clone:

```bash
git fetch origin
git checkout main && git pull --ff-only
git merge --squash origin/<branch>
git commit            # subject: PR title (#<number>); body: PR description
git push origin main  # admin bypass of the main ruleset
git push origin --delete <branch>
```

GitHub closes the pull request when its commits reach `main`. The ruleset on `main`
still applies to everyone else.
