"""The Udiconnect Plus integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from uuid import uuid4

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UdiconnectAuthError, UdiconnectClient, UdiconnectError, UdiDevice
from .const import CONF_DEVICE_UUID, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import UdiconnectConfigEntry, UdiconnectCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.COVER, Platform.UPDATE]

# Keys used by v1 config entries (rezendeneto/udiconnect-plus-ha)
LEGACY_CONF_EMAIL = "E-Mail"
LEGACY_CONF_PASSWORD = "Password"  # noqa: S105
LEGACY_UNIQUE_ID_PREFIX = "udiconnect_plus_device_id_"


async def async_setup_entry(hass: HomeAssistant, entry: UdiconnectConfigEntry) -> bool:
    """Set up Udiconnect Plus from a config entry."""
    client = UdiconnectClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DEVICE_UUID],
    )

    try:
        await client.async_login()
    except UdiconnectAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except UdiconnectError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = UdiconnectCoordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()
    _async_migrate_device_ids(hass, entry, coordinator.data)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


@callback
def _async_migrate_device_ids(
    hass: HomeAssistant, entry: UdiconnectConfigEntry, devices: dict[str, UdiDevice]
) -> None:
    """Move registry entries from the cloud deviceId to the device unique id.

    Up to 1.0.2 the deviceId was the entity unique id and the device identifier.
    This needs the device list, so it runs here and not in async_migrate_entry.
    """
    unique_ids = {
        device.device_id: unique_id
        for unique_id, device in devices.items()
        if unique_id != device.device_id
    }
    if not unique_ids:
        return

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        old = entity_entry.unique_id
        for device_id, unique_id in unique_ids.items():
            if old != device_id and not old.startswith(f"{device_id}_"):
                continue
            new = unique_id + old[len(device_id) :]
            if not entity_registry.async_get_entity_id(
                entity_entry.domain, DOMAIN, new
            ):
                entity_registry.async_update_entity(
                    entity_entry.entity_id, new_unique_id=new
                )
            break

    device_registry = dr.async_get(hass)
    taken = {
        identifier
        for device_entry in device_registry.devices.values()
        for identifier in device_entry.identifiers
    }
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        identifiers = {
            (domain, unique_ids.get(value, value) if domain == DOMAIN else value)
            for domain, value in device_entry.identifiers
        }
        added = identifiers - device_entry.identifiers
        if added and not added & taken:
            device_registry.async_update_device(
                device_entry.id, new_identifiers=identifiers
            )
            taken |= added


async def _async_update_listener(
    hass: HomeAssistant, entry: UdiconnectConfigEntry
) -> None:
    """Handle options update."""
    coordinator = entry.runtime_data
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    if coordinator.update_interval != timedelta(seconds=scan_interval):
        coordinator.update_interval = timedelta(seconds=scan_interval)
        await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: UdiconnectConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: UdiconnectConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device that the cloud no longer reports."""
    return not any(
        identifier[0] == DOMAIN and identifier[1] in entry.runtime_data.data
        for identifier in device_entry.identifiers
    )


async def async_migrate_entry(
    hass: HomeAssistant, entry: UdiconnectConfigEntry
) -> bool:
    """Migrate old config entries."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version > 3:
        return False

    if entry.version == 1:
        data = dict(entry.data)
        email = data.pop(LEGACY_CONF_EMAIL, data.get(CONF_EMAIL))
        password = data.pop(LEGACY_CONF_PASSWORD, data.get(CONF_PASSWORD))
        if not email or not password:
            _LOGGER.error("Cannot migrate entry %s: no credentials", entry.entry_id)
            return False
        data[CONF_EMAIL] = email
        data[CONF_PASSWORD] = password
        data.setdefault(CONF_DEVICE_UUID, str(uuid4()))

        @callback
        def _migrate_unique_id(entity_entry: er.RegistryEntry) -> dict[str, str] | None:
            if entity_entry.unique_id.startswith(LEGACY_UNIQUE_ID_PREFIX):
                return {
                    "new_unique_id": entity_entry.unique_id.removeprefix(
                        LEGACY_UNIQUE_ID_PREFIX
                    )
                }
            return None

        await er.async_migrate_entries(hass, entry.entry_id, _migrate_unique_id)

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            unique_id=entry.unique_id or email.strip().lower(),
            version=2,
        )
        _LOGGER.info("Migrated config entry %s to version 2", entry.entry_id)

    if entry.version == 2:
        # Version 3 keys devices and entities by deviceUS. The registries are
        # rewritten in async_setup_entry; the bump keeps older releases from
        # loading the entry and creating a second set of entities.
        hass.config_entries.async_update_entry(entry, version=3)
        _LOGGER.info("Migrated config entry %s to version 3", entry.entry_id)

    return True
