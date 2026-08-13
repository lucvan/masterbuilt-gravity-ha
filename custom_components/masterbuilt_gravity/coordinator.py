"""Data update coordinator for Masterbuilt Gravity."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MasterbuiltApi, MasterbuiltApiError, MasterbuiltAuthError
from .const import (
    CONF_DEVICES,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    CONF_TRACK_HISTORY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    LAST_COOK_MAX_POINTS,
    THING_SALT,
)
from .control import MasterbuiltControl
from .history import series_from_snapshots


def thing_name_for(mac_address: str) -> str:
    """AWS IoT thing name for a device mac (same derivation as api.thing_name)."""
    import hashlib

    return hashlib.md5(f"{mac_address[4:].lower()}{THING_SALT}".encode()).hexdigest()

_LOGGER = logging.getLogger(__name__)


def _reported_timestamp(doc: dict[str, Any]) -> datetime | None:
    """When the grill itself last reported anything, from shadow metadata.

    AWS IoT stamps every reported leaf with the time the *device* sent it, so the
    newest leaf timestamp is the last time the grill spoke. The envelope's own
    ``timestamp`` is when the cloud served the document and keeps advancing even
    after the grill goes silent, which is precisely the case we need to catch.
    """
    metadata = (doc.get("metadata") or {}).get("reported")
    newest: float | None = None

    def walk(node: Any) -> None:
        nonlocal newest
        if isinstance(node, dict):
            ts = node.get("timestamp")
            if isinstance(ts, (int, float)):
                newest = ts if newest is None else max(newest, ts)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(metadata)
    if newest is None:
        envelope = doc.get("timestamp")
        newest = envelope if isinstance(envelope, (int, float)) else None
    if newest is None:
        return None
    return datetime.fromtimestamp(newest, tz=timezone.utc)


class MasterbuiltCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the cloud and exposes per-device reported shadow state.

    ``self.data`` maps macAddress -> reported state dict.
    ``self.devices`` maps macAddress -> device metadata (givenName, model, ...).
    ``self.reported_at`` maps macAddress -> when the grill last reported.
    ``self.cook`` maps macAddress -> current cook session row (no samples).
    ``self.last_cook`` maps macAddress -> previous completed cook, with series.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MasterbuiltApi) -> None:
        options = entry.options
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.api = api
        self.entry = entry
        self.devices: dict[str, dict[str, Any]] = {}
        self.reported_at: dict[str, datetime | None] = {}
        self.cook: dict[str, dict[str, Any]] = {}
        self.last_cook: dict[str, dict[str, Any]] = {}
        self.stale_after = timedelta(
            seconds=options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER)
        )
        self.track_history = options.get(CONF_TRACK_HISTORY, True)
        self._selected: list[str] | None = options.get(CONF_DEVICES) or None
        self._powered: dict[str, bool] = {}
        self.control = MasterbuiltControl(hass, entry.entry_id)

    async def async_set_grill_target(self, mac: str, value: int) -> None:
        """Write a grill setpoint, then refresh so the UI reflects it."""
        await self.control.async_set_grill_target(thing_name_for(mac), value)
        await self.async_request_refresh()

    async def async_set_probe_target(self, mac: str, probe: int, value: int) -> None:
        await self.control.async_set_probe_target(thing_name_for(mac), probe, value)
        await self.async_request_refresh()

    def is_stale(self, mac: str) -> bool:
        """True when the grill has not reported within the staleness window."""
        reported_at = self.reported_at.get(mac)
        if reported_at is None:
            return False
        return datetime.now(timezone.utc) - reported_at > self.stale_after

    def age(self, mac: str) -> float | None:
        """Seconds since the grill last reported, or None if unknown."""
        reported_at = self.reported_at.get(mac)
        if reported_at is None:
            return None
        return (datetime.now(timezone.utc) - reported_at).total_seconds()

    async def _async_discover(self) -> None:
        for dev in await self.api.async_get_devices():
            mac = dev.get("macAddress")
            if not mac:
                continue
            if self._selected and mac not in self._selected:
                continue
            self.devices[mac] = dev

    async def _async_refresh_sessions(self, mac: str) -> None:
        """Refresh the cook-window row and, if it changed, the last finished cook.

        The session *list* is cheap — one small row per cook, no samples — so it
        is safe to call when cook state changes. Fetching a full session is not
        cheap (thousands of snapshots, megabytes), so that only happens when a
        cook actually finishes.
        """
        try:
            sessions = await self.api.async_get_sessions(mac)
        except MasterbuiltApiError as err:
            _LOGGER.debug("Session list unavailable for %s: %s", mac, err)
            return

        self.cook[mac] = next(
            (s for s in sessions if s.get("state") == "ACTIVE"), {}
        )

        if not self.track_history:
            return

        finished = next(
            (
                s
                for s in sessions
                if s.get("state") != "ACTIVE" and (s.get("snapshotCount") or 0)
            ),
            None,
        )
        if finished is None or self.last_cook.get(mac, {}).get("id") == finished["id"]:
            return

        try:
            full = await self.api.async_get_session(mac, finished["id"])
        except MasterbuiltApiError as err:
            _LOGGER.debug("Last-cook fetch failed for %s: %s", mac, err)
            return

        start = full.get("start") or finished.get("start") or 0
        series, unit = series_from_snapshots(
            full.get("snapshots") or [], start, LAST_COOK_MAX_POINTS
        )
        self.last_cook[mac] = {
            "id": full.get("id"),
            "start": start,
            "end": full.get("end"),
            "snapshot_count": full.get("snapshotCount"),
            "unit": unit,
            "series": series,
        }

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            if not self.devices:
                await self._async_discover()

            result: dict[str, dict[str, Any]] = {}

            for mac in self.devices:
                try:
                    doc = await self.api.async_get_shadow_document(mac)
                except MasterbuiltApiError as err:
                    # One grill failing should not blank the others; keep the
                    # last known state and let the staleness sensors show it.
                    _LOGGER.debug("Shadow fetch failed for %s: %s", mac, err)
                    result[mac] = (self.data or {}).get(mac, {})
                    continue

                reported = doc.get("state", {}).get("reported", {}) or {}
                result[mac] = reported
                self.reported_at[mac] = _reported_timestamp(doc)

                # Session lookups only on the first poll and on power
                # transitions — never on every tick.
                powered = bool(reported.get("pwrOn"))
                if mac not in self._powered or self._powered[mac] != powered:
                    self._powered[mac] = powered
                    await self._async_refresh_sessions(mac)

            return result
        except MasterbuiltAuthError as err:
            # Surfaces HA's "reconfigure / re-enter password" flow rather than
            # retrying bad credentials every 30 seconds forever.
            raise ConfigEntryAuthFailed(str(err)) from err
        except MasterbuiltApiError as err:
            raise UpdateFailed(str(err)) from err
