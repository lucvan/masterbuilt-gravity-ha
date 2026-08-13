# Masterbuilt Gravity (Unofficial) — Home Assistant

Home Assistant integration for **Masterbuilt Gravity Series** charcoal grills and smokers, reading live cook telemetry from Masterbuilt's cloud.

> **Fork notice.** This is a fork of [hruskin/masterbuilt-gravity-ha](https://github.com/hruskin/masterbuilt-gravity-ha) by Martin Hruška, who did the original reverse-engineering of the CAS cloud API and wrote the integration this builds on. MIT-licensed, and that license and copyright are retained. This fork diverges: it adds device selection and reauth to onboarding, fixes Fahrenheit display, and adds staleness diagnostics and in-integration cook history. Issues here, not upstream.

Not affiliated with, endorsed by, or supported by Masterbuilt or Middleby.

## What you get

| | |
|---|---|
| **Temperatures** | Grill, target setpoint, and up to 4 meat probes with their targets |
| **State** | Power, heating, engaged, at-temperature, hopper door, lid, errors |
| **Per-probe** | "At temperature" sensor per probe, with a tolerance offset matching the app's own notification behaviour |
| **Diagnostics** | Signal strength, last reported, data age, **stale data** |
| **History** | A downsampled current-cook series for charting, maintained in the integration |

Read-only. See [Writing setpoints](#writing-setpoints) for why.

## Install

**HACS** → ⋮ → *Custom repositories* → add `https://github.com/lucvan/masterbuilt-gravity-ha`, category **Integration** → install → restart Home Assistant.

Then *Settings → Devices & Services → Add Integration → Masterbuilt Gravity*.

Requires Home Assistant 2024.11 or newer.

## Onboarding

1. **Sign in** with the same email and password you use in the Masterbuilt mobile app. They are stored in Home Assistant's config entry and sent only to Masterbuilt's own cloud.
2. **Pick your grills.** If the account has more than one paired grill you choose which to add; a single grill is added automatically.
3. If Masterbuilt later rejects the password, Home Assistant raises its normal **reauthentication** prompt instead of silently failing — re-enter the password and it reconnects.

### Options

*Settings → Devices & Services → Masterbuilt Gravity → Configure*

| Option | Default | Notes |
|---|---|---|
| Polling interval | 30 s | Cloud poll cadence |
| Treat data as stale after | 300 s | Drives the **Stale data** sensor |
| Track current-cook history | on | Turn off if you chart purely from Recorder |

## Read this before you build automations

**The grill can keep cooking while its WiFi module wedges.** When that happens the cloud keeps serving the last shadow it received, and after a while reports `pwrOn: false` with no grill temperature — while the smoker is physically still running at temperature. Every ordinary entity here will calmly report "off".

That is what the **Stale data** binary sensor is for. It watches the *device's own* report timestamps in the shadow metadata, not the envelope timestamp the cloud refreshes on every request.

```yaml
automation:
  - alias: Smoker telemetry lost mid-cook
    trigger:
      - platform: state
        entity_id: binary_sensor.smoker_stale_data
        to: "on"
        for: "00:05:00"
    action:
      - service: notify.mobile_app
        data:
          message: >-
            Lost contact with the smoker. It may still be cooking —
            check it physically before trusting Home Assistant.
```

Gate anything safety-adjacent on `binary_sensor.*_stale_data` being `off`, and never on `binary_sensor.*_power` alone.

## Temperatures show in the grill's own unit

The grill reports whether it is set to Fahrenheit or Celsius, and entities are registered with that as the **suggested** display unit. On a metric Home Assistant a Fahrenheit grill therefore still reads in °F, matching the appliance's own panel, rather than being silently converted.

To display the other unit, override it per entity in *Settings → Entities → (entity) → Unit of Measurement*. That choice is remembered.

If you previously built Fahrenheit template sensors to work around this, you can delete them.

## Charting the current cook

`sensor.<grill>_current_cook_history` holds the cook so far. Its state is the point count; the data is in attributes:

```yaml
cook_start: "2026-08-13T09:12:04+00:00"   # ISO-8601
cook_end: null                             # set when the grill powers off
active: true
unit: "°F"
series:
  grill:  [[0, 78.0], [63, 141.5], ...]    # [seconds since cook_start, value]
  target: [[0, 250.0], ...]
  probe1: [[0, 41.0], ...]
```

Points are stored as offsets rather than timestamps to keep the payload small — the whole series is capped at 150 points per channel and decimates itself as a cook runs long, so it stays comfortably inside Recorder's 16 KiB attribute ceiling.

A cook starts when the grill powers on and ends when it powers off. **While the shadow is stale, nothing is recorded** — a dropout shows as a gap rather than a flat line extended through a period nobody was actually measuring.

An example [apexcharts-card](https://github.com/RomRider/apexcharts-card) config is in [`docs/dashboard.md`](docs/dashboard.md).

## Fetching a whole cook

Masterbuilt's cloud keeps every cook at roughly 10-second resolution, server-side, and it survives Home Assistant restarts. That is far too much data to poll — an overnight cook is a few thousand samples and megabytes of JSON — so it is exposed as a service that returns a response rather than as entity state. Nothing is fetched until you ask, and nothing lands in Recorder.

```yaml
action: masterbuilt_gravity.get_cook_history
data:
  device_id: <your grill>
  max_points: 300      # thins each series; omit session_id for the latest cook
response_variable: cook
```

Returns:

```yaml
session:
  id: 8637642
  state: INACTIVE
  start: 1786561848     # unix seconds
  end: 1786592241
  snapshot_count: 2633
unit: "°F"
series:
  grill:  [[0, 83.0], [89, 96.0], ...]   # [seconds since session start, value]
  target: [[0, 225.0], ...]
  probe1: [[0, 76.0], ...]
```

Omit `session_id` for the most recent cook. Pass one from a previous response to fetch an older one — completed cooks stay available, snapshots included.

A 2633-sample cook thinned to 300 points per series is about 14 KB.

### Keeping Recorder tidy

The history sensor's attributes are bounded but not tiny. If you don't need them in long-term history:

```yaml
recorder:
  exclude:
    entities:
      - sensor.smoker_current_cook_history
```

## Writing setpoints

Not supported, and not for lack of trying. Masterbuilt's cloud runs two planes: the CAS REST API this integration reads from, and an AWS IoT device shadow. **All writes go over MQTT to the shadow**, authenticated with a per-install X.509 certificate the app provisions for itself. There is no setpoint route on the REST API.

The transport is understood; the exact `desired` document for a setpoint change is not yet confirmed, and shipping a guess that could move a live fire is not worth it. Contributions welcome if you capture one.

## Credits

- [Martin Hruška](https://github.com/hruskin) — original integration and CAS API reverse-engineering.
- Cloud architecture (two-plane model, session/history routes, IoT provisioning flow) mapped from static analysis of the Masterbuilt Android app.

## License

MIT — see [LICENSE](LICENSE).
