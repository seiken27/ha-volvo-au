"""Volvo AU number platform \u2014 charge current limit."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
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
    async_add_entities(
        [VolvoChargeCurrentLimit(coordinator), VolvoTargetSoc(coordinator)]
    )


class VolvoChargeCurrentLimit(VolvoEntity, NumberEntity):
    _attr_name = "Charge current limit"
    _attr_icon = "mdi:current-ac"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = 6
    _attr_native_max_value = 32
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.client.vin}_charge_current_limit"
        )

    @property
    def native_value(self) -> float | None:
        snap = self.coordinator.data or {}
        v = (
            ((snap.get("amp_limit") or {}).get("ampLimit") or {}).get("ampLimit")
        )
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        amps = int(round(value))
        _LOGGER.debug("SetAmpLimit(%s A)", amps)
        res = await self.coordinator.client.set_amp_limit(amps)
        if not res.get("ok"):
            _LOGGER.warning("SetAmpLimit failed: %s", res)
        self.coordinator.note_command()


class VolvoTargetSoc(VolvoEntity, NumberEntity):
    """Target state-of-charge for charging, set as a custom (one-off) target."""

    _attr_name = "Target SoC"
    _attr_icon = "mdi:battery-charging-high"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 20
    _attr_native_max_value = 100
    _attr_native_step = 10
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_target_soc"

    @property
    def native_value(self) -> float | None:
        snap = self.coordinator.data or {}
        v = (
            ((snap.get("target_soc") or {}).get("targetSoc") or {}).get(
                "batteryChargeTargetLevel"
            )
        )
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        level = int(round(value))
        _LOGGER.debug("SetTargetSoc(%s%%)", level)
        res = await self.coordinator.client.set_target_soc(level)
        if not res.get("ok"):
            _LOGGER.warning("SetTargetSoc failed: %s", res)
        self.coordinator.note_command()
