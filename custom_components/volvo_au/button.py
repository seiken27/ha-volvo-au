"""Volvo AU button platform: refresh and car commands."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VolvoCoordinator
from .entity import VolvoEntity, async_gate_unsupported, async_remove_registered_entities
from .online import ensure_car_online

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VolvoCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Start and stop charging are done by the Charging switch (19 Nov 2026).
    async_remove_registered_entities(
        hass, "button", [f"{DOMAIN}_{coordinator.client.vin}_{key}" for key in ("charge_start", "charge_stop")]
    )
    buttons = [
        VolvoRefreshButton(coordinator),
        *(VolvoCommandButton(coordinator, spec) for spec in COMMAND_BUTTONS),
    ]
    async_add_entities(async_gate_unsupported(hass, coordinator, "button", buttons))


class VolvoRefreshButton(VolvoEntity, ButtonEntity):
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: VolvoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


# (unique id suffix, name, icon, what (for messages), client method, keyword arguments).
# The unique id suffixes are the ones the entities had before 16 Nov 2026: do not change them.
COMMAND_BUTTONS: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
    ("flash", "Flash", "mdi:car-light-high", "Flash", "flash", {}),
    ("honk", "Honk", "mdi:bullhorn-outline", "Honk", "honk", {}),
    ("honk_flash", "Honk & flash", "mdi:bullhorn", "Honk and flash", "honk_and_flash", {}),
    ("window_open", "Open windows", "mdi:window-open", "Open windows", "window_control", {"open": True}),
    ("window_close", "Close windows", "mdi:window-closed", "Close windows", "window_control", {"open": False}),
    ("engine_remote_start", "Engine remote start", "mdi:engine", "Engine remote start", "engine_remote_start", {}),
    ("reduced_alarm", "Reduced alarm", "mdi:alarm-bell", "Lock with reduced alarm", "reduced_alarm", {}),
    ("tailgate_control", "Tailgate control", "mdi:truck-tailgate", "Open tailgate", "tailgate_control", {"open": True}),
    ("tailgate_close", "Close tailgate", "mdi:truck-tailgate-closed", "Close tailgate", "tailgate_control", {"open": False}),
]


# Which of the car's own flags each command needs (the suffixes above).
BUTTON_CAPABILITIES = {
    "flash": "supportsFlash",
    "honk": "supportsHonkAndFlash",
    "honk_flash": "supportsHonkAndFlash",
    "window_open": "supportsRemoteWindowControl",
    "window_close": "supportsRemoteWindowControl",
    "reduced_alarm": "supportsLockReducedGuard",
    "tailgate_control": "supportsRemoteTrunkControl",
    "tailgate_close": "supportsRemoteTrunkControl",
}


def raise_if_command_failed(res: dict[str, Any], what: str) -> None:
    """Tell the person when Volvo refused a command or did not answer.

    Before 16 Nov 2026 Flash and Honk & flash ignored the result, and the other buttons only logged it,
    so a failed press looked like a success.
    Note: ok here only means that Volvo's server took the request. On 16 Nov 2026 it also said ok while
    the car was offline, so a press that passes this check can still do nothing on the car.
    """
    if res.get("ok"):
        return
    if res.get("timeout"):
        raise HomeAssistantError(
            f"{what}: Volvo did not answer ({res.get('error')}). The car may be offline; check the Volvo app."
        )
    raise HomeAssistantError(
        f"{what} failed: {res.get('error') or res.get('grpc_message') or res.get('http_status')}"
    )


class VolvoCommandButton(VolvoEntity, ButtonEntity):
    """One car command. A failure is logged and raised, so the person sees it."""

    def __init__(self, coordinator: VolvoCoordinator, spec: tuple) -> None:
        super().__init__(coordinator)
        suffix, name, icon, self._what, self._method, self._kwargs = spec
        self._attr_translation_key = suffix  # the name is in translations/en.json
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.vin}_{suffix}"
        self.requires_combustion_engine = self.requires_engine_remote_start = suffix == "engine_remote_start"
        self.requires_capability = BUTTON_CAPABILITIES.get(suffix)

    async def async_press(self) -> None:
        ensure_car_online(self.coordinator)  # raises when the car is known offline; nothing is sent
        res = await getattr(self.coordinator.client, self._method)(**self._kwargs)
        if not isinstance(res, dict):
            res = {"ok": False, "error": f"unexpected reply {res!r}"}
        if not res.get("ok"):
            _LOGGER.warning("%s failed: %s", self._what, res)
        self.coordinator.note_command()
        raise_if_command_failed(res, self._what)
