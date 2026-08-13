"""In-memory current-cook history.

Replaces what used to be an external cron job that scraped HA's Recorder over
the REST API, converted units, and POSTed a synthetic sensor back. Doing it in
the coordinator means the integration is self-contained, and it removes the two
failure modes that approach had:

* the scraper stored an ISO-8601 timestamp per point, so a long cook grew the
  state attributes past Recorder's 16 KiB ceiling and the sensor started being
  dropped. Points here are ``[seconds_since_cook_start, value]``.
* it extended flat values to "now" for as long as it ran, so a wedged WiFi
  module produced a chart line that kept advancing through a cook that had
  actually stopped being reported. Recording here is gated on shadow freshness.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .const import (
    HISTORY_MAX_POINTS,
    HISTORY_MIN_DELTA,
    HISTORY_MIN_INTERVAL,
    TARGET_OFF,
)


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


class CookHistory:
    """Tracks one grill's current cook and a downsampled series per channel."""

    def __init__(self) -> None:
        self.start: datetime | None = None
        self.end: datetime | None = None
        self.active = False
        self.unit: str | None = None
        self.series: dict[str, list[list[float]]] = {k: [] for k in EXTRACTORS}

    def _reset(self, now: datetime, unit: str | None) -> None:
        self.start = now
        self.end = None
        self.active = True
        self.unit = unit
        self.series = {k: [] for k in EXTRACTORS}

    def update(self, reported: dict[str, Any], now: datetime, fresh: bool) -> None:
        """Fold one poll into the current cook.

        ``fresh`` is False when the shadow has stopped updating; in that case the
        cook is left exactly as it was rather than being extended or closed, so a
        network stall is visibly a gap instead of a fabricated flat line.
        """
        if not fresh:
            return

        powered = bool(reported.get("pwrOn"))
        unit = "°F" if reported.get("fah") else "°C"

        if powered and not self.active:
            self._reset(now, unit)
        elif not powered:
            if self.active:
                self.active = False
                self.end = now
            return

        self.unit = unit
        for name, extract in EXTRACTORS.items():
            value = extract(reported)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            self._append(name, value, now)

    def _append(self, name: str, value: float, now: datetime) -> None:
        assert self.start is not None
        points = self.series[name]
        offset = round((now - self.start).total_seconds())

        if points:
            last_offset, last_value = points[-1]
            moved = abs(value - last_value) >= HISTORY_MIN_DELTA
            if not moved and offset - last_offset < HISTORY_MIN_INTERVAL:
                # Nothing interesting happened; keep the tail pinned to now so
                # the chart still reaches the present without adding a point.
                points[-1] = [offset, last_value]
                return

        points.append([offset, round(value, 1)])
        if len(points) > HISTORY_MAX_POINTS:
            self._decimate(name)

    def _decimate(self, name: str) -> None:
        """Halve a series by dropping every other point, keeping first and last.

        Cheap, allocation-free enough, and preserves shape well for a smoothly
        varying temperature curve. The effect is that a long cook silently
        doubles its sampling interval rather than being truncated.
        """
        points = self.series[name]
        kept = points[::2]
        if points[-1] is not kept[-1]:
            kept.append(points[-1])
        self.series[name] = kept

    def as_attributes(self) -> dict[str, Any]:
        """Attribute payload for the history sensor."""
        return {
            "cook_start": self.start.isoformat() if self.start else None,
            "cook_end": self.end.isoformat() if self.end else None,
            "active": self.active,
            "unit": self.unit,
            "series": {k: v for k, v in self.series.items() if v},
        }

    @property
    def point_count(self) -> int:
        return sum(len(v) for v in self.series.values())
