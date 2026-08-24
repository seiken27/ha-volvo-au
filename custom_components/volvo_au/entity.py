"""Common entity base for Volvo AU."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VolvoCoordinator


class VolvoEntity(CoordinatorEntity[VolvoCoordinator]):
    """Common base — every entity shares one DeviceInfo per VIN."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._vin = coordinator.client.vin

    @property
    def device_info(self) -> DeviceInfo:
        """Build device info from the real model/registration where we have
        it (fetched from /car-information/car during setup — see
        config_flow.py), falling back gracefully for entries configured
        before this data was persisted.
        """
        coordinator = self.coordinator
        vin = self._vin
        model = coordinator.model_name or "Volvo EV"
        model_display = f"{model} ({coordinator.model_year})" if coordinator.model_year else model

        name_parts = [model]
        if coordinator.registration_plate:
            name_parts.append(f"({coordinator.registration_plate})")
        else:
            name_parts.append(f"({vin[-6:]})")

        sw_version = None
        if coordinator.data:
            sw_version = (coordinator.data.get("software_info") or {}).get("version")

        return DeviceInfo(
            identifiers={(DOMAIN, vin)},
            name=" ".join(name_parts),
            manufacturer="Volvo",
            model=model_display,
            serial_number=vin,
            sw_version=sw_version,
        )
