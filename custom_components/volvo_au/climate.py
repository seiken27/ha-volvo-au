"""Parking climate settings: temperature and seat or steering wheel heating, for cars that support them.

EXPERIMENTAL: written from the C3 ``ClimatizationStart`` message that Polestar uses (the same service on the same
backend) and the capability flags in Volvo's car record. The XC40 and EX40 do not offer these settings, so none of this
has been run against a real car that does. Pure Python (no Home Assistant imports) so it can be tested on its own.
"""

from __future__ import annotations

import struct
from typing import Any

# The car's own flag for each setting, and the field that says how many levels it has (when it says).
TEMPERATURE_FLAG = "supportsParkingClimatizationCompartmentTemperatureSettings"
SEATS = {
    # name: (flag, levels key, request field)
    "front_right_seat": ("supportsParkingClimatizationFrontSeatsSettings", "parkingClimatizationFrontSeatsSettingLevels", 4),
    "front_left_seat": ("supportsParkingClimatizationFrontSeatsSettings", "parkingClimatizationFrontSeatsSettingLevels", 5),
    "rear_right_seat": ("supportsParkingClimatizationRearSeatsSettings", "parkingClimatizationRearSeatsSettingLevels", 6),
    "rear_left_seat": ("supportsParkingClimatizationRearSeatsSettings", "parkingClimatizationRearSeatsSettingLevels", 7),
    "steering_wheel": ("supportsParkingClimatizationSteeringWheelSettings", "parkingClimatizationSteeringWheelSettingLevels", 8),
}
# C3 HeatingIntensity: OFF = 1, LEVEL1 = 2, LEVEL2 = 3, LEVEL3 = 4
_INTENSITY = {"off": 1, "level1": 2, "level2": 3, "level3": 4}
DEFAULT_TEMPERATURE = 21.0
DEFAULT_LIMITS = (16.0, 28.0)


def supported(car: dict[str, Any] | None, flag: str) -> bool:
    """True only when the car's record says so (the XC40 says false; a missing flag means no)."""
    return (car or {}).get(flag) is True


def seat_options(car: dict[str, Any] | None, levels_key: str) -> list[str]:
    """``off`` plus one option per level (the record's level count when it is a number, else three)."""
    value = (car or {}).get(levels_key)
    count = value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3 else 3
    return ["off"] + [f"level{n}" for n in range(1, count + 1)]


def temperature_limits(car: dict[str, Any] | None) -> tuple[float, float]:
    low = (car or {}).get("parkingClimatizationCompartmentTemperatureMinLimit")
    high = (car or {}).get("parkingClimatizationCompartmentTemperatureMaxLimit")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low < high:
        return float(low), float(high)
    return DEFAULT_LIMITS


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        out.append(b | (0x80 if value else 0))
        if not value:
            return bytes(out)


def start_message(vin: str, temperature: float | None = None, seats: dict[str, str] | None = None) -> bytes:
    """ClimatizationStartRequest: {1 = request {1 = vin}, 2 = start, 3 = temperature (float32), 4..8 = heating levels}."""
    inner = _varint(1 << 3 | 2) + _varint(len(vin)) + vin.encode("ascii")
    out = _varint(1 << 3 | 2) + _varint(len(inner)) + inner + _varint(2 << 3) + _varint(1)
    if temperature is not None:
        out += _varint(3 << 3 | 5) + struct.pack("<f", float(temperature))
    for name, level in sorted(((n, lv) for n, lv in (seats or {}).items() if n in SEATS), key=lambda item: SEATS[item[0]][2]):
        if level in _INTENSITY:
            out += _varint(SEATS[name][2] << 3) + _varint(_INTENSITY[level])
    return out
