"""Parking climate settings (pure module, no Home Assistant imports)."""

import importlib.util
import struct
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "climate", Path(__file__).parents[1] / "custom_components" / "volvo_au" / "climate.py"
)
climate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(climate)

VIN = "YV1ABC12345678901"
BASE = b"\x0a\x13\x0a\x11" + VIN.encode() + b"\x10\x01"


def test_plain_start_is_unchanged():
    assert climate.start_message(VIN) == BASE


def test_temperature_and_seats_appended_in_field_order():
    msg = climate.start_message(VIN, 22.5, {"steering_wheel": "level2", "front_left_seat": "level3", "front_right_seat": "off"})
    assert msg == BASE + b"\x1d" + struct.pack("<f", 22.5) + b"\x20\x01" + b"\x28\x04" + b"\x40\x03"


def test_unknown_names_and_levels_ignored():
    assert climate.start_message(VIN, None, {"nope": "level1", "rear_left_seat": "level9"}) == BASE


def test_supported_only_when_true():
    assert climate.supported({"x": True}, "x")
    assert not climate.supported({"x": False}, "x")
    assert not climate.supported({}, "x")
    assert not climate.supported(None, "x")


def test_options_and_limits():
    assert climate.seat_options({}, "k") == ["off", "level1", "level2", "level3"]
    assert climate.seat_options({"k": 2}, "k") == ["off", "level1", "level2"]
    assert climate.seat_options({"k": True}, "k") == ["off", "level1", "level2", "level3"]
    assert climate.temperature_limits({}) == (16.0, 28.0)
    car = {
        "parkingClimatizationCompartmentTemperatureMinLimit": 18,
        "parkingClimatizationCompartmentTemperatureMaxLimit": 26,
    }
    assert climate.temperature_limits(car) == (18.0, 26.0)
