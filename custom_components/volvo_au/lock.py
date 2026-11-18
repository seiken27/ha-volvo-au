"""Volvo AU lock entity."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity, async_gate_unsupported
from .online import ensure_car_online

_LOGGER = logging.getLogger(__name__)


def _raise_if_failed(res: dict[str, Any], what: str) -> None:
    """Tell the person that a car command was not confirmed (15 Nov 2026: a timeout used to surface as
    an unexpected error). The state still refreshes for a minute, so a late success shows up."""
    if res.get("ok"):
        return
    if res.get("timeout"):
        raise HomeAssistantError(
            f"{what}: the car did not confirm ({res.get('error')}). It may still happen: "
            "Volvo's command service may be stalled. Check the lock state in a minute, or use the Volvo app."
        )
    raise HomeAssistantError(f"{what} failed: {res.get('error') or res.get('grpc_message') or res.get('http_status')}")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        async_gate_unsupported(hass, coordinator, "lock", [VolvoLock(coordinator), VolvoTailgateLock(coordinator)])
    )


class VolvoLock(VolvoEntity, LockEntity):
    _attr_translation_key = "lock"
    requires_capability = "supportsLock"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_lock"

    @property
    def is_locked(self) -> bool | None:
        ext = (self.coordinator.data or {}).get("exterior") or {}
        ext = ext.get("exterior") or {}
        v = ext.get("centralLock")
        if v is None:
            return None
        if v.endswith("_LOCKED"):
            return True
        if v.endswith("_UNLOCKED"):
            return False
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("Lock command")
        res = await self.coordinator.client.lock()
        if not res.get("ok"):
            _LOGGER.warning("Lock failed: %s", res)
        self.coordinator.note_command()
        _raise_if_failed(res, "Lock")

    async def async_unlock(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("Unlock command")
        res = await self.coordinator.client.unlock()
        if not res.get("ok"):
            _LOGGER.warning("Unlock failed: %s", res)
        self.coordinator.note_command()
        _raise_if_failed(res, "Unlock")


class VolvoTailgateLock(VolvoEntity, LockEntity):
    """Tailgate lock: unlock pops the tailgate, lock re-locks the whole car."""

    _attr_translation_key = "tailgate_lock"
    requires_capability = "supportsRemoteTrunkUnlock"
    _attr_icon = "mdi:car-back"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_tailgate_lock"
        # Latched view of "is the tailgate effectively unlocked right now?".
        # We can't read this directly from the car — there's no tailgate-lock signal
        # and the central lock doesn't change when only the boot is unlocked. So we
        # track it ourselves and clear it when the car gives us a clean signal.
        self._latched_unlocked: bool = False
        self._last_central_lock: str | None = None
        self._was_tailgate_open: bool = False

    def _read_central(self) -> str | None:
        ext = ((self.coordinator.data or {}).get("exterior") or {}).get(
            "exterior"
        ) or {}
        return ext.get("centralLock")

    def _read_tailgate_open(self) -> bool | None:
        ext = ((self.coordinator.data or {}).get("exterior") or {}).get(
            "exterior"
        ) or {}
        v = ext.get("tailgate")
        if v is None:
            return None
        return v.endswith("_OPEN") or v.endswith("_AJAR")

    @property
    def is_locked(self) -> bool | None:
        # If user/elsewhere locked the car, that overrides our latched unlock.
        cl = self._read_central()
        if cl is None:
            return None
        if cl.endswith("_LOCKED") and not self._latched_unlocked:
            return True
        if cl.endswith("_UNLOCKED"):
            return False
        # Locked centrally but we previously unlocked the tailgate -> still unlocked.
        return False if self._latched_unlocked else True

    def _handle_coordinator_update(self) -> None:
        cl = self._read_central()
        tg_open = self._read_tailgate_open()
        # Lock-all (centralLock flipped LOCKED) clears the latched unlock when we also
        # observe the tailgate has closed since we unlocked it.
        if self._latched_unlocked:
            if cl and cl.endswith("_LOCKED"):
                if self._was_tailgate_open and tg_open is False:
                    # opened and then closed again -> consider it re-locked
                    self._latched_unlocked = False
                    self._was_tailgate_open = False
            if tg_open:
                self._was_tailgate_open = True
        self._last_central_lock = cl
        super()._handle_coordinator_update()

    async def async_lock(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("Tailgate lock -> lock all")
        self._latched_unlocked = False
        self._was_tailgate_open = False
        self.async_write_ha_state()
        res = await self.coordinator.client.lock()
        if not res.get("ok"):
            _LOGGER.warning("Lock failed: %s", res)
        self.coordinator.note_command()
        _raise_if_failed(res, "Lock")

    async def async_unlock(self, **kwargs: Any) -> None:
        ensure_car_online(self.coordinator)
        _LOGGER.debug("Unlock tailgate")
        self._latched_unlocked = True
        self._was_tailgate_open = bool(self._read_tailgate_open())
        self.async_write_ha_state()
        res = await self.coordinator.client.unlock_tailgate()
        if not res.get("ok"):
            _LOGGER.warning("Unlock tailgate failed: %s", res)
            self._latched_unlocked = False
            self.async_write_ha_state()
        self.coordinator.note_command()
        _raise_if_failed(res, "Unlock tailgate")
