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
    BRANDS,
    CONF_BRAND,
    CONF_CONTROL,
    CONF_DEVICES,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PROFILES,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    CONF_TRACK_HISTORY,
    DEFAULT_BRAND,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    MODEL_PROFILES,
    PROFILE_AUTO,
    PROFILE_STANDARD,
    brand_label,
    model_profile_label,
)

_PROFILE_FIELD = "profile"

_LOGGER = logging.getLogger(__name__)

STEP_USER = vol.Schema(
    {
        vol.Required(CONF_BRAND, default=DEFAULT_BRAND): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=slug, label=meta["label"])
                    for slug, meta in BRANDS.items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
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


def _device_label(device: dict[str, Any], fallback: str) -> str:
    """Human-readable label for the device picker.

    The cloud's ``givenName`` is whatever the account holder typed into the app
    and is often left at a default, so fall back to the model and always show
    enough of the MAC to tell two identical grills apart.
    """
    mac = device.get("macAddress", "")
    name = device.get("givenName") or device.get("model") or fallback
    return f"{name} ({mac[-6:]})" if mac else name


def _profile_name(code: str | None) -> str | None:
    """"Kamado Joe Konnected Joe" for a recognised code, flagged if provisional."""
    profile = MODEL_PROFILES.get(code or "")
    if profile is None:
        return None
    name = f"{BRANDS[profile['brand']]['label']} {profile['name']}"
    return name if profile["validated"] else f"{name} (provisional)"


def _profile_schema(model: str | None, default: str) -> vol.Schema:
    """One grill's profile picker: Automatic, Standard, then every known model."""
    detected = _profile_name(model)
    options = [
        SelectOptionDict(
            value=PROFILE_AUTO,
            label=f"Automatic — {detected}" if detected
            else "Automatic — model not recognised, keeps every entity",
        ),
        SelectOptionDict(
            value=PROFILE_STANDARD, label="Standard — every probe slot, no model profile"
        ),
        *(
            SelectOptionDict(value=code, label=model_profile_label(code))
            for code in MODEL_PROFILES
        ),
    ]
    # A stored choice that's no longer offered (a code dropped from the table)
    # would fail validation on submit, so fall back to Standard.
    if default not in {o["value"] for o in options}:
        default = PROFILE_STANDARD
    return vol.Schema(
        {
            vol.Required(_PROFILE_FIELD, default=default): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        }
    )


def _profile_placeholders(device: dict[str, Any], fallback: str) -> dict[str, str]:
    model = device.get("model")
    return {
        "grill": _device_label(device, fallback),
        "model": model or "none",
        "detected": _profile_name(model) or "not a recognised model",
    }


async def _async_fetch_devices(
    hass, email: str, password: str, brand: str = DEFAULT_BRAND
) -> list[dict[str, Any]]:
    """Log in and return the account's paired devices."""
    api = MasterbuiltApi(async_get_clientsession(hass), email, password, brand)
    await api.async_login()
    return await api.async_get_devices()


class MasterbuiltConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in, pick grills, then choose read-only or control."""

    VERSION = 1
    # 1.2: read-only mode. See async_migrate_entry for why older entries keep
    # control on while new ones default to off.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._brand: str = DEFAULT_BRAND
        self._email: str | None = None
        self._password: str | None = None
        self._devices: list[dict[str, Any]] = []
        self._selected: list[str] = []
        self._profiles: dict[str, str] = {}
        self._profile_queue: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            brand = user_input.get(CONF_BRAND, DEFAULT_BRAND)
            email = user_input[CONF_EMAIL]
            # Masterbuilt entries keep the bare email as their unique id --
            # they predate brand support, and re-keying them would need an
            # entry migration for no benefit. Other brands are namespaced, so
            # one address can hold an account with each.
            unique_id = email.lower()
            if brand != DEFAULT_BRAND:
                unique_id = f"{brand}:{unique_id}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            try:
                devices = await _async_fetch_devices(
                    self.hass, email, user_input[CONF_PASSWORD], brand
                )
            except MasterbuiltAuthError:
                errors["base"] = "invalid_auth"
            except MasterbuiltApiError:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    self._brand = brand
                    self._email = email
                    self._password = user_input[CONF_PASSWORD]
                    self._devices = devices
                    if len(devices) == 1:
                        mac = devices[0].get("macAddress")
                        self._selected = [mac] if mac else []
                        return await self._async_start_profiles()
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which of several paired grills to bring into HA."""
        if user_input is not None:
            self._selected = [m for m in user_input[CONF_DEVICES] if m]
            return await self._async_start_profiles()

        fallback = f"{brand_label(self._brand)} grill"
        options = [
            SelectOptionDict(value=d["macAddress"], label=_device_label(d, fallback))
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

    async def _async_start_profiles(self) -> ConfigFlowResult:
        self._profile_queue = list(self._selected)
        return await self.async_step_profile()

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One screen per grill: the detected model code, Automatic by default."""
        if user_input is not None:
            self._profiles[self._profile_queue.pop(0)] = user_input[_PROFILE_FIELD]
        if not self._profile_queue:
            return await self.async_step_control()
        mac = self._profile_queue[0]
        device = next((d for d in self._devices if d.get("macAddress") == mac), {})
        return self.async_show_form(
            step_id="profile",
            data_schema=_profile_schema(device.get("model"), PROFILE_AUTO),
            description_placeholders=_profile_placeholders(
                device, f"{brand_label(self._brand)} grill"
            ),
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read-only or control, asked explicitly and defaulting to read-only.

        A separate step rather than a checkbox on the sign-in form: it is a
        decision about letting Home Assistant change a live-fire appliance,
        and it deserves its own screen and its own explanation.
        """
        if user_input is not None:
            return self._create(user_input[CONF_CONTROL])
        return self.async_show_form(
            step_id="control",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONTROL, default=False): BooleanSelector()}
            ),
        )

    def _create(self, control: bool) -> ConfigFlowResult:
        label = brand_label(self._brand)
        return self.async_create_entry(
            title=f"{label} ({self._email})" if self._email else label,
            data={
                CONF_BRAND: self._brand,
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
            },
            options={
                CONF_DEVICES: self._selected,
                CONF_PROFILES: self._profiles,
                CONF_CONTROL: control,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when the stored password stops working."""
        self._brand = entry_data.get(CONF_BRAND, DEFAULT_BRAND)
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
                    self.hass,
                    entry.data[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    entry.data.get(CONF_BRAND, DEFAULT_BRAND),
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
    """Settings, and each grill's model profile, behind a menu.

    A menu rather than chained steps, so changing the polling interval never
    means paging through a profile screen for every grill.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, str] = {}
        self._queue: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "profiles"])

    async def async_step_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._queue = list(self._grills())
        return await self.async_step_profile()

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self.config_entry.options
        stored = options.get(CONF_PROFILES) or {}
        if user_input is not None:
            self._profiles[self._queue.pop(0)] = user_input[_PROFILE_FIELD]
        if not self._queue:
            return self.async_create_entry(
                data={**options, CONF_PROFILES: {**stored, **self._profiles}}
            )
        mac = self._queue[0]
        device = self._grills()[mac]
        return self.async_show_form(
            step_id="profile",
            # No stored choice means Standard: what the grill has been running.
            data_schema=_profile_schema(
                device.get("model"), stored.get(mac, PROFILE_STANDARD)
            ),
            description_placeholders=_profile_placeholders(
                device, f"{brand_label(self.config_entry.data.get(CONF_BRAND))} grill"
            ),
        )

    def _grills(self) -> dict[str, dict[str, Any]]:
        """Grills with their metadata, from the running entry when it's loaded."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None and runtime.devices:
            return dict(runtime.devices)
        return {
            mac: {"macAddress": mac}
            for mac in self.config_entry.options.get(CONF_DEVICES) or []
        }

    async def async_step_settings(
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
                vol.Required(
                    CONF_CONTROL,
                    default=options.get(CONF_CONTROL, False),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
