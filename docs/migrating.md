# Migrating from a helper-based setup

This fork absorbs a few things people commonly bolted on around the upstream integration. If you built any of them, you can now delete them.

## Fahrenheit template sensors

**Delete them.** Entities are now registered with the grill's own unit as the suggested display unit, so a Fahrenheit grill reads °F even on a metric Home Assistant.

If you had a package like `smoker_dashboard_fahrenheit.yaml` creating `sensor.*_fahrenheit` template entities plus an `input_select` to switch units:

1. Point any dashboard cards at the plain entities (`sensor.smoker_grill_temperature`, not `sensor.smoker_grill_temperature_fahrenheit`).
2. Remove the package file and reload YAML.
3. Delete the now-orphaned `input_select` helpers from *Settings → Devices & Services → Helpers*.

To display a unit other than the grill's, override it per entity in *Settings → Entities → (entity) → Unit of Measurement*. It persists, and no template sensors are involved.

## Cook-history scraping scripts

**Delete them, and don't replace them.** If you ran a cron job that queried Recorder over the REST API and POSTed a synthetic history sensor back, the answer is not a better version of that sensor — it is to chart the temperature entities directly. Recorder already has them; that job was reading data out of the database and writing a copy back in.

1. Stop and remove the cron job, and its long-lived access token if it existed only for this.
2. Repoint chart cards at the plain sensors — `sensor.smoker_grill_temperature` and friends — with no `data_generator`. See [`dashboard.md`](dashboard.md).
3. Delete the `sensor.smoker_current_cook_history` entity the old script created. It will linger as unavailable; it disappears on restart once nothing recreates it.

What you get instead:

- `sensor.<grill>_cook_start` — when the current cook began, from the cloud's own session record. This is the one thing Recorder genuinely could not tell you, and it is what the old script was really computing.
- `sensor.<grill>_last_cook` — the previous completed cook with a decimated series, fetched once when it ends.
- `masterbuilt_gravity.get_cook_history` / `list_cooks` — any cook ever, including ones from before Home Assistant knew about the grill. Reads your Recorder when it covers the cook, the cloud when it doesn't.

Two behaviours deliberately changed:

- **Values stay in the grill's own unit**, not force-converted to Fahrenheit.
- **Gaps stay gaps.** The old script extended the last known value forward for as long as it ran, which turned a wedged WiFi module into a chart line advancing through a period nobody was measuring.

## Dashboard span rewriting

If you ran a script that periodically rewrote your Lovelace config to set `apex_config.xaxis.min`/`max` to the current cook window: **stop it and remove it.**

There is no in-integration replacement, by choice — nothing here will edit your stored dashboard. Use a fixed `graph_span` and the chart toolbar's zoom. See the [dashboard notes](dashboard.md#about-the-time-window).

## What you gain

- Reauthentication prompt instead of silent failure when the password changes.
- Device selection if the account has several grills.
- `binary_sensor.<grill>_stale_data`, which catches the grill-still-cooking-but-offline case that every other entity misreports as "off".
- Polling interval and staleness threshold configurable from the UI.
