"""Common entity base for Volvo AU."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .powertrain import has_combustion_engine, supports_engine_remote_start


class VolvoEntity(CoordinatorEntity[VolvoCoordinator]):
    """Common base — every entity shares one DeviceInfo per VIN."""

    _attr_has_entity_name = True
    # True for entities that only make sense on a car with a combustion engine (diesel, petrol or hybrid).
    requires_combustion_engine = False
    # True for entities that need the car's own "supports engine remote start" flag.
    requires_engine_remote_start = False

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

        # Model + last 6 of the VIN: stable, so entity IDs never depend on the plate.
        name_parts = [model, f"({vin[-6:]})"]

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


@callback
def async_gate_combustion_only(
    hass: HomeAssistant, coordinator: VolvoCoordinator, domain: str, entities: list[VolvoEntity]
) -> list[VolvoEntity]:
    """Drop combustion-only entities when the car says it is electric (or says it has no engine remote start).

    They carry ``disabled_by=INTEGRATION`` and come back by themselves if the car ever reports a
    combustion engine. Entities the user disabled are left alone. A car that did not say what powers
    it keeps everything.
    """
    registry = er.async_get(hass)
    electric = has_combustion_engine(coordinator.car_info) is False
    no_remote_start = supports_engine_remote_start(coordinator.car_info) is False
    kept: list[VolvoEntity] = []
    for entity in entities:
        if not (entity.requires_combustion_engine or entity.requires_engine_remote_start):
            kept.append(entity)
            continue
        unsupported = (entity.requires_combustion_engine and electric) or (
            entity.requires_engine_remote_start and no_remote_start
        )
        entity_id = registry.async_get_entity_id(domain, DOMAIN, entity.unique_id)
        entry = registry.async_get(entity_id) if entity_id else None
        if not unsupported:
            if entry is not None and entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                registry.async_update_entity(entity_id, disabled_by=None)
            kept.append(entity)
        elif entry is not None and entry.disabled_by is None:
            registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    return kept
