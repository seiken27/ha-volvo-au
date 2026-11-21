# Charging and climate Live Activities / notifications

The integration doesn't send notifications itself. It exposes the sensors, and
the Home Assistant Companion app turns them into a **Live Activity** (iOS) or
**live update** (Android): a persistent, silently-refreshing card on the lock
screen with a progress bar and a countdown.

These two example automations give you:

| Activity | Shows | Ends |
| --- | --- | --- |
| **Charging** | State of charge as a progress bar, charge power in the title, a countdown to the estimated finish, the charge target | "Charged" (audible) when finished, "Paused" (audible) if charging stops while still plugged in; cleared on unplug, or 30 min after "Charged" |
| **Climate** (preconditioning) | A countdown of the climatisation time left, what started it, outside temperature | Cleared immediately if stopped early, otherwise "Done" and cleared 5 min later, or as soon as a door opens |

> [!NOTE]
> The same payload works on **both iOS and Android**: the Companion app on each
> platform understands `live_update: true`, `tag`, `progress`, `chronometer`,
> `when` and `critical_text`. A few things are platform specific, see
> [Platform notes](#platform-notes).

## Requirements

- **iOS:** iOS 17.2 or later (iPhone), Home Assistant Core 2026.7.0 or later,
  and Live Activities allowed under *iOS Settings → Home Assistant*. The first
  activity asks you to accept a one-time privacy disclosure. The app must be
  able to reach Home Assistant to sync its tokens, so away from home you need a
  remote connection. Live Activities are not available on iPad.
- **Android:** Android 16 or later. On Samsung you may need *Live notifications
  for all apps* (developer options) for the status-bar chip to appear.
- A `notify.mobile_app_<device>` service for each phone you want to notify
  (*Developer tools → Actions*, search `notify.mobile_app`).
- These entities from the integration (your entity IDs will contain your own
  car's name and registration, shown here as `xc40_abc123`):

| Entity | Used for |
| --- | --- |
| `sensor.xc40_abc123_charging_status` | `charging`, `idle`, `done`, ... |
| `sensor.xc40_abc123_charger_connection` | `connected` / `disconnected` |
| `sensor.xc40_abc123_battery` | State of charge (%) |
| `sensor.xc40_abc123_time_to_full` | Minutes until full (the target, not necessarily 100%) |
| `sensor.xc40_abc123_charging_power` | Watts |
| `number.xc40_abc123_target_soc` | Charge limit (%) |
| `switch.xc40_abc123_climatization` | Attributes `runtime_left_minutes` and `start_reason` |
| `sensor.xc40_abc123_exterior_temperature` | Optional, outside temperature |
| `binary_sensor.xc40_abc123_{front,rear}_{left,right}_door` | Optional, end the climate activity when a door opens |

> [!IMPORTANT]
> Find-and-replace `xc40_abc123` with your own prefix, and set `devices` in each
> automation to your own notify services.

## How it works

- **One tag per activity** (`volvo-charging`, `volvo-climate`). Every update
  with the same tag replaces the previous one, so there is only ever one card.
  Sending `message: clear_notification` with the same tag removes it.
- **`chronometer` + `when` + `when_relative: false`** draw the countdown. `when`
  is the finish time as a Unix timestamp, so the clock keeps counting down on
  the phone between updates without any HA traffic.
- **`silent: true`** on every refresh so the lock screen updates quietly; only
  the final states (Charged, Paused) use `silent: false` to make a sound.
- **Debounced.** The car flaps between `charging`, `idle` and `done` for a
  while around the end of a session, so "finished" and "stopped" only fire
  after the state has been stable for 2 minutes.
- **5 second delay** after a trigger so the state of charge and finish time
  (which update from separate API fields) have settled before reading them.
- **`mode: restart`** so a fresh trigger cancels any in-flight run.

## Charging

```yaml
alias: Live Activity - Volvo charging
description: >-
  Live activity (tag volvo-charging). Refreshes on SoC/ETA changes and every
  5 minutes. Refreshes are silent; Charged and Paused are audible, shown after
  2 minutes stable. Keeps Charged for 30 minutes, clears on unplug.
mode: restart
triggers:
  - trigger: state
    entity_id: sensor.xc40_abc123_charging_status
    to: charging
    id: start
  - trigger: state
    entity_id: sensor.xc40_abc123_charging_status
    from: charging
    to: idle
    for: "00:02:00"
    id: stopped
  - trigger: state
    entity_id: sensor.xc40_abc123_charging_status
    from: charging
    to: done
    for: "00:02:00"
    id: finished
  - trigger: state
    entity_id: sensor.xc40_abc123_charging_status
    to: done
    for: "00:30:00"
    id: expire
  - trigger: state
    entity_id: sensor.xc40_abc123_charger_connection
    to: disconnected
    id: unplugged
  - trigger: time_pattern
    minutes: /5
    id: update
  - trigger: state
    entity_id:
      - sensor.xc40_abc123_battery
      - sensor.xc40_abc123_time_to_full
    id: soc
variables:
  # Every phone that should get the activity.
  devices:
    - notify.mobile_app_my_phone
    - notify.mobile_app_my_other_phone
  # Dashboard opened when the activity is tapped.
  url: /lovelace/volvo
  accent: "#3B7EA1"
actions:
  - choose:
      - alias: Clear when unplugged, or 30 min after charged
        conditions:
          - condition: trigger
            id: [unplugged, expire]
        sequence:
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    message: clear_notification
                    data:
                      tag: volvo-charging

      - alias: Charged (finished or target reached, still plugged in)
        conditions:
          - condition: trigger
            id: finished
          - condition: state
            entity_id: sensor.xc40_abc123_charging_status
            state: done
        sequence:
          - variables:
              soc: "{{ states('sensor.xc40_abc123_battery') | float(0) | round(0) | int }}"
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    title: Volvo
                    message: "Charged · {{ soc }}%"
                    data:
                      tag: volvo-charging
                      live_update: true
                      critical_text: "{{ soc }}%"
                      progress: "{{ soc }}"
                      progress_max: 100
                      notification_icon: mdi:battery-check
                      notification_icon_color: "{{ accent }}"
                      color: "{{ accent }}"
                      progress_bar_color: "{{ accent }}"
                      url: "{{ url }}"
                      silent: false

      - alias: Paused (stopped but still plugged in)
        conditions:
          - condition: trigger
            id: stopped
          - condition: state
            entity_id: sensor.xc40_abc123_charging_status
            state: idle
          - condition: state
            entity_id: sensor.xc40_abc123_charger_connection
            state: connected
        sequence:
          - variables:
              soc: "{{ states('sensor.xc40_abc123_battery') | float(0) | round(0) | int }}"
              target: "{{ states('number.xc40_abc123_target_soc') | float(100) | round(0) | int }}"
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    title: Volvo
                    message: "Paused · plugged in · target {{ target }}%"
                    data:
                      tag: volvo-charging
                      live_update: true
                      critical_text: "{{ soc }}%"
                      progress: "{{ soc }}"
                      progress_max: 100
                      notification_icon: mdi:ev-plug-type2
                      notification_icon_color: "#ECECE7"
                      color: "#ECECE7"
                      progress_bar_color: "#ECECE7"
                      url: "{{ url }}"
                      silent: false
    default:
      - alias: Let SoC, ETA and status settle together
        delay: "00:00:05"
      - variables:
          soc: "{{ states('sensor.xc40_abc123_battery') | float(0) | round(0) | int }}"
          eta: "{{ states('sensor.xc40_abc123_time_to_full') | int(0) }}"
          finish: "{{ ((now().timestamp() + eta * 60) | int) if eta > 0 else 0 }}"
          kw: "{{ (states('sensor.xc40_abc123_charging_power') | float(0) / 1000) | round(1) }}"
          target: "{{ states('number.xc40_abc123_target_soc') | float(100) | round(0) | int }}"
      - if:
          - condition: state
            entity_id: sensor.xc40_abc123_charging_status
            state: charging
        then:
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    title: "Volvo · {{ kw }} kW"
                    message: "Charging · target {{ target }}%"
                    data:
                      tag: volvo-charging
                      live_update: true
                      critical_text: "{{ soc }}%"
                      progress: "{{ soc }}"
                      progress_max: 100
                      chronometer: "{{ eta > 0 }}"
                      when: "{{ finish }}"
                      when_relative: false
                      notification_icon: mdi:ev-station
                      notification_icon_color: "{{ accent }}"
                      color: "{{ accent }}"
                      progress_bar_color: "{{ accent }}"
                      url: "{{ url }}"
                      silent: true
      - alias: Refresh "Charged" if the SoC arrives late
        if:
          - condition: state
            entity_id: sensor.xc40_abc123_charging_status
            state: done
          - condition: trigger
            id: soc
        then:
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    title: Volvo
                    message: "Charged · {{ soc }}%"
                    data:
                      tag: volvo-charging
                      live_update: true
                      critical_text: "{{ soc }}%"
                      progress: "{{ soc }}"
                      progress_max: 100
                      notification_icon: mdi:battery-check
                      notification_icon_color: "{{ accent }}"
                      color: "{{ accent }}"
                      progress_bar_color: "{{ accent }}"
                      url: "{{ url }}"
                      silent: true
```

## Climate (preconditioning)

The countdown uses the `runtime_left_minutes` attribute of the climatization
switch, which the car reports (and which is shorter when started from a timer
in the car). If the car reports none, it falls back to the 30 minute default
counted from when climate started. `start_reason` (for example
*Remote*, *Timer*) is shown in the message when the car provides it.

```yaml
alias: Live Activity - Volvo climate
description: >-
  Live activity (tag volvo-climate) while climatisation is running, however it
  was started. Silent updates. Clears at once if stopped before its 30 minutes
  are up, otherwise shows Done and clears 5 minutes after it stops, or as soon
  as any door is opened.
mode: restart
triggers:
  - trigger: state
    entity_id: switch.xc40_abc123_climatization
    to: "on"
    id: start
  - trigger: state
    entity_id: switch.xc40_abc123_climatization
    to: "off"
    id: stop
  - trigger: state
    entity_id: switch.xc40_abc123_climatization
    to: "off"
    for: "00:05:00"
    id: clear
  - trigger: state
    entity_id:
      - binary_sensor.xc40_abc123_front_left_door
      - binary_sensor.xc40_abc123_front_right_door
      - binary_sensor.xc40_abc123_rear_left_door
      - binary_sensor.xc40_abc123_rear_right_door
    from: "off"
    to: "on"
    id: clear
  - trigger: time_pattern
    minutes: /5
    id: update
variables:
  devices:
    - notify.mobile_app_my_phone
    - notify.mobile_app_my_other_phone
  url: /lovelace/volvo
  accent: "#3B7EA1"
actions:
  - choose:
      - alias: Clear 5 min after it stops, or as soon as a door is opened
        conditions:
          - condition: trigger
            id: clear
        sequence:
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    message: clear_notification
                    data:
                      tag: volvo-climate

      - alias: Stopped early, so clear at once instead of showing Done
        conditions:
          - condition: trigger
            id: stop
          - condition: template
            value_template: >-
              {{ (now() - trigger.from_state.last_changed).total_seconds() < 28 * 60 }}
        sequence:
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    message: clear_notification
                    data:
                      tag: volvo-climate

      - alias: Climate finished
        conditions:
          - condition: trigger
            id: stop
        sequence:
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    title: Volvo · Climate
                    message: Climate finished
                    data:
                      tag: volvo-climate
                      live_update: true
                      critical_text: Done
                      notification_icon: mdi:check-circle
                      notification_icon_color: "{{ accent }}"
                      color: "{{ accent }}"
                      url: "{{ url }}"
                      silent: true
    default:
      - alias: Let the runtime-left attribute update with the status
        delay: "00:00:05"
      - alias: Only while running and no door is open
        if:
          - condition: state
            entity_id: switch.xc40_abc123_climatization
            state: "on"
          - condition: state
            entity_id:
              - binary_sensor.xc40_abc123_front_left_door
              - binary_sensor.xc40_abc123_front_right_door
              - binary_sensor.xc40_abc123_rear_left_door
              - binary_sensor.xc40_abc123_rear_right_door
            state: "off"
        then:
          - variables:
              mins_attr: "{{ state_attr('switch.xc40_abc123_climatization', 'runtime_left_minutes') | int(0) }}"
              elapsed: "{{ ((now() - states.switch.xc40_abc123_climatization.last_changed).total_seconds() / 60) | int }}"
              mins: "{{ mins_attr if mins_attr > 0 else ([30 - elapsed, 0] | max) }}"
              finish: "{{ (now().timestamp() + mins * 60) | int }}"
              source: >-
                {% set r = (state_attr('switch.xc40_abc123_climatization', 'start_reason') | string) | replace('START_REASON_', '') %}
                {{ '' if r in ['UNSPECIFIED', '', 'None'] else r | replace('_', ' ') | lower | capitalize }}
              outside: "{{ states('sensor.xc40_abc123_exterior_temperature') | float(none) }}"
          - repeat:
              for_each: "{{ devices }}"
              sequence:
                - action: "{{ repeat.item }}"
                  data:
                    title: Volvo · Climate
                    message: >-
                      {{ ['Climatization', source,
                          (outside | round(0) | int ~ '°C outside') if outside is not none else '']
                         | select | join(' · ') }}
                    data:
                      tag: volvo-climate
                      live_update: true
                      critical_text: A/C
                      chronometer: "{{ mins > 0 }}"
                      when: "{{ finish }}"
                      when_relative: false
                      notification_icon: mdi:fan
                      notification_icon_color: "{{ accent }}"
                      color: "{{ accent }}"
                      progress_bar_color: "{{ accent }}"
                      url: "{{ url }}"
                      silent: true
```

## Platform notes

Based on the [Companion app Live Activities docs](https://companion.home-assistant.io/docs/notifications/live-activities/).

> [!IMPORTANT]
> **`title` is required and fixed.** iOS sets the title when the activity starts
> and later updates can't change it, which is why the charging title (with the
> kW figure) is only accurate at the start. On Android, an update without a
> `title` is shown as a plain banner instead of a live update.

- **Colours:** `notification_icon_color` is the iOS icon colour and `color` is
  the Android one, so both are sent and each platform ignores the other's.
- **iOS only:** `silent`, `url` (resend it on every update, it isn't sticky),
  `progress_bar_color`, `background_color`, `text_color`.
- **`critical_text`:** the Dynamic Island compact text on iOS and the status-bar
  chip on Android (replaced by the timer when `chronometer` is on).
- **Progress bar** only shows when both `progress` and `progress_max` are set.
- **Expiry:** iOS ends an activity after up to 8 hours (an Apple limit), which
  covers a charge or preconditioning run. A very long charge restarts on the
  next update once the state changes.
- **Update rate:** iOS throttles frequent updates and may delay or drop them.
  That's why these automations refresh every 5 minutes and rely on the
  `chronometer` countdown rather than per-second updates.
- **Only one platform:** list only that platform's notify services in `devices`.
- **Ending an activity** manually: send `message: clear_notification` with the
  tag, or on iOS use *Settings → Live Activities* in the app.

## Troubleshooting

> [!WARNING]
> **A new activity won't start on iOS after lots of testing.** iOS limits how
> many can be started in a short time, with no error logged. Wait a while, send
> `clear_notification` for the tag first, or use a different tag.

- **Nothing appears:** check notification permission, that the device's notify
  service name is right, and (iOS) that Live Activities are enabled for the app.
- **Countdown missing or wrong:** `time_to_full` is `unknown` when the car isn't
  actively charging, so `chronometer` is false and the card shows progress only.
- **Card stays after unplugging:** the tag in the clear action must match the
  tag used to create it.
- **Duplicate cards:** keep a single automation per tag; that is why the whole
  lifecycle (start, update, finish, clear) lives in one automation.
