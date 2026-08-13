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


EXTRACTORS = {
    "grill": _grill,
    "target": _target,
    "probe1": _probe(1),
    "probe2": _probe(2),
    "probe3": _probe(3),
    "probe4": _probe(4),
}


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
    ``[seconds_since_session_start, value]``. Returns ``(series, unit)``, unit
    read from the first snapshot that carries the ``fah`` flag.
    """
    ordered = sorted(snapshots, key=lambda s: s.get("timestamp") or 0)
    series: dict[str, list[list[float]]] = {name: [] for name in EXTRACTORS}
    unit: str | None = None

    for snap in ordered:
        shadow = snap.get("shadow") or {}
        if unit is None and "fah" in shadow:
            unit = "°F" if shadow.get("fah") else "°C"
        offset = (snap.get("timestamp") or start) - start
        for name, extract in EXTRACTORS.items():
            value = extract(shadow)
            if value is None:
                continue
            try:
                series[name].append([offset, round(float(value), 1)])
            except (TypeError, ValueError):
                continue

    populated = {k: v for k, v in series.items() if v}
    if limit is not None:
        populated = {k: decimate(v, limit) for k, v in populated.items()}
    return populated, unit
