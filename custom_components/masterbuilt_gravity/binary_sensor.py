"""Binary sensor platform for Masterbuilt Gravity."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MasterbuiltConfigEntry
from .const import active_errors, probe_present, probe_reached, target_reached
from .entity import MasterbuiltEntity


@dataclass(frozen=True, kw_only=True)
class MbBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    present_fn: Callable[[dict[str, Any]], bool] | None = None


BINARY_SENSORS: tuple[MbBinaryDescription, ...] = (
    MbBinaryDescription(
        key="power",
        translation_key="power",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda r: r.get("pwrOn"),
    ),
    MbBinaryDescription(
        key="heating",
        translation_key="heating",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda r: r.get("heat", {}).get("t2", {}).get("heating"),
    ),
    MbBinaryDescription(
        key="engaged",
        translation_key="engaged",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.get("engaged"),
    ),
    MbBinaryDescription(
        key="door_open",
        translation_key="door_open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda r: r.get("doorOpn"),
    ),
    MbBinaryDescription(
        key="lid_open",
        translation_key="lid_open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda r: r.get("lidOpn"),
    ),
    MbBinaryDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda r: bool(active_errors(r)),
        attrs_fn=lambda r: {"codes": active_errors(r), "raw": r.get("errors")},
    ),
    MbBinaryDescription(
        key="target_reached",
        translation_key="target_reached",
        value_fn=target_reached,
    ),
)

PROBE_REACHED: tuple[MbBinaryDescription, ...] = tuple(
    MbBinaryDescription(
        key=f"probe{n}_reached",
        translation_key=f"probe{n}_reached",
        icon="mdi:thermometer-check",
        value_fn=lambda r, n=n: probe_reached(r, n),
        present_fn=lambda r, n=n: probe_present(r, n),
    )
    for n in (1, 2, 3, 4)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MasterbuiltConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[MasterbuiltEntity] = [
        MasterbuiltBinarySensor(coordinator, mac, desc)
        for mac in coordinator.devices
        for desc in (*BINARY_SENSORS, *PROBE_REACHED)
    ]
    entities += [MasterbuiltStaleSensor(coordinator, mac) for mac in coordinator.devices]
    async_add_entities(entities)


class MasterbuiltBinarySensor(MasterbuiltEntity, BinarySensorEntity):
    entity_description: MbBinaryDescription

    def __init__(self, coordinator, mac: str, description: MbBinaryDescription) -> None:
        super().__init__(coordinator, mac, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.reported)

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


class MasterbuiltStaleSensor(MasterbuiltEntity, BinarySensorEntity):
    """On when the cloud shadow has stopped being updated by the grill.

    This exists because the failure it detects is genuinely misleading: the
    controller can carry on cooking while its WiFi module wedges, and the cloud
    then serves a frozen shadow that eventually reads ``pwrOn: false`` with no
    ``mainTemp``. Every other entity here will calmly report "off". Alert on
    this, not on the power sensor.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "stale"

    def __init__(self, coordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "stale")

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_stale(self._mac)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_reported": self.coordinator.reported_at.get(self._mac),
            "age_seconds": self.coordinator.age(self._mac),
            "threshold_seconds": self.coordinator.stale_after.total_seconds(),
        }
