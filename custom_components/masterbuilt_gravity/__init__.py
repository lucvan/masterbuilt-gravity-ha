"""The Masterbuilt Gravity integration."""
from __future__ import annotations

import importlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import MasterbuiltApi
from .const import (
    CONF_BRAND,
    CONF_CONTROL,
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_BRAND,
    IOT_STORAGE_VERSION,
    iot_store_key,
)
from .coordinator import MasterbuiltCoordinator
from .services import async_register_services

READ_PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONTROL_PLATFORMS = [*READ_PLATFORMS, Platform.NUMBER]

type MasterbuiltConfigEntry = ConfigEntry[MasterbuiltCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MasterbuiltConfigEntry) -> bool:
    """Set up Masterbuilt Gravity from a config entry.

    Read-only is an architectural mode, not a hidden switch. Unless control is
    enabled the control module is never imported, so no Cognito sign-in, no
    certificate provisioning and no AWS IoT connection can happen, paho is never
    loaded, and the number platform is never set up. Setpoint entities and any
    stored certificate left by an earlier control-enabled run are removed, so
    turning control off leaves nothing behind that could write.

    The boundary is which code runs, not which libraries are in memory: boto3
    and pycognito are loaded regardless, because Home Assistant's own cloud
    client (hass-nabucasa) imports both.
    """
    session = async_get_clientsession(hass)
    api = MasterbuiltApi(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_BRAND, DEFAULT_BRAND),
    )
    control = None
    if entry.options.get(CONF_CONTROL, False):
        # In the executor: importing a module on the event loop trips Home
        # Assistant's blocking-import detection.
        module = await hass.async_add_executor_job(
            importlib.import_module, f"{__package__}.control"
        )
        control = module.MasterbuiltControl(hass, entry.entry_id)
    else:
        await _async_remove_control_artifacts(hass, entry)

    coordinator = MasterbuiltCoordinator(hass, entry, api, control)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, _platforms(coordinator))
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MasterbuiltConfigEntry) -> bool:
    """Unload a config entry.

    The platform set comes from what setup actually loaded, not from the current
    options. Options are applied before the reload unloads, so reading the flag
    here would unload the wrong platforms whenever control had just changed.
    """
    return await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry.runtime_data)
    )


async def async_migrate_entry(hass: HomeAssistant, entry: MasterbuiltConfigEntry) -> bool:
    """Carry an entry forward to the current config-entry version."""
    if entry.version > 1:
        # Written by a newer major version; refuse rather than guess.
        return False
    if entry.minor_version < 2:
        # 1.2 introduced read-only mode, off by default for new installs. Every
        # older entry was created while setpoint controls shipped unconditionally,
        # so it migrates with control ON. Silently removing working controls on
        # upgrade is no way to introduce a safer default; Configure turns it off.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CONTROL: True}, minor_version=2
        )
    return True


async def async_remove_entry(hass: HomeAssistant, entry: MasterbuiltConfigEntry) -> None:
    """Delete the stored control certificate along with the entry."""
    await Store(hass, IOT_STORAGE_VERSION, iot_store_key(entry.entry_id)).async_remove()


def _platforms(coordinator: MasterbuiltCoordinator) -> list[Platform]:
    return CONTROL_PLATFORMS if coordinator.control is not None else READ_PLATFORMS


async def _async_remove_control_artifacts(
    hass: HomeAssistant, entry: MasterbuiltConfigEntry
) -> None:
    """Remove whatever an earlier control-enabled run left able to write."""
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == Platform.NUMBER:
            registry.async_remove(reg_entry.entity_id)
    await Store(hass, IOT_STORAGE_VERSION, iot_store_key(entry.entry_id)).async_remove()


async def _async_reload_entry(hass: HomeAssistant, entry: MasterbuiltConfigEntry) -> None:
    """Reload when options change (polling, staleness, history, control)."""
    await hass.config_entries.async_reload(entry.entry_id)
