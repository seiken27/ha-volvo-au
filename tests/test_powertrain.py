"""Powertrain facts read from the C3 car record (the module has no Home Assistant imports)."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "powertrain", Path(__file__).parents[1] / "custom_components" / "volvo_au" / "powertrain.py"
)
powertrain = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(powertrain)


@pytest.mark.parametrize(
    ("car", "engine", "combustion"),
    [
        ({"propulsion": {"engineType": "ENGINE_TYPE_ELECTRIC"}}, "electric", False),
        ({"propulsion": {"engineType": 1}}, "electric", False),
        ({"propulsion": {"engineType": "ENGINE_TYPE_PETROL_HYBRID"}}, "petrol_hybrid", True),
        ({"propulsion": {"engineType": "3"}}, "petrol", True),
        ({"propulsion": {"engineType": "ENGINE_TYPE_DIESEL"}}, "diesel", True),
        ({"propulsion": {"engineType": "ENGINE_TYPE_UNSPECIFIED"}}, None, None),
        ({"propulsion": {"engineType": "ENGINE_TYPE_SOMETHING_NEW"}}, None, None),
        ({"propulsion": {}}, None, None),
        ({}, None, None),
        (None, None, None),
        ({"propulsion": {"engineType": "ENGINE_TYPE_ELECTRIC", "fuelTankVolume": 40}}, "electric", True),
    ],
)
def test_engine_type_and_combustion(car, engine, combustion):
    assert powertrain.engine_type(car) == engine
    assert powertrain.has_combustion_engine(car) is combustion


def test_drivetrain_and_battery_capacity():
    car = {"propulsion": {"drivetrainType": "DRIVETRAIN_TYPE_AWD", "batteryCapacityKwh": 81.608}}
    assert powertrain.drivetrain_type(car) == "awd"
    assert powertrain.battery_capacity_kwh(car) == 81.6
    assert powertrain.drivetrain_type({}) is None
    assert powertrain.battery_capacity_kwh({"propulsion": {"batteryCapacityKwh": 0}}) is None
    assert powertrain.battery_capacity_kwh({"propulsion": {"batteryCapacityKwh": "x"}}) is None
