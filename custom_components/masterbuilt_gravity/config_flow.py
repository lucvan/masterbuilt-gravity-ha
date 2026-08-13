"""Config, reauth and options flows for Masterbuilt Gravity."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MasterbuiltApi, MasterbuiltApiError, MasterbuiltAuthError
from .const import (
    CONF_DEVICES,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    CONF_TRACK_HISTORY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)

STEP_REAUTH = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


def _device_label(device: dict[str, Any]) -> str:
    """Human-readable label for the device picker.

    The cloud's ``givenName`` is whatever the account holder typed into the app
    and is often left at a default, so fall back to the model and always show
    enough of the MAC to tell two identical grills apart.
    """
    mac = device.get("macAddress", "")
    name = device.get("givenName") or device.get("model") or "Masterbuilt grill"
    return f"{name} ({mac[-6:]})" if mac else name


async def _async_fetch_devices(
    hass, email: str, password: str
) -> list[dict[str, Any]]:
    """Log in and return the account's paired devices."""
    api = MasterbuiltApi(async_get_clientsession(hass), email, password)
    await api.async_login()
    return await api.async_get_devices()


class MasterbuiltConfigFlow(ConfigFlow, domain=DOMAIN):
    """Email/password onboarding, then pick which grills to add."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._password: str | None = None
        self._devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            try:
                devices = await _async_fetch_devices(
                    self.hass, email, user_input[CONF_PASSWORD]
                )
            except MasterbuiltAuthError:
                errors["base"] = "invalid_auth"
            except MasterbuiltApiError:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    self._email = email
                    self._password = user_input[CONF_PASSWORD]
                    self._devices = devices
                    if len(devices) == 1:
                        return self._create(devices[0].get("macAddress"))
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which of several paired grills to bring into HA."""
        if user_input is not None:
            return self._create(*user_input[CONF_DEVICES])

        options = [
            SelectOptionDict(value=d["macAddress"], label=_device_label(d))
            for d in self._devices
            if d.get("macAddress")
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICES, default=[o["value"] for o in options]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema)

    def _create(self, *macs: str | None) -> ConfigFlowResult:
        selected = [m for m in macs if m]
        return self.async_create_entry(
            title=self._email or "Masterbuilt",
            data={CONF_EMAIL: self._email, CONF_PASSWORD: self._password},
            options={CONF_DEVICES: selected},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when the stored password stops working."""
        self._email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await _async_fetch_devices(
                    self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except MasterbuiltAuthError:
                errors["base"] = "invalid_auth"
            except MasterbuiltApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH,
            description_placeholders={"email": entry.data.get(CONF_EMAIL, "")},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MasterbuiltOptionsFlow()


class MasterbuiltOptionsFlow(OptionsFlow):
    """Polling cadence, staleness threshold, and cook-history tracking."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10, max=300, step=5, unit_of_measurement="s",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(
                    CONF_STALE_AFTER,
                    default=options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=60, max=3600, step=30, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_TRACK_HISTORY,
                    default=options.get(CONF_TRACK_HISTORY, True),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
