"""Thin async client for the Masterbuilt / Middleby CAS cloud API."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

from .const import APP_BASIC, DEFAULT_BRAND, THING_SALT, cas_base

_LOGGER = logging.getLogger(__name__)


class MasterbuiltAuthError(Exception):
    """Raised when login fails (bad credentials)."""


class MasterbuiltApiError(Exception):
    """Raised on other API/transport errors."""


def device_mac(mac_address: str) -> str:
    """Strip the paired-device prefix to get the grill's own MAC.

    ``/paired-device`` reports a 16-character address (e.g. ``424840F520A3CC06``)
    carrying a 2-byte prefix, but the session routes are keyed on the grill's
    real 12-character MAC (``40f520a3cc06``). Passing the prefixed form to
    ``/sessions`` returns ``200`` with an empty list rather than an error, which
    is a good way to spend an afternoon concluding the history API is empty.
    """
    return mac_address[4:].lower()


def thing_name(mac_address: str) -> str:
    """Derive the AWS IoT thing name from the API mac address.

    Same 2-byte-prefix strip as :func:`device_mac`, then md5 with a fixed salt.
    """
    return hashlib.md5(f"{device_mac(mac_address)}{THING_SALT}".encode()).hexdigest()


class MasterbuiltApi:
    """Handles login, device listing and shadow polling."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        brand: str | None = DEFAULT_BRAND,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._cas_base = cas_base(brand)
        self._token: str | None = None

    async def async_login(self) -> str:
        """Authenticate and cache the bearer token."""
        url = f"{self._cas_base}/api/v1/auth/login"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": APP_BASIC,
        }
        body = {"username": self._email, "password": self._password}
        try:
            async with self._session.post(url, json=body, headers=headers) as resp:
                if resp.status in (400, 401, 403):
                    raise MasterbuiltAuthError(f"login rejected ({resp.status})")
                if resp.status != 200:
                    raise MasterbuiltApiError(f"login failed ({resp.status})")
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise MasterbuiltApiError(f"login transport error: {err}") from err
        token = data.get("token")
        if not token:
            raise MasterbuiltApiError("login response missing token")
        self._token = token
        return token

    async def _authed_get(self, path: str) -> Any:
        """GET with bearer token, re-authenticating once on 401."""
        if self._token is None:
            await self.async_login()
        for attempt in range(2):
            headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}
            try:
                async with self._session.get(f"{self._cas_base}{path}", headers=headers) as resp:
                    if resp.status == 401 and attempt == 0:
                        await self.async_login()
                        continue
                    if resp.status != 200:
                        raise MasterbuiltApiError(f"GET {path} -> {resp.status}")
                    return await resp.json()
            except aiohttp.ClientError as err:
                raise MasterbuiltApiError(f"GET {path} transport error: {err}") from err
        raise MasterbuiltApiError(f"GET {path} failed after re-auth")

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the list of paired devices (grills)."""
        data = await self._authed_get("/api/v1/paired-device")
        return data if isinstance(data, list) else []

    async def async_get_shadow_document(self, mac_address: str) -> dict[str, Any]:
        """Return the full shadow document for a device.

        The envelope carries ``timestamp`` (and per-field ``metadata``) which the
        coordinator needs to tell a fresh shadow from a frozen one — the reported
        block alone cannot distinguish "grill is off" from "cloud stopped
        updating an hour ago".
        """
        path = (
            f"/api/v1/paired-device/{mac_address}/shadows/current"
            f"?thing_name={thing_name(mac_address)}"
        )
        return await self._authed_get(path) or {}

    async def async_get_shadow(self, mac_address: str) -> dict[str, Any]:
        """Return just the reported shadow state, or {} if unavailable."""
        doc = await self.async_get_shadow_document(mac_address)
        return doc.get("state", {}).get("reported", {}) or {}

    async def async_get_sessions(self, mac_address: str) -> list[dict[str, Any]]:
        """Return cook sessions, newest first.

        Cheap: one small row per cook (``id``, ``state``, ``start``, ``end``,
        ``snapshotCount``) with no sample data attached.
        """
        data = await self._authed_get(
            f"/api/v1/paired-device/{device_mac(mac_address)}/sessions"
        )
        return data if isinstance(data, list) else []

    async def async_get_last_session(self, mac_address: str) -> dict[str, Any]:
        """Return the most recent cook session, or {} when there is none."""
        data = await self._authed_get(
            f"/api/v1/paired-device/{device_mac(mac_address)}/sessions/last"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_session(
        self, mac_address: str, session_id: str | int
    ) -> dict[str, Any]:
        """Return one cook session including every shadow snapshot.

        Expensive and unbounded: the cloud samples roughly every 10 seconds and
        returns the whole cook inline, so an overnight brisket is several
        thousand snapshots and megabytes of JSON. Never call this on the poll
        loop — it exists for the on-demand history service.
        """
        data = await self._authed_get(
            f"/api/v1/paired-device/{device_mac(mac_address)}/sessions/{session_id}"
        )
        return data if isinstance(data, dict) else {}
