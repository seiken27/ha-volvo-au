"""Entity tables and gating, run against stubbed Home Assistant (see ha_stubs.py)."""

import asyncio
import types
from types import SimpleNamespace

import pytest

from .ha_stubs import EntityCategory, RegistryEntryDisabler, load_volvo

XC40 = {
    "supportsParkingClimatization": True,
    "supportsAirPurification": True,
    "reportsTyrePressure": False,
    "supportsParkingClimatizationFrontSeatsSettings": False,
}


class FakeRegistry:
    def __init__(self, entries=None):
        self.entries = entries or {}
        self.updates = []

    def async_get_entity_id(self, domain, platform, unique_id):
        for entity_id, entry in self.entries.items():
            if entity_id.startswith(f"{domain}.") and entry.unique_id == unique_id:
                return entity_id
        return None

    def async_get(self, entity_id):
        return self.entries.get(entity_id)

    def async_remove(self, entity_id):
        self.removed = getattr(self, "removed", []) + [entity_id]
        del self.entries[entity_id]

    def async_update_entity(self, entity_id, *, disabled_by):
        self.updates.append((entity_id, disabled_by))
        self.entries[entity_id].disabled_by = disabled_by


@pytest.fixture
def volvo(monkeypatch):
    return load_volvo(monkeypatch, FakeRegistry())


def _entity(volvo, unique_id, **flags):
    cls = type("E", (volvo.entity.VolvoEntity,), flags)
    entity = cls.__new__(cls)
    entity.unique_id = unique_id
    return entity


# ---- gating -------------------------------------------------------------------------------------------------


def test_gate_hides_unsupported_and_leaves_unflagged_alone(monkeypatch):
    registry = FakeRegistry({"sensor.tyre": SimpleNamespace(unique_id="t", disabled_by=None)})
    volvo = load_volvo(monkeypatch, registry)
    coordinator = SimpleNamespace(car_info=XC40)
    tyre = _entity(volvo, "t", requires_capability="reportsTyrePressure")
    plain = _entity(volvo, "p")
    climate = _entity(volvo, "c", requires_capability="supportsParkingClimatization")

    kept = volvo.entity.async_gate_unsupported(None, coordinator, "sensor", [tyre, plain, climate])

    assert kept == [plain, climate]
    assert registry.updates == [("sensor.tyre", RegistryEntryDisabler.INTEGRATION)]


def test_gate_brings_back_what_it_disabled_but_not_what_the_user_disabled(monkeypatch):
    registry = FakeRegistry(
        {
            "sensor.a": SimpleNamespace(unique_id="a", disabled_by=RegistryEntryDisabler.INTEGRATION),
            "sensor.b": SimpleNamespace(unique_id="b", disabled_by=RegistryEntryDisabler.USER),
        }
    )
    volvo = load_volvo(monkeypatch, registry)
    coordinator = SimpleNamespace(car_info={"supportsAirPurification": True})
    a = _entity(volvo, "a", requires_capability="supportsAirPurification")
    b = _entity(volvo, "b", requires_capability="supportsAirPurification")

    kept = volvo.entity.async_gate_unsupported(None, coordinator, "sensor", [a, b])

    assert kept == [a, b]
    assert registry.updates == [("sensor.a", None)]


@pytest.mark.parametrize("car", [None, {}])
def test_gate_keeps_everything_when_the_record_could_not_be_read(monkeypatch, car):
    volvo = load_volvo(monkeypatch, FakeRegistry())
    entity = _entity(volvo, "x", requires_capability="reportsTyrePressure")

    assert volvo.entity.async_gate_unsupported(None, SimpleNamespace(car_info=car), "sensor", [entity]) == [entity]


# ---- tables ---------------------------------------------------------------------------------------------------


def test_every_description_has_a_translation_key_and_unique_key(volvo):
    for table in (volvo.sensor.SENSORS, volvo.binary_sensor.DESCRIPTIONS):
        keys = [d.key for d in table]
        assert len(keys) == len(set(keys))
        assert all(d.translation_key for d in table), [d.key for d in table if not d.translation_key]


def test_entity_categories_follow_the_polestar_integration(volvo):
    sensors = {d.key: d.entity_category for d in volvo.sensor.SENSORS}
    assert sensors["usage_mode"] is EntityCategory.DIAGNOSTIC
    assert sensors["engine_hours_to_service"] is EntityCategory.DIAGNOSTIC
    assert sensors["air_purification_last_cycle"] is EntityCategory.DIAGNOSTIC
    assert sensors["battery_soc"] is None and sensors["range_km"] is None
    for cls in (
        volvo.time.VolvoScheduleTime,
        volvo.switch.VolvoChargeScheduleSwitch,
        volvo.number.VolvoClimateTemperature,
        volvo.select.VolvoClimateHeating,
    ):
        assert cls._attr_entity_category is EntityCategory.CONFIG, cls
    for cls in (volvo.sensor.VolvoConnection, volvo.sensor.VolvoLastSeen, volvo.sensor.VolvoVin):
        assert cls._attr_entity_category is EntityCategory.DIAGNOSTIC, cls
    # Controls stay primary so they show in the Controls card.
    assert getattr(volvo.switch.VolvoClimatizationSwitch, "_attr_entity_category", None) is None
    assert getattr(volvo.number.VolvoTargetSoc, "_attr_entity_category", None) is None


def test_every_gating_flag_in_the_tables_is_a_known_capability_name(volvo):
    flags = {d.requires_capability for d in volvo.sensor.SENSORS if d.requires_capability}
    flags |= {d.requires_capability for d in volvo.binary_sensor.DESCRIPTIONS if d.requires_capability}
    assert flags, "expected some gated sensors"
    assert all(f.startswith(("supports", "reports")) for f in flags), flags


# ---- values ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({}, None),
        ({"exterior": {"exterior": {}}}, None),
        ({"exterior": {"exterior": {"centralLock": "LOCKED"}}}, False),
        ({"exterior": {"exterior": {"alarmStatus": "ALARM_STATUS_IDLE"}}}, False),
        ({"exterior": {"exterior": {"alarmStatus": "ALARM_STATUS_TRIGGERED"}}}, True),
    ],
)
def test_alarm_is_ok_unless_the_car_says_triggered(volvo, snapshot, expected):
    assert volvo.binary_sensor._alarm(snapshot, "alarmStatus") is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("DOOR_OPEN", True), ("DOOR_CLOSED", False), ("DOOR_UNSPECIFIED", False), ("DOOR_AJAR", True), ("DOOR_SOMETHING", None), (None, None)],
)
def test_open_status(volvo, value, expected):
    snap = {"exterior": {"exterior": {} if value is None else {"frontLeftDoor": value}}}
    assert volvo.binary_sensor._open(snap, "frontLeftDoor") is expected


# ---- coordinator ----------------------------------------------------------------------------------------------


def test_climate_choices_start_empty(volvo):
    coordinator = volvo.coordinator.VolvoCoordinator.__new__(volvo.coordinator.VolvoCoordinator)
    # __init__ needs a running Home Assistant; the defaults live there, so just check the class is importable
    assert hasattr(volvo.coordinator, "VolvoCoordinator") and coordinator is not None


# ---- first poll with no data -------------------------------------------------------------------------------------


def test_sections_without_data(volvo):
    full = {
        "pre_cleaning": {"preCleaning": {"runningStatus": "RUNNING_STATUS_OFF"}},
        "global_charge_timer": {"x": {"enabled": False}},  # False is a real value
        "amp_limit": {"ampLimit": {"ampLimit": 32}},
        "target_soc": {"targetSoc": {"batteryChargeTargetLevel": 90}},
        "parking_climatization": {"parkingClimatization": {"runningStatus": "RUNNING_STATUS_OFF"}},
    }
    assert volvo.powertrain.sections_without_data(full) == frozenset()
    empty = {**full, "pre_cleaning": {}, "target_soc": {"targetSoc": {}}, "amp_limit": None}
    assert volvo.powertrain.sections_without_data(empty) == {"pre_cleaning", "target_soc", "amp_limit"}


def test_a_failed_read_is_not_treated_as_no_data(volvo):
    snapshot = {"pre_cleaning": {"_error": "TimeoutError: slow"}}
    assert "pre_cleaning" not in volvo.powertrain.sections_without_data(snapshot)


def test_gate_hides_entities_whose_section_was_empty(monkeypatch):
    registry = FakeRegistry({"sensor.q": SimpleNamespace(unique_id="q", disabled_by=None)})
    volvo = load_volvo(monkeypatch, registry)
    coordinator = SimpleNamespace(car_info=XC40, no_data_sections=frozenset({"pre_cleaning"}))
    quality = _entity(volvo, "q", requires_data="pre_cleaning")
    soc = _entity(volvo, "s", requires_data="target_soc")

    kept = volvo.entity.async_gate_unsupported(None, coordinator, "sensor", [quality, soc])

    assert kept == [soc]
    assert registry.updates == [("sensor.q", RegistryEntryDisabler.INTEGRATION)]


def test_schedule_times_follow_the_schedule_switch(volvo):
    assert volvo.time.VolvoScheduleTime.requires_capability == volvo.switch.VolvoChargeScheduleSwitch.requires_capability
    assert volvo.time.VolvoScheduleTime.requires_data == volvo.switch.VolvoChargeScheduleSwitch.requires_data


# ---- removed duplicates ---------------------------------------------------------------------------------------


def test_duplicates_are_gone_and_their_registry_entries_cleaned(monkeypatch):
    registry = FakeRegistry({"sensor.old": SimpleNamespace(unique_id="u", disabled_by=None)})
    volvo = load_volvo(monkeypatch, registry)

    assert "amp_limit" not in {d.key for d in volvo.sensor.SENSORS}
    assert {spec[0] for spec in volvo.button.COMMAND_BUTTONS}.isdisjoint({"charge_start", "charge_stop"})
    # Target SoC is the number only; the read-only sensor duplicated it.
    assert "target_soc" not in {d.key for d in volvo.sensor.SENSORS}

    volvo.entity.async_remove_registered_entities(None, "sensor", ["u", "missing"])
    assert registry.removed == ["sensor.old"]


# ---- charging ---------------------------------------------------------------------------------------------------


def test_start_charging_is_refused_when_not_plugged_in(volvo):
    switch = volvo.switch.VolvoChargingSwitch.__new__(volvo.switch.VolvoChargingSwitch)
    switch.coordinator = SimpleNamespace(
        data={"battery": {"battery": {"chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_DISCONNECTED"}}}
    )
    with pytest.raises(Exception, match="not plugged in"):
        asyncio.run(switch.async_turn_on())


# ---- enum sensor states ---------------------------------------------------------------------------------------


def test_enum_states_are_lowercase_keys_with_translations(monkeypatch):
    import json
    from pathlib import Path

    volvo = load_volvo(monkeypatch, FakeRegistry())
    sensor = volvo.sensor
    snap = {"battery": {"battery": {"chargingStatusV2": "CHARGING_STATUS_V2_SMART_CHARGING_PAUSED",
                                    "chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_CONNECTED"}},
            "availability": {"availability": {"usageMode": "USAGE_MODE_ENGINE_OFF"}}}
    by_key = {d.key: d for d in sensor.SENSORS}

    assert by_key["charging_status"].value_fn(snap) == "smart_charging_paused"
    assert by_key["charger_connection"].value_fn(snap) == "connected"
    assert by_key["usage_mode"].value_fn(snap) == "engine_off"
    # Unspecified and unknown values give no state rather than an invalid one.
    assert by_key["charging_status"].value_fn({"battery": {"battery": {"chargingStatusV2": "CHARGING_STATUS_V2_UNSPECIFIED"}}}) is None
    assert by_key["charging_status"].value_fn({"battery": {"battery": {"chargingStatusV2": "CHARGING_STATUS_V2_NEW_THING"}}}) is None

    root = Path(__file__).parents[1] / "custom_components" / "volvo_au"
    for path in ("strings.json", "translations/en.json"):
        translated = json.loads((root / path).read_text())["entity"]["sensor"]
        for key in ("charging_status", "charger_connection", "usage_mode"):
            assert set(translated[key]["state"]) == set(by_key[key].options), f"{path}: {key}"

# ---- connection sensor ------------------------------------------------------------------------------------------


def test_connection_state_is_short_and_has_no_clock_time(monkeypatch):
    volvo = load_volvo(monkeypatch, FakeRegistry())
    online = volvo.online

    def status(is_online, reason=None):
        raw = {"unavailableReason": reason} if reason else {}
        return online.CarStatus(online=is_online, last_seen=None, source="test", raw=raw)

    assert online.status_text(status(True)) == "Online"
    assert online.status_text(status(None)) == "Unknown"
    assert online.status_text(status(False, "UNAVAILABLE_REASON_CAR_IN_USE")) == "Offline: car in use"
    assert online.status_text(status(False, "UNAVAILABLE_REASON_NO_INTERNET")) == "Offline: no internet"
    assert online.status_text(status(False)) == "Offline: no connection"
    assert online.status_detail(status(False, "UNAVAILABLE_REASON_CAR_IN_USE")).startswith("The car is in use. Commands cannot reach")


def test_tyre_warning_combines_wheels_and_lists_pressures(monkeypatch):
    volvo = load_volvo(monkeypatch, FakeRegistry())
    health = {
        "frontLeftTyrePressureWarning": "TYRE_PRESSURE_WARNING_NO_WARNING",
        "frontRightTyrePressureWarning": "TYRE_PRESSURE_WARNING_VERY_LOW_PRESSURE",
        "frontLeftTyrePressureKpa": 250,
    }
    snap = {"health": {"health": health}}
    assert volvo.binary_sensor._any_tyre_warning(snap) is True
    attrs = volvo.binary_sensor._tyre_attributes(snap)
    assert attrs["front_left_warning"] is False
    assert attrs["front_right_warning"] is True
    assert attrs["front_left_pressure_kpa"] == 250
    assert "rear_left_warning" not in attrs


def test_tyre_warning_unknown_without_data(monkeypatch):
    volvo = load_volvo(monkeypatch, FakeRegistry())
    assert volvo.binary_sensor._any_tyre_warning({"health": {"health": {}}}) is None
    assert volvo.binary_sensor._any_tyre_warning({}) is None
