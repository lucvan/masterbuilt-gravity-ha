"""Thin async client for the Masterbuilt / Middleby CAS cloud API."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

from .const import APP_BASIC, CAS_BASE, THING_SALT

_LOGGER = logging.getLogger(__name__)


class MasterbuiltAuthError(Exception):
    """Raised when login fails (bad credentials)."""


class MasterbuiltApiError(Exception):
    """Raised on other API/transport errors."""


def thing_name(mac_address: str) -> str:
    """Derive the AWS IoT thing name from the API mac address.

    The API mac (e.g. ``424840F520A3CC06``) carries a 2-byte prefix; the thing
    name is md5 of the lowercased remainder plus a fixed salt.
    """
    base = mac_address[4:].lower()
    return hashlib.md5(f"{base}{THING_SALT}".encode()).hexdigest()


class MasterbuiltApi:
    """Handles login, device listing and shadow polling."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None

    async def async_login(self) -> str:
        """Authenticate and cache the bearer token."""
        url = f"{CAS_BASE}/api/v1/auth/login"
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
                async with self._session.get(f"{CAS_BASE}{path}", headers=headers) as resp:
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
        """Return the device's cook sessions (most recent first, per the API)."""
        data = await self._authed_get(f"/api/v1/paired-device/{mac_address}/sessions")
        return data if isinstance(data, list) else []

    async def async_get_last_session(self, mac_address: str) -> dict[str, Any]:
        """Return the most recent cook session, or {} when there is none."""
        data = await self._authed_get(
            f"/api/v1/paired-device/{mac_address}/sessions/last"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_session(
        self, mac_address: str, session_id: str | int
    ) -> dict[str, Any]:
        """Return one cook session, including its shadow snapshots."""
        data = await self._authed_get(
            f"/api/v1/paired-device/{mac_address}/sessions/{session_id}"
        )
        return data if isinstance(data, dict) else {}
