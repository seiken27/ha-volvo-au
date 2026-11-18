"""Volvo driving journal (trips): request building and reply parsing.

Pure Python (no Home Assistant imports) so it can be tested on its own.

Two APIs, both found in the Volvo app:
- REST ``GET /journallog-trip-api/vehicles/{vin}/trips?quantity=&startDate=&stopDate=`` lists trips (startDate and
  stopDate are ISO timestamps such as 2026-11-19T00:00:00Z; a plain date is refused with HTTP 400).
- gRPC ``tripmanager.publicapi.mobile.v1.TripManagerMobileService/GetTripsSummary`` totals trips over a time range.

Units seen on a real XC40 (19 Nov 2026): times are epoch milliseconds, distance is metres, odometers are metres,
``electricalConsumption`` looks like Wh and ``electricalRegeneration`` like kWh. Those two are not confirmed.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime
from typing import Any

SUMMARY_PATH = "/tripmanager.publicapi.mobile.v1.TripManagerMobileService/GetTripsSummary"
TRIPS_PATH = "/journallog-trip-api/vehicles/{vin}/trips"


def month_start(now: datetime) -> datetime:
    """Midnight on the first day of ``now``'s month, in the same time zone."""
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _place(pos: dict[str, Any] | None) -> dict[str, Any]:
    pos = pos or {}
    street = " ".join(str(pos[k]) for k in ("streetName", "streetNumber") if pos.get(k)) or pos.get("streetAddress")
    return {
        "street": street or None,
        "city": pos.get("city") or None,
        "region": pos.get("region") or None,
        "postal_code": pos.get("postalCode") or None,
        "country": pos.get("iso2CountryCode") or None,
    }


def _ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def parse_trip(raw: dict[str, Any], *, with_coordinates: bool = False) -> dict[str, Any] | None:
    """Flatten one trip from the REST list into plain, unit-converted fields. None when it has no details."""
    details = [d for d in (raw.get("tripDetails") or []) if isinstance(d, dict)]
    if not details:
        return None
    first, last = details[0], details[-1]
    start_ms, end_ms = first.get("startTime"), last.get("endTime")
    distance_m = sum(d.get("distance") or 0 for d in details)
    used_kwh = sum(d.get("electricalConsumption") or 0 for d in details) / 1000.0  # Wh -> kWh
    regen_kwh = sum(d.get("electricalRegeneration") or 0 for d in details)
    fuel_l = sum(d.get("fuelConsumption") or 0 for d in details)
    duration_s = (end_ms - start_ms) / 1000 if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)) else None
    km = distance_m / 1000.0
    trip: dict[str, Any] = {
        "id": raw.get("id"),
        "category": (raw.get("category") or "").lower() or None,
        "name": raw.get("name") or None,
        "notes": raw.get("userNotes") or None,
        "start_time": _ms_to_iso(start_ms),
        "end_time": _ms_to_iso(end_ms),
        "distance_km": round(km, 2),
        "duration_min": round(duration_s / 60, 1) if duration_s else None,
        "average_speed_kmh": round(km / (duration_s / 3600), 1) if duration_s and km else None,
        "energy_used_kwh": round(used_kwh, 3),
        "energy_regenerated_kwh": round(regen_kwh, 3),
        "average_consumption_kwh_100km": round(used_kwh / km * 100, 1) if km and used_kwh else None,
        "fuel_used_l": round(fuel_l, 2) if fuel_l else None,
        "start_odometer_km": round(first["startOdometer"] / 1000, 1) if first.get("startOdometer") is not None else None,
        "end_odometer_km": round(last["endOdometer"] / 1000, 1) if last.get("endOdometer") is not None else None,
        "start_place": _place(first.get("startPosition")),
        "end_place": _place(last.get("endPosition")),
    }
    if with_coordinates:
        for key, pos in (("start", first.get("startPosition")), ("end", last.get("endPosition"))):
            pos = pos or {}
            trip[f"{key}_latitude"], trip[f"{key}_longitude"] = pos.get("latitude"), pos.get("longitude")
    return trip


def parse_trips(body: Any, *, with_coordinates: bool = False) -> list[dict[str, Any]]:
    """Trips from the REST reply, newest first as the server sends them."""
    out = []
    for raw in (body or {}).get("trips") or []:
        trip = parse_trip(raw, with_coordinates=with_coordinates)
        if trip:
            out.append(trip)
    return out


def place_text(place: dict[str, Any] | None) -> str | None:
    place = place or {}
    parts = [place.get("street"), place.get("city")]
    return ", ".join(p for p in parts if p) or None


# ----- GetTripsSummary (protobuf) -----


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _len_delim(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def summary_request(vin: str, start_ms: int, end_ms: int) -> bytes:
    """GetTripsSummaryRequest {vin = 1, filters = 2 {time_range = 2 {start_ms = 1, end_ms = 2}}}, gRPC-framed."""
    time_range = _varint(1 << 3) + _varint(start_ms) + _varint(2 << 3) + _varint(end_ms)
    body = _len_delim(1, vin.encode()) + _len_delim(2, _len_delim(2, time_range))
    return b"\x00" + struct.pack(">I", len(body)) + body


def _fields(buf: bytes) -> dict[int, Any]:
    out: dict[int, Any] = {}
    i = 0
    while i < len(buf):
        key = shift = 0
        while True:
            b = buf[i]; i += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        field, wire = key >> 3, key & 7
        if wire == 0:
            val = shift = 0
            while True:
                b = buf[i]; i += 1
                val |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            out[field] = val
        elif wire == 1:
            out[field] = struct.unpack("<d", buf[i:i + 8])[0]; i += 8
        elif wire == 5:
            out[field] = struct.unpack("<f", buf[i:i + 4])[0]; i += 4
        elif wire == 2:
            length = shift = 0
            while True:
                b = buf[i]; i += 1
                length |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            out[field] = buf[i:i + length]; i += length
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return out


def _double_value(blob: Any) -> float | None:
    """google.protobuf.DoubleValue; an empty message is 0."""
    if not isinstance(blob, bytes):
        return None
    return float(_fields(blob).get(1, 0.0))


def parse_summary(frame: bytes) -> dict[str, Any]:
    """GetTripsSummaryResponse: totals over the requested range."""
    f = _fields(frame)
    money = _fields(f[9]) if isinstance(f.get(9), bytes) else {}
    electricity_cost = None
    if money:
        electricity_cost = {
            "currency": (money.get(1) or b"").decode() or None,
            "amount": round(money.get(2, 0) + money.get(3, 0) / 1e9, 2),
        }
    return {
        "trip_count": f.get(1, 0),
        "distance_km": round(f.get(2, 0) / 1000, 1),
        "duration_h": round(f.get(3, 0) / 3_600_000, 2),
        "energy_used_kwh": round(_double_value(f.get(5)) or 0.0, 1),
        "average_consumption_kwh_100km": round(_double_value(f.get(6)) or 0.0, 1),
        "electricity_cost": electricity_cost,
    }
