"""Volvo AU sensor platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity


def _path(snap: dict[str, Any], *keys: str) -> Any:
    cur: Any = snap
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _pretty_enum(raw: Any, *prefixes: str) -> str | None:
    """Strip a known backend enum prefix and return a sentence-case label,
    e.g. "CHARGING_STATUS_V2_IDLE" -> "Idle". HA does *not* auto-capitalize
    sensor states for us (that only happens for device_class="enum" plus a
    matching translation string, which this integration doesn't define —
    same as the Polestar integration's own enum_name() helper, which also
    just lowercases), so we do it here instead of leaving raw/lowercase
    text on screen.

    ``prefixes`` are tried in order; the first one that matches is
    stripped (useful when a field has drifted between a V1/V2 naming,
    e.g. ``CHARGING_STATUS_V2_IDLE`` vs ``CHARGING_STATUS_IDLE``).
    """
    if not isinstance(raw, str) or not raw:
        return None
    s = raw
    for prefix in prefixes:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.replace("_", " ").strip().lower()
    if not s:
        return None
    return s[0].upper() + s[1:]


def _round1(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return round(float(v), 1)
    return None


def _epoch_to_dt(seconds: Any) -> datetime | None:
    if seconds in (None, 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(seconds), tz=UTC)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, kw_only=True)
class VolvoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[VolvoSensorDescription, ...] = (
    # Battery
    VolvoSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "batteryChargeLevelPercentage"),
    ),
    VolvoSensorDescription(
        key="range_km",
        name="Range",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "estimatedDistanceToEmptyKm"),
    ),
    VolvoSensorDescription(
        key="time_to_full",
        name="Time to full",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "estimatedChargingTimeToFullMinutes"),
    ),
    VolvoSensorDescription(
        key="charging_status",
        name="Charging status",
        icon="mdi:ev-station",
        value_fn=lambda s: _pretty_enum(
            _path(s, "battery", "battery", "chargingStatusV2")
            or _path(s, "battery", "battery", "chargingStatus"),
            "CHARGING_STATUS_V2_",
            "CHARGING_STATUS_",
        ),
    ),
    VolvoSensorDescription(
        key="charger_connection",
        name="Charger connection",
        icon="mdi:power-plug",
        value_fn=lambda s: _pretty_enum(
            _path(s, "battery", "battery", "chargerConnectionStatus"),
            "CHARGER_CONNECTION_STATUS_",
        ),
    ),
    VolvoSensorDescription(
        key="charging_power",
        name="Charging power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "chargingPowerWatts") or 0,
    ),
    VolvoSensorDescription(
        key="charging_current",
        name="Charging current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "chargingCurrentAmps") or 0,
    ),
    VolvoSensorDescription(
        key="charging_voltage",
        name="Charging voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "chargingVoltageVolts") or 0,
    ),
    VolvoSensorDescription(
        key="avg_consumption",
        name="Average consumption",
        native_unit_of_measurement="kWh/100km",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "averageEnergyConsumptionKwhPer100Km"),
    ),
    # Odometer
    VolvoSensorDescription(
        key="odometer_km",
        name="Odometer",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: (
            (_path(s, "odometer", "odometer", "odometerMeters") or 0) / 1000.0
        ) or None,
    ),
    VolvoSensorDescription(
        key="trip_manual_km",
        name="Trip (manual)",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "odometer", "odometer", "tripMeterManualKm"),
    ),
    VolvoSensorDescription(
        key="trip_auto_km",
        name="Trip (auto)",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "odometer", "odometer", "tripMeterAutomaticKm"),
    ),
    VolvoSensorDescription(
        key="avg_speed",
        name="Average speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "odometer", "odometer", "averageSpeedKmPerHour"),
    ),
    # Charge target / amp limit
    VolvoSensorDescription(
        key="target_soc",
        name="Target SoC",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "target_soc", "targetSoc", "batteryChargeTargetLevel"),
    ),
    VolvoSensorDescription(
        key="amp_limit",
        name="Charge current limit",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "amp_limit", "ampLimit", "ampLimit"),
    ),
    # Usage / availability
    VolvoSensorDescription(
        key="usage_mode",
        name="Usage mode",
        icon="mdi:car-info",
        value_fn=lambda s: _pretty_enum(
            _path(s, "availability", "availability", "usageMode"),
            "USAGE_MODE_",
        ),
    ),
    # Outside temperature reported by Volvo's weather service
    VolvoSensorDescription(
        key="exterior_temperature",
        name="Exterior temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _round1(_path(s, "weather", "temperature_c")),
    ),
    # Service / health
    VolvoSensorDescription(
        key="distance_to_service",
        name="Distance to service",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "health", "health", "distanceToServiceKm"),
    ),
    VolvoSensorDescription(
        key="days_to_service",
        name="Days to service",
        native_unit_of_measurement=UnitOfTime.DAYS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "health", "health", "daysToService"),
    ),
    VolvoSensorDescription(
        key="engine_hours_to_service",
        name="Engine hours to service",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "health", "health", "engineHoursToService"),
    ),
    # Air purification
    VolvoSensorDescription(
        key="air_quality_index",
        name="Cabin air quality index",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(
            s, "pre_cleaning", "preCleaning", "measuredAirQualityIndex"
        ),
    ),
    VolvoSensorDescription(
        key="air_purification_last_cycle",
        name="Air purification last cycle",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _epoch_to_dt(
            _path(s, "pre_cleaning", "preCleaning", "lastCycleCompleted", "seconds")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(VolvoSensor(coordinator, d) for d in SENSORS)
    async_add_entities([VolvoLocationAddress(coordinator), VolvoSoftwareVersion(coordinator)])


class VolvoSensor(VolvoEntity, SensorEntity):
    entity_description: VolvoSensorDescription

    def __init__(
        self,
        coordinator: VolvoCoordinator,
        description: VolvoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_{description.key}"

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator.data or {})
        except Exception:  # noqa: BLE001
            return None


class VolvoLocationAddress(VolvoEntity, SensorEntity):
    """Reverse-geocoded street address of the last parked location (OSM Nominatim)."""

    _attr_name = "Location address"
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_location_address"
        self._last_coords: tuple[float, float] | None = None
        self._cached: str | None = None
        self._cached_attrs: dict[str, Any] = {}

    @property
    def native_value(self) -> str | None:
        return self._cached

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._cached_attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._maybe_geocode()

    def _handle_coordinator_update(self) -> None:
        self.hass.async_create_task(self._maybe_geocode())
        super()._handle_coordinator_update()

    async def _maybe_geocode(self) -> None:
        loc = (self.coordinator.data or {}).get("location") or {}
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            return
        # Round to ~10m to avoid duplicate lookups for tiny GPS jitter.
        key = (round(lat, 4), round(lon, 4))
        if key == self._last_coords and self._cached is not None:
            return

        # Snap to any HA zone the coords fall inside (Home, work, etc.)
        # This avoids OSM giving us weird answers when the car is parked on top
        # of a road/tunnel polygon that's geographically near a building.
        zone_match = self._matching_zone(lat, lon)
        if zone_match is not None:
            self._last_coords = key
            self._cached = zone_match
            self._cached_attrs = {
                "zone": zone_match,
                "latitude": lat,
                "longitude": lon,
            }
            self.async_write_ha_state()
            return

        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.hass)
        params = {
            "format": "jsonv2",
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "zoom": "18",
            "addressdetails": "1",
        }
        try:
            async with session.get(
                "https://nominatim.openstreetmap.org/reverse",
                params=params,
                headers={"User-Agent": "volvo_au-ha-integration/1.0"},
                timeout=15,
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception:  # noqa: BLE001
            return
        self._last_coords = key
        addr = data.get("address", {})
        # Build a friendlier display than Nominatim's raw display_name.
        # Nominatim writes "Name, 25, Martin Place, ..." — humans expect "25 Martin Place".
        house_number = addr.get("house_number")
        road = addr.get("road") or addr.get("pedestrian") or addr.get("footway")
        suburb = (
            addr.get("suburb")
            or addr.get("neighbourhood")
            or addr.get("city_district")
        )
        city = addr.get("city") or addr.get("town") or addr.get("village")
        postcode = addr.get("postcode")

        parts: list[str] = []
        if road:
            parts.append(f"{house_number} {road}" if house_number else road)
        elif addr.get("shop") or addr.get("amenity") or addr.get("building"):
            parts.append(addr.get("shop") or addr.get("amenity") or addr.get("building"))
        if suburb:
            parts.append(suburb)
        if city and city != suburb:
            parts.append(city)
        if postcode:
            parts.append(postcode)
        pretty = ", ".join(p for p in parts if p)
        self._cached = pretty or data.get("display_name")
        self._cached_attrs = {
            "house_number": house_number,
            "road": road,
            "suburb": suburb,
            "city": city,
            "postcode": postcode,
            "country": addr.get("country"),
            "display_name": data.get("display_name"),
            "latitude": lat,
            "longitude": lon,
        }
        self.async_write_ha_state()

    def _matching_zone(self, lat: float, lon: float) -> str | None:
        """Return friendly name of the HA zone the coords fall inside.

        Prefers zone.home over other zones; otherwise picks the smallest match.
        """
        from math import asin, cos, radians, sin, sqrt

        def _inside(zlat: float, zlon: float, radius: float) -> bool:
            r = 6371000.0  # m
            dlat = radians(zlat - lat)
            dlon = radians(zlon - lon)
            a = (
                sin(dlat / 2) ** 2
                + cos(radians(lat)) * cos(radians(zlat)) * sin(dlon / 2) ** 2
            )
            return 2 * r * asin(sqrt(a)) <= float(radius)

        # 1. Always prefer Home if we're inside it.
        home = self.hass.states.get("zone.home")
        if home is not None:
            zlat = home.attributes.get("latitude")
            zlon = home.attributes.get("longitude")
            radius = home.attributes.get("radius")
            if zlat is not None and zlon is not None and radius is not None:
                if _inside(zlat, zlon, radius):
                    return home.attributes.get("friendly_name") or "Home"

        # 2. Otherwise pick the smallest non-Home zone match.
        best: tuple[float, str] | None = None
        for state in self.hass.states.async_all("zone"):
            if state.entity_id == "zone.home":
                continue
            if state.attributes.get("passive"):
                continue
            zlat = state.attributes.get("latitude")
            zlon = state.attributes.get("longitude")
            radius = state.attributes.get("radius")
            if zlat is None or zlon is None or radius is None:
                continue
            if not _inside(zlat, zlon, radius):
                continue
            name = (
                state.attributes.get("friendly_name")
                or state.name
                or state.entity_id.split(".", 1)[-1].replace("_", " ").title()
            )
            if best is None or float(radius) < best[0]:
                best = (float(radius), name)
        return best[1] if best else None


class VolvoSoftwareVersion(VolvoEntity, SensorEntity):
    """Car software version reported by the OTA discovery service."""

    _attr_name = "Software version"
    _attr_icon = "mdi:package-up"
    _attr_entity_category = None

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_software_version"

    def _info(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("software_info") or {}

    @property
    def native_value(self) -> str | None:
        return self._info().get("version")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info()
        attrs: dict[str, Any] = {}
        for key in ("title", "ref_code", "ref_label", "status", "update_id"):
            val = info.get(key)
            if val is not None:
                attrs[key] = val
        ts = info.get("timestamp")
        if isinstance(ts, int) and ts > 0:
            dt = _epoch_to_dt(ts)
            if dt is not None:
                attrs["timestamp"] = dt.isoformat()
        notes = info.get("release_notes")
        if notes:
            attrs["release_notes"] = notes
        return attrs
