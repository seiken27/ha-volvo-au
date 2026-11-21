# Volvo C3 — Home Assistant integration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=volvo_au)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Native Home Assistant custom integration for Volvo cars on Volvo's C3 platform, using the same services as the Volvo Cars app. Battery, location, doors, locks, climate, charging settings and more, with commands sent back through the same channel the app uses.

The matching integration for Polestar cars is [Polestar C3](https://github.com/BeauGiles/ha-polestar-c3). The two talk to the same backend and share entity names, states and behaviour.

> [!WARNING]
> **Unofficial and unsupported.** This is a community project. It is **not made, endorsed or supported by Volvo Cars**, and Volvo Cars does not know about it. It uses the same private services as the Volvo Cars app, not Volvo's official API. Volvo can change or block those services at any time, and this integration can stop working without notice.
>
> **Check the official integration first.** If you live in a region supported by Volvo's official API ([supported locations](https://developer.volvocars.com/terms-and-conditions/apis-supported-locations/)), use the official [Volvo integration](https://www.home-assistant.io/integrations/volvo/) built into Home Assistant instead. Use this one only if the official integration isn't available where you live, or doesn't offer what you need.

> [!NOTE]
> **Why "Volvo C3"?** The integration uses Volvo's C3 services, the same ones the Volvo Cars app uses, not Volvo's official public API (which is not available in some countries, including Australia). Nothing in it is specific to one market, so it is named after the platform, like Polestar C3.

## Supported cars

Built and tested on an **Australian Volvo XC40 Recharge (2023)**. The integration uses Volvo's C3 platform, the same one the Volvo Cars app uses, so other Volvo cars on that platform should work, but features differ between models and not every model has been tested. The integration asks the car which features it supports and hides the rest, so a missing entity usually means the car doesn't offer it. For example, the XC40 reports no tyre pressures and no climate settings.

Reports of what works and what doesn't on your model are welcome. Please [open an issue](https://github.com/BeauGiles/ha-volvo-c3/issues).

## Features

Volvo C3 and [Polestar C3](https://github.com/BeauGiles/ha-polestar-c3) talk to the same Volvo Cars C3 platform, so most features are the same. This table is shared by both READMEs.

✅ supported · 🧪 experimental (limited testing) · ◐ only on cars that report it · ➖ not available

### Read

| Feature | Volvo C3 | Polestar C3 |
| --- | :---: | :---: |
| State of charge, range | ✅ | ✅ |
| Charging status, power, current, voltage, charger connection | ✅ | ✅ |
| Time to full charge | ✅ | ✅ |
| Time to target charge and to minimum SoC | ➖ | ✅ |
| Charge port, plugged in | ✅ | ✅ |
| Saved charge locations, "at charge location" | ➖ | ✅ |
| Location, with address | ✅ | ✅ |
| Parked location, altitude, heading, speed | ➖ | ✅ |
| Doors, windows, hood, tailgate | ✅ | ✅ |
| Sunroof | ◐ | ➖ |
| Central lock, boot lock | ✅ | ✅ |
| Alarm triggered, In use, Online, Connection | ✅ | ✅ |
| Odometer, trip meters, average consumption, average speed | ✅ | ✅ (average speed hidden on the Polestar 2) |
| Outside temperature | ✅ | ✅ |
| Service required (with urgency), days, distance and engine hours to service | ✅ | ✅ |
| Tyre warning, per-wheel grades | ◐ | ◐ |
| Per-wheel tyre pressures | ◐ | ◐ |
| 12V battery, brake fluid, washer fluid, light status | ✅ | ✅ |
| Engine coolant, oil level (combustion cars) | ◐ | ➖ |
| Climate status and time remaining | ➖ | ✅ |
| Cabin air quality | ✅ | ✅ |
| Software version | ✅ | ✅ |
| Driving journal: last trip, trips and distance this month (opt in) | ✅ | ➖ |
| VIN, vehicle info, registration plate | ✅ | ✅ |

### Control

| Feature | Volvo C3 | Polestar C3 |
| --- | :---: | :---: |
| Lock, unlock, lock with reduced alarm | ✅ | ✅ |
| Boot lock (unlock the tailgate only) | ✅ | ✅ |
| Open and close tailgate | ✅ | ➖ |
| Flash lights, honk, honk and flash | ✅ | ✅ |
| Open and close all windows | ✅ | ✅ |
| Start and stop climate | ✅ | ✅ |
| Climate target temperature | ◐ | ◐ |
| Seat and steering wheel heating | 🧪 ◐ | ◐ |
| Engine remote start (starts cabin climate on an EV) | ✅ | ➖ |
| Air purification | ✅ | ✅ |
| Target SoC, charging amp limit | ✅ | ✅ |
| Charging schedule: switch, start and end times | ✅ | ✅ |
| Start and stop charging | ✅ | ✅ |
| Parking climate timers (calendars) | ➖ | ✅ |
| Software update: schedule, install, cancel | ➖ | ✅ |
| Manage saved charge locations (services) | ➖ | ✅ |

### Behaviour

| Feature | Volvo C3 | Polestar C3 |
| --- | :---: | :---: |
| Clear error when a command is refused (offline, privacy mode, in use, busy, timeout) | 🧪 | ✅ |
| Live updates streamed from the car's cloud | 🧪 | ✅ |
| Adaptive polling (faster while charging, driving or running climate) | ✅ | ✅ |
| Entities hidden for features the car doesn't have | ✅ | ✅ |
| Reconfigure flow, diagnostics download | ✅ | ✅ |
| Demo mode | ➖ | ✅ |
| More than one car (one entry per car) | ✅ | ✅ |

> [!CAUTION]
> Commands act on a real car. You are responsible for how you use them and for any automations you build on them.

Every command shows an error in Home Assistant when Volvo refuses it or does not answer, and is refused before it is sent while Volvo reports the car offline. (Volvo's server answers HTTP 200 to a command even when the car is offline, so without this check a command could silently do nothing.)

### Entities

| Platform | Examples |
| --- | --- |
| Sensor | State of charge, range, charging status, charger connection, charging power, odometer, trip meters, service sensors, tyre pressures, software version, Connection, Vehicle info, VIN |
| Binary sensor | Online, In use, Plugged in, Alarm triggered, Tyre warning, Service required, 12V battery, Brake fluid, Washer fluid, Light status, doors, windows, hood, tailgate, sunroof, charge port |
| Device tracker | Location |
| Lock | Lock, Boot lock |
| Switch | Climate, Charging, Charging schedule, Air purification |
| Number | Target SoC, Charging amp limit, Climate target temperature |
| Select | Seat and steering wheel heating |
| Button | Flash lights, Honk, Honk and flash, Lock with reduced alarm, Open and Close windows, Tailgate control, Close tailgate, Engine remote start, Refresh |
| Time | Charging schedule start and end |

Entity IDs come from the car model and registration, for example `sensor.xc40_abc123_state_of_charge`. Which entities appear depends on what the car supports. The **Online** sensor says whether the car can receive commands now, and **Connection** reads "Online" or a short "Offline: reason", with the full explanation and the time the car went offline as attributes.

## Install

### Via HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=BeauGiles&repository=ha-volvo-c3&category=integration)

Click the badge above to add this repo to HACS in one click, then download **Volvo C3** and restart Home Assistant.

Manual steps if the badge doesn't work:

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/BeauGiles/ha-volvo-c3` with category **Integration**.
3. Find **Volvo C3** in HACS and click **Download**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration → Volvo C3**.

### Manual

1. Copy `custom_components/volvo_au/` into your Home Assistant `config/custom_components/`.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Volvo C3**.

### Updating

> [!IMPORTANT]
> After updating, **restart Home Assistant**. A reload is not enough. Read the [changelog](CHANGELOG.md) first, because some updates change sensor states or remove entities.

## Sign in

Uses Volvo ID OAuth with PKCE + DPoP, same as the iOS app. You'll need:

- A Volvo Cars account paired with the vehicle in the official iOS app first
- Strongly recommended: a **secondary Volvo account** added as a "key user", paired via QR from the primary owner's app, used **only** by this integration — so the integration's token refresh activity doesn't disturb your primary phone's session

### Capturing the `volvooncall://` callback URL

During config-flow, you'll be sent to Volvo ID in a browser to sign in (+ 2FA). After login, Volvo tries to redirect to a `volvooncall://oauth/code?code=...&state=...` URL that the iOS app would normally handle. On a desktop browser there's no app to catch it, so you need to grab the URL manually and paste it back into Home Assistant.

> [!TIP]
> **Use Chrome or Edge with DevTools.** Safari silently swallows custom-scheme redirects and won't show the URL anywhere.

1. Open **Chrome** or **Edge**.
2. Open **DevTools** (F12 or ⌥⌘I on Mac) → **Network** tab.
3. Tick **"Preserve log"** so the redirect entry doesn't get wiped.
4. Paste the Volvo ID login URL from Home Assistant into the address bar and sign in (complete 2FA).
5. After login, the page will go blank or show an error — that's expected. In the Network tab, scroll to the **last entry**: it'll be a request to `volvooncall://oauth/code?...` (usually shown in red as `(failed)`).
6. Right-click it → **Copy → Copy URL** (or **Copy link address**).
7. Paste that full URL back into the Home Assistant config-flow field.

<img src="docs/images/devtools-callback.png" alt="DevTools showing the volvooncall:// callback URL in the Network tab" width="75%">

*The bits you care about: **Network** tab open, **Preserve log** ticked, then the failed `callback?code=...` entry at the bottom — Request URL on the right is what you copy.*

> [!IMPORTANT]
> The `state` parameter in the URL must match the one Home Assistant generated for the current flow attempt. If you cancel and restart the flow, request a fresh login URL. Don't reuse an old one.

## Options

Open **Settings → Devices & Services → Volvo C3 → Configure**.

- **Idle poll interval** (default 300 seconds, between 60 and 86400) and **active poll interval** (default 60 seconds, between 10 and 3600, never slower than idle). The active interval is used while charging, with a door open, with climate on or while the car is in use. After a trip ends the integration polls every 5 seconds for 5 minutes (until you lock the car), and for 60 seconds after any command.
- **Live updates** (experimental): keep a connection open to Volvo so changes arrive as they happen. If Volvo only ever sends one reading for a service, that service falls back to polling by itself.
- **Driving journal** (off by default): adds Last trip, Trips this month and Distance this month, and the `get_trips` and `get_trips_summary` services. Off by default because trips include where you drove. The journal must also be turned on in the Volvo app.

> [!NOTE]
> None of this polling wakes the car. Volvo's cloud reads cached telemetry that the car pushes opportunistically.

## Services

- `volvo_au.get_trips`: list trips for a period, optionally with coordinates.
- `volvo_au.get_trips_summary`: totals for a period.

Both target one of the integration's sensors and need the driving journal turned on. Fields are in **Developer tools → Actions**.

## Notifications and Live Activities

Example automations for a charging and a climate (preconditioning) Live Activity on iOS and Android: see [docs/live-activities.md](docs/live-activities.md).

## FAQ

**An entity is missing.**
The integration hides entities for features the car says it doesn't have. Check [Supported cars](#supported-cars).

**A command failed.**
Volvo refuses a command in some situations, for example when the car is offline, being driven, or already busy. The error in Home Assistant says which, where Volvo tells us. Try again in a few minutes.

**Something stopped working.**
Download diagnostics from **Settings → Devices & Services → Volvo C3 → ⋮ → Download diagnostics** and attach it to an issue.

## For developers

The code that speaks to Volvo's servers is in `custom_components/volvo_au/volvo_api.py`. It talks to the same backend the Volvo Cars iOS app uses. Run `uv run --with pytest --with voluptuous pytest -q` for the tests. A design for sharing the backend code with Polestar C3 is in [docs/design-shared-c3-library.md](docs/design-shared-c3-library.md).

## ☕ Support

If this saved you some time and you'd like to support me, you can leave a tip at [b3.au/tip-jar](https://b3.au/tip-jar).

## License

MIT — see [LICENSE](./LICENSE).

## Disclaimer

This project is independent. It is not affiliated with, authorised by, endorsed by or supported by Volvo Cars or any of its subsidiaries. "Volvo" and related names and logos are trademarks of their owners and are used only to say which cars this works with.

> [!WARNING]
> **Unofficial access.** It was built by reverse engineering how the Volvo Cars app talks to Volvo's servers. It does not use Volvo's published API. Volvo can change that at any time, with no warning and no migration path.

- **Terms of service.** Using it may breach the Volvo Cars terms of service in some regions, and could lead to your account being limited or blocked. Consider using a secondary account that has been given access to your car, not your main one.
- **It controls a real car.** Commands such as lock, unlock, start climate and start charging act on your actual vehicle. You are responsible for how you use them, and for automations you build on them. Do not rely on this for anything safety critical.
- **Your credentials.** Sign-in tokens are stored in your Home Assistant configuration. Protect your Home Assistant backups and access accordingly.
- **No warranty and no support from Volvo.** Use it at your own risk. It is provided as is, as set out in the licence. The authors are not responsible for any consequences of using it, including vehicle malfunctions, warranty implications or account restrictions. **Please do not contact Volvo Cars or your dealer for help with this integration**; open an issue here instead.

### Official options

If you live in a region supported by Volvo's official API, the built-in Home Assistant [Volvo integration](https://www.home-assistant.io/integrations/volvo/) is the supported way to connect your car. Check the [supported locations](https://developer.volvocars.com/terms-and-conditions/apis-supported-locations/). This integration exists for people outside those regions, or who need something the official one doesn't provide.
