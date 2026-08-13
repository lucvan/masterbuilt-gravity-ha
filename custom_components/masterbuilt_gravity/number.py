"""Number platform — writable meat-probe targets.

One entity per probe, shown only while that probe is plugged in. Setting it
writes probes.pN.trgt to the shadow. Probe temperature readings live on the
sensor platform; this is purely the settable target.
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MasterbuiltConfigEntry
from .const import TARGET_OFF
from .entity import MasterbuiltEntity

_PROBE_MIN_F = 32
_PROBE_MAX_F = 300
# Grill limits mirror heat.t2.min/max, reported in Fahrenheit regardless of the
# grill's display unit.
_GRILL_MIN_F = 150
_GRILL_MAX_F = 700


def _to_display(reported: dict, fahrenheit: int) -> int:
    """Convert a Fahrenheit constant into the grill's own unit."""
    if reported.get("fah"):
        return fahrenheit
    return round((fahrenheit - 32) * 5 / 9)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MasterbuiltConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[MasterbuiltEntity] = []
    for mac in coordinator.devices:
        entities.append(MasterbuiltGrillTarget(coordinator, mac))
        entities.extend(
            MasterbuiltProbeTarget(coordinator, mac, n) for n in (1, 2, 3, 4)
        )
    async_add_entities(entities)


class _TempNumber(MasterbuiltEntity, NumberEntity):
    """Shared temperature-number behaviour in the grill's own unit."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 5

    @property
    def native_unit_of_measurement(self) -> str:
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.reported.get("fah")
            else UnitOfTemperature.CELSIUS
        )


class MasterbuiltGrillTarget(_TempNumber):
    """Settable grill chamber target (heat.t2.trgt)."""

    _attr_translation_key = "grill_target_set"
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "grill_target_set")

    @property
    def native_min_value(self) -> float:
        return _to_display(self.reported, self.reported.get("heat", {}).get("t2", {}).get("min", _GRILL_MIN_F))

    @property
    def native_max_value(self) -> float:
        return _to_display(self.reported, self.reported.get("heat", {}).get("t2", {}).get("max", _GRILL_MAX_F))

    @property
    def native_value(self) -> float | None:
        val = self.reported.get("heat", {}).get("t2", {}).get("trgt")
        return None if val is None or val == TARGET_OFF else val

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_grill_target(self._mac, round(value))


class MasterbuiltProbeTarget(_TempNumber):
    """Settable target for one meat probe."""

    _attr_native_step = 1

    def __init__(self, coordinator, mac: str, probe: int) -> None:
        super().__init__(coordinator, mac, f"probe{probe}_target_set")
        self._probe = probe
        self._attr_translation_key = f"probe{probe}_target_set"

    @property
    def native_min_value(self) -> float:
        return _to_display(self.reported, _PROBE_MIN_F)

    @property
    def native_max_value(self) -> float:
        return _to_display(self.reported, _PROBE_MAX_F)

    @property
    def native_value(self) -> float | None:
        val = (self.reported.get("probes") or {}).get(f"p{self._probe}", {}).get("trgt")
        return val or None

    @property
    def available(self) -> bool:
        # Only available while the probe is physically plugged in.
        return super().available and f"p{self._probe}" in (self.reported.get("probes") or {})

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_probe_target(self._mac, self._probe, round(value))
