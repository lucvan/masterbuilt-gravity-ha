# Example dashboard

An [apexcharts-card](https://github.com/RomRider/apexcharts-card) config for the current cook, plus a staleness banner. Install apexcharts-card from HACS (Frontend) first.

Replace `smoker` in the entity IDs with your own device's slug.

## Current-cook chart

Reads the integration's `current_cook_history` attributes directly, so it does not depend on Recorder retention and needs no helper entities, templates, or external scripts.

```yaml
type: custom:apexcharts-card
graph_span: 12h
span:
  end: minute
header:
  show: true
  show_states: true
  colorize_states: true
  title: Current cook
now:
  show: true
  label: Now
  color: "#8e8e93"
all_series_config:
  type: line
  curve: straight
  show:
    legend_value: true
series:
  - entity: sensor.smoker_current_cook_history
    name: Grill
    color: "#ff3b30"
    stroke_width: 5
    data_generator: |
      const a = entity.attributes;
      if (!a.cook_start) return [];
      const t0 = new Date(a.cook_start).getTime();
      return ((a.series || {}).grill || []).map(p => [t0 + p[0] * 1000, p[1]]);
  - entity: sensor.smoker_current_cook_history
    name: Target
    color: "#ffcc00"
    stroke_width: 2
    data_generator: |
      const a = entity.attributes;
      if (!a.cook_start) return [];
      const t0 = new Date(a.cook_start).getTime();
      return ((a.series || {}).target || []).map(p => [t0 + p[0] * 1000, p[1]]);
  - entity: sensor.smoker_current_cook_history
    name: Probe 1
    color: "#32ade6"
    stroke_width: 4
    data_generator: |
      const a = entity.attributes;
      if (!a.cook_start) return [];
      const t0 = new Date(a.cook_start).getTime();
      return ((a.series || {}).probe1 || []).map(p => [t0 + p[0] * 1000, p[1]]);
apex_config:
  chart:
    height: 420
    background: transparent
    animations:
      enabled: false
  dataLabels:
    enabled: false
  markers:
    size: 0
    hover:
      size: 5
  stroke:
    curve: straight
    lineCap: round
    width: [5, 2, 4]
    dashArray: [0, 8, 0]
  grid:
    borderColor: "#4a4a4a"
    strokeDashArray: 3
  legend:
    show: true
    position: top
  tooltip:
    shared: true
    intersect: false
    x:
      format: HH:mm
  states:
    normal: { filter: { type: none } }
    hover: { filter: { type: none } }
    active: { filter: { type: none } }
  yaxis:
    decimalsInFloat: 0
    title:
      text: Temperature
  xaxis:
    type: datetime
```

Three series only — grill, target, probe 1 — because overlapping probe lines make the chart unreadable at a glance. Probes 2–4 are in the series data if you want them; add another block. Hover and active fading are disabled so overlapping flat lines stay solid.

### About the time window

`graph_span: 12h` is a fixed window. The chart will not auto-zoom to the exact cook duration.

If you previously ran a script that rewrote `apex_config.xaxis.min`/`max` on a timer to achieve auto-zoom, that is deliberately not reproduced here — an integration should not be editing your stored dashboard config behind your back, and the whole point of this version is that it runs without any external helper. Set `graph_span` to whatever suits your typical cook and use the chart's own toolbar to zoom.

**Do not wrap this card in `config-template-card`** to make the span dynamic. It is a known way to make the chart disappear entirely.

## Stale-data banner

Shows only when the grill has stopped reporting — the failure mode where it may still be physically cooking while every other entity reads "off".

```yaml
type: conditional
conditions:
  - entity: binary_sensor.smoker_stale_data
    state: "on"
card:
  type: markdown
  content: >-
    ## ⚠️ No contact with the smoker

    Last reported {{ relative_time(states.sensor.smoker_last_reported.state | as_datetime) }} ago.

    The grill may still be cooking. Check it physically — do not trust the
    readings below.
```

## Status card

```yaml
type: entities
title: Smoker
entities:
  - entity: sensor.smoker_grill_temperature
    name: Grill
  - entity: sensor.smoker_target_temperature
    name: Target
  - entity: sensor.smoker_heat_intensity
    name: Heat
  - type: divider
  - entity: sensor.smoker_probe_1_temperature
    name: Probe 1
  - entity: sensor.smoker_probe_2_temperature
    name: Probe 2
  - entity: sensor.smoker_probe_3_temperature
    name: Probe 3
  - entity: sensor.smoker_probe_4_temperature
    name: Probe 4
  - type: divider
  - entity: binary_sensor.smoker_power
    name: Power
  - entity: binary_sensor.smoker_at_temperature
    name: At temperature
  - entity: binary_sensor.smoker_hopper_door
    name: Hopper door
  - entity: binary_sensor.smoker_stale_data
    name: Stale data
```

Probe entities become unavailable when the probe is unplugged, so they drop out of the card on their own.
