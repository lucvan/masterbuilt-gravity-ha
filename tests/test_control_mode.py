"""Setup-flow default, migration, and toggling control in both directions."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.masterbuilt_gravity.const import DOMAIN, iot_store_key

from .conftest import MAC, assert_no_errors

DATA = {"brand": "kamado_joe", "email": "a@b.c", "password": "pw"}
FAKE_CERT = {"cert": "C", "key": "K", "device_id": "ha-x", "root_ca": "R"}
SETTINGS = {"scan_interval": 30, "stale_after": 300, "track_history": True}


def _entry(options, minor=2):
    return MockConfigEntry(
        domain=DOMAIN, data=DATA, options=options, version=1, minor_version=minor,
        unique_id="kamado_joe:a@b.c", title="Kamado Joe (a@b.c)",
    )


def _numbers(hass, entry):
    registry = er.async_get(hass)
    return [
        e for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "number"
    ]


def _loaded_platforms(hass, entry):
    """What is actually attached, from each EntityComponent's own record.

    Not async_get_platforms(): unload resets platforms without destroying them,
    so that list only ever grows across reloads.
    """
    components = hass.data.get(DATA_INSTANCES, {})
    return sorted(
        d for d in ("binary_sensor", "number", "sensor")
        if d in components and entry.entry_id in components[d]._platforms
    )


def _seed_cert(hass_storage, entry):
    key = iot_store_key(entry.entry_id)
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": FAKE_CERT}
    return key


async def _set_control(hass, entry, value):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SETTINGS, "control": value}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # the update listener reloads the entry
    assert entry.state is config_entries.ConfigEntryState.LOADED, entry.state


async def _sign_in(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], DATA)


async def test_new_install_defaults_to_readonly(hass: HomeAssistant, mock_api) -> None:
    result = await _sign_in(hass)
    # One paired grill skips the device picker and lands on its profile step.
    assert result["step_id"] == "profile"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "control"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {"devices": [MAC], "profiles": {MAC: "auto"}, "control": False}
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.minor_version == 2
    assert _numbers(hass, entry) == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_new_install_can_opt_in(hass: HomeAssistant, mock_api) -> None:
    result = await _sign_in(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})  # Automatic
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"control": True})
    assert result["options"] == {"devices": [MAC], "profiles": {MAC: "auto"}, "control": True}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert len(_numbers(hass, entry)) == 4  # grill + 3 probe targets: Konnected Joe profile
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_existing_entry_migrates_with_control_on(hass: HomeAssistant, mock_api) -> None:
    """A pre-1.2 entry has no control key and must keep its working controls."""
    entry = _entry({"devices": [MAC]}, minor=1)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert entry.options == {"devices": [MAC], "control": True}
    assert len(_numbers(hass, entry)) == 5
    assert entry.runtime_data.control is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_turning_control_off_removes_everything_that_could_write(
    hass: HomeAssistant, real_install, mock_api, hass_storage, caplog
) -> None:
    entry = _entry({"devices": [MAC], "control": True})
    entry.add_to_hass(hass)
    key = _seed_cert(hass_storage, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _loaded_platforms(hass, entry) == ["binary_sensor", "number", "sensor"]

    await _set_control(hass, entry, False)

    assert entry.runtime_data.control is None
    assert _numbers(hass, entry) == []
    assert key not in hass_storage, "control certificate survived read-only"
    assert hass.states.get("number.joe_grill_target") is None
    assert _loaded_platforms(hass, entry) == ["binary_sensor", "sensor"]
    assert_no_errors(caplog)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _loaded_platforms(hass, entry) == []
    assert_no_errors(caplog)


async def test_turning_control_back_on_restores_controls(
    hass: HomeAssistant, real_install, mock_api, caplog
) -> None:
    entry = _entry({"devices": [MAC], "control": False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _loaded_platforms(hass, entry) == ["binary_sensor", "sensor"]

    await _set_control(hass, entry, True)

    assert entry.runtime_data.control is not None
    assert len(_numbers(hass, entry)) == 5
    assert _loaded_platforms(hass, entry) == ["binary_sensor", "number", "sensor"]
    assert_no_errors(caplog)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _loaded_platforms(hass, entry) == []
    assert_no_errors(caplog)


async def test_toggling_repeatedly_stays_consistent(
    hass: HomeAssistant, real_install, mock_api, caplog
) -> None:
    """on -> off -> on -> off. A stale platform from one toggle breaks the next."""
    entry = _entry({"devices": [MAC], "control": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for value, expected in ((False, 0), (True, 5), (False, 0)):
        await _set_control(hass, entry, value)
        assert len(_numbers(hass, entry)) == expected
        want = ["binary_sensor", "number", "sensor"] if value else ["binary_sensor", "sensor"]
        assert _loaded_platforms(hass, entry) == want
        assert_no_errors(caplog)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _loaded_platforms(hass, entry) == []
    assert_no_errors(caplog)


async def test_removing_the_entry_deletes_the_certificate(
    hass: HomeAssistant, mock_api, hass_storage
) -> None:
    entry = _entry({"devices": [MAC], "control": True})
    entry.add_to_hass(hass)
    key = _seed_cert(hass_storage, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
