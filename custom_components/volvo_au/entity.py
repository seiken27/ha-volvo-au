"""Common entity base for Volvo AU."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .powertrain import capability, has_combustion_engine, supports_engine_remote_start


class VolvoEntity(CoordinatorEntity[VolvoCoordinator]):
    """Common base — every entity shares one DeviceInfo per VIN."""

    _attr_has_entity_name = True
    # True for entities that only make sense on a car with a combustion engine (diesel, petrol or hybrid).
    requires_combustion_engine = False
    # True for entities that need the car's own "supports engine remote start" flag.
    requires_engine_remote_start = False
    # Name of a flag in the car's own record (``supportsFlash``, ``reportsTyrePressure``, ...). The entity is
    # hidden when the car says that flag is false.
    requires_capability: str | None = None

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


def _is_gated(entity: VolvoEntity) -> bool:
    return bool(
        entity.requires_combustion_engine or entity.requires_engine_remote_start or entity.requires_capability
    )


def _is_unsupported(entity: VolvoEntity, car: dict | None) -> bool:
    """True only when the car's own record says this entity's feature is not there."""
    if entity.requires_combustion_engine and has_combustion_engine(car) is False:
        return True
    if entity.requires_engine_remote_start and supports_engine_remote_start(car) is False:
        return True
    return bool(entity.requires_capability) and capability(car, entity.requires_capability) is False


@callback
def async_gate_unsupported(
    hass: HomeAssistant, coordinator: VolvoCoordinator, domain: str, entities: list[VolvoEntity]
) -> list[VolvoEntity]:
    """Drop entities for features the car says it does not have, and disable them in the registry.

    Uses the car's own record from ``/car-information/car``: an engine type of electric, a ``false``
    capability flag (for example ``reportsTyrePressure``) or ``supportsEngineRemoteStart: false``.
    Dropped entities carry ``disabled_by=INTEGRATION`` and come back by themselves if the car later
    says it supports the feature. Entities the user disabled are left alone. A flag the car does not
    report, or a record that could not be read, keeps everything.
    """
    registry = er.async_get(hass)
    kept: list[VolvoEntity] = []
    for entity in entities:
        if not _is_gated(entity):
            kept.append(entity)
            continue
        unsupported = _is_unsupported(entity, coordinator.car_info)
        entity_id = registry.async_get_entity_id(domain, DOMAIN, entity.unique_id)
        entry = registry.async_get(entity_id) if entity_id else None
        if not unsupported:
            if entry is not None and entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                registry.async_update_entity(entity_id, disabled_by=None)
            kept.append(entity)
        elif entry is not None and entry.disabled_by is None:
            registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    return kept
