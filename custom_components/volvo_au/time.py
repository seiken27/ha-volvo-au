"""Volvo AU time platform — charge schedule start/end times."""

from __future__ import annotations

import logging
from datetime import time as dtime
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity
from .schedule import current_schedule

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            VolvoScheduleTime(coordinator, kind="start"),
            VolvoScheduleTime(coordinator, kind="stop"),
        ]
    )


class VolvoScheduleTime(VolvoEntity, TimeEntity):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: VolvoCoordinator, *, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind  # "start" or "stop"
        self._attr_translation_key = f"charge_schedule_{kind}"
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.client.vin}_charge_schedule_{kind}"
        )

    @property
    def native_value(self) -> dtime | None:
        sched = current_schedule(self.coordinator.data or {})
        if not sched:
            return None
        if self._kind == "start":
            return dtime(sched["start_h"], sched["start_m"])
        return dtime(sched["end_h"], sched["end_m"])

    async def async_set_value(self, value: dtime) -> None:
        sched = current_schedule(self.coordinator.data or {}) or {
            "enabled": False,
            "start_h": 0,
            "start_m": 0,
            "end_h": 0,
            "end_m": 0,
        }
        if self._kind == "start":
            sched["start_h"] = value.hour
            sched["start_m"] = value.minute
        else:
            sched["end_h"] = value.hour
            sched["end_m"] = value.minute

        res = await self.coordinator.client.set_global_charge_timer(
            start_hour=sched["start_h"],
            start_minute=sched["start_m"],
            end_hour=sched["end_h"],
            end_minute=sched["end_m"],
            enabled=sched["enabled"],
        )
        if not res.get("ok"):
            _LOGGER.warning("SetGlobalChargeTimer failed: %s", res)
        self.coordinator.note_command()
