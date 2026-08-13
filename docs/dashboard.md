# Example dashboard

> If you just want a working UI, install the companion [Masterbuilt Cook Card](https://github.com/lucvan/masterbuilt-cook-card) instead — it does all of this from a single device id, and can chart cooks that Recorder never saw. What follows is for building your own.

[apexcharts-card](https://github.com/RomRider/apexcharts-card) configs for a live cook and a finished one, plus a staleness banner. Install apexcharts-card from HACS (Frontend) first.

Replace `smoker` in the entity IDs with your own device's slug.

## Simplest option: the built-in graph

No custom cards needed. `hours_to_show` is fixed and always ends at now, so it cannot show a past cook — but for watching one in progress it is perfectly good:

```yaml
type: history-graph
hours_to_show: 12
fit_y_data: true
entities:
  - sensor.smoker_grill_temperature
  - sensor.smoker_target_temperature
  - sensor.smoker_probe_1_temperature
```

## Live cook

Reads the temperature sensors straight from Recorder — no helper entities, no templates, no history sensor in between.

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
  extend_to: now
  fill_raw: last
  group_by:
    duration: 1min
    func: last
  show:
    legend_value: true
series:
  - entity: sensor.smoker_grill_temperature
    name: Grill
    color: "#ff3b30"
    stroke_width: 5
  - entity: sensor.smoker_target_temperature
    name: Target
    color: "#ffcc00"
    stroke_width: 2
  - entity: sensor.smoker_probe_1_temperature
    name: Probe 1
    color: "#32ade6"
    stroke_width: 4
apex_config: &chart_style
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
```

Three series only — grill, target, probe 1 — because overlapping probe lines make the chart unreadable at a glance. Add more blocks for probes 2–4 if you want them. Hover and active fading are disabled so overlapping flat lines stay solid.

### About the time window

`graph_span: 12h` is a fixed window; the chart does not auto-zoom to the cook.

`sensor.smoker_cook_start` holds when the current cook began, so you can see the elapsed time on a card, but apexcharts-card cannot bind `graph_span` to an entity. If you previously ran a script that rewrote the stored dashboard config to set `xaxis.min`/`max` on a timer, that is deliberately not reproduced — set `graph_span` to suit your typical cook and use the chart toolbar to zoom.

**Do not wrap this card in `config-template-card`** to make the span dynamic. It is a known way to make the chart disappear entirely.

## Last completed cook

`sensor.smoker_last_cook` carries the finished cook as offset series, so this chart needs no history query at all. Offsets are seconds from the cook's start, which makes the x-axis elapsed time — usually more useful than wall-clock for comparing cooks.

```yaml
type: custom:apexcharts-card
graph_span: 24h
header:
  show: true
  title: Last cook
series:
  - entity: sensor.smoker_last_cook
    name: Grill
    color: "#ff3b30"
    stroke_width: 5
    data_generator: |
      const s = (entity.attributes.series || {}).grill || [];
      const t0 = (entity.attributes.start || 0) * 1000;
      return s.map(p => [t0 + p[0] * 1000, p[1]]);
  - entity: sensor.smoker_last_cook
    name: Target
    color: "#ffcc00"
    stroke_width: 2
    data_generator: |
      const s = (entity.attributes.series || {}).target || [];
      const t0 = (entity.attributes.start || 0) * 1000;
      return s.map(p => [t0 + p[0] * 1000, p[1]]);
  - entity: sensor.smoker_last_cook
    name: Probe 1
    color: "#32ade6"
    stroke_width: 4
    data_generator: |
      const s = (entity.attributes.series || {}).probe1 || [];
      const t0 = (entity.attributes.start || 0) * 1000;
      return s.map(p => [t0 + p[0] * 1000, p[1]]);
apex_config: *chart_style
```

## Older cooks

Anything further back is fetched on demand, including cooks from before Home Assistant knew about the grill. There is no card for this — a response-only action can't be called from Lovelace — so use *Developer Tools → Actions*, or a script.

```yaml
script:
  export_cook:
    sequence:
      - action: masterbuilt_gravity.list_cooks
        data:
          device_id: !input grill
        response_variable: cooks
      - action: masterbuilt_gravity.get_cook_history
        data:
          device_id: !input grill
          session_id: "{{ cooks.cooks[1].id }}"   # [0] is the newest
          max_points: 500
        response_variable: cook
      - action: notify.persistent_notification
        data:
          message: >-
            Cook {{ cook.session.id }} from {{ cook.source }}:
            {{ cook.series.grill | length }} points,
            peak {{ cook.series.grill | map(attribute=1) | max }}{{ cook.unit }}
```

`source` in the response tells you whether it came from your own Recorder or from Masterbuilt's cloud.

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
  - entity: sensor.smoker_cook_start
    name: Cooking since
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
