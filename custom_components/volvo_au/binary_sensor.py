"""Volvo AU binary sensors — doors, windows, hood, tailgate, charge port."""

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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity


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
        return None
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


def _warning(snap: dict[str, Any], key: str) -> bool | None:
    """True when the warning enum signals an active fault."""
    h = (snap.get("health") or {}).get("health") or {}
    v = h.get(key)
    if not isinstance(v, str):
        return None
    if v.endswith("_UNSPECIFIED") or v.endswith("_UNKNOWN"):
        return None
    return not v.endswith("_NO_WARNING")


def _any_light_warning(snap: dict[str, Any]) -> bool | None:
    h = (snap.get("health") or {}).get("health") or {}
    lw = h.get("lightWarnings") or {}
    if not lw:
        return None
    saw_known = False
    for v in lw.values():
        if not isinstance(v, str):
            continue
        if v.endswith("_UNSPECIFIED") or v.endswith("_UNKNOWN"):
            continue
        saw_known = True
        if not v.endswith("_NO_WARNING"):
            return True
    return False if saw_known else None


def _in_use(snap: dict[str, Any]) -> bool | None:
    """True when Volvo reports the car is not abandoned (i.e. in use/driving)."""
    avail = (snap.get("availability") or {}).get("availability") or {}
    um = avail.get("usageMode")
    if not isinstance(um, str) or not um:
        return None
    if um in ("USAGE_MODE_UNSPECIFIED", "USAGE_MODE_UNKNOWN"):
        return None
    return um != "USAGE_MODE_ABANDONED"


@dataclass(frozen=True, kw_only=True)
class VolvoBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


DESCRIPTIONS: tuple[VolvoBinaryDescription, ...] = (
    VolvoBinaryDescription(
        key="door_fl",
        name="Front-left door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "frontLeftDoor"),
    ),
    VolvoBinaryDescription(
        key="door_fr",
        name="Front-right door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "frontRightDoor"),
    ),
    VolvoBinaryDescription(
        key="door_rl",
        name="Rear-left door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "rearLeftDoor"),
    ),
    VolvoBinaryDescription(
        key="door_rr",
        name="Rear-right door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "rearRightDoor"),
    ),
    VolvoBinaryDescription(
        key="tailgate",
        name="Tailgate",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "tailgate"),
    ),
    VolvoBinaryDescription(
        key="hood",
        name="Hood",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: _open(s, "hood"),
    ),
    VolvoBinaryDescription(
        key="window_fl",
        name="Front-left window",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "frontLeftWindow"),
    ),
    VolvoBinaryDescription(
        key="window_fr",
        name="Front-right window",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "frontRightWindow"),
    ),
    VolvoBinaryDescription(
        key="window_rl",
        name="Rear-left window",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "rearLeftWindow"),
    ),
    VolvoBinaryDescription(
        key="window_rr",
        name="Rear-right window",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "rearRightWindow"),
    ),
    VolvoBinaryDescription(
        key="sunroof",
        name="Sunroof",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda s: _open(s, "sunroof"),
    ),
    VolvoBinaryDescription(
        key="alarm_triggered",
        name="Alarm triggered",
        device_class=BinarySensorDeviceClass.SAFETY,
        value_fn=lambda s: _alarm(s, "alarmStatus"),
    ),
    VolvoBinaryDescription(
        key="charge_port",
        name="Charge port",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda s: _open(s, "tankLid"),
    ),
    VolvoBinaryDescription(
        key="plugged_in",
        name="Plugged in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=_connected,
    ),
    # Service / health warnings
    VolvoBinaryDescription(
        key="service_warning",
        name="Service warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "serviceWarning"),
    ),
    VolvoBinaryDescription(
        key="brake_fluid_warning",
        name="Brake fluid low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "brakeFluidLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="engine_coolant_warning",
        name="Engine coolant low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "engineCoolantLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="oil_level_warning",
        name="Oil level low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "oilLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="washer_fluid_warning",
        name="Washer fluid low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "washerFluidLevelWarning"),
    ),
    VolvoBinaryDescription(
        key="tyre_fl_warning",
        name="Tyre front-left pressure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "frontLeftTyrePressureWarning"),
    ),
    VolvoBinaryDescription(
        key="tyre_fr_warning",
        name="Tyre front-right pressure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "frontRightTyrePressureWarning"),
    ),
    VolvoBinaryDescription(
        key="tyre_rl_warning",
        name="Tyre rear-left pressure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "rearLeftTyrePressureWarning"),
    ),
    VolvoBinaryDescription(
        key="tyre_rr_warning",
        name="Tyre rear-right pressure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "rearRightTyrePressureWarning"),
    ),
    VolvoBinaryDescription(
        key="low_voltage_battery_warning",
        name="12V battery low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _warning(s, "lowVoltageBatteryWarning"),
    ),
    VolvoBinaryDescription(
        key="light_warning",
        name="Light warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_any_light_warning,
    ),
    VolvoBinaryDescription(
        key="in_use",
        name="In use",
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
    async_add_entities(VolvoBinarySensor(coordinator, d) for d in DESCRIPTIONS)


class VolvoBinarySensor(VolvoEntity, BinarySensorEntity):
    entity_description: VolvoBinaryDescription

    def __init__(
        self,
        coordinator: VolvoCoordinator,
        description: VolvoBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        try:
            return self.entity_description.value_fn(self.coordinator.data or {})
        except Exception:  # noqa: BLE001
            return None
