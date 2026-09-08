# Masterbuilt Gravity (Unofficial) — Home Assistant

Home Assistant integration for **Masterbuilt Gravity Series** charcoal grills and smokers and the **Kamado Joe Konnected Joe**, reading live cook telemetry from Middleby's cloud.

> **Fork notice.** This is a fork of [hruskin/masterbuilt-gravity-ha](https://github.com/hruskin/masterbuilt-gravity-ha) by Martin Hruška, who did the original reverse-engineering of the CAS cloud API and wrote the integration this builds on. MIT-licensed, and that license and copyright are retained. This fork diverges: it adds device selection and reauth to onboarding, fixes Fahrenheit display, adds staleness diagnostics and in-integration cook history, and — as of v0.6.0 — **settable grill and probe temperatures**. v0.7.0 adds Kamado Joe support. Issues here, not upstream.

Not affiliated with, endorsed by, or supported by Masterbuilt, Kamado Joe, or Middleby.

## Supported grills

| Brand | Status |
|---|---|
| **Masterbuilt Gravity Series** | Telemetry and setpoint control |
| **Kamado Joe Konnected Joe** | Telemetry and setpoint control |

Middleby serves both brands from one backend behind per-brand hostnames — same app key, same routes, same shadow format — so everything the integration *reads* behaves identically once you pick the brand during setup.

Writes take a different path: setpoints go to AWS IoT rather than the REST API, and reach Masterbuilt-owned endpoints whichever brand you choose. That works on both, confirmed on a Konnected Joe (model `C:G:018:1:D`) in [#2](https://github.com/lucvan/masterbuilt-gravity-ha/issues/2).

The two grills differ in what they expose, and the integration adjusts: a Konnected Joe has no hopper, so its **Hopper door** sensor is not created at all, and the `heat.t2.intensity` value it shares with the Gravity Series is named **Fan speed** there, which is what it actually drives.

## What you get

| | |
|---|---|
| **Temperatures** | Grill, target setpoint, and up to 4 meat probes with their targets |
| **State** | Power, heating, engaged, at-temperature, hopper door, lid, errors |
| **Per-probe** | "At temperature" sensor per probe, with a tolerance offset matching the app's own notification behaviour |
| **Diagnostics** | Signal strength, last reported, data age, **stale data** |
| **History** | A downsampled current-cook series for charting, maintained in the integration |
| **Control** | Settable **grill** and **probe** targets — see [Setting temperatures](#setting-temperatures) |

## Install

**HACS** → ⋮ → *Custom repositories* → add `https://github.com/lucvan/masterbuilt-gravity-ha`, category **Integration** → install → restart Home Assistant.

Then *Settings → Devices & Services → Add Integration → Masterbuilt Gravity*.

Requires Home Assistant 2024.11 or newer.

## Onboarding

1. **Pick your brand.** Masterbuilt or Kamado Joe — this selects which cloud host to sign in to, and nothing else.
2. **Sign in** with the same email and password you use in that brand's mobile app. They are stored in Home Assistant's config entry and sent only to that manufacturer's own cloud.
3. **Pick your grills.** If the account has more than one paired grill you choose which to add; a single grill is added automatically.
4. If the cloud later rejects the password, Home Assistant raises its normal **reauthentication** prompt instead of silently failing — re-enter the password and it reconnects.

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

**Chart the temperature sensors directly.** Home Assistant's Recorder already stores every one of them, so there is no history sensor here duplicating that — a sensor holding a growing series in its attributes would rewrite that entire payload into the `states` table on every poll, which is how attribute-size problems start.

What Recorder cannot tell you is where one cook ends and the next begins, so that part *is* an entity:

| Entity | |
|---|---|
| `sensor.<grill>_cook_start` | When the current cook began, from the cloud's own session record. Unknown when not cooking. Attributes carry the session `id` and snapshot count |
| `sensor.<grill>_last_cook` | The previous completed cook, with a decimated series in attributes for charting. Fetched once when a cook ends, not polled |

For a ready-made UI, install the companion **[Masterbuilt Cook Card](https://github.com/lucvan/masterbuilt-cook-card)** — it sizes the live chart to the cook, browses past cooks, and surfaces the staleness warning, with no YAML beyond a device id.

To build your own instead, [`docs/dashboard.md`](docs/dashboard.md) has example configs using the built-in `history-graph` and [apexcharts-card](https://github.com/RomRider/apexcharts-card).

## Fetching any past cook

Masterbuilt's cloud keeps every cook the grill has ever run, at roughly 10-second resolution — including cooks from long before Home Assistant knew the grill existed. Two actions expose that, both returning responses rather than writing to entity state.

**Find a cook:**

```yaml
action: masterbuilt_gravity.list_cooks
data:
  device_id: <your grill>
response_variable: cooks
```

Returns `id`, `state`, `start`, `end` and `snapshot_count` per cook, newest first. Cheap — no sample data.

**Fetch one:**

```yaml
action: masterbuilt_gravity.get_cook_history
data:
  device_id: <your grill>
  session_id: 8637642    # omit for the most recent cook
  max_points: 300
response_variable: cook
```

```yaml
session: {id: 8637642, state: INACTIVE, start: 1786561848, end: 1786592241, snapshot_count: 2633}
source: recorder         # or "cloud"
unit: "°F"
series:
  grill:  [[0, 83.0], [89, 96.0], ...]   # [seconds since cook start, value]
  target: [[0, 225.0], ...]
  probe1: [[0, 76.0], ...]
```

### Where the data comes from

By default (`source: auto`) this **reads your own Recorder history when it covers the cook**, and only falls back to Masterbuilt's cloud when it does not — a cook that predates the integration, one that ran while Home Assistant was down, or one Recorder has since purged. The `source` field in the response tells you which was used.

Force it either way with `source: local` or `source: cloud`. `local` never leaves Home Assistant; `cloud` always gives the finer ~10-second sampling.

An 8-hour, 2633-sample cook thinned to 300 points per series is about 14 KB, against roughly 1.3 MB raw.

### Keeping Recorder tidy

`sensor.<grill>_last_cook` carries a series in its attributes. It only changes once per cook, so it costs one row rather than one per poll, but if you don't want it in long-term history:

```yaml
recorder:
  exclude:
    entities:
      - sensor.smoker_last_cook
```

Excluding it does not affect the `get_cook_history` action.

## Setting temperatures

Grill and probe targets are settable:

| Entity | |
|---|---|
| `number.<grill>_grill_target` | The grill chamber setpoint |
| `number.<grill>_probe_N_target` | Each probe's target, shown only while that probe is plugged in |

Both are `number` entities in the grill's own unit, so they read and set in °F on a Fahrenheit grill without conversion. Set them from the UI, a script, or `number.set_value`.

### How it works, and why it's slower than a read

The CAS REST API this integration reads from has **no write route**. Control goes to the grill's AWS IoT device shadow over MQTT, authenticated with a per-install X.509 certificate. On the first write the integration mints that certificate (Cognito → `CreateKeysAndCertificate` → server-side policy attach), caches it, and reuses it thereafter — so the first setpoint change after setup takes a few seconds longer while the certificate is provisioned.

The grill's controller applies the change within a few seconds; the entity updates on the next poll. The setpoint is clamped by the controller to its own limits (150–700 °F chamber).

This write path is deliberately **not** brand-routed the way reads are: the certificate is minted against Masterbuilt's AWS account and the shadow published to Masterbuilt's IoT endpoint whichever brand you picked at setup. Middleby appears to run one control plane behind the per-brand REST hosts, and it is confirmed working on both a Masterbuilt Gravity and a Kamado Joe Konnected Joe.

The chamber range comes from `heat.t2.min`/`max` in the grill's own shadow, so each grill offers exactly what its app does — up to 700 °F on a Gravity Series, and 371 °C on a Konnected Joe set to Celsius.

**Power on/off is deliberately not implemented.** That command is unverified, and turning a live fire on or off from a guessed payload is not a risk this integration takes.

### Dependencies

Writes pull in `boto3`, `pycognito`, and `paho-mqtt` (declared in the manifest; Home Assistant installs them automatically). They are used only for the control path — reads need none of them.

## Reporting a problem

*Settings → Devices & Services → Masterbuilt Gravity → ⋮ → Download diagnostics* dumps the raw shadow document your grill last published, plus the entry's options and cook state. Email, password, MAC and similar identifiers are redacted; the telemetry is not, because that is the part worth looking at.

Attach that to an issue. Most questions here come down to which keys a particular model reports and what unit their values are in, and the dump answers both without a round of guessing.

## Credits

- [Martin Hruška](https://github.com/hruskin) — original integration and CAS API reverse-engineering.
- Cloud architecture (two-plane model, session/history routes, IoT provisioning flow) mapped from static analysis of the Masterbuilt Android app.

## License

MIT — see [LICENSE](LICENSE).
