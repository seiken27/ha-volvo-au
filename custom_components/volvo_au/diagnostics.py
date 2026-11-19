"""Diagnostics download for Volvo C3: settings, the car's capability flags and the latest snapshot, minus personal data."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_VIN, DOMAIN
from .redact import redact


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    vin = entry.data.get(CONF_VIN)
    streams = {
        name: {"proven": policy.proven, "snapshot_only": policy.snapshot_only}
        for name, policy in coordinator.stream_policies.items()
    }
    interval = coordinator.update_interval
    return redact(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "car_record": coordinator.car_info,
            "snapshot": coordinator.data,
            "polling": {
                "update_interval_s": interval.total_seconds() if interval else None,
                "last_update_success": coordinator.last_update_success,
                "live_updates": coordinator._live_updates,
                "streams": streams,
                "driving_journal": coordinator.driving_journal,
            },
        },
        vin=vin,
    )
