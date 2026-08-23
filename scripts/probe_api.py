"""Log in to the Udiconnect Plus cloud and dump the account payload.

Useful for bug reports and for checking the integration against a real
account without running Home Assistant. Personal data is redacted.

    cp .env.example .env        # fill in UDI_EMAIL / UDI_PASSWORD
    python3 scripts/probe_api.py
    python3 scripts/probe_api.py --raw payload.json
    python3 scripts/probe_api.py --set 64207 50     # moves the curtain!
    python3 scripts/probe_api.py --stop 64207

Environment variables override the .env file. Requires aiohttp only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from typing import Any
import uuid

import aiohttp

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.udiconnect_plus.api import (  # noqa: E402
    UdiconnectClient,
    parse_devices,
)

ENV_FILE = ROOT / ".env"
REDACT_KEYS = {
    "email",
    "accesstoken",
    "access-token",
    "accesstokens",
    "token",
    "password",
    "firstname",
    "lastname",
    "fullname",
    "name",
    "phone",
    "phonenumber",
    "cellphone",
    "cpf",
    "document",
    "address",
    "latitude",
    "longitude",
    "pushtoken",
    "ssid",
    "physicaladdress",
    "thirdpartydata",
}


def load_credentials() -> tuple[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    email = os.environ.get("UDI_EMAIL") or values.get("UDI_EMAIL")
    password = os.environ.get("UDI_PASSWORD") or values.get("UDI_PASSWORD")
    if not email or not password:
        sys.exit(f"UDI_EMAIL and UDI_PASSWORD not set (environment or {ENV_FILE})")
    return email, password


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "**REDACTED**" if key.lower() in REDACT_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def dump(payload: Any) -> None:
    if not isinstance(payload, dict):
        print(f"unexpected payload type: {type(payload).__name__}")
        return
    print("top-level keys:", sorted(payload))
    account = payload.get("account")
    if not isinstance(account, dict):
        print("no 'account' object")
        return
    print("account keys:", sorted(account))

    homes = account.get("homeList") or []
    print(f"{len(homes)} home(s)")
    for home in homes:
        summary = {k: v for k, v in home.items() if k != "deviceList"}
        print(f"\nhome {home.get('homeId')!r}:")
        print(json.dumps(redact(summary), ensure_ascii=False, indent=2))
        for item in home.get("deviceList") or []:
            print(f"\ndevice {item.get('deviceId')!r}:")
            print(json.dumps(redact(item), ensure_ascii=False, indent=2))

    print("\nparsed:")
    for device in parse_devices(payload).values():
        print(
            f"  {device.device_id}: {device.name!r} "
            f"controller_type={device.controller_type!r} category={device.category!r} "
            f"model={device.model!r} position={device.position} "
            f"online={device.is_online} calibrating={device.is_calibrating} "
            f"low_battery={device.low_battery} fw={device.firmware_version!r} "
            f"cover={device.is_cover}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--raw", metavar="FILE", help="save the redacted payload as JSON"
    )
    parser.add_argument(
        "--set", nargs=2, metavar=("DEVICE_ID", "POSITION"), help="move a curtain"
    )
    parser.add_argument("--stop", metavar="DEVICE_ID", help="stop a curtain")
    parser.add_argument(
        "--uuid", default=str(uuid.uuid4()), help="device UUID sent on login"
    )
    args = parser.parse_args()

    email, password = load_credentials()
    async with aiohttp.ClientSession() as session:
        client = UdiconnectClient(session, email, password, args.uuid)
        await client.async_login()
        print("login ok")

        if args.set:
            device_id, position = args.set[0], int(args.set[1])
            await client.async_set_position(device_id, position)
            print(f"device {device_id} -> position {position}")
        if args.stop:
            await client.async_stop(args.stop)
            print(f"device {args.stop} stopped")

        payload = await client.async_sync_account()
        dump(payload)
        if args.raw:
            pathlib.Path(args.raw).write_text(
                json.dumps(redact(payload), ensure_ascii=False, indent=2)
            )
            print(f"\nsaved to {args.raw}")


if __name__ == "__main__":
    asyncio.run(main())
