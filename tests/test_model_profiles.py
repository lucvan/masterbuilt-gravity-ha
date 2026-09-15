"""Model profiles never narrow a grill that hasn't been matched to one."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.masterbuilt_gravity.const import DOMAIN

from .conftest import MAC, assert_no_errors

KJ = "C:G:018:1:D"
GRAVITY_800 = "C:G:P26:1:D"
MAC2 = "4248aabbccddeeff"
KJ_DATA = {"brand": "kamado_joe", "email": "a@b.c", "password": "pw"}
MB_DATA = {"brand": "masterbuilt", "email": "owner@example.com", "password": "pw"}


def _device(mac=MAC, model=KJ, name="Joe"):
    return {"macAddress": mac, "model": model, "givenName": name}


def _entry(data, options):
    return MockConfigEntry(
        domain=DOMAIN, data=data, options=options, version=1, minor_version=2,
        unique_id=data["email"], title="grill",
    )


def _uid_exists(hass, platform, mac, key):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{mac}_{key}") is not None


def _probe_slots(hass, mac):
    """Probe slots with a temperature sensor registered, e.g. [1, 2, 3]."""
    return [n for n in (1, 2, 3, 4) if _uid_exists(hass, "sensor", mac, f"probe{n}_temp")]


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _choose_profile(hass, entry, choice):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "profiles"}
    )
    assert result["step_id"] == "profile"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"profile": choice}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_upgraded_entry_without_profiles_keeps_every_entity(
    hass: HomeAssistant, mock_api
) -> None:
    """A recognised Konnected Joe must NOT be narrowed just because a profile for
    it now exists: no stored choice means Standard."""
    await _setup(hass, entry := _entry(KJ_DATA, {"devices": [MAC], "control": True}))
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]
    assert _uid_exists(hass, "number", MAC, "probe4_target_set")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_automatic_narrows_a_recognised_konnected_joe(hass: HomeAssistant, mock_api) -> None:
    options = {"devices": [MAC], "profiles": {MAC: "auto"}, "control": True}
    await _setup(hass, entry := _entry(KJ_DATA, options))
    assert _probe_slots(hass, MAC) == [1, 2, 3]
    for platform, key in (
        ("sensor", "probe4_target"), ("binary_sensor", "probe4_reached"),
        ("number", "probe4_target_set"), ("binary_sensor", "door_open"),
    ):
        assert not _uid_exists(hass, platform, MAC, key), key
    assert hass.states.get("sensor.joe_fan_speed") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_gravity_800_profile(hass: HomeAssistant, mock_api) -> None:
    mock_api.async_get_devices.return_value = [_device(model=GRAVITY_800, name="Smoker")]
    options = {"devices": [MAC], "profiles": {MAC: "auto"}, "control": True}
    await _setup(hass, entry := _entry(MB_DATA, options))

    # Four ports, confirmed on the controller. Automatic must not hide any --
    # an earlier profile inferred two from cook history and would have.
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]
    assert _uid_exists(hass, "binary_sensor", MAC, "door_open"), "hopper door must stay"
    assert hass.states.get("sensor.smoker_heat_intensity") is not None
    assert hass.states.get("sensor.smoker_fan_speed") is None
    assert _uid_exists(hass, "number", MAC, "probe4_target_set")

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MAC)})
    assert (device.manufacturer, device.model, device.model_id) == (
        "Masterbuilt", "Gravity Series 800", GRAVITY_800
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unrecognised_model_keeps_every_entity(hass: HomeAssistant, mock_api) -> None:
    mock_api.async_get_devices.return_value = [_device(model="C:G:999:9:Z")]
    options = {"devices": [MAC], "profiles": {MAC: "auto"}, "control": True}
    await _setup(hass, entry := _entry(KJ_DATA, options))
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_manual_override_beats_detection(hass: HomeAssistant, mock_api) -> None:
    """Detected as a Konnected Joe, but the owner says Pellet Joe."""
    await _setup(hass, entry := _entry(KJ_DATA, {"devices": [MAC], "profiles": {MAC: "P:G:018:1:D"}}))
    assert _probe_slots(hass, MAC) == [1, 2]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_code_dropped_from_the_table_falls_back_to_standard(
    hass: HomeAssistant, mock_api
) -> None:
    await _setup(hass, entry := _entry(KJ_DATA, {"devices": [MAC], "profiles": {MAC: "X:X:000:0:X"}}))
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_switching_profile_removes_and_restores_probe_entities(
    hass: HomeAssistant, real_install, mock_api, caplog
) -> None:
    await _setup(hass, entry := _entry(KJ_DATA, {"devices": [MAC], "control": True}))
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]

    await _choose_profile(hass, entry, "auto")
    assert _probe_slots(hass, MAC) == [1, 2, 3]
    assert not _uid_exists(hass, "number", MAC, "probe4_target_set")
    assert not _uid_exists(hass, "binary_sensor", MAC, "probe4_reached")
    assert entry.options["control"] is True, "a profile change must not touch other options"

    await _choose_profile(hass, entry, "standard")
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]
    assert _uid_exists(hass, "number", MAC, "probe4_target_set")

    components = hass.data[DATA_INSTANCES]
    assert all(
        entry.entry_id in components[d]._platforms for d in ("sensor", "binary_sensor", "number")
    )
    assert_no_errors(caplog)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert_no_errors(caplog)


async def test_setup_asks_for_a_profile_per_grill(hass: HomeAssistant, mock_api) -> None:
    mock_api.async_get_devices.return_value = [
        _device(MAC, KJ, "Joe"), _device(MAC2, "C:G:999:9:Z", "Mystery"),
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], KJ_DATA)
    assert result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"devices": [MAC, MAC2]}
    )

    assert result["step_id"] == "profile"
    assert result["description_placeholders"]["model"] == KJ
    assert result["description_placeholders"]["detected"] == "Kamado Joe Konnected Joe"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["step_id"] == "profile"
    assert result["description_placeholders"]["detected"] == "not a recognised model"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"profile": "standard"}
    )

    assert result["step_id"] == "control"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["options"]["profiles"] == {MAC: "auto", MAC2: "standard"}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_0_6_era_entry_upgrades_cleanly(
    hass: HomeAssistant, real_install, mock_api, caplog
) -> None:
    """The oldest shape in the wild: data without a brand key, options {} (grills
    discovered rather than listed), config-entry minor version 1."""
    mock_api.async_get_devices.return_value = [_device(model=GRAVITY_800, name="Smoker")]
    entry = MockConfigEntry(
        domain=DOMAIN, data={"email": "owner@example.com", "password": "pw"}, options={},
        version=1, minor_version=1, unique_id="owner@example.com", title="owner@example.com",
    )
    await _setup(hass, entry)

    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, 2)
    assert entry.options == {"control": True}
    assert _probe_slots(hass, MAC) == [1, 2, 3, 4]
    assert _uid_exists(hass, "binary_sensor", MAC, "door_open")
    registry = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len([e for e in registry if e.domain == "number"]) == 5
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MAC)})
    assert (device.manufacturer, device.model) == ("Masterbuilt", "Gravity Series 800")

    # Configure -> Grill models works with no device list stored.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "profiles"}
    )
    assert result["step_id"] == "profile"
    assert result["description_placeholders"]["detected"] == "Masterbuilt Gravity Series 800"
    assert_no_errors(caplog)
    assert await hass.config_entries.async_unload(entry.entry_id)
