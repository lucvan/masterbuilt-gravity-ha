"""Cook-history action: local Recorder first, cloud as the archive.

Recorder already holds every reading for cooks Home Assistant was around for,
at whatever resolution it polled. Masterbuilt's cloud holds every cook ever, at
~10 s, but a completed cook is thousands of snapshots and megabytes of JSON.

So this reads locally when local data actually covers the cook, and only falls
back to the cloud when it does not — a cook that predates the integration, one
that happened while HA was down, or one Recorder has since purged.

Returned as an action response: nothing is written to entity state, and nothing
lands in the database.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .history import decimate, series_from_snapshots

_LOGGER = logging.getLogger(__name__)

SERVICE_GET_COOK_HISTORY = "get_cook_history"
SERVICE_LIST_COOKS = "list_cooks"

ATTR_DEVICE_ID = "device_id"
ATTR_SESSION_ID = "session_id"
ATTR_MAX_POINTS = "max_points"
ATTR_SOURCE = "source"

# Series name -> the unique_id suffix of the entity holding it.
SERIES_KEYS = {
    "grill": "grill_temp",
    "target": "target_temp",
    "probe1": "probe1_temp",
    "probe2": "probe2_temp",
    "probe3": "probe3_temp",
    "probe4": "probe4_temp",
}

# Local data counts as covering a cook when it starts and ends within this
# fraction of the cook's duration. Recorder's first sample naturally lands a
# poll interval or so after the grill switched on, so this cannot be exact.
COVERAGE_TOLERANCE = 0.05
MIN_TOLERANCE_S = 120

SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_SESSION_ID): vol.Any(cv.string, int),
        vol.Optional(ATTR_MAX_POINTS, default=300): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=5000)
        ),
        vol.Optional(ATTR_SOURCE, default="auto"): vol.In(["auto", "local", "cloud"]),
    }
)


def _resolve(hass: HomeAssistant, device_id: str) -> tuple[Any, str, str]:
    """Map a device registry id to (coordinator, mac, device_id)."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device {device_id}")

    mac = next((ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None)
    if mac is None:
        raise ServiceValidationError(f"Device {device_id} is not a Masterbuilt grill")

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
            return entry.runtime_data, mac, device.id

    raise ServiceValidationError(f"No loaded config entry for device {device_id}")


def _entity_map(hass: HomeAssistant, device_id: str, mac: str) -> dict[str, str]:
    """Series name -> entity_id, for entities that exist and are enabled."""
    registry = er.async_get(hass)
    by_unique = {
        entry.unique_id: entry
        for entry in er.async_entries_for_device(
            registry, device_id, include_disabled_entities=False
        )
    }
    found: dict[str, str] = {}
    for series, key in SERIES_KEYS.items():
        entry = by_unique.get(f"{mac}_{key}")
        if entry is not None:
            found[series] = entry.entity_id
    return found


async def _from_recorder(
    hass: HomeAssistant,
    entities: dict[str, str],
    start: datetime,
    end: datetime,
    limit: int,
) -> tuple[dict[str, list[list[float]]], str | None, tuple[float, float] | None]:
    """Build series from Recorder. Returns (series, unit, (first, last) offsets)."""
    from homeassistant.components.recorder import get_instance, history

    states = await get_instance(hass).async_add_executor_job(
        lambda: history.get_significant_states(
            hass,
            start,
            end,
            list(entities.values()),
            include_start_time_state=True,
            significant_changes_only=False,
            minimal_response=False,
        )
    )

    base = start.timestamp()
    window = max(0.0, end.timestamp() - base)
    series: dict[str, list[list[float]]] = {}
    unit: str | None = None
    first: float | None = None
    last: float | None = None

    for name, entity_id in entities.items():
        points: list[list[float]] = []
        for state in states.get(entity_id) or []:
            if state.state in (None, "", "unknown", "unavailable"):
                continue
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                continue
            if unit is None:
                unit = state.attributes.get("unit_of_measurement")
            offset = state.last_updated.timestamp() - base
            if offset < 0:
                offset = 0.0
            points.append([round(offset), round(value, 1)])
            # Coverage is judged across all series, not per series: the question
            # is whether Recorder was running, and a sensor that simply did not
            # change is not evidence of a gap.
            first = offset if first is None else min(first, offset)
            last = offset if last is None else max(last, offset)

        if not points:
            continue
        # Recorder only stores changes, so a held value — a setpoint left alone
        # for hours — has its last row early in the window and would otherwise
        # chart as a line that stops dead partway across. Carry it to the end.
        if window and points[-1][0] < window:
            points.append([round(window), points[-1][1]])
        series[name] = decimate(points, limit)

    span = (first, last) if first is not None and last is not None else None
    return series, unit, span


def _covers(span: tuple[float, float] | None, duration: float) -> bool:
    """Whether local data spans enough of the cook to be worth using."""
    if span is None or duration <= 0:
        return False
    tolerance = max(duration * COVERAGE_TOLERANCE, MIN_TOLERANCE_S)
    return span[0] <= tolerance and span[1] >= duration - tolerance


async def _async_get_cook_history(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator, mac, device_id = _resolve(hass, call.data[ATTR_DEVICE_ID])
    api = coordinator.api
    limit = call.data[ATTR_MAX_POINTS]
    source = call.data[ATTR_SOURCE]
    session_id = call.data.get(ATTR_SESSION_ID)

    # The session list is cheap and carries no samples; it is what tells us the
    # cook's window, which we need whichever source we end up reading.
    sessions = await api.async_get_sessions(mac)
    if not sessions:
        return {"session": None, "source": None, "series": {}}

    if session_id is None:
        session = sessions[0]
    else:
        session = next((s for s in sessions if str(s.get("id")) == str(session_id)), None)
        if session is None:
            raise ServiceValidationError(f"No cook session with id {session_id}")

    start_ts = session.get("start") or 0
    end_ts = session.get("end") or int(datetime.now(timezone.utc).timestamp())
    duration = max(0, end_ts - start_ts)

    meta = {
        "id": session.get("id"),
        "state": session.get("state"),
        "start": start_ts,
        "end": session.get("end"),
        "snapshot_count": session.get("snapshotCount"),
    }

    if source in ("auto", "local"):
        entities = _entity_map(hass, device_id, mac)
        if entities:
            series, unit, span = await _from_recorder(
                hass,
                entities,
                datetime.fromtimestamp(start_ts, tz=timezone.utc),
                datetime.fromtimestamp(end_ts, tz=timezone.utc),
                limit,
            )
            if series and (source == "local" or _covers(span, duration)):
                return {
                    "session": meta,
                    "source": "recorder",
                    "unit": unit,
                    "series": series,
                }
        if source == "local":
            return {"session": meta, "source": "recorder", "unit": None, "series": {}}
        _LOGGER.debug(
            "Recorder does not cover session %s; falling back to cloud", meta["id"]
        )

    full = await api.async_get_session(mac, meta["id"])
    snapshots = full.get("snapshots") or []
    series, unit = series_from_snapshots(snapshots, full.get("start") or start_ts, limit)
    return {"session": meta, "source": "cloud", "unit": unit, "series": series}


async def _async_list_cooks(call: ServiceCall) -> ServiceResponse:
    """Every cook the cloud still has, newest first. No samples, so it is cheap.

    Exists so you can find a ``session_id`` to pass to ``get_cook_history``,
    including cooks from before Home Assistant knew about the grill.
    """
    coordinator, mac, _ = _resolve(call.hass, call.data[ATTR_DEVICE_ID])
    sessions = await coordinator.api.async_get_sessions(mac)
    return {
        "cooks": [
            {
                "id": s.get("id"),
                "state": s.get("state"),
                "start": s.get("start"),
                "end": s.get("end"),
                "snapshot_count": s.get("snapshotCount"),
            }
            for s in sessions
        ]
    }


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration actions once."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_COOK_HISTORY):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_COOK_HISTORY,
        _async_get_cook_history,
        schema=SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_COOKS,
        _async_list_cooks,
        schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
