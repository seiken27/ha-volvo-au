"""Volvo (AU) integration setup."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_APP_INSTALLATION_ID,
    CONF_DPOP_PRIVATE_KEY_PEM,
    CONF_MODEL_NAME,
    CONF_MODEL_YEAR,
    CONF_REFRESH_TOKEN,
    CONF_ACTIVE_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_REGISTRATION_PLATE,
    CONF_VIN,
    DEFAULT_APP_INSTALLATION_ID,
    DOMAIN,
    POLL_ACTIVE,
    POLL_IDLE,
)
from .coordinator import VolvoCoordinator
from .volvo_api import VolvoClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TIME,
    Platform.DEVICE_TRACKER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    data = entry.data
    session = async_get_clientsession(hass)

    client = VolvoClient(
        session,
        vin=data[CONF_VIN],
        dpop_key_pem=data[CONF_DPOP_PRIVATE_KEY_PEM],
        refresh_token=data[CONF_REFRESH_TOKEN],
        app_installation_id=data.get(
            CONF_APP_INSTALLATION_ID, DEFAULT_APP_INSTALLATION_ID
        ),
    )

    # Persist rotated refresh tokens back into the config entry
    def _on_refresh_rotated(new_refresh: str) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_REFRESH_TOKEN: new_refresh},
        )

    client.set_token_updated_callback(_on_refresh_rotated)

    # One-off migration: entries created before model/registration info was
    # persisted (see config_flow.py's _finish_with_vin) won't have it yet.
    # Backfill it from list_cars() so the device page isn't stuck on the
    # generic "Volvo EV" fallback until the integration is removed and
    # re-added.
    if data.get(CONF_MODEL_NAME) is None:
        data = await _migrate_model_info(hass, entry, client, data)

    car_info = await _fetch_car_info(client, data[CONF_VIN])

    coordinator = VolvoCoordinator(
        hass,
        client,
        model_name=data.get(CONF_MODEL_NAME),
        model_year=data.get(CONF_MODEL_YEAR),
        # An override typed in the options wins over the plate Volvo reports.
        registration_plate=entry.options.get(CONF_REGISTRATION_PLATE) or data.get(CONF_REGISTRATION_PLATE),
        car_info=car_info,
        idle_interval=entry.options.get(CONF_IDLE_INTERVAL, POLL_IDLE),
        active_interval=entry.options.get(CONF_ACTIVE_INTERVAL, POLL_ACTIVE),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The listener also fires when a rotated refresh token is saved to entry.data, so only
    # reload when the options (plate override, poll intervals) actually changed.
    options_at_setup = dict(entry.options)

    async def _async_options_updated(hass: HomeAssistant, updated: ConfigEntry) -> None:
        if dict(updated.options) != options_at_setup:
            await hass.config_entries.async_reload(updated.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True



async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _fetch_car_info(client: VolvoClient, vin: str) -> dict | None:
    """The car's own record (powertrain, capabilities). Best effort: ``None`` on any failure."""
    try:
        cars = await client.list_cars()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not read the car record", exc_info=True)
        return None
    return next((c for c in cars if isinstance(c, dict) and c.get("vin") == vin), None)


async def _migrate_model_info(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: VolvoClient,
    data: dict,
) -> dict:
    """Backfill model_name/model_year/registration_plate for entries set up
    before this data was persisted. Best-effort: any failure (offline,
    auth not ready yet, unexpected API shape) just leaves the entry as-is
    and setup continues using the generic device name/model fallback.
    """
    try:
        cars = await client.list_cars()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Model info migration: list_cars() failed", exc_info=True)
        return data

    car = next((c for c in cars if c.get("vin") == data[CONF_VIN]), None)
    if car is None:
        _LOGGER.debug(
            "Model info migration: VIN %s not found in list_cars() response",
            data[CONF_VIN],
        )
        return data

    new_data = {
        **entry.data,
        CONF_MODEL_NAME: car.get("modelName"),
        CONF_MODEL_YEAR: car.get("modelYear"),
        CONF_REGISTRATION_PLATE: car.get("registrationPlate"),
    }
    hass.config_entries.async_update_entry(entry, data=new_data)
    _LOGGER.info(
        "Backfilled model info for %s: %s %s",
        data[CONF_VIN],
        car.get("modelName"),
        car.get("modelYear"),
    )
    return new_data
