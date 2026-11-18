"""Volvo AU switch platform — manual charge schedule on/off."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity, async_gate_unsupported
from .online import ensure_car_online
from .schedule import current_schedule

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        async_gate_unsupported(
            hass,
            coordinator,
            "switch",
            [
                VolvoChargeScheduleSwitch(coordinator),
                VolvoClimatizationSwitch(coordinator),
                VolvoAirPurificationSwitch(coordinator),
            ],
        )
    )


class VolvoChargeScheduleSwitch(VolvoEntity, SwitchEntity):
    _attr_name = "Charge schedule"
    requires_capability = "supportsGlobalChargeTimer"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.client.vin}_charge_schedule_enabled"
        )

    @property
    def is_on(self) -> bool | None:
        sched = current_schedule(self.coordinator.data or {})
        return sched["enabled"] if sched else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._apply(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._apply(enabled=False)

    async def _apply(self, *, enabled: bool) -> None:
        sched = current_schedule(self.coordinator.data or {}) or {
            "start_h": 0,
            "start_m": 0,
            "end_h": 0,
            "end_m": 0,
        }
        res = await self.coordinator.client.set_global_charge_timer(
            start_hour=sched["start_h"],
            start_minute=sched["start_m"],
            end_hour=sched["end_h"],
            end_minute=sched["end_m"],
            enabled=enabled,
        )
        if not res.get("ok"):
            _LOGGER.warning("SetGlobalChargeTimer failed: %s", res)
        self.coordinator.note_command()


class VolvoClimatizationSwitch(VolvoEntity, SwitchEntity):
    _attr_name = "Climatization"
    requires_capability = "supportsParkingClimatization"
    _attr_icon = "mdi:car-defrost-front"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.client.vin}_climatization"
        )

    @property
    def is_on(self) -> bool | None:
        pc = (
            (self.coordinator.data or {}).get("parking_climatization") or {}
        ).get("parkingClimatization") or {}
        status = pc.get("runningStatus")
        if not status:
            return None
        # RUNNING_STATUS_OFF / RUNNING_STATUS_ON (plus *_HEATING / *_VENTILATION variants)
        return status not in ("RUNNING_STATUS_OFF", "RUNNING_STATUS_UNSPECIFIED")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        pc = (
            (self.coordinator.data or {}).get("parking_climatization") or {}
        ).get("parkingClimatization") or {}
        if not pc:
            return None
        return {
            "runtime_left_minutes": pc.get("runtimeLeftMinutes"),
            "ventilation": pc.get("ventilation"),
            "start_reason": pc.get("startReason"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("ClimatizationStart")
        res = await self.coordinator.client.climatization_start()
        if not res.get("ok"):
            _LOGGER.warning("ClimatizationStart failed: %s", res)
        self.coordinator.note_command()

    async def async_turn_off(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("ClimatizationStop")
        res = await self.coordinator.client.climatization_stop()
        if not res.get("ok"):
            _LOGGER.warning("ClimatizationStop failed: %s", res)
        self.coordinator.note_command()


class VolvoAirPurificationSwitch(VolvoEntity, SwitchEntity):
    _attr_name = "Air purification"
    requires_capability = "supportsAirPurification"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.client.vin}_air_purification"
        )

    @property
    def is_on(self) -> bool | None:
        pc = (
            (self.coordinator.data or {}).get("pre_cleaning") or {}
        ).get("preCleaning") or {}
        status = pc.get("runningStatus")
        if not status:
            return None
        return status not in ("RUNNING_STATUS_OFF", "RUNNING_STATUS_UNSPECIFIED")

    async def async_turn_on(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("PreCleaning start")
        res = await self.coordinator.client.precleaning_start()
        if not res.get("ok"):
            _LOGGER.warning("PreCleaning start failed: %s", res)
        self.coordinator.note_command()

    async def async_turn_off(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("PreCleaning stop")
        res = await self.coordinator.client.precleaning_stop()
        if not res.get("ok"):
            _LOGGER.warning("PreCleaning stop failed: %s", res)
        self.coordinator.note_command()
