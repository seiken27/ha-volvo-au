"""Volvo C3 charge schedule helpers."""

from __future__ import annotations

from typing import Any


def current_schedule(snap: dict[str, Any]) -> dict[str, Any] | None:
    """Return {enabled, start_h, start_m, end_h, end_m} or None if not yet polled."""
    gct = (snap.get("global_charge_timer") or {}).get("globalChargeTimer")
    if not gct:
        return None
    start = gct.get("start") or {}
    stop = gct.get("stop") or {}
    return {
        "enabled": bool(gct.get("activated")),
        "start_h": int(start.get("hour", 0) or 0),
        "start_m": int(start.get("minute", 0) or 0),
        "end_h": int(stop.get("hour", 0) or 0),
        "end_m": int(stop.get("minute", 0) or 0),
    }
