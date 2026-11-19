"""Volvo C3 switch platform — manual charge schedule on/off."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity, async_gate_unsupported
from .binary_sensor import _connected
from .button import raise_if_command_failed
from .online import ensure_car_online
from .powertrain import climate_action
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
                VolvoChargingSwitch(coordinator),
                VolvoChargeScheduleSwitch(coordinator),
                VolvoClimatizationSwitch(coordinator),
                VolvoAirPurificationSwitch(coordinator),
            ],
        )
    )


class VolvoChargingSwitch(VolvoEntity, SwitchEntity):
    """On while the car is charging. Turning it on or off sends Start or Stop charging."""

    _attr_translation_key = "charging"
    requires_capability = "supportsChargingFunctions"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_charging"

    @property
    def is_on(self) -> bool | None:
        batt = ((self.coordinator.data or {}).get("battery") or {}).get("battery") or {}
        status = batt.get("chargingStatusV2") or batt.get("chargingStatus")
        if not isinstance(status, str) or status.endswith(("_UNSPECIFIED", "_UNKNOWN")):
            return None
        return status.startswith("CHARGING_STATUS_CHARGING")

    async def async_turn_on(self, **kwargs: Any) -> None:
        if _connected(self.coordinator.data or {}) is False:
            raise HomeAssistantError("The car is not plugged in, so it cannot start charging. Nothing was sent.")
        await self._send("charge_start", "Start charging")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send("charge_stop", "Stop charging")

    async def _send(self, method: str, what: str) -> None:
        ensure_car_online(self.coordinator)
        res = await getattr(self.coordinator.client, method)()
        if not isinstance(res, dict):
            res = {"ok": False, "error": f"unexpected reply {res!r}"}
        self.coordinator.note_command()
        raise_if_command_failed(res, what)
        await self.coordinator.async_request_refresh()


class VolvoChargeScheduleSwitch(VolvoEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "charging_schedule"
    requires_capability = "supportsGlobalChargeTimer"
    requires_data = "global_charge_timer"
    _attr_icon = "mdi:clock-outline"

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
    _attr_translation_key = "climatization"
    requires_capability = "supportsParkingClimatization"
    requires_data = "parking_climatization"
    _attr_icon = "mdi:air-conditioner"

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
        attrs = {
            "runtime_left_minutes": pc.get("runtimeLeftMinutes"),
            "ventilation": pc.get("ventilation"),
            "start_reason": pc.get("startReason"),
        }
        action = climate_action(pc)
        if action:
            attrs["action"] = action  # heating / cooling / ventilating / not_required
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("ClimatizationStart")
        res = await self.coordinator.client.climatization_start(
            self.coordinator.climate_temperature, dict(self.coordinator.climate_seats) or None
        )
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
    _attr_translation_key = "air_purification"
    requires_capability = "supportsAirPurification"
    requires_data = "pre_cleaning"
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
