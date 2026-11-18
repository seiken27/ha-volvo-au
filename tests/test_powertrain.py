"""Powertrain facts read from the C3 car record (the module has no Home Assistant imports)."""

import importlib.util
import re
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


# The real shape: an Australian XC40, read from the car on 18 Nov 2026 (flat keys, plain enum names).
XC40 = {
    "engineType": "ELECTRIC",
    "drivetrain": "AWD",
    "fuelTankVolume": None,
    "batteryCapacity": 78.182,
    "supportsEngineRemoteStart": False,
    "structureWeek": 202246,
    "registrationPlate": None,
}


def test_real_xc40_record():
    assert powertrain.engine_type(XC40) == "electric"
    assert powertrain.drivetrain_type(XC40) == "awd"
    assert powertrain.battery_capacity_kwh(XC40) == 78.2
    assert powertrain.has_combustion_engine(XC40) is False
    assert powertrain.supports_engine_remote_start(XC40) is False
    assert powertrain.structure_week(XC40) == "202246"
    assert powertrain.build_week(XC40) == "2022-W46"


@pytest.mark.parametrize(
    ("car", "engine", "combustion"),
    [
        ({"engineType": "PETROL_HYBRID"}, "petrol_hybrid", True),
        ({"engineType": "DIESEL", "fuelTankVolume": 60}, "diesel", True),
        ({"engineType": "ELECTRIC", "fuelTankVolume": 45}, "electric", True),
        ({"engineType": "SOMETHING_NEW"}, None, None),
    ],
)
def test_flat_shape_variants(car, engine, combustion):
    assert powertrain.engine_type(car) == engine
    assert powertrain.has_combustion_engine(car) is combustion


def test_engine_remote_start_flag_and_structure_week_edge_cases():
    assert powertrain.supports_engine_remote_start({"supportsEngineRemoteStart": True}) is True
    assert powertrain.supports_engine_remote_start({}) is None
    assert powertrain.supports_engine_remote_start(None) is None
    assert powertrain.structure_week({"structureWeek": 2022}) is None
    assert powertrain.build_week({}) is None


# The capability flags every gated entity reads, as the real Australian XC40 reported them (18 Nov 2026).
# reportsLightWarnings is shortened: only whether it has entries matters.
XC40_FLAGS = {
    "reportsAvgElectricConsumption": True,
    "reportsAvgSpeed": True,
    "reportsElectricRange": True,
    "reportsLightWarnings": [
        "LIGHT_WARNING_BRAKE_LIGHT_LEFT",
        "LIGHT_WARNING_HIGH_BEAM_LEFT"
    ],
    "reportsTyrePressure": False,
    "supportsAirPurification": True,
    "supportsAirPurificationInternalMeasurement": True,
    "supportsChargeAmperageLimitSetting": True,
    "supportsChargingFunctions": True,
    "supportsFlash": True,
    "supportsGlobalChargeTimer": True,
    "supportsHonkAndFlash": True,
    "supportsLock": True,
    "supportsLockReducedGuard": True,
    "supportsParkingClimatization": True,
    "supportsRemoteTrunkControl": True,
    "supportsRemoteTrunkUnlock": True,
    "supportsRemoteWindowControl": True,
    "supportsTargetChargeLevelSetting": True
}


def _gated_keys():
    """Every capability flag named by ``requires_capability`` or the button table in the integration source."""
    root = Path(__file__).parents[1] / "custom_components" / "volvo_au"
    keys = set()
    for path in root.glob("*.py"):
        keys |= set(re.findall(r'"((?:supports|reports)[A-Za-z]+)"', path.read_text()))
    return keys - {"supportsEngineRemoteStart", "supportsEngineStart"}


def test_capability_reads_flags_lists_and_unknowns():
    assert powertrain.capability({"supportsFlash": True}, "supportsFlash") is True
    assert powertrain.capability({"supportsFlash": False}, "supportsFlash") is False
    assert powertrain.capability({"reportsLightWarnings": ["a"]}, "reportsLightWarnings") is True
    assert powertrain.capability({"reportsLightWarnings": []}, "reportsLightWarnings") is False
    assert powertrain.capability({}, "supportsFlash") is None
    assert powertrain.capability(None, "supportsFlash") is None
    assert powertrain.capability({"supportsFlash": "yes"}, "supportsFlash") is None


def test_only_tyre_pressure_is_hidden_on_the_real_xc40():
    keys = _gated_keys()
    assert keys <= set(XC40_FLAGS), f"add these flags to XC40_FLAGS: {sorted(keys - set(XC40_FLAGS))}"
    hidden = {k for k in keys if powertrain.capability(XC40_FLAGS, k) is False}
    assert hidden == {"reportsTyrePressure"}
