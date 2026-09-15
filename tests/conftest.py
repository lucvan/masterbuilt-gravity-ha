"""Shared fixtures: a mocked cloud API and a Home Assistant that looks installed."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

MAC = "424824d7eb86a6ee"
DEVICE = {"macAddress": MAC, "model": "C:G:018:1:D", "givenName": "Joe"}

# Reported state lifted from a real Konnected Joe diagnostics dump (issue #2).
REPORTED = {
    "RSSI": -80, "doorOpn": False, "engaged": False, "errors": [0, 0, 0, 0, 0],
    "fah": False, "lidOpn": False, "mainTemp": 19, "pwrOn": False,
    "heat": {"t2": {"heating": False, "intensity": 0, "max": 371, "min": 65, "trgt": -17}},
    "probes": {"p1": {"temp": 21, "trgt": 0}},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_api():
    """The CAS client, returning one Konnected Joe unless a test says otherwise."""
    api = AsyncMock()
    api.async_login.return_value = "token"
    api.async_get_devices.return_value = [DEVICE]
    api.async_get_shadow_document.return_value = {
        "state": {"reported": REPORTED}, "metadata": {}, "timestamp": 1788794808,
    }
    api.async_get_sessions.return_value = []
    with patch(
        "custom_components.masterbuilt_gravity.MasterbuiltApi", return_value=api
    ), patch(
        "custom_components.masterbuilt_gravity.config_flow.MasterbuiltApi", return_value=api
    ):
        yield api


@pytest.fixture
async def real_install(hass: HomeAssistant):
    """Load the entity components the way a real install already has them.

    A fresh test instance has never loaded `number`, and Home Assistant returns
    early when unloading a platform whose component isn't there. That hides the
    "unload the wrong platform set" bug entirely. Every real install has these
    components loaded by something.
    """
    for domain in ("sensor", "binary_sensor", "number"):
        assert await async_setup_component(hass, domain, {})


def assert_no_errors(caplog) -> None:
    errors = [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]
    assert not errors, " | ".join(f"{r.name}: {r.getMessage()}" for r in errors)
