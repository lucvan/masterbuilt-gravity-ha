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
from .const import target_or_none
from .entity import MasterbuiltEntity

_PROBE_MIN_F = 32
_PROBE_MAX_F = 300
# Fallbacks for a shadow that carries no heat.t2.min/max. Fahrenheit, and
# converted at read time -- unlike the reported limits, which already arrive in
# the grill's own unit. See _TempNumber._limit.
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
        return self._limit("min", _GRILL_MIN_F)

    @property
    def native_max_value(self) -> float:
        return self._limit("max", _GRILL_MAX_F)

    def _limit(self, key: str, fallback_f: int) -> float:
        """Chamber limit, in the grill's own unit.

        heat.t2.min/max arrive in the same unit as heat.t2.trgt beside them --
        the grill's own -- so they are used as reported, and only the
        Fahrenheit fallbacks get converted. Converting the reported limits as
        though they were always Fahrenheit is unfalsifiable on a Fahrenheit
        grill and roughly halves the range on a Celsius one: a Konnected Joe
        reporting a 370 C ceiling was offering 188 C (#2).
        """
        limit = (self.reported.get("heat") or {}).get("t2", {}).get(key)
        if limit is not None:
            return limit
        return _to_display(self.reported, fallback_f)

    @property
    def native_value(self) -> float | None:
        return target_or_none(self.reported.get("heat", {}).get("t2", {}).get("trgt"))

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
