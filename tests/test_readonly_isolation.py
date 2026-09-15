"""Read-only mode never runs the control stack.

Must run in its own pytest process: sys.modules is process-wide, so any earlier
test that sets up a control-enabled entry imports the control module and makes
this check meaningless. CI runs this file separately from the rest.
"""
from __future__ import annotations

import sys

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.masterbuilt_gravity.const import DOMAIN

from .conftest import MAC


async def test_readonly_never_loads_the_control_stack(hass: HomeAssistant, mock_api) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"brand": "kamado_joe", "email": "a@b.c", "password": "pw"},
        options={"devices": [MAC], "control": False},
        version=1, minor_version=2, unique_id="kamado_joe:a@b.c",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "custom_components.masterbuilt_gravity.control" not in sys.modules
    # boto3 and pycognito are not checked: Home Assistant itself imports both,
    # via homeassistant.helpers.aiohttp_client -> hass_nabucasa.auth.cognito.
    # paho is only ever imported by this integration's control path.
    assert "paho" not in sys.modules, "paho was imported in read-only mode"

    registry = er.async_get(hass)
    domains = {e.domain for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert domains == {"binary_sensor", "sensor"}
    assert entry.runtime_data.control is None
    assert await hass.config_entries.async_unload(entry.entry_id)
