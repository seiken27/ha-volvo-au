"""Volvo AU sensor platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    MATCH_ALL,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity, async_gate_unsupported, async_remove_registered_entities
from . import journal
from .online import car_status, status_text
from .volvo_api import VolvoApiError
from .powertrain import flatten_attributes, build_week, drivetrain_type, engine_type, structure_week, tyre_pressure_kpa
from .powertrain import battery_capacity_kwh as car_battery_capacity_kwh


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
    requires_combustion_engine: bool = False
    requires_capability: str | None = None
    requires_data: str | None = None


def _tyre(wheel: str, label: str) -> VolvoSensorDescription:
    return VolvoSensorDescription(
        key=f"tyre_{wheel}_pressure",
        requires_capability="reportsAbsoluteTyrePressure",
        translation_key=f"tyre_{wheel}_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        suggested_unit_of_measurement=UnitOfPressure.PSI,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda s: tyre_pressure_kpa((s.get("health") or {}).get("health"), wheel),
    )


SENSORS: tuple[VolvoSensorDescription, ...] = (
    # Battery
    VolvoSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "batteryChargeLevelPercentage"),
    ),
    VolvoSensorDescription(
        key="range_km",
        requires_capability="reportsElectricRange",
        translation_key="range_km",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "estimatedDistanceToEmptyKm"),
    ),
    VolvoSensorDescription(
        key="time_to_full",
        translation_key="time_to_full",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "estimatedChargingTimeToFullMinutes"),
    ),
    VolvoSensorDescription(
        key="charging_status",
        translation_key="charging_status",
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
        translation_key="charger_connection",
        icon="mdi:power-plug",
        value_fn=lambda s: _pretty_enum(
            _path(s, "battery", "battery", "chargerConnectionStatus"),
            "CHARGER_CONNECTION_STATUS_",
        ),
    ),
    VolvoSensorDescription(
        key="charging_power",
        translation_key="charging_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "chargingPowerWatts") or 0,
    ),
    VolvoSensorDescription(
        key="charging_current",
        translation_key="charging_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "chargingCurrentAmps") or 0,
    ),
    VolvoSensorDescription(
        key="charging_voltage",
        translation_key="charging_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "chargingVoltageVolts") or 0,
    ),
    VolvoSensorDescription(
        key="avg_consumption",
        requires_capability="reportsAvgElectricConsumption",
        translation_key="avg_consumption",
        native_unit_of_measurement="kWh/100km",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "battery", "battery", "averageEnergyConsumptionKwhPer100Km"),
    ),
    # Odometer
    VolvoSensorDescription(
        key="odometer_km",
        translation_key="odometer_km",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: (
            (_path(s, "odometer", "odometer", "odometerMeters") or 0) / 1000.0
        ) or None,
    ),
    VolvoSensorDescription(
        key="trip_manual_km",
        translation_key="trip_manual_km",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "odometer", "odometer", "tripMeterManualKm"),
    ),
    VolvoSensorDescription(
        key="trip_auto_km",
        translation_key="trip_auto_km",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "odometer", "odometer", "tripMeterAutomaticKm"),
    ),
    VolvoSensorDescription(
        key="avg_speed",
        requires_capability="reportsAvgSpeed",
        translation_key="avg_speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "odometer", "odometer", "averageSpeedKmPerHour"),
    ),
    # Usage / availability
    VolvoSensorDescription(
        key="usage_mode",
        translation_key="usage_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:car-info",
        value_fn=lambda s: _pretty_enum(
            _path(s, "availability", "availability", "usageMode"),
            "USAGE_MODE_",
        ),
    ),
    # Outside temperature reported by Volvo's weather service
    VolvoSensorDescription(
        key="exterior_temperature",
        translation_key="exterior_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _round1(_path(s, "weather", "temperature_c")),
    ),
    # Service / health
    VolvoSensorDescription(
        key="distance_to_service",
        translation_key="distance_to_service",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "health", "health", "distanceToServiceKm"),
    ),
    VolvoSensorDescription(
        key="days_to_service",
        translation_key="days_to_service",
        native_unit_of_measurement=UnitOfTime.DAYS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "health", "health", "daysToService"),
    ),
    VolvoSensorDescription(
        key="engine_hours_to_service",
        translation_key="engine_hours_to_service",
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_combustion_engine=True,
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(s, "health", "health", "engineHoursToService"),
    ),
    # Air purification
    VolvoSensorDescription(
        key="air_quality_index",
        requires_data="pre_cleaning",
        requires_capability="supportsAirPurificationInternalMeasurement",
        translation_key="air_quality_index",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _path(
            s, "pre_cleaning", "preCleaning", "measuredAirQualityIndex"
        ),
    ),
    VolvoSensorDescription(
        key="air_purification_last_cycle",
        requires_data="pre_cleaning",
        requires_capability="supportsAirPurification",
        translation_key="air_purification_last_cycle",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _epoch_to_dt(
            _path(s, "pre_cleaning", "preCleaning", "lastCycleCompleted", "seconds")
        ),
    ),
    _tyre("front_left", "front-left"),
    _tyre("front_right", "front-right"),
    _tyre("rear_left", "rear-left"),
    _tyre("rear_right", "rear-right"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Replaced by the Charge current limit and Target SoC numbers (19 Nov 2026).
    async_remove_registered_entities(
        hass, "sensor", [f"{DOMAIN}_{coordinator.client.vin}_{key}" for key in ("amp_limit", "target_soc")]
    )
    async_add_entities(async_gate_unsupported(hass, coordinator, "sensor", [VolvoSensor(coordinator, d) for d in SENSORS]))
    if car_battery_capacity_kwh(coordinator.car_info):
        async_add_entities([VolvoBatteryCapacity(coordinator)])
    if coordinator.driving_journal:
        async_add_entities([VolvoLastTrip(coordinator), VolvoTripsThisMonth(coordinator), VolvoDistanceThisMonth(coordinator)])
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service(
            "get_trips",
            {
                vol.Optional("start"): cv.datetime,
                vol.Optional("stop"): cv.datetime,
                vol.Optional("quantity", default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
                vol.Optional("include_coordinates", default=False): cv.boolean,
            },
            "async_get_trips",
            supports_response=SupportsResponse.ONLY,
        )
        platform.async_register_entity_service(
            "get_trips_summary",
            {vol.Optional("start"): cv.datetime, vol.Optional("stop"): cv.datetime},
            "async_get_trips_summary",
            supports_response=SupportsResponse.ONLY,
        )
    async_add_entities([VolvoLocationAddress(coordinator), VolvoSoftwareVersion(coordinator), VolvoVin(coordinator), VolvoVehicleInfo(coordinator), VolvoLastSeen(coordinator), VolvoConnection(coordinator)])


class VolvoSensor(VolvoEntity, SensorEntity):
    entity_description: VolvoSensorDescription

    def __init__(
        self,
        coordinator: VolvoCoordinator,
        description: VolvoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.requires_combustion_engine = description.requires_combustion_engine
        self.requires_capability = description.requires_capability
        self.requires_data = description.requires_data
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_{description.key}"

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator.data or {})
        except Exception:  # noqa: BLE001
            return None


class VolvoLocationAddress(VolvoEntity, SensorEntity):
    """Reverse-geocoded street address of the last parked location (OSM Nominatim)."""

    _attr_translation_key = "location_address"
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


class VolvoVin(VolvoEntity, SensorEntity):
    """The VIN, with the registration plate (if known) as an attribute."""

    _attr_translation_key = "vin"
    _attr_icon = "mdi:card-account-details-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_vin"

    @property
    def native_value(self) -> str:
        return self._vin

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        attrs = {
            "registration_plate": c.registration_plate,
            "model": c.model_name,
            "model_year": c.model_year,
            "structure_week": structure_week(c.car_info),
            "build_week": build_week(c.car_info),
            "powertrain": engine_type(c.car_info),
            "drivetrain": drivetrain_type(c.car_info),
        }
        return {k: v for k, v in attrs.items() if v}


class VolvoBatteryCapacity(VolvoEntity, SensorEntity):
    """Battery capacity as the car reports it (from /car-information/car)."""

    _attr_translation_key = "battery_capacity"
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_battery_capacity"

    @property
    def native_value(self) -> float | None:
        return car_battery_capacity_kwh(self.coordinator.car_info)


class VolvoVehicleInfo(VolvoEntity, SensorEntity):
    """The car's raw record from /car-information/car, as attributes. Disabled by default.

    For checking what a car reports (powertrain, capabilities, what a performance upgrade changes)
    without signing in again outside Home Assistant. The VIN is left out.
    """

    _attr_translation_key = "vehicle_info"
    _attr_icon = "mdi:text-box-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _unrecorded_attributes = frozenset({MATCH_ALL})  # one attribute per field: keep them out of the database

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_vehicle_info"

    @property
    def native_value(self) -> str:
        return "ok" if self.coordinator.car_info else "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        car = {k: v for k, v in (self.coordinator.car_info or {}).items() if k != "vin"}
        return {
            "powertrain": engine_type(self.coordinator.car_info),
            "drivetrain": drivetrain_type(self.coordinator.car_info),
            "battery_capacity_kwh": car_battery_capacity_kwh(self.coordinator.car_info),
            **flatten_attributes(car),
        }


class VolvoSoftwareVersion(VolvoEntity, SensorEntity):
    """Car software version reported by the OTA discovery service."""

    _attr_translation_key = "software_version"
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


class VolvoConnection(VolvoEntity, SensorEntity):
    """The car connection in one plain sentence, e.g. \"Offline since 19:32: the car has no mobile internet\".

    The voice assistant reads states, not attributes, so the reason is in the state.
    """

    _attr_translation_key = "connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:car-connected"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_connection"

    @property
    def native_value(self) -> str:
        return status_text(car_status(self.coordinator.data))[:255]


class VolvoLastSeen(VolvoEntity, SensorEntity):
    """Newest car-side timestamp in the data Volvo sent (the car's own time, not our poll time)."""

    _attr_translation_key = "last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:car-wireless"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_last_seen"

    @property
    def native_value(self) -> datetime | None:
        return car_status(self.coordinator.data).last_seen


class VolvoLastTrip(VolvoEntity, SensorEntity):
    """The newest trip in the Volvo driving journal. The state is when it ended; the rest are attributes."""

    _attr_translation_key = "last_trip"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_last_trip"

    def _trip(self) -> dict[str, Any] | None:
        trip = (self.coordinator.data or {}).get("last_trip")
        return trip if isinstance(trip, dict) and "_error" not in trip else None

    @property
    def native_value(self) -> datetime | None:
        end = (self._trip() or {}).get("end_time")
        return datetime.fromisoformat(end) if end else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        trip = self._trip()
        if not trip:
            return None
        attrs = {k: v for k, v in trip.items() if k not in ("start_place", "end_place", "end_time") and v is not None}
        attrs["start_place"] = journal.place_text(trip.get("start_place"))
        attrs["end_place"] = journal.place_text(trip.get("end_place"))
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_get_trips(
        self, start: datetime | None = None, stop: datetime | None = None, quantity: int = 10,
        include_coordinates: bool = False,
    ) -> ServiceResponse:
        try:
            trips = await self.coordinator.client.get_trips(
                quantity=quantity, start=start, stop=stop, with_coordinates=include_coordinates
            )
        except VolvoApiError as err:
            raise HomeAssistantError(f"Volvo did not return trips: {err}") from err
        return {"trips": trips}

    async def async_get_trips_summary(self, start: datetime | None = None, stop: datetime | None = None) -> ServiceResponse:
        stop = stop or datetime.now(UTC)
        start = start or stop - timedelta(days=30)
        try:
            return await self.coordinator.client.get_trips_summary(start, stop)
        except VolvoApiError as err:
            raise HomeAssistantError(f"Volvo did not return the trip summary: {err}") from err


class _VolvoMonthTotal(VolvoEntity, SensorEntity):
    """A total over the current calendar month from the driving journal; HA's statistics restart it each month."""

    _attr_state_class = SensorStateClass.TOTAL
    _field = ""
    _key = ""

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_{self._key}"

    def _month(self) -> dict[str, Any] | None:
        month = (self.coordinator.data or {}).get("trips_month")
        return month if isinstance(month, dict) and "_error" not in month else None

    @property
    def native_value(self) -> float | int | None:
        month = self._month()
        return month.get(self._field) if month else None

    @property
    def last_reset(self) -> datetime | None:
        month = self._month()
        return datetime.fromisoformat(month["month_start"]) if month else None


class VolvoTripsThisMonth(_VolvoMonthTotal):
    _attr_translation_key = "trips_this_month"
    _attr_icon = "mdi:car-clock"
    _field = "trip_count"
    _key = "trips_this_month"


class VolvoDistanceThisMonth(_VolvoMonthTotal):
    _attr_translation_key = "distance_this_month"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 1
    _field = "distance_km"
    _key = "distance_this_month"
