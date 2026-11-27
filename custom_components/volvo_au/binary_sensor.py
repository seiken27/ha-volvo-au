"""Volvo C3 binary sensors — doors, windows, hood, tailgate, charge port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity, async_gate_unsupported, async_remove_registered_entities
from .online import car_status
from .powertrain import in_use, light_status, service_attributes, tyre_grade, tyre_pressure_kpa


def _ext(snap: dict[str, Any], key: str) -> str | None:
    ext = (snap.get("exterior") or {}).get("exterior") or {}
    v = ext.get(key)
    return v if isinstance(v, str) else None


def _open(snap: dict[str, Any], key: str) -> bool | None:
    v = _ext(snap, key)
    if v is None:
        return None
    if v.endswith("_OPEN") or v.endswith("_AJAR"):
        return True
    if v.endswith("_CLOSED") or v.endswith("_UNSPECIFIED"):
        return False


def has_sunroof(snap: dict[str, Any] | None) -> bool | None:
    """Does the car report a sunroof? None when there is no exterior data yet.

    A car without one has no ``sunroof`` value, or ``..._UNSPECIFIED``.
    """
    if not snap or not (snap.get("exterior") or {}).get("exterior"):
        return None
    v = _ext(snap, "sunroof")
    return v is not None and not v.endswith("_UNSPECIFIED")


def _alarm(snap: dict[str, Any], key: str) -> bool | None:
    """True when the car's theft-alarm enum reports TRIGGERED.

    NOTE: the exact JSON key name below ("alarmStatus") is a best-effort
    guess, not yet confirmed against a live ExteriorService/GetExterior
    payload. The app-side proto enum is AlarmStatus with values
    ALARM_STATUS_UNSPECIFIED / ALARM_STATUS_IDLE (or _OFF) /
    ALARM_STATUS_TRIGGERED, matching the _TRIGGERED/_UNSPECIFIED suffix
    convention used elsewhere in this file, but the flat "Digital Twin"
    field name it's exposed under here hasn't been verified. If this sensor
    stays stuck at "off"/unavailable, enable debug logging and inspect a
    raw get_exterior() response to find the real key, then update it here.
    """
    v = _ext(snap, key)
    if v is None:
        # A car that has reported its exterior but sends no alarm state is not in alarm (shown as OK).
        return False if (snap.get("exterior") or {}).get("exterior") else None
    if v.endswith("_TRIGGERED"):
        return True
    if v.endswith("_IDLE") or v.endswith("_OFF") or v.endswith("_UNSPECIFIED"):
        return False
    return None


def _connected(snap: dict[str, Any]) -> bool | None:
    v = ((snap.get("battery") or {}).get("battery") or {}).get(
        "chargerConnectionStatus"
    )
    if v is None:
        return None
    return v.endswith("_CONNECTED")


def _battery_thermal_event(snap: dict[str, Any]) -> bool | None:
    """Health field 48, ``batteryThermalEventTriggered`` (Volvo app's own name).

    Proto3 JSON leaves a false bool out, so a health reply without the key
    means no event. Without any health reply the state is unknown.
    """
    h = (snap.get("health") or {}).get("health")
    if not isinstance(h, dict):
        return None
    return h.get("batteryThermalEventTriggered") is True


def _warning(snap: dict[str, Any], key: str) -> bool | None:
    """True when the warning enum signals an active fault."""
    h = (snap.get("health") or {}).get("health") or {}
    v = h.get(key)
    if not isinstance(v, str):
        return None
    if v.endswith("_UNSPECIFIED") or v.endswith("_UNKNOWN"):
        return None
    return not v.endswith("_NO_WARNING")


def _health_lights(snap: dict[str, Any]) -> Any:
    return ((snap.get("health") or {}).get("health") or {}).get("lightWarnings")


def _any_light_warning(snap: dict[str, Any]) -> bool | None:
    return light_status(_health_lights(snap))[0]


def _light_attributes(snap: dict[str, Any]) -> dict[str, Any]:
    return light_status(_health_lights(snap))[1]


_TYRE_WHEELS = (
    ("front_left", "frontLeft"),
    ("front_right", "frontRight"),
    ("rear_left", "rearLeft"),
    ("rear_right", "rearRight"),
)


def _any_tyre_warning(snap: dict[str, Any]) -> bool | None:
    """On when any wheel reports a warning; unknown when none reports anything."""
    states = [_warning(snap, f"{prefix}TyrePressureWarning") for _, prefix in _TYRE_WHEELS]
    known = [s for s in states if s is not None]
    return any(known) if known else None


def _tyre_attributes(snap: dict[str, Any]) -> dict[str, Any]:
    """Per-wheel warning grade and pressure (kPa); only for cars that report individual pressures."""
    health = (snap.get("health") or {}).get("health")
    pressures = {wheel: tyre_pressure_kpa(health, wheel) for wheel, _ in _TYRE_WHEELS}
    if not any(pressures.values()):
        return {}
    attrs: dict[str, Any] = {}
    for wheel, prefix in _TYRE_WHEELS:
        grade = tyre_grade((health or {}).get(f"{prefix}TyrePressureWarning"))
        if grade is not None:
            attrs[f"{wheel}_warning"] = grade
        if pressures[wheel] is not None:
            attrs[f"{wheel}_pressure_kpa"] = pressures[wheel]
    return attrs


def _service_attributes(snap: dict[str, Any]) -> dict[str, Any]:
    return service_attributes(((snap.get("health") or {}).get("health") or {}).get("serviceWarning"))


def _in_use(snap: dict[str, Any]) -> bool | None:
    """True while the car is being driven or Volvo says it is unavailable because it is in use."""
    avail = (snap.get("availability") or {}).get("availability") or {}
    return in_use(avail, car_status(snap).online)


@dataclass(frozen=True, kw_only=True)
class VolvoBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    requires_capability: str | None = None
    icon_on: str | None = None
    icon_off: str | None = None


DESCRIPTIONS: tuple[VolvoBinaryDescription, ...] = (
    VolvoBinaryDescription(
        key="door_fl",
        translation_key="door_fl",
        icon_on="mdi:car-door-lock-open",
        icon_off="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "frontLeftDoor"),
    ),
    VolvoBinaryDescription(
        key="door_fr",
        translation_key="door_fr",
        icon_on="mdi:car-door-lock-open",
        icon_off="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "frontRightDoor"),
    ),
    VolvoBinaryDescription(
        key="door_rl",
        translation_key="door_rl",
        icon_on="mdi:car-door-lock-open",
        icon_off="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "rearLeftDoor"),
    ),
    VolvoBinaryDescription(
        key="door_rr",
        translation_key="door_rr",
        icon_on="mdi:car-door-lock-open",
        icon_off="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "rearRightDoor"),
    ),
    VolvoBinaryDescription(
        key="tailgate",
        translation_key="tailgate",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "tailgate"),
    ),
    VolvoBinaryDescription(
        key="hood",
        translation_key="hood",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "hood"),
    ),
    VolvoBinaryDescription(
        key="window_fl",
        translation_key="window_fl",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "frontLeftWindow"),
    ),
    VolvoBinaryDescription(
        key="window_fr",
        translation_key="window_fr",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "frontRightWindow"),
    ),
    VolvoBinaryDescription(
        key="window_rl",
        translation_key="window_rl",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "rearLeftWindow"),
    ),
    VolvoBinaryDescription(
        key="window_rr",
        translation_key="window_rr",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "rearRightWindow"),
    ),
    VolvoBinaryDescription(
        key="sunroof",
        translation_key="sunroof",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "sunroof"),
    ),
    VolvoBinaryDescription(
        key="alarm_triggered",
        translation_key="alarm_triggered",
        device_class=BinarySensorDeviceClass.PROBLEM,  # shows OK / Problem
        value_fn=lambda s: _alarm(s, "alarmStatus"),
    ),
    VolvoBinaryDescription(
        key="charge_port",
        translation_key="charge_port",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda s: _open(s, "tankLid"),
    ),
    VolvoBinaryDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=_connected,
    ),
    # Service / health warnings
    VolvoBinaryDescription(
        key="service_warning",
        translation_key="service_warning",
        icon_on="mdi:alert-circle",
        icon_off="mdi:car-wrench",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "serviceWarning"),
        attrs_fn=_service_attributes,
    ),
    VolvoBinaryDescription(
        key="brake_fluid_warning",
        translation_key="brake_fluid_warning",
        icon_on="mdi:alert-circle",
        icon_off="mdi:car-brake-fluid-level",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _warning(s, "brakeFluidLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="engine_coolant_warning",
        translation_key="engine_coolant_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _warning(s, "engineCoolantLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="oil_level_warning",
        translation_key="oil_level_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _warning(s, "oilLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="washer_fluid_warning",
        translation_key="washer_fluid_warning",
        icon_on="mdi:alert-circle",
        icon_off="mdi:wiper-wash",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "washerFluidLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="tyre_warning",
        requires_capability="reportsTyrePressure",
        translation_key="tyre_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon_off="mdi:car-tire-alert",
        icon_on="mdi:alert-circle",
        value_fn=_any_tyre_warning,
        attrs_fn=_tyre_attributes,
    ),
    VolvoBinaryDescription(
        key="low_voltage_battery_warning",
        translation_key="low_voltage_battery_warning",
        icon_on="mdi:battery-alert",
        icon_off="mdi:car-battery",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _warning(s, "lowVoltageBatteryWarning"),
    ),
    VolvoBinaryDescription(
        key="battery_thermal_event",
        translation_key="battery_thermal_event",
        icon_on="mdi:fire-alert",
        icon_off="mdi:car-battery",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_battery_thermal_event,
    ),
    VolvoBinaryDescription(
        key="light_warning",
        requires_capability="reportsLightWarnings",
        translation_key="light_warning",
        icon_on="mdi:alert-circle",
        icon_off="mdi:car-light-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_any_light_warning,
        attrs_fn=_light_attributes,
    ),
    VolvoBinaryDescription(
        key="in_use",
        translation_key="in_use",
        device_class=BinarySensorDeviceClass.MOVING,
        value_fn=_in_use,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    # The four per-wheel tyre warnings became one Tyre warning with per-wheel attributes (19 Nov 2026).
    async_remove_registered_entities(
        hass,
        "binary_sensor",
        [f"{DOMAIN}_{coordinator.client.vin}_tyre_{w}_warning" for w in ("fl", "fr", "rl", "rr")],
    )
    entities = [VolvoBinarySensor(coordinator, d) for d in DESCRIPTIONS]
    if has_sunroof(coordinator.data) is False:
        # Only some cars (for example the XC40) have a sunroof. Do not create the sensor, and remove
        # one that an earlier version created.
        entities = [e for e in entities if e.entity_description.key != "sunroof"]
        registry = er.async_get(hass)
        stale = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DOMAIN}_{coordinator.client.vin}_sunroof")
        if stale:
            registry.async_remove(stale)
    async_add_entities(async_gate_unsupported(hass, coordinator, "binary_sensor", entities))
    async_add_entities([VolvoOnlineSensor(coordinator)])


class VolvoBinarySensor(VolvoEntity, BinarySensorEntity):
    entity_description: VolvoBinaryDescription

    def __init__(
        self,
        coordinator: VolvoCoordinator,
        description: VolvoBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.requires_capability = description.requires_capability
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        try:
            return self.entity_description.value_fn(self.coordinator.data or {})
        except Exception:  # noqa: BLE001
            return None

    @property
    def icon(self) -> str | None:
        """Follow the state when the description has state icons (an unknown state shows the off icon)."""
        d = self.entity_description
        if d.icon_on is None and d.icon_off is None:
            return super().icon
        return d.icon_on if self.is_on else d.icon_off

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        if fn is None:
            return None
        try:
            return fn(self.coordinator.data or {}) or None
        except Exception:  # noqa: BLE001
            return None


class VolvoOnlineSensor(VolvoEntity, BinarySensorEntity):
    """Is the car online? Unknown (no state) when Volvo gives no evidence. See online.py."""

    _attr_translation_key = "online"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_online"

    @property
    def is_on(self) -> bool | None:
        return car_status(self.coordinator.data).online

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = car_status(self.coordinator.data)
        # "availability_raw" shows what Volvo sends today, so the real connectivity key can be confirmed.
        return {
            "evidence": st.source,
            "last_seen": st.last_seen.isoformat() if st.last_seen else None,
            "availability_raw": st.raw,
        }
