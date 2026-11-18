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
    "supportsDrivingJournal": True,
    "reportsAvgElectricConsumption": True,
    "reportsAvgSpeed": True,
    "reportsElectricRange": True,
    "reportsLightWarnings": [
        "LIGHT_WARNING_BRAKE_LIGHT_LEFT",
        "LIGHT_WARNING_HIGH_BEAM_LEFT"
    ],
    "reportsAbsoluteTyrePressure": False,
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
    assert hidden == {"reportsTyrePressure", "reportsAbsoluteTyrePressure"}


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"heatOrCoolAction": "HEAT_OR_COOL_ACTION_COOLING"}, "cooling"),
        ({"heatOrCoolAction": "HEAT_OR_COOL_ACTION_COOLING_FAST"}, "cooling"),
        ({"heatOrCoolAction": "HEAT_OR_COOL_ACTION_HEATING"}, "heating"),
        ({"heatOrCoolAction": "HEAT_OR_COOL_ACTION_VENTILATION_ONLY"}, "ventilating"),
        ({"heatOrCoolAction": "HEAT_OR_COOL_ACTION_NOT_REQUIRED"}, "not_required"),
        ({"runningStatus": "RUNNING_STATUS_ON_HEATING"}, "heating"),
        ({"runningStatus": "RUNNING_STATUS_OFF"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_climate_action(record, expected):
    assert powertrain.climate_action(record) == expected


def test_tyre_pressure_kpa():
    health = {"frontLeftTyrePressureKpa": 250.04, "rearRightTyrePressure": 240, "frontRightTyrePressureKpa": 0}
    assert powertrain.tyre_pressure_kpa(health, "front_left") == 250.0
    assert powertrain.tyre_pressure_kpa(health, "rear_right") == 240.0
    assert powertrain.tyre_pressure_kpa(health, "front_right") is None  # zero means no reading
    assert powertrain.tyre_pressure_kpa(health, "rear_left") is None
    assert powertrain.tyre_pressure_kpa(None, "front_left") is None
    assert powertrain.tyre_pressure_kpa(health, "spare") is None


def test_flatten_attributes():
    flat = powertrain.flatten_attributes(
        {
            "batteryCapacity": 78.182,
            "supportsFlash": True,
            "chargeAmperageMaxLimit": 32,
            "accountLinkCreationOptions": ["SCAN_QR", "ASK_OWNER"],
            "propulsion": {"engineType": "ENGINE_TYPE_ELECTRIC", "empty": None},
            "bidirectionalChargingGridOperatorInverterNumber": None,
            "blank": "",
            "none": [],
        }
    )
    assert flat == {
        "battery_capacity": 78.182,
        "supports_flash": True,
        "charge_amperage_max_limit": 32,
        "account_link_creation_options": "SCAN_QR, ASK_OWNER",
        "propulsion_engine_type": "ENGINE_TYPE_ELECTRIC",
    }
    assert powertrain.flatten_attributes(None) == {}


def test_driving_only_for_driving_mode():
    assert powertrain.driving("USAGE_MODE_DRIVING") is True
    for mode in ("USAGE_MODE_INACTIVE", "USAGE_MODE_ACTIVE", "USAGE_MODE_ABANDONED"):
        assert powertrain.driving(mode) is False
    assert powertrain.driving("USAGE_MODE_UNSPECIFIED") is None
    assert powertrain.driving(None) is None


def test_light_status_lists_each_light_and_flags_failures():
    problem, attrs = powertrain.light_status(
        {
            "frontLeftTurnIndicatorWarning": "LIGHT_WARNING_NO_WARNING",
            "rightLowBeamWarning": "LIGHT_WARNING_FAILURE",
            "highBeamWarning": "LIGHT_WARNING_UNSPECIFIED",
        }
    )
    assert problem is True
    assert attrs == {
        "front_left_turn_indicator": "ok",
        "right_low_beam": "failure",
        "high_beam": "unknown",
    }


def test_light_status_all_ok_and_nothing_known():
    assert powertrain.light_status({"aWarning": "X_NO_WARNING"})[0] is False
    assert powertrain.light_status({"aWarning": "X_UNKNOWN"})[0] is None
    assert powertrain.light_status({}) == (None, {})
    assert powertrain.light_status(None) == (None, {})


def test_in_use_follows_driving_or_the_car_in_use_reason():
    drive = {"usageMode": "USAGE_MODE_ACTIVE", "unavailableReason": "UNAVAILABLE_REASON_CAR_IN_USE"}
    assert powertrain.in_use({"usageMode": "USAGE_MODE_DRIVING"}, True) is True
    assert powertrain.in_use(drive, False) is True  # the 19 Nov drive: offline, "the car is in use"
    assert powertrain.in_use(drive, None) is True
    assert powertrain.in_use(drive, True) is False  # a stale reason on a car that is online again
    assert powertrain.in_use({"usageMode": "USAGE_MODE_INACTIVE", "unavailableReason": "UNAVAILABLE_REASON_NO_INTERNET"}, False) is False
    assert powertrain.in_use({}, None) is None
    assert powertrain.in_use(None, None) is None
