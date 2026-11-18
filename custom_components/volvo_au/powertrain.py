"""Powertrain facts from the C3 ``/car-information/car`` reply (no Home Assistant imports).

Checked against a real XC40 (Australia): the record is flat, with ``engineType: "ELECTRIC"``,
``drivetrain: "AWD"``, ``batteryCapacity: 78.182``, ``fuelTankVolume: null`` and
``supportsEngineRemoteStart: false``. The Polestar app's protobuf nests these under ``propulsion``
(``engineType``, ``drivetrainType``, ``batteryCapacityKwh``), so both shapes are read, and an enum can be a
name (``ELECTRIC`` or ``ENGINE_TYPE_ELECTRIC``) or a number.
"""

from __future__ import annotations

from typing import Any

_ENGINE_TYPES = {
    0: None,
    1: "electric",
    2: "diesel",
    3: "petrol",
    4: "petrol_hybrid",
    5: "diesel_hybrid",
}
_DRIVETRAIN_TYPES = {0: None, 1: "awd", 2: "rwd", 3: "fwd"}
COMBUSTION_ENGINE_TYPES = frozenset({"diesel", "petrol", "petrol_hybrid", "diesel_hybrid"})


def _enum(value: Any, numbers: dict[int, str | None], prefix: str) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return numbers.get(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return numbers.get(int(text))
        text = text.upper().removeprefix(prefix)
        name = text.lower()
        return name if name in numbers.values() else None
    return None


def _first(car: dict[str, Any] | None, *paths: tuple[str, ...]) -> Any:
    """The first non-null value found at any of ``paths`` (a key, or a key inside ``propulsion``)."""
    for path in paths:
        node: Any = car or {}
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            return node
    return None


def engine_type(car: dict[str, Any] | None) -> str | None:
    """``electric``, ``diesel``, ``petrol``, ``petrol_hybrid``, ``diesel_hybrid``, or ``None`` if unknown."""
    return _enum(_first(car, ("engineType",), ("propulsion", "engineType")), _ENGINE_TYPES, "ENGINE_TYPE_")


def drivetrain_type(car: dict[str, Any] | None) -> str | None:
    return _enum(
        _first(car, ("drivetrain",), ("propulsion", "drivetrainType")), _DRIVETRAIN_TYPES, "DRIVETRAIN_TYPE_"
    )


def supports_engine_remote_start(car: dict[str, Any] | None) -> bool | None:
    """The car's own flag, or ``None`` when the reply does not have it."""
    value = _first(car, ("supportsEngineRemoteStart",), ("supportsEngineStart",))
    return value if isinstance(value, bool) else None


def battery_capacity_kwh(car: dict[str, Any] | None) -> float | None:
    value = _first(car, ("batteryCapacity",), ("propulsion", "batteryCapacityKwh"))
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        kwh = float(value)
    except ValueError:
        return None
    return round(kwh, 1) if kwh > 0 else None


def has_combustion_engine(car: dict[str, Any] | None) -> bool | None:
    """True or False when the car says, ``None`` when it did not (so callers keep the entity)."""
    tank = _first(car, ("fuelTankVolume",), ("propulsion", "fuelTankVolume"))
    kind = engine_type(car)
    if kind in COMBUSTION_ENGINE_TYPES or (isinstance(tank, (int, float)) and not isinstance(tank, bool) and tank > 0):
        return True
    if kind == "electric":
        return False
    return None


def structure_week(car: dict[str, Any] | None) -> str | None:
    """The production structure week as ``YYYYWW`` (the reply has it as a number, e.g. 202246)."""
    value = (car or {}).get("structureWeek")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value).strip()
    return text if text.isdigit() and len(text) == 6 else None


def build_week(car: dict[str, Any] | None) -> str | None:
    """``202246`` -> ``2022-W46``."""
    week = structure_week(car)
    return f"{week[:4]}-W{week[4:]}" if week else None


def capability(car: dict[str, Any] | None, key: str) -> bool | None:
    """One of the car's own ``supports...`` / ``reports...`` flags.

    ``False`` means the car says it is not supported, ``True`` that it is, and ``None`` that the reply does
    not say (a missing record, a missing key, or an unexpected value), so callers keep the entity.
    A list (``reportsLightWarnings``) counts as supported when it has any entries.
    """
    value = (car or {}).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return bool(value)
    return None


# --- Live readings (unverified key names: no test car reports them yet) --------------------------------------

_ACTIONS = ("cooling_fast", "cooling", "heating", "ventilation_only", "ventilation", "not_required")


def climate_action(climatization: dict | None) -> str | None:
    """Heating, cooling or ventilating, from the parking climatisation record.

    Reads ``heatOrCoolAction`` (the C3 field is ``heat_or_cool_action``) and falls back to the
    running-status variants (``RUNNING_STATUS_ON_HEATING`` ...). Returns None when the car says nothing.
    """
    if not isinstance(climatization, dict):
        return None
    for raw in (climatization.get("heatOrCoolAction"), climatization.get("runningStatus")):
        if not isinstance(raw, str):
            continue
        text = raw.lower()
        for action in _ACTIONS:
            if action.replace("_", "") in text.replace("_", ""):
                return {"cooling_fast": "cooling", "ventilation": "ventilating", "ventilation_only": "ventilating"}.get(
                    action, action
                )
    return None


_WHEELS = {
    "front_left": "frontLeft",
    "front_right": "frontRight",
    "rear_left": "rearLeft",
    "rear_right": "rearRight",
}


def tyre_pressure_kpa(health: dict | None, wheel: str) -> float | None:
    """Pressure in kPa for ``front_left`` / ``front_right`` / ``rear_left`` / ``rear_right``."""
    if not isinstance(health, dict) or wheel not in _WHEELS:
        return None
    prefix = _WHEELS[wheel]
    for key in (f"{prefix}TyrePressureKpa", f"{prefix}TyrePressure"):
        value = health.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return round(float(value), 1)
    return None


def flatten_attributes(data, prefix: str = "") -> dict:
    """Turn nested data into flat snake_case attributes so Home Assistant lists them one per row.

    ``{"batteryCapacity": 78.1, "propulsion": {"engineType": "X"}}`` becomes
    ``{"battery_capacity": 78.1, "propulsion_engine_type": "X"}``. Empty values are left out and
    lists of simple values become one comma-separated string.
    """
    import re

    out: dict = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        name = re.sub(r"[^0-9a-z]+", "_", re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", f"{prefix}{key}").lower()).strip("_")
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            out.update(flatten_attributes(value, f"{name}_"))
        elif isinstance(value, (list, tuple)):
            out[name] = ", ".join(str(item) for item in value)
        else:
            out[name] = value
    return out


def driving(usage_mode: Any) -> bool | None:
    """True only while Volvo reports the car is being driven (``USAGE_MODE_DRIVING``).

    Inactive, Active and Abandoned all mean not driving. ``None`` when Volvo says nothing useful.
    """
    if not isinstance(usage_mode, str) or not usage_mode:
        return None
    if usage_mode.endswith(("_UNSPECIFIED", "_UNKNOWN")):
        return None
    return usage_mode.endswith("_DRIVING")


def _snake(name: str) -> str:
    out = []
    for ch in name:
        if ch.isupper() and out:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def light_status(light_warnings: Any) -> tuple[bool | None, dict[str, str]]:
    """(any light failing?, per-light status) from the health ``lightWarnings`` map.

    Names become ``front_left_turn_indicator`` (camelCase to snake_case, trailing ``warning`` dropped).
    A value is ``ok``, ``failure`` or ``unknown``. The problem flag is ``None`` when no light reports.
    """
    if not isinstance(light_warnings, dict) or not light_warnings:
        return None, {}
    attrs: dict[str, str] = {}
    saw_known = bad = False
    for key, value in light_warnings.items():
        name = _snake(str(key))
        name = name[: -len("_warning")] if name.endswith("_warning") else name
        if not isinstance(value, str) or value.endswith(("_UNSPECIFIED", "_UNKNOWN")):
            attrs[name] = "unknown"
            continue
        saw_known = True
        if value.endswith("_NO_WARNING"):
            attrs[name] = "ok"
        else:
            attrs[name] = "failure"
            bad = True
    return (bad if saw_known else None), attrs
