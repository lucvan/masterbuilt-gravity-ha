"""Constants for the Masterbuilt Gravity integration."""
from __future__ import annotations

import base64
from typing import Any

DOMAIN = "masterbuilt_gravity"

# ---------------------------------------------------------------------------
# CAS REST backend (reverse-engineered from the official app v1.0.41).
#
# Middleby serves several of its grill brands from the same backend behind
# per-brand CAS hostnames. The app key, thing-name salt and the shadow and
# session routes are all identical; only the host differs, which is why a
# Kamado Joe Konnected Joe works with nothing changed but this value. Anything
# that later turns out to vary by brand belongs in this table, not in the code.
# ---------------------------------------------------------------------------
DEFAULT_BRAND = "masterbuilt"

BRANDS: dict[str, dict[str, Any]] = {
    "masterbuilt": {
        "label": "Masterbuilt",
        "cas_base": "https://cas.masterbuilt.com",
        "default_name": "Masterbuilt Gravity",
        "default_model": "Gravity Series",
        "has_hopper": True,
        "intensity_key": "heat_intensity",
    },
    "kamado_joe": {
        "label": "Kamado Joe",
        "cas_base": "https://cas.kamadojoe.com",
        "default_name": "Kamado Joe",
        "default_model": "Konnected Joe",
        # A Konnected Joe has no hopper, but still reports doorOpn -- always
        # false -- so the sensor has to be dropped by brand. Probing the shadow
        # for the key would keep it (#2). The same heat.t2.intensity value is
        # fan speed on this grill, so it is named for what it is.
        "has_hopper": False,
        "intensity_key": "fan_speed",
    },
}


def brand_traits(brand: str | None) -> dict[str, Any]:
    """Resolve a brand slug to its table entry.

    Config entries created before multi-brand support carry no brand key, so a
    missing or unrecognised slug has to resolve to Masterbuilt.
    """
    return BRANDS.get(brand or DEFAULT_BRAND, BRANDS[DEFAULT_BRAND])


def cas_base(brand: str | None) -> str:
    """CAS REST host for a brand slug."""
    return brand_traits(brand)["cas_base"]


def brand_label(brand: str | None) -> str:
    """Display name for a brand slug."""
    return brand_traits(brand)["label"]


# ---------------------------------------------------------------------------
# Model profiles.
#
# The shadow format is generic across Middleby grills, so a key being present
# proves nothing about the hardware -- a Konnected Joe reports doorOpn with no
# hopper to open. A profile records what one model is known to have, and the
# entity set is narrowed to match.
#
# Profiles only ever narrow a grill that has been matched to one, by Automatic
# or by hand. An unrecognised model code, and every grill set up before profiles
# existed, gets the standard set: every probe slot and the brand's defaults.
# Removing entities people already rely on is the failure this is built around.
#
# The Kamado Joe entries are from rellerton/kamado-joe-ha, shared for reuse.
# Probe counts on its two provisional models come from Kamado Joe's product
# documentation, not from observed hardware.
# ---------------------------------------------------------------------------
CONF_PROFILES = "profiles"
PROFILE_AUTO = "auto"
PROFILE_STANDARD = "standard"
STANDARD_PROBES = 4
PROBE_ENTITY_KEYS = ("temp", "target", "reached", "target_set")

MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "C:G:P26:1:D": {
        "brand": "masterbuilt",
        "name": "Gravity Series 800",
        # Four ports, confirmed on the physical controller. Usage is not
        # hardware: across 39 recorded cooks this grill only ever reported on
        # ports 1 and 2, and a profile built from that would have hidden two
        # real ports from every Gravity 800 on Automatic.
        "probes": 4,
        "has_hopper": True,
        "intensity_key": "heat_intensity",
        "validated": True,
    },
    "C:G:018:1:D": {
        "brand": "kamado_joe",
        "name": "Konnected Joe",
        "probes": 3,
        "has_hopper": False,
        "intensity_key": "fan_speed",
        "validated": True,
    },
    "C:G:024:1:D": {
        "brand": "kamado_joe",
        "name": "Big Konnected Joe",
        "probes": 3,
        "has_hopper": False,
        "intensity_key": "fan_speed",
        "validated": False,
    },
    "P:G:018:1:D": {
        "brand": "kamado_joe",
        "name": "Pellet Joe",
        "probes": 2,
        # A pellet grill has a hopper, but nothing shows its doorOpn is wired to
        # one; a generic shadow key is not evidence of a sensor.
        "has_hopper": False,
        "intensity_key": "fan_speed",
        "validated": False,
    },
}


def model_profile_label(code: str) -> str:
    """"Kamado Joe Konnected Joe (C:G:018:1:D)", flagged when provisional."""
    profile = MODEL_PROFILES[code]
    label = f"{BRANDS[profile['brand']]['label']} {profile['name']} ({code})"
    return label if profile["validated"] else f"{label} — provisional"


def grill_traits(brand: str | None, model: str | None, choice: str | None) -> dict[str, Any]:
    """Resolve what one grill exposes from its profile choice.

    ``choice`` is Automatic, Standard, or a model code. Anything that doesn't
    resolve to a known profile -- no choice stored, an unrecognised detected
    code, a code since dropped from the table -- gives the standard set, never
    less.
    """
    base = brand_traits(brand)
    standard = {
        "profile": None,
        "probes": STANDARD_PROBES,
        "has_hopper": base["has_hopper"],
        "intensity_key": base["intensity_key"],
    }
    if choice in (None, PROFILE_STANDARD):
        return standard
    code = model if choice == PROFILE_AUTO else choice
    profile = MODEL_PROFILES.get(code or "")
    if profile is None:
        return standard
    return {
        "profile": code,
        "probes": profile["probes"],
        "has_hopper": profile["has_hopper"],
        "intensity_key": profile["intensity_key"],
    }


def probe_number(key: str) -> int | None:
    """The probe slot an entity key belongs to, or None."""
    if key.startswith("probe") and key[5:6].isdigit():
        return int(key[5])
    return None


def excluded_keys(traits: dict[str, Any]) -> set[str]:
    """Entity keys a resolved profile leaves out, for exact registry cleanup."""
    keys = {
        f"probe{n}_{suffix}"
        for n in range(traits["probes"] + 1, STANDARD_PROBES + 1)
        for suffix in PROBE_ENTITY_KEYS
    }
    if not traits["has_hopper"]:
        keys.add("door_open")
    return keys

# App-level key used as HTTP Basic auth for the unauthenticated login endpoint.
_APP_KEY = (
    "XB7RVSq2IfoBO7894f6Vb4OVxlml0PIQBx~e:"
    "aMMIKdZV7UPboDjBus1pf4aOkQZT08miQ5PH85gwW0XjDwML"
)
APP_BASIC = "Basic " + base64.b64encode(_APP_KEY.encode()).decode()

# thingName = md5( lower(macAddress[4:]) + THING_SALT )
THING_SALT = ".Kavry9-vaqsar-wirtok"

# ---------------------------------------------------------------------------
# AWS IoT control plane (writes). Every value below is an app-level constant
# extracted from the public APK — NOT a per-user secret. The app authenticates
# all installs to AWS as a single hardcoded service identity, then a fresh
# per-install X.509 certificate is minted and the CAS backend attaches the
# shadow policy to it. Setpoint writes are MQTT publishes to the device shadow.
# See control.py for the flow. These are base64-wrapped only to keep them out
# of plain-text grep, exactly as the app ships them; they are not confidential.
#
# These stay module-level rather than moving into BRANDS: the control plane is
# shared across Middleby brands where CAS is not. A Kamado Joe install mints
# its certificate against these same Masterbuilt-owned endpoints, and grill and
# probe setpoint writes are confirmed working that way on a Konnected Joe
# (model C:G:018:1:D, 2026-09-07). Reads are brand-routed; writes need not be.
# ---------------------------------------------------------------------------
AWS_REGION = "us-east-2"
COGNITO_USER_POOL_ID = "us-east-2_91Wt2hzCz"
COGNITO_CLIENT_ID = "1s3lnige0e77ojajdfn9pnsso4"
COGNITO_IDENTITY_POOL_ID = "us-east-2:ff94e741-672e-4b13-86a4-78b9e89614bf"
_COGNITO_CLIENT_SECRET = "MTVidXNsazhjN29ndXZxZWc1djFycGdmdHNiMWQ1aGQyMDc5ZzN1aTJlbzJjbzFvZDQ0dA=="
_SVC_USER = "cmF1bCtjZXJ0aWZpY2F0ZXNAd2VhcmVlbnZveS5jb20="
_SVC_PASS = "ZzAzMEpGMDU0MzA1IQ=="
COGNITO_CLIENT_SECRET = base64.b64decode(_COGNITO_CLIENT_SECRET).decode()
SVC_USERNAME = base64.b64decode(_SVC_USER).decode()
SVC_PASSWORD = base64.b64decode(_SVC_PASS).decode()

IOT_ATS_ENDPOINT = "a386xm06thrxxr-ats.iot.us-east-2.amazonaws.com"
# Attach-policy endpoint (unauthenticated); binds the minted cert to the thing's
# shadow policy. Not present as a literal in the APK — captured from live traffic.
ATTACH_POLICY_URL = (
    "http://masterbuiltaws-production.6k9fbw6cvt.us-west-2.elasticbeanstalk.com/aws/policy"
)

# Per-entry storage for the control certificate. Defined here, not in
# control.py, so the integration can delete it without importing the control
# stack: read-only mode must never load boto3 just to clean up after itself.
IOT_STORAGE_VERSION = 1


def iot_store_key(entry_id: str) -> str:
    """Storage key holding one config entry's control certificate."""
    return f"{DOMAIN}.{entry_id}.iot"


# Target temperature sentinel meaning "not set / off" (0 F == ~-17 C).
TARGET_OFF = -17


def target_or_none(value: Any) -> Any:
    """A setpoint, or None when it means "no target set".

    The grill expresses "off" in its own display unit, so the sentinel moves
    with the unit: -17 in Celsius, confirmed on a grill reporting pwrOn false
    (#2), which is 0 F carried across and rounded. A Fahrenheit grill therefore
    reports a plain 0, and reading that literally puts "0 F" on the target
    sensor instead of leaving it blank.

    Both are treated as unset. 0 is not a valid target in either unit -- the
    grills report a minimum of 65 C / 150 F -- and the probe helpers already
    treat it that way, so this makes grill and probe handling agree.
    """
    if value is None or value == TARGET_OFF or value == 0:
        return None
    return value

# The grill/app fire their "reached" notification a few degrees below the
# setpoint (observed ~2 C; the exact offset isn't cleanly exposed). Applied to
# both grill and probe "at temperature" detection. Tune here if needed.
REACHED_TOLERANCE_C = 3
REACHED_TOLERANCE_F = 5


def _reached(current: float, target: float, fah: bool | None) -> bool:
    tol = REACHED_TOLERANCE_F if fah else REACHED_TOLERANCE_C
    return current >= target - tol

DEFAULT_SCAN_INTERVAL = 30  # seconds

CONF_BRAND = "brand"
CONF_CONTROL = "control"
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

# The previous completed cook is fetched from the cloud once, when a cook ends,
# and published as a state attribute. Affordable only because it changes once
# per cook; a live series would rewrite this payload into Recorder's states
# table on every poll. Points are [offset_seconds_from_start, value], which
# costs ~10 bytes each against Recorder's 16 KiB attribute ceiling.
LAST_COOK_MAX_POINTS = 150

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
