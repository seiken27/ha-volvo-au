"""Slow-poll tiers: readers whose data rarely changes are re-read on a longer cadence than the main poll.

Pure Python (no Home Assistant imports) so it can be tested on its own.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

# Seconds between real reads. Anything not listed is read on every poll.
SLOW_TTLS: dict[str, float] = {
    "software_info": 3600.0,
    "weather": 900.0,
    "target_soc": 900.0,
    "amp_limit": 900.0,
    "global_charge_timer": 900.0,
}

# Readers whose cache must be dropped when we change the value ourselves.
INVALIDATED_BY: dict[str, tuple[str, ...]] = {
    "set_target_soc": ("target_soc",),
    "set_amp_limit": ("amp_limit",),
    "set_global_charge_timer": ("global_charge_timer",),
}


class SlowTiers:
    def __init__(self, ttls: dict[str, float] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttls = SLOW_TTLS if ttls is None else ttls
        self._clock = clock
        self._store: dict[str, tuple[float, Any]] = {}

    def is_slow(self, name: str) -> bool:
        return name in self._ttls

    def cached(self, name: str) -> tuple[bool, Any]:
        """(fresh, value). ``fresh`` is False when the reader is due, the value may still be a stale fallback."""
        entry = self._store.get(name)
        if entry is None:
            return False, None
        stored_at, value = entry
        return self._clock() - stored_at < self._ttls.get(name, 0.0), value

    def has(self, name: str) -> bool:
        return name in self._store

    def put(self, name: str, value: Any) -> None:
        self._store[name] = (self._clock(), value)

    def invalidate(self, *names: str) -> None:
        for name in names:
            self._store.pop(name, None)


def is_active(snap: dict[str, Any]) -> bool:
    """Heuristic: is something happening that needs the faster poll?

    Charging, a door, window or lock open, the alarm, the climate or air purification running, or the car
    being used. A car that is merely plugged in (not charging) is **not** active: it can sit on a charger all
    night, and nothing changes quickly.
    """
    try:
        batt = (snap.get("battery") or {}).get("battery") or {}
        if batt.get("chargingStatus", "").startswith("CHARGING_STATUS_CHARGING"):
            return True

        ext = (snap.get("exterior") or {}).get("exterior") or {}
        for k, v in ext.items():
            if not isinstance(v, str):
                continue
            # Door/hood/tailgate/window/lock status enums end with _OPEN / _UNLOCKED
            if v.endswith("_OPEN") or v.endswith("_UNLOCKED") or v.endswith("_AJAR"):
                return True
            # Alarm status enum ends with _TRIGGERED (AlarmStatus.ALARM_STATUS_TRIGGERED).
            # Scanned by suffix like the rest of this loop since the exact flat
            # JSON key it's exposed under (binary_sensor.py's _alarm() guesses
            # "alarmStatus") isn't confirmed yet against a live payload.
            if v.endswith("_TRIGGERED"):
                return True

        pc = (snap.get("parking_climatization") or {}).get("parkingClimatization") or {}
        rs = pc.get("runningStatus", "")
        if rs and rs not in ("RUNNING_STATUS_OFF", "RUNNING_STATUS_UNSPECIFIED"):
            return True

        pre = (snap.get("pre_cleaning") or {}).get("preCleaning") or {}
        rs2 = pre.get("runningStatus", "")
        if rs2 and rs2 not in ("RUNNING_STATUS_OFF", "RUNNING_STATUS_UNSPECIFIED"):
            return True

        avail = (snap.get("availability") or {}).get("availability") or {}
        um = avail.get("usageMode", "")
        if um and um not in (
            "USAGE_MODE_ABANDONED",
            "USAGE_MODE_UNSPECIFIED",
            "USAGE_MODE_UNKNOWN",
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False
