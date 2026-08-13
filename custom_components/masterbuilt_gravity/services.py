"""On-demand cook-history service.

The cloud keeps every cook at roughly 10-second resolution and returns the whole
thing inline — an overnight cook is a few thousand snapshots and megabytes of
JSON. That is far too much to poll or to hold in entity state, but it is exactly
what you want when exporting a cook or drawing a finished chart.

So it is a service with a response instead of an entity: nothing is fetched
until asked for, and the result never touches the state machine or Recorder.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .history import EXTRACTORS

_LOGGER = logging.getLogger(__name__)

SERVICE_GET_COOK_HISTORY = "get_cook_history"

ATTR_DEVICE_ID = "device_id"
ATTR_SESSION_ID = "session_id"
ATTR_MAX_POINTS = "max_points"

SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_SESSION_ID): vol.Any(cv.string, int),
        vol.Optional(ATTR_MAX_POINTS, default=300): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=5000)
        ),
    }
)


def _decimate(points: list[list[float]], limit: int) -> list[list[float]]:
    """Evenly thin a series to at most ``limit`` points, keeping the endpoints."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[int(i * step)] for i in range(limit)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def _resolve(hass: HomeAssistant, device_id: str) -> tuple[Any, str]:
    """Map a device registry id to its coordinator and MAC."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device {device_id}")

    mac = next(
        (ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None
    )
    if mac is None:
        raise ServiceValidationError(f"Device {device_id} is not a Masterbuilt grill")

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
            return entry.runtime_data, mac

    raise ServiceValidationError(f"No loaded config entry for device {device_id}")


async def _async_get_cook_history(call: ServiceCall) -> ServiceResponse:
    coordinator, mac = _resolve(call.hass, call.data[ATTR_DEVICE_ID])
    api = coordinator.api
    session_id = call.data.get(ATTR_SESSION_ID)
    limit = call.data[ATTR_MAX_POINTS]

    if session_id is None:
        sessions = await api.async_get_sessions(mac)
        if not sessions:
            return {"session": None, "series": {}}
        session_id = sessions[0]["id"]

    session = await api.async_get_session(mac, session_id)
    snapshots = session.get("snapshots") or []

    # The API returns snapshots newest-first; charts want oldest-first.
    snapshots = sorted(snapshots, key=lambda s: s.get("timestamp") or 0)

    start = session.get("start") or (
        snapshots[0].get("timestamp") if snapshots else 0
    )
    unit = None
    series: dict[str, list[list[float]]] = {name: [] for name in EXTRACTORS}

    for snap in snapshots:
        shadow = snap.get("shadow") or {}
        if unit is None and "fah" in shadow:
            unit = "°F" if shadow.get("fah") else "°C"
        offset = (snap.get("timestamp") or start) - start
        for name, extract in EXTRACTORS.items():
            value = extract(shadow)
            if value is None:
                continue
            try:
                series[name].append([offset, round(float(value), 1)])
            except (TypeError, ValueError):
                continue

    return {
        "session": {
            "id": session.get("id"),
            "state": session.get("state"),
            "start": session.get("start"),
            "end": session.get("end"),
            "snapshot_count": session.get("snapshotCount"),
        },
        "unit": unit,
        "series": {k: _decimate(v, limit) for k, v in series.items() if v},
    }


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_COOK_HISTORY):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_COOK_HISTORY,
        _async_get_cook_history,
        schema=SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
