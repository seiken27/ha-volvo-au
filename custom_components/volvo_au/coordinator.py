"""DataUpdateCoordinator for Volvo AU integration.

Cadence:
- idle: POLL_IDLE (5 min, an option) -> parked, even when plugged in but not charging
- active: POLL_ACTIVE (1 min, an option) -> charging, in use, a door/window/lock open, climate running
- post-command burst: POLL_POST_CMD_FAST (5 s) for POLL_POST_CMD_WINDOW (60 s)
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    POLL_ACTIVE,
    POLL_IDLE,
    POLL_POST_CMD_FAST,
    POLL_POST_CMD_WINDOW,
)
from .polltiers import is_active
from .volvo_api import VolvoApiError, VolvoAuthError, VolvoClient

_LOGGER = logging.getLogger(__name__)


_AUTH_FAILURE_STATUS_CODES = ("400", "401", "403")


def _is_auth_failure(err: VolvoAuthError) -> bool:
    """Best-effort split between a dead refresh token (prompt for reauth)
    and a transient failure at Volvo's token endpoint (just retry later).

    VolvoAuthError's message embeds the HTTP status from
    VolvoClient._refresh()'s "refresh failed: {status} ..." format. A
    400/401/403 there means the refresh token itself was rejected
    (expired/revoked/invalid_grant) — that needs a fresh login. Anything
    else (5xx, timeouts) is treated as transient so a blip on Volvo's side
    doesn't send the user through a reauth prompt for no reason.
    """
    msg = str(err)
    return any(f"refresh failed: {code}" in msg for code in _AUTH_FAILURE_STATUS_CODES)


class VolvoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the iOS gateway with adaptive cadence."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: VolvoClient,
        *,
        model_name: str | None = None,
        model_year: int | str | None = None,
        registration_plate: str | None = None,
        car_info: dict[str, Any] | None = None,
        idle_interval: int = POLL_IDLE,
        active_interval: int = POLL_ACTIVE,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {client.vin}",
            update_interval=timedelta(seconds=idle_interval),
        )
        self._idle_interval = idle_interval
        # The active poll is never slower than the idle one.
        self._active_interval = min(active_interval, idle_interval)
        self.client = client
        self.model_name = model_name
        self.model_year = model_year
        self.registration_plate = registration_plate
        # The car's own record from /car-information/car (powertrain, capabilities); None if it could not be read.
        self.car_info = car_info
        self._last_command_at: float = 0.0
        self._post_trip_until: float = 0.0
        self._prev_in_use: bool | None = None

    def note_command(self) -> None:
        """Call right after a Lock/Unlock so we re-poll fast for a minute."""
        self._last_command_at = time.time()
        # Schedule a fast refresh in 2s (give the car time to react)
        self.hass.async_create_task(self._burst_refresh())

    async def _burst_refresh(self) -> None:
        """Short polling burst right after a command."""
        import asyncio

        for delay in (2, 5, 10, 20, 40):
            await asyncio.sleep(delay)
            try:
                await self.async_request_refresh()
            except Exception:  # noqa: BLE001
                pass
            if time.time() - self._last_command_at > POLL_POST_CMD_WINDOW:
                return

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            snap = await self.client.snapshot()
        except VolvoAuthError as e:
            if _is_auth_failure(e):
                raise ConfigEntryAuthFailed(str(e)) from e
            raise UpdateFailed(f"auth: {e}") from e
        except VolvoApiError as e:
            raise UpdateFailed(f"api: {e}") from e

        # Adapt cadence for next round
        now = time.time()
        # Detect end-of-trip: usage_mode transitioned from in-use -> abandoned.
        # Start a 5-min 5s burst so the lock state catches up quickly when
        # Hien locks the car after parking. Burst clears early once the car
        # is fully centrally locked.
        try:
            avail = (snap.get("availability") or {}).get("availability") or {}
            um = avail.get("usageMode", "")
            in_use = bool(
                um
                and um
                not in (
                    "USAGE_MODE_ABANDONED",
                    "USAGE_MODE_UNSPECIFIED",
                    "USAGE_MODE_UNKNOWN",
                )
            )
            if self._prev_in_use and not in_use:
                # Just stopped using the car. Start 5-min burst.
                self._post_trip_until = now + 300
                _LOGGER.debug("end-of-trip detected; starting 5min lock burst")
            self._prev_in_use = in_use
        except Exception:  # noqa: BLE001
            pass

        # End burst early if centrally locked.
        if self._post_trip_until > now:
            try:
                ext = (snap.get("exterior") or {}).get("exterior") or {}
                cl = ext.get("centralLock") or ""
                if cl.endswith("_LOCKED"):
                    _LOGGER.debug("car locked; ending post-trip burst early")
                    self._post_trip_until = 0.0
            except Exception:  # noqa: BLE001
                pass

        if now - self._last_command_at < POLL_POST_CMD_WINDOW:
            self.update_interval = timedelta(seconds=POLL_POST_CMD_FAST)
        elif self._post_trip_until > now:
            self.update_interval = timedelta(seconds=POLL_POST_CMD_FAST)
        elif is_active(snap):
            self.update_interval = timedelta(seconds=self._active_interval)
        else:
            self.update_interval = timedelta(seconds=self._idle_interval)
        return snap
