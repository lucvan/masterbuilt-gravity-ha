"""Constants for the Masterbuilt Gravity integration."""
from __future__ import annotations

import base64

DOMAIN = "masterbuilt_gravity"

# CAS REST backend (reverse-engineered from the official app v1.0.41)
CAS_BASE = "https://cas.masterbuilt.com"

# App-level key used as HTTP Basic auth for the unauthenticated login endpoint.
_APP_KEY = (
    "XB7RVSq2IfoBO7894f6Vb4OVxlml0PIQBx~e:"
    "aMMIKdZV7UPboDjBus1pf4aOkQZT08miQ5PH85gwW0XjDwML"
)
APP_BASIC = "Basic " + base64.b64encode(_APP_KEY.encode()).decode()

# thingName = md5( lower(macAddress[4:]) + THING_SALT )
THING_SALT = ".Kavry9-vaqsar-wirtok"

# Target temperature sentinel meaning "not set / off" (0 F == ~-17 C).
TARGET_OFF = -17

# The grill/app fire their "reached" notification a few degrees below the
# setpoint (observed ~2 C; the exact offset isn't cleanly exposed). Applied to
# both grill and probe "at temperature" detection. Tune here if needed.
REACHED_TOLERANCE_C = 3
REACHED_TOLERANCE_F = 5


def _reached(current: float, target: float, fah: bool | None) -> bool:
    tol = REACHED_TOLERANCE_F if fah else REACHED_TOLERANCE_C
    return current >= target - tol

DEFAULT_SCAN_INTERVAL = 30  # seconds

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DEVICES = "devices"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_STALE_AFTER = "stale_after"
CONF_TRACK_HISTORY = "track_history"

# The controller can keep cooking while its WiFi module wedges: the cloud then
# serves a frozen shadow, eventually reporting pwrOn=false with no mainTemp,
# while the grill is physically still running. Treat a shadow older than this as
# untrustworthy rather than believing pwrOn. See binary_sensor "stale".
DEFAULT_STALE_AFTER = 300  # seconds

# In-memory current-cook history. Kept deliberately small: the whole series is
# published as a state attribute, and HA's Recorder rejects attribute payloads
# over 16 KiB. Points are stored as [offset_seconds_from_cook_start, value] so
# each costs ~10 bytes instead of the ~30 an ISO-8601 timestamp would.
HISTORY_SERIES = ("grill", "target", "probe1", "probe2", "probe3", "probe4")
HISTORY_MAX_POINTS = 150  # per series
HISTORY_MIN_INTERVAL = 60  # seconds between recorded points
HISTORY_MIN_DELTA = 2.0  # degrees; record sooner when the value moves this much

# Reported "errors" is a fixed-size list of error codes (0 == no error in that
# slot). Known code -> human text. Some texts are server-defined; extend as we
# observe more codes.
KNOWN_ERRORS: dict[int, str] = {
    4: "Charcoal failed to ignite",
}


def active_errors(reported: dict) -> list[int]:
    """Return the list of non-zero error codes currently reported."""
    return [c for c in (reported.get("errors") or []) if c]


def error_text(reported: dict) -> str:
    """Human-readable error summary, or 'OK' when no error is active."""
    active = active_errors(reported)
    if not active:
        return "OK"
    return ", ".join(KNOWN_ERRORS.get(c, f"Error {c}") for c in active)


def probe_present(reported: dict, n: int) -> bool:
    """True when meat probe ``n`` is currently plugged in."""
    return f"p{n}" in (reported.get("probes") or {})


def probe_reached(reported: dict, n: int) -> bool:
    """True when probe ``n`` has a target set and has reached it (with offset)."""
    p = (reported.get("probes") or {}).get(f"p{n}") or {}
    trgt, temp = p.get("trgt"), p.get("temp")
    if not trgt or temp is None:  # trgt 0/None == no target set
        return False
    return _reached(temp, trgt, reported.get("fah"))


def target_reached(reported: dict) -> bool:
    """Approximate 'at temperature': powered, valid setpoint, temp within tolerance.

    The reported shadow has no explicit "reached" flag and the grill fires its
    push notification a couple of degrees below the setpoint, so a small
    tolerance is applied. Note: ``engaged`` is NOT cooking – it is false once the
    grill settles at temperature – so it must not gate this.
    """
    trgt = (reported.get("heat") or {}).get("t2", {}).get("trgt")
    main = reported.get("mainTemp")
    if not reported.get("pwrOn") or main is None or trgt is None or trgt <= 0:
        return False
    return _reached(main, trgt, reported.get("fah"))
