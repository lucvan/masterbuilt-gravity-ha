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

**Delete them.** `sensor.<grill>_current_cook_history` is maintained by the integration itself.

If you ran a cron job that queried Recorder over the REST API and POSTed a synthetic history sensor back:

1. Stop and remove the cron job.
2. Delete the script and its long-lived access token if it existed only for this.
3. Repoint chart cards at `sensor.<grill>_current_cook_history` and update the `data_generator` — the attribute format changed. Points are now `[seconds_since_cook_start, value]` rather than `[iso_timestamp, value]`, so the conversion is:

   ```js
   const t0 = new Date(entity.attributes.cook_start).getTime();
   return ((entity.attributes.series || {}).grill || []).map(p => [t0 + p[0] * 1000, p[1]]);
   ```

   See [`dashboard.md`](dashboard.md) for the full card.
4. Remove the `sensor.smoker_current_cook_history` entity created by the old REST POST — it is a different entity from the integration's and will linger as unavailable. *Developer Tools → States* will show the stale one; it disappears on restart since nothing recreates it.

Two behaviours deliberately changed:

- **Values are recorded in the grill's own unit**, not force-converted to Fahrenheit. The `unit` attribute tells you which.
- **Nothing is recorded while the shadow is stale.** The old script extended the last known value forward for as long as it ran, which turned a wedged WiFi module into a chart line that kept advancing through a period nobody was measuring. Dropouts are now visible as gaps.

## Dashboard span rewriting

If you ran a script that periodically rewrote your Lovelace config to set `apex_config.xaxis.min`/`max` to the current cook window: **stop it and remove it.**

There is no in-integration replacement, by choice — nothing here will edit your stored dashboard. Use a fixed `graph_span` and the chart toolbar's zoom. See the [dashboard notes](dashboard.md#about-the-time-window).

## What you gain

- Reauthentication prompt instead of silent failure when the password changes.
- Device selection if the account has several grills.
- `binary_sensor.<grill>_stale_data`, which catches the grill-still-cooking-but-offline case that every other entity misreports as "off".
- Polling interval and staleness threshold configurable from the UI.
