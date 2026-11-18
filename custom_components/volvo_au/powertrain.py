"""Powertrain facts from the C3 ``/car-information/car`` reply (no Home Assistant imports).

The reply is protobuf JSON, so an enum can arrive as its name (``ENGINE_TYPE_PETROL_HYBRID``) or its
number, and a field with its default value (0 or false) is left out entirely.
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


def _propulsion(car: dict[str, Any] | None) -> dict[str, Any]:
    propulsion = (car or {}).get("propulsion")
    return propulsion if isinstance(propulsion, dict) else {}


def engine_type(car: dict[str, Any] | None) -> str | None:
    """``electric``, ``diesel``, ``petrol``, ``petrol_hybrid``, ``diesel_hybrid``, or ``None`` if unknown."""
    return _enum(_propulsion(car).get("engineType"), _ENGINE_TYPES, "ENGINE_TYPE_")


def drivetrain_type(car: dict[str, Any] | None) -> str | None:
    return _enum(_propulsion(car).get("drivetrainType"), _DRIVETRAIN_TYPES, "DRIVETRAIN_TYPE_")


def battery_capacity_kwh(car: dict[str, Any] | None) -> float | None:
    value = _propulsion(car).get("batteryCapacityKwh")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        kwh = float(value)
    except ValueError:
        return None
    return round(kwh, 1) if kwh > 0 else None


def has_combustion_engine(car: dict[str, Any] | None) -> bool | None:
    """True or False when the car says, ``None`` when it did not (so callers keep the entity)."""
    tank = _propulsion(car).get("fuelTankVolume")
    kind = engine_type(car)
    if kind in COMBUSTION_ENGINE_TYPES or (isinstance(tank, (int, float)) and not isinstance(tank, bool) and tank > 0):
        return True
    if kind == "electric":
        return False
    return None
