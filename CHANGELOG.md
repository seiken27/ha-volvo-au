# Changelog

All notable changes to Volvo C3 (domain `volvo_au`). This integration and [Polestar C3](https://github.com/BeauGiles/ha-polestar-c3) talk to the same Volvo C3 backend and were brought in line with each other on 18 and 19 November 2026, so most entries here have a matching entry in the Polestar changelog.

Dates are 2026.

## Unreleased

### Changed

- **README:** an unofficial and unsupported warning at the top and a fuller Disclaimer, pointing to Volvo's official Home Assistant integration for the regions its API supports.
- Documentation uses GitHub alert boxes (note, tip, important, warning) for the points that matter.

## 0.3.1 (21 November)

### Changed

- The `User-Agent` sent to Volvo is now `vca-ios/7.6.0` (was `vca-ios/6.9.0`), matching the current Volvo Cars app version.

## 0.3.0 (18 to 21 November)

### Read this before updating

- **Sensor states are now lowercase keys.** Charging status, Charger connection and Usage mode are enum sensors that report `charging`, `idle`, `scheduled`, `done`, `connected`, `disconnected`, `driving`, `engine_on` and so on, and the frontend shows translated text. Automations and dashboards that compare to `Charging`, `Idle` or `Connected` must use the lowercase keys. Charging status: `charging, idle, scheduled, discharging, error, smart_charging, done, smart_charging_paused`. Charger connection: `connected, disconnected, fault`. Usage mode: `abandoned, inactive, convenience, active, driving, engine_on, engine_off`. A value outside these lists shows as unknown and is logged once.
- **The Connection sensor state is short:** `Online`, `Offline: <short reason>` or `Unknown`. The longer explanation and `offline_since` are attributes.
- **Entities were removed or merged** (see Removed). Registry entries for them are deleted at setup.
- **Display names changed**, entity IDs did not. Cards that show the entity's own name will look different; cards with hand-typed names will not.
- **Why it is called "Volvo C3":** the integration uses Volvo's C3 services, the same ones the Volvo Cars app uses, not Volvo's official public API (which is not available in some countries, including Australia). Nothing in it is specific to one market, so it is named after the platform, not a country, in the same way as Polestar C3.
- The domain is still `volvo_au`. Changing it would orphan existing entries and entity IDs, so it is left for a later migration.

### Harmonised with Polestar C3

- Renamed to **Volvo C3** (display name, config flow titles, HACS name, README, class names). The integration works for more than Australian cars.
- Shared entity names in sentence case: Service required, 12V battery, Brake fluid, Washer fluid, Front left door and window (no hyphens), Charge port, Online, State of charge, Outside temperature, Time to full charge, Trip meter (auto) and (manual), Cabin air quality index, Flash lights, Honk and flash, Lock with reduced alarm, Charging amp limit, Target SoC, Climate target temperature, Front left seat heating (and the other seats and steering wheel), Climate, Air purification, Charging schedule with Charging schedule start and end, Boot lock. Tyre pressure sensors are "Front left tyre pressure" and so on.
- Icons copied from Polestar, including state-dependent icons on doors and problem sensors. Volvo no longer sets its own icon on the location tracker, Boot lock and Usage mode.
- Enum sensors with translated states, whole-number display by default for distance, range, battery, speed and times, entity categories (configuration and diagnostic), and the same attribute names for warnings.
- Same behaviour: Start charging is refused with a clear message when the car is not plugged in; Alarm triggered shows OK instead of Unknown when no alarm is reported; In use also follows "unavailable because the car is in use"; reconfigure flow; entity names come from translations; hidden entities for features the car says it lacks; diagnostics download.

### Added

- **Tyre warning:** one binary sensor. Attributes give each wheel's grade (`ok`, `very_low`, `low`, `high`) and pressure in kPa, only on cars that report individual pressures. Separate pressure sensors appear only on those cars.
- **Live Activities guide:** [docs/live-activities.md](docs/live-activities.md) has example automations for charging and climate Live Activities on iOS and Android.
- **Service required** attributes `service_urgency` (`almost_due`, `due`, `overdue`) and `service_reason` (`regular`, `engine_hours`, `distance`).
- **Command refusal reasons:** the car's reply to a command is now read. A refusal says why (privacy mode on, car being driven, another command in progress, timeouts) instead of looking like success. Not yet tested against a real refusal. A "car offline" status is not treated as a refusal because it was seen on commands that worked.
- **Driving journal (opt in):** Last trip sensor, Trips this month and Distance this month, and the services `get_trips` and `get_trips_summary`. Off by default because trips include places.
- **Live update streams** (experimental; each service falls back to polling).
- **Climate controls** for cars that support them (experimental, untested on a real car): target temperature and seat and steering wheel heating, applied the next time climate starts. Hidden on cars whose record says they are not supported (the XC40).
- **Diagnostics download** and a disabled-by-default Vehicle info sensor with the car record, VIN sensor, optional registration plate, stable device name.
- **Options:** idle and active poll intervals. Plugged in but not charging stays on the idle poll.
- **Per-tyre pressure sensors**, climate action attribute, Light status attributes, Charging switch, Honk button, Sunroof sensor only on cars that report a sunroof.
- When an account has several cars, only cars not set up yet are offered.

### Changed

- **Cabin air quality index** now reports PM2.5 in µg/m³ (PM2.5 device class), like Polestar's.
- **Target SoC, Charging amp limit and Charging schedule** are re-read every 2 minutes instead of every 15, so changes made in the Volvo app show up within about a poll (5 minutes idle, 1 minute while charging). The app does not push these.
- Slow-changing data (software, weather) is polled less often.
- Charging schedule and Boot lock names match Polestar.
- First-poll check: entities with no data from the car are hidden.

### Removed

- **Last seen sensor.** It changed every few seconds and filled the activity log. The Online sensor keeps `last_seen` as an attribute; Connection keeps `offline_since`.
- **Target SoC sensor.** The Target SoC number replaces it.
- The four per-wheel tyre warning binary sensors (replaced by Tyre warning with attributes).

### Fixed

- Alarm triggered no longer shows Unknown on cars that send no alarm state.
- A timed-out command is reported as such instead of an unexpected error.
