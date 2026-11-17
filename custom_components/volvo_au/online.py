"""Is the car online?

Volvo's server answers every car command with HTTP 200 even when the car is offline (16 Nov 2026),
so the reply to a command cannot tell us. This module reads two things from the data we already poll:

1. An explicit connectivity value in the availability reply (a key whose name has connect, online,
   reachab or availab, with a value such as ONLINE, OFFLINE, AVAILABLE, UNAVAILABLE, NO_INTERNET).
   The exact key name is not confirmed yet: the Attributes of the "Online" sensor show what Volvo sends.
2. The newest car-side timestamp in the state replies (a protobuf Timestamp object with "seconds", or an
   ISO time under a key that ends in time, timestamp or updated). It is shown as "Last seen". It only decides
   "offline" when const.OFFLINE_AFTER_S is set above 0 (off by default, see const.py).

If neither tells us, the state is unknown (None) and a command is sent as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from . import const as _const

# Sections of the snapshot that come from the car. Not weather (Volvo's weather service) and not
# software_info (the publish time of an update).
_CAR_SECTIONS = (
    "battery", "odometer", "exterior", "health", "availability",
    "parking_climatization", "pre_cleaning", "target_soc", "amp_limit",
    "global_charge_timer", "location",
)
_KEY_HINTS = ("connect", "online", "reachab", "availab")
_OFFLINE_WORDS = ("OFFLINE", "UNAVAILABLE", "DISCONNECTED", "NOT_CONNECTED", "NO_INTERNET", "UNREACHABLE")
_ONLINE_WORDS = ("ONLINE", "AVAILABLE", "CONNECTED", "REACHABLE")
_TIME_KEY_ENDS = ("timestamp", "time", "updatedat", "updated")


@dataclass(frozen=True)
class CarStatus:
    online: bool | None  # None = unknown
    last_seen: datetime | None
    source: str  # which evidence decided: "availability.<key>", "timestamp" or "none"
    raw: dict[str, Any]  # the availability reply, for the Attributes of the Online sensor


def _enum_word(val: str) -> str:
    """The value of an enum without its prefix: AVAILABILITY_STATUS_UNAVAILABLE -> UNAVAILABLE,
    CONNECTION_STATE_OFFLINE -> OFFLINE. Compared whole, never as a substring."""
    up = val.strip().upper()
    for sep in ("_STATUS_", "_STATE_"):
        if sep in up:
            up = up.split(sep)[-1]
    return up


def _explicit(avail: dict[str, Any]) -> tuple[bool | None, str]:
    offline_key = online_key = None
    for key, val in avail.items():
        low = str(key).lower()
        # A reason field never decides: on 17 Nov 2026 03:25 the car was AVAILABLE while unavailableReason
        # still said UNAVAILABLE_REASON_UNSPECIFIED, and a substring match called the car offline.
        if low == "usagemode" or "reason" in low or not any(h in low for h in _KEY_HINTS):
            continue
        if isinstance(val, bool):
            if not any(h in low for h in ("connect", "online", "reachab")):
                continue
            if val:
                online_key = online_key or key
            else:
                offline_key = offline_key or key
        elif isinstance(val, str):
            word = _enum_word(val)
            if word in _OFFLINE_WORDS:
                offline_key = offline_key or key
            elif word in _ONLINE_WORDS:
                online_key = online_key or key
    if offline_key:  # any sign of offline wins
        return False, f"availability.{offline_key}"
    if online_key:
        return True, f"availability.{online_key}"
    return None, "none"


def _to_dt(value: Any) -> datetime | None:
    try:
        if isinstance(value, str):
            if value.isdigit():
                value = int(value)
            else:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1_000_000_000:
            if value > 10_000_000_000:  # milliseconds
                value = value / 1000
            return datetime.fromtimestamp(value, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _walk(node: Any, out: list[datetime]) -> None:
    if isinstance(node, dict):
        if "seconds" in node:
            dt = _to_dt(node.get("seconds"))
            if dt:
                out.append(dt)
        for key, val in node.items():
            low = str(key).lower()
            if isinstance(val, (dict, list)):
                _walk(val, out)
            elif low.endswith(_TIME_KEY_ENDS) and low != "seconds":
                dt = _to_dt(val)
                if dt:
                    out.append(dt)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out)


def newest_car_timestamp(data: dict[str, Any], now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    found: list[datetime] = []
    for name in _CAR_SECTIONS:
        sec = data.get(name)
        if isinstance(sec, dict):
            _walk(sec, found)
    # A time in the future is not a report from the car.
    found = [d for d in found if (d - now).total_seconds() < 300]
    return max(found) if found else None


def car_status(data: dict[str, Any] | None, now: datetime | None = None) -> CarStatus:
    data = data or {}
    now = now or datetime.now(UTC)
    avail = (data.get("availability") or {}).get("availability") or {}
    raw = avail if isinstance(avail, dict) else {}
    last_seen = newest_car_timestamp(data, now)
    online, source = _explicit(raw)
    if online is None and _const.OFFLINE_AFTER_S > 0 and last_seen is not None:
        online = (now - last_seen).total_seconds() <= _const.OFFLINE_AFTER_S
        source = "timestamp"
    return CarStatus(online, last_seen, source, raw)


# Volvo's unavailableReason values in plain words. The reply on 16 Nov 2026 had
# UNAVAILABLE_REASON_NO_INTERNET; the others are from Volvo's public command-accessibility API.
_REASONS = {
    "NO_INTERNET": "the car has no mobile internet (weak signal?)",
    "POWER_SAVING_MODE": "the car is in power saving mode",
    "CAR_IN_USE": "the car is in use",
    "CAR_IN_REMOTE_USE": "the car is in use from the app",
}


def reason_text(raw: dict[str, Any] | None) -> str:
    """Plain words for Volvo's unavailableReason, or \"\" when there is none."""
    key = str((raw or {}).get("unavailableReason") or "").upper().replace("UNAVAILABLE_REASON_", "")
    if not key or key == "UNSPECIFIED":
        return ""
    return _REASONS.get(key, key.lower().replace("_", " "))


def status_text(st: CarStatus) -> str:
    """One sentence for a person or the voice assistant (state of the Connection sensor, max 255 characters)."""
    if st.online is None:
        return "Unknown: Volvo does not say whether the car is online."
    if st.online:
        return "Online: the car can receive commands."
    when = f" since {dt_util.as_local(st.last_seen).strftime('%H:%M')}" if st.last_seen is not None else ""
    why = reason_text(st.raw) or "no connection to the car"
    return f"Offline{when}: {why}. Commands cannot reach the car until it is online again."


def ensure_car_online(coordinator: Any) -> None:
    """Raise before a car command is sent when the car is known to be offline."""
    st = car_status(getattr(coordinator, "data", None))
    if st.online is False:
        when = ""
        if st.last_seen is not None:
            when = f" since {dt_util.as_local(st.last_seen).strftime('%H:%M')}"
        why = reason_text(st.raw) or "no mobile signal?"
        raise HomeAssistantError(
            f"The car is offline{when} ({why}). Nothing was sent. Check the Volvo app."
        )
