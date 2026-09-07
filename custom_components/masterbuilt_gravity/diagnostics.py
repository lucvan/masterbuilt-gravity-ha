"""Diagnostics for Masterbuilt Gravity.

Bug reports about this integration are nearly always questions about what the
grill actually put in its shadow -- which keys exist on a given model, and what
unit their values are in. Guessing at that from a description costs a round
trip per question, so this dumps the raw shadow document rather than a tidied
summary of the fields the integration happens to read today.

The document is fetched live rather than taken from the coordinator, which
keeps only ``state.reported``: the ``metadata`` block carries the per-leaf
device timestamps behind the staleness sensors, and it is exactly what is
needed when someone reports a grill stuck "off" mid-cook.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MasterbuiltConfigEntry
from .api import MasterbuiltApiError
from .const import CONF_BRAND, DEFAULT_BRAND, cas_base

# Identity, not telemetry. Redacted at any depth, including keys the
# integration does not read but a future model might report.
TO_REDACT = {
    "email",
    "username",
    "password",
    "token",
    "accessToken",
    "refreshToken",
    "mac",
    "macAddress",
    "thingName",
    "thing_name",
    "serial",
    "serialNumber",
    "givenName",
    "userId",
    "ownerId",
    "deviceId",
    "ssid",
    "SSID",
    "ip",
    "ipAddress",
    "latitude",
    "longitude",
}


def _summarise_last_cook(last_cook: dict[str, Any]) -> dict[str, Any]:
    """Keep the last cook's shape, drop its bulk.

    The series is thousands of points and answers nothing a bug report asks;
    the point counts confirm it was populated at all.
    """
    summary = dict(last_cook)
    series = summary.get("series")
    if isinstance(series, dict):
        summary["series"] = {name: len(points) for name, points in series.items()}
    return summary


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MasterbuiltConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    brand = entry.data.get(CONF_BRAND, DEFAULT_BRAND)
    cached = coordinator.data or {}

    grills: dict[str, Any] = {}
    # Keyed by position rather than MAC: the MAC is redacted everywhere else,
    # and a dict key it appears in would defeat that.
    for index, (mac, device) in enumerate(coordinator.devices.items(), start=1):
        try:
            document: dict[str, Any] = await coordinator.api.async_get_shadow_document(mac)
        except MasterbuiltApiError as err:
            document = {"error": str(err)}

        reported_at = coordinator.reported_at.get(mac)
        grills[f"grill_{index}"] = {
            "device": async_redact_data(device, TO_REDACT),
            "shadow_document": async_redact_data(document, TO_REDACT),
            "coordinator_reported": async_redact_data(cached.get(mac) or {}, TO_REDACT),
            "reported_at": reported_at.isoformat() if reported_at else None,
            "stale": coordinator.is_stale(mac),
            "age_seconds": coordinator.age(mac),
            "current_cook": async_redact_data(coordinator.cook.get(mac) or {}, TO_REDACT),
            "last_cook": _summarise_last_cook(coordinator.last_cook.get(mac) or {}),
        }

    return {
        "entry": {
            "brand": brand,
            "cas_base": cas_base(brand),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "grill_count": len(coordinator.devices),
        "grills": grills,
    }
