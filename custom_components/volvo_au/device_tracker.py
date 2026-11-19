"""Volvo C3 device_tracker — last parked GPS location."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VolvoLocationTracker(coordinator)])


class VolvoLocationTracker(VolvoEntity, TrackerEntity):
    _attr_translation_key = "location"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_location"

    def _loc(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("location") or None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        loc = self._loc()
        return loc.get("latitude") if loc else None

    @property
    def longitude(self) -> float | None:
        loc = self._loc()
        return loc.get("longitude") if loc else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        loc = self._loc()
        if not loc:
            return None
        return {"last_parked_timestamp": loc.get("timestamp")}
