"""Turning cloud cook sessions into chartable series.

Deliberately *not* a local history buffer. Home Assistant's Recorder already
stores every temperature sensor this integration creates, so re-recording the
same numbers into a state attribute would duplicate the database and, because
attributes are rewritten on every state change, would write the whole growing
payload back to the ``states`` table every poll.

Live charting therefore uses plain Recorder history on the individual sensors.
What Recorder cannot supply is the cook *window* and finished cooks at the
cloud's own ~10-second resolution, so that is all this module deals with.
"""
from __future__ import annotations

from typing import Any

from homeassistant.util.unit_conversion import TemperatureConverter

from .const import TARGET_OFF


def _grill(r: dict[str, Any]) -> Any:
    return r.get("mainTemp")


def _target(r: dict[str, Any]) -> Any:
    val = r.get("heat", {}).get("t2", {}).get("trgt")
    return None if val is None or val == TARGET_OFF else val


def _probe(n: int):
    def _fn(r: dict[str, Any]) -> Any:
        return (r.get("probes") or {}).get(f"p{n}", {}).get("temp")

    return _fn


def _probe_target(n: int):
    def _fn(r: dict[str, Any]) -> Any:
        # 0 / missing means no target set for that probe, which should leave a
        # gap in the series rather than plot a line along the bottom axis.
        val = (r.get("probes") or {}).get(f"p{n}", {}).get("trgt")
        return val or None

    return _fn


EXTRACTORS = {
    "grill": _grill,
    "target": _target,
    "probe1": _probe(1),
    "probe1_target": _probe_target(1),
    "probe2": _probe(2),
    "probe2_target": _probe_target(2),
    "probe3": _probe(3),
    "probe3_target": _probe_target(3),
    "probe4": _probe(4),
    "probe4_target": _probe_target(4),
}


def _snapshot_unit(snapshot: dict[str, Any]) -> str | None:
    """Unit in force when a snapshot was taken, or None if it does not say."""
    shadow = snapshot.get("shadow") or {}
    if "fah" not in shadow:
        return None
    return "°F" if shadow.get("fah") else "°C"


def decimate(points: list[list[float]], limit: int) -> list[list[float]]:
    """Evenly thin a series to at most ``limit`` points, keeping both endpoints."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[int(i * step)] for i in range(limit)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def series_from_snapshots(
    snapshots: list[dict[str, Any]], start: int, limit: int | None = None
) -> tuple[dict[str, list[list[float]]], str | None]:
    """Turn cloud session snapshots into offset series, oldest first.

    The API returns snapshots newest-first; charts want the opposite. Points are
    ``[seconds_since_session_start, value]``. Returns ``(series, unit)``.

    The unit is a setting on the appliance itself and each snapshot carries the
    ``fah`` flag in force when it was taken, so a cook spanning a change on the
    grill's own panel holds both. Every point is normalised to the unit the cook
    ended in; taking the first unit and labelling the rest with it charts a step
    where the grill appears to double in temperature. That is the same defect
    the Recorder path had -- see ``_from_recorder`` in services.py -- and every
    series here is a temperature, so all of them need it.
    """
    ordered = sorted(snapshots, key=lambda s: s.get("timestamp") or 0)
    series: dict[str, list[list[float]]] = {name: [] for name in EXTRACTORS}

    declared = [u for u in (_snapshot_unit(s) for s in ordered) if u]
    unit = declared[-1] if declared else None
    # Snapshots before the first declaration are assumed to be in the first
    # unit declared, not the last: the flag is missing, not changed.
    current = declared[0] if declared else None

    for snap in ordered:
        shadow = snap.get("shadow") or {}
        current = _snapshot_unit(snap) or current
        offset = (snap.get("timestamp") or start) - start
        for name, extract in EXTRACTORS.items():
            value = extract(shadow)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if unit and current and current != unit:
                try:
                    value = TemperatureConverter.convert(value, current, unit)
                except (TypeError, ValueError):
                    continue
            series[name].append([offset, round(value, 1)])

    populated = {k: v for k, v in series.items() if v}
    if limit is not None:
        populated = {k: decimate(v, limit) for k, v in populated.items()}
    return populated, unit
