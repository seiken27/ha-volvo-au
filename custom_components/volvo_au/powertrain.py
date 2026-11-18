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
