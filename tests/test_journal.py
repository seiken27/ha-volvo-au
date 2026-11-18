import importlib.util
import struct
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location("journal_under_test", Path(__file__).parent.parent / "custom_components/volvo_au/journal.py")
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)

RAW = {
    "category": "UNASSIGNED", "id": "abc", "name": "", "trip": None, "userNotes": "",
    "tripDetails": [{
        "distance": 4850, "electricalConsumption": 1200.07, "electricalRegeneration": 0.4, "fuelConsumption": 0.0,
        "startOdometer": 57059000, "endOdometer": 57063000,
        "startTime": 1794989788157, "endTime": 1794990320486,
        "startPosition": {"city": "A", "streetName": "Main St", "streetNumber": "1", "latitude": 1.5, "longitude": 2.5},
        "endPosition": {"city": "B", "streetAddress": "2 Side Rd"},
    }],
}


def test_parse_trip_units():
    t = journal.parse_trip(RAW)
    assert t["distance_km"] == 4.85
    assert t["energy_used_kwh"] == 1.2
    assert t["energy_regenerated_kwh"] == 0.4
    assert t["duration_min"] == 8.9
    assert t["average_speed_kmh"] == 32.8
    assert t["average_consumption_kwh_100km"] == 24.7
    assert t["category"] == "unassigned" and t["name"] is None and t["notes"] is None
    assert t["start_odometer_km"] == 57059.0
    assert t["start_place"]["street"] == "Main St 1" and t["end_place"]["street"] == "2 Side Rd"
    assert "start_latitude" not in t
    assert journal.parse_trip(RAW, with_coordinates=True)["start_latitude"] == 1.5
    assert datetime.fromisoformat(t["end_time"]).year == 2026


def test_parse_trips_skips_empty():
    assert journal.parse_trips({"trips": [{"id": "x", "tripDetails": []}, RAW]})[0]["id"] == "abc"
    assert journal.parse_trips(None) == []


def test_place_text():
    assert journal.place_text({"street": "Main St 1", "city": "A"}) == "Main St 1, A"
    assert journal.place_text(None) is None


def test_summary_request_roundtrip():
    framed = journal.summary_request("VIN", 1000, 2000)
    assert framed[0] == 0 and struct.unpack(">I", framed[1:5])[0] == len(framed) - 5
    top = journal._fields(framed[5:])
    assert top[1] == b"VIN"
    tr = journal._fields(journal._fields(top[2])[2])
    assert (tr[1], tr[2]) == (1000, 2000)


def test_parse_summary():
    def ld(f, p): return journal._len_delim(f, p)
    def vi(f, v): return journal._varint(f << 3) + journal._varint(v)
    dbl = lambda x: journal._varint(1 << 3 | 1) + struct.pack("<d", x)  # noqa: E731
    frame = (vi(1, 336) + vi(2, 3556023) + vi(3, 276118995) + ld(4, b"") + ld(5, dbl(679.06)) + ld(6, dbl(19.1))
             + ld(7, b"") + ld(9, ld(1, b"AUD") + vi(2, 259) + vi(3, 327283120)))
    s = journal.parse_summary(frame)
    assert s["trip_count"] == 336 and s["distance_km"] == 3556.0 and s["duration_h"] == 76.7
    assert s["energy_used_kwh"] == 679.1 and s["average_consumption_kwh_100km"] == 19.1
    assert s["electricity_cost"] == {"currency": "AUD", "amount": 259.33}


def test_month_start():
    from datetime import timedelta, timezone

    tz = timezone(timedelta(hours=11))
    assert journal.month_start(datetime(2026, 11, 19, 14, 30, 5, 99, tzinfo=tz)) == datetime(2026, 11, 1, tzinfo=tz)
    assert journal.month_start(datetime(2026, 12, 1, 0, 0, tzinfo=UTC)) == datetime(2026, 12, 1, tzinfo=UTC)
