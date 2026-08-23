"""Every translation file must carry the same keys as en.json."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

TRANSLATIONS = (
    pathlib.Path(__file__).parent.parent
    / "custom_components"
    / "udiconnect_plus"
    / "translations"
)


def _keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        if isinstance(value, dict):
            keys |= _keys(value, f"{prefix}{key}.")
        else:
            keys.add(f"{prefix}{key}")
    return keys


@pytest.mark.parametrize(
    "path",
    sorted(p for p in TRANSLATIONS.glob("*.json") if p.name != "en.json"),
    ids=lambda p: p.stem,
)
def test_translation_keys_match_english(path: pathlib.Path) -> None:
    english = _keys(json.loads((TRANSLATIONS / "en.json").read_text()))
    other = _keys(json.loads(path.read_text()))
    assert english - other == set(), f"{path.name} is missing keys"
    assert other - english == set(), f"{path.name} has keys not in en.json"


def test_placeholders_match_english() -> None:
    def placeholders(data: dict, prefix: str = "") -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                out |= placeholders(value, f"{prefix}{key}.")
            else:
                out[f"{prefix}{key}"] = set(re.findall(r"\{(\w+)\}", value))
        return out

    english = placeholders(json.loads((TRANSLATIONS / "en.json").read_text()))
    for path in TRANSLATIONS.glob("*.json"):
        other = placeholders(json.loads(path.read_text()))
        for key, expected in english.items():
            assert other.get(key, set()) == expected, (
                f"{path.name}: placeholders differ in {key}"
            )
