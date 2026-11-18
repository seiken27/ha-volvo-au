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
