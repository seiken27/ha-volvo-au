"""Volvo C3 select platform: seat and steering wheel heating for the next climatization start (EXPERIMENTAL).

Only cars whose own record says they support those settings get these (the XC40/EX40 do not), so this has not been
run against a real car.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import climate
from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    car = coordinator.car_info
    async_add_entities(
        VolvoClimateHeating(coordinator, name, levels_key)
        for name, (flag, levels_key, _field) in climate.SEATS.items()
        if climate.supported(car, flag)
    )


class VolvoClimateHeating(VolvoEntity, SelectEntity, RestoreEntity):
    """Heating level for one seat or the steering wheel, used the next time climatization is started."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:car-seat-heater"

    def __init__(self, coordinator: VolvoCoordinator, name: str, levels_key: str) -> None:
        super().__init__(coordinator)
        self._name = name
        self._attr_translation_key = name
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_climate_{name}"
        self._attr_options = climate.seat_options(coordinator.car_info, levels_key)
        self._attr_current_option = "off"
        if name == "steering_wheel":
            self._attr_icon = "mdi:steering"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self._attr_current_option = last.state
        self.coordinator.climate_seats[self._name] = self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.coordinator.climate_seats[self._name] = option
        self.async_write_ha_state()
