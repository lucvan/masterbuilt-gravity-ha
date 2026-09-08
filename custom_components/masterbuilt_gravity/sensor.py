"""Sensor platform for Masterbuilt Gravity."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MasterbuiltConfigEntry
from .const import active_errors, brand_traits, error_text, target_or_none
from .entity import MasterbuiltEntity


def _heat_target(r: dict[str, Any]) -> Any:
    return target_or_none(r.get("heat", {}).get("t2", {}).get("trgt"))


def _probe_temp(n: int) -> Callable[[dict[str, Any]], Any]:
    return lambda r: r.get("probes", {}).get(f"p{n}", {}).get("temp")


def _probe_target(n: int) -> Callable[[dict[str, Any]], Any]:
    def _fn(r: dict[str, Any]) -> Any:
        val = r.get("probes", {}).get(f"p{n}", {}).get("trgt")
        return None if not val else val
    return _fn


def _probe_present(n: int) -> Callable[[dict[str, Any]], bool]:
    return lambda r: f"p{n}" in (r.get("probes") or {})


@dataclass(frozen=True, kw_only=True)
class MbSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[dict[str, Any]], Any]
    is_temperature: bool = False
    present_fn: Callable[[dict[str, Any]], bool] | None = None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[MbSensorDescription, ...] = (
    MbSensorDescription(
        key="grill_temp",
        translation_key="grill_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        is_temperature=True,
        value_fn=lambda r: r.get("mainTemp"),
    ),
    MbSensorDescription(
        key="target_temp",
        translation_key="target_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        is_temperature=True,
        value_fn=_heat_target,
    ),
    MbSensorDescription(
        key="heat_intensity",
        translation_key="heat_intensity",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        value_fn=lambda r: r.get("heat", {}).get("t2", {}).get("intensity"),
    ),
    MbSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.get("RSSI"),
    ),
    MbSensorDescription(
        key="error",
        translation_key="error",
        icon="mdi:alert-circle",
        value_fn=error_text,
        attrs_fn=lambda r: {"codes": active_errors(r), "raw": r.get("errors")},
    ),
)

PROBE_SENSORS: tuple[MbSensorDescription, ...] = tuple(
    desc
    for n in (1, 2, 3, 4)
    for desc in (
        MbSensorDescription(
            key=f"probe{n}_temp",
            translation_key=f"probe{n}_temp",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            is_temperature=True,
            value_fn=_probe_temp(n),
            present_fn=_probe_present(n),
        ),
        MbSensorDescription(
            key=f"probe{n}_target",
            translation_key=f"probe{n}_target",
            device_class=SensorDeviceClass.TEMPERATURE,
            is_temperature=True,
            entity_registry_enabled_default=False,
            value_fn=_probe_target(n),
            present_fn=_probe_present(n),
        ),
    )
)


DIAGNOSTIC_SENSORS: tuple[MbSensorDescription, ...] = (
    MbSensorDescription(
        key="last_reported",
        translation_key="last_reported",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud-clock-outline",
        value_fn=lambda r: None,  # sourced from the coordinator, not the shadow
    ),
    MbSensorDescription(
        key="data_age",
        translation_key="data_age",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="s",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:timer-sand",
        value_fn=lambda r: None,
    ),
)


def _for_brand(
    descriptions: tuple[MbSensorDescription, ...], brand: str | None
) -> tuple[MbSensorDescription, ...]:
    """Rename heat intensity where the brand calls it something else.

    heat.t2.intensity drives the fan, and Kamado Joe presents it as fan speed
    rather than heat intensity. Only the translation key and icon change: the
    description key is the unique_id suffix, so renaming that would orphan
    every existing entity and lose its history.
    """
    key = brand_traits(brand)["intensity_key"]
    if key == "heat_intensity":
        return descriptions
    return tuple(
        replace(desc, translation_key=key, icon="mdi:fan")
        if desc.key == "heat_intensity"
        else desc
        for desc in descriptions
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MasterbuiltConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    sensors = _for_brand(SENSORS, coordinator.brand)
    entities: list[MasterbuiltEntity] = []
    for mac in coordinator.devices:
        for desc in (*sensors, *PROBE_SENSORS):
            entities.append(MasterbuiltSensor(coordinator, mac, desc))
        for desc in DIAGNOSTIC_SENSORS:
            entities.append(MasterbuiltFreshnessSensor(coordinator, mac, desc))
        entities.append(MasterbuiltCookStartSensor(coordinator, mac))
        if coordinator.track_history:
            entities.append(MasterbuiltLastCookSensor(coordinator, mac))
    async_add_entities(entities)


class MasterbuiltSensor(MasterbuiltEntity, SensorEntity):
    """A single reported-state value."""

    entity_description: MbSensorDescription

    def __init__(self, coordinator, mac: str, description: MbSensorDescription) -> None:
        super().__init__(coordinator, mac, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.reported)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.is_temperature:
            return (
                UnitOfTemperature.FAHRENHEIT
                if self.reported.get("fah")
                else UnitOfTemperature.CELSIUS
            )
        return self.entity_description.native_unit_of_measurement

    @property
    def suggested_unit_of_measurement(self) -> str | None:
        """Display in whatever unit the grill itself is set to.

        Without this, HA converts the grill's native Fahrenheit into Celsius on a
        metric system, so the dashboard disagrees with the appliance's own panel.
        This only seeds the default at registration — a user who wants the other
        unit can still override it per entity, and that choice sticks.
        """
        if self.entity_description.is_temperature:
            return self.native_unit_of_measurement
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn:
            return self.entity_description.attrs_fn(self.reported)
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        present = self.entity_description.present_fn
        return present(self.reported) if present else True


class MasterbuiltFreshnessSensor(MasterbuiltEntity, SensorEntity):
    """How recently the grill reported, independent of what it reported."""

    entity_description: MbSensorDescription

    def __init__(self, coordinator, mac: str, description: MbSensorDescription) -> None:
        super().__init__(coordinator, mac, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.entity_description.key == "last_reported":
            return self.coordinator.reported_at.get(self._mac)
        age = self.coordinator.age(self._mac)
        return None if age is None else round(age)


class MasterbuiltCookStartSensor(MasterbuiltEntity, SensorEntity):
    """When the current cook began, per the cloud's own session record.

    This is the one thing Recorder cannot work out for you. Every temperature
    reading is already in the database; what is not is where one cook ends and
    the next begins. Pair it with Recorder history to bound a chart, or with
    ``relative_time()`` for an elapsed-time card.

    Unknown when no cook is in progress.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-play-outline"
    _attr_translation_key = "cook_start"

    def __init__(self, coordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "cook_start")

    @property
    def native_value(self) -> datetime | None:
        start = (self.coordinator.cook.get(self._mac) or {}).get("start")
        if not start:
            return None
        return datetime.fromtimestamp(start, tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cook = self.coordinator.cook.get(self._mac) or {}
        if not cook:
            return None
        return {"session_id": cook.get("id"), "snapshot_count": cook.get("snapshotCount")}


class MasterbuiltLastCookSensor(MasterbuiltEntity, SensorEntity):
    """The previous completed cook, fetched from the cloud once when it ends.

    Carries a decimated series in attributes so a dashboard can chart a finished
    cook without a script. That is affordable precisely because it changes once
    per cook — unlike a live series, which would rewrite its whole payload into
    the ``states`` table on every poll.

    For arbitrary older cooks, use the ``get_cook_history`` action instead.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:history"
    _attr_translation_key = "last_cook"

    def __init__(self, coordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "last_cook")

    @property
    def native_value(self) -> datetime | None:
        end = (self.coordinator.last_cook.get(self._mac) or {}).get("end")
        if not end:
            return None
        return datetime.fromtimestamp(end, tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.coordinator.last_cook.get(self._mac) or None
