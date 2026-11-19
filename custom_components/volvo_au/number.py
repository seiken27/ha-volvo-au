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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import climate
from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .button import raise_if_command_failed
from .entity import VolvoEntity, async_gate_unsupported
from .online import ensure_car_online

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[VolvoEntity] = [VolvoChargeCurrentLimit(coordinator), VolvoTargetSoc(coordinator)]
    # Experimental: only for cars whose own record says they take a parking climate temperature (not the XC40/EX40).
    if climate.supported(coordinator.car_info, climate.TEMPERATURE_FLAG):
        entities.append(VolvoClimateTemperature(coordinator))
    async_add_entities(async_gate_unsupported(hass, coordinator, "number", entities))


class VolvoChargeCurrentLimit(VolvoEntity, NumberEntity):
    _attr_translation_key = "charge_current_limit"
    requires_capability = "supportsChargeAmperageLimitSetting"
    requires_data = "amp_limit"
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
        ensure_car_online(self.coordinator)
        amps = int(round(value))
        _LOGGER.debug("SetAmpLimit(%s A)", amps)
        res = await self.coordinator.client.set_amp_limit(amps)
        if not res.get("ok"):
            _LOGGER.warning("SetAmpLimit failed: %s", res)
        self.coordinator.note_command()
        # Before 17 Nov 2026 a refused value was only logged, so the slider just jumped back.
        raise_if_command_failed(res, f"Charge current {amps} A")


class VolvoTargetSoc(VolvoEntity, NumberEntity):
    """Target state-of-charge for charging, set as a custom (one-off) target."""

    _attr_translation_key = "target_soc"
    requires_capability = "supportsTargetChargeLevelSetting"
    requires_data = "target_soc"
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
        ensure_car_online(self.coordinator)
        level = int(round(value))
        _LOGGER.debug("SetTargetSoc(%s%%)", level)
        res = await self.coordinator.client.set_target_soc(level)
        if not res.get("ok"):
            _LOGGER.warning("SetTargetSoc failed: %s", res)
        self.coordinator.note_command()
        raise_if_command_failed(res, f"Charge limit {level} %")


class VolvoClimateTemperature(VolvoEntity, NumberEntity, RestoreEntity):
    """Temperature to use the next time climatization is started (EXPERIMENTAL, untested on a real car)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "climate_temperature"
    _attr_icon = "mdi:thermometer-auto"
    _attr_native_unit_of_measurement = "\u00b0C"
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_climate_temperature"
        low, high = climate.temperature_limits(coordinator.car_info)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._value = min(max(climate.DEFAULT_TEMPERATURE, low), high)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            try:
                self._value = min(max(float(last.state), self._attr_native_min_value), self._attr_native_max_value)
            except (TypeError, ValueError):
                pass
        self.coordinator.climate_temperature = self._value

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = float(value)
        self.coordinator.climate_temperature = self._value
        self.async_write_ha_state()
