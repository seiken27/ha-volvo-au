"""DataUpdateCoordinator for Volvo C3 integration.

Cadence:
- idle: POLL_IDLE (5 min, an option) -> parked, even when plugged in but not charging
- active: POLL_ACTIVE (1 min, an option) -> charging, in use, a door/window/lock open, climate running
- post-command burst: POLL_POST_CMD_FAST (5 s) for POLL_POST_CMD_WINDOW (60 s)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    POLL_ACTIVE,
    POLL_IDLE,
    POLL_POST_CMD_FAST,
    POLL_POST_CMD_WINDOW,
)
from .livestream import STREAMS, StreamPolicy
from .polltiers import is_active
from .powertrain import capability, sections_without_data
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
        live_updates: bool = True,
        driving_journal: bool = False,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {client.vin}",
            update_interval=timedelta(seconds=idle_interval),
        )
        self._idle_interval = idle_interval
        self._live_updates = live_updates
        # Opt in (options). Needs a car whose own record says it has a journal (None = not stated, so try).
        self.driving_journal = driving_journal and capability(car_info, "supportsDrivingJournal") is not False
        client.supports_journal = self.driving_journal
        client.local_now = dt_util.now
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}
        # Sections a live stream has delivered; a failed or empty poll keeps their value.
        self._streamed: set[str] = set()
        self.stream_policies: dict[str, StreamPolicy] = {}
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
        # Snapshot sections the first poll found empty; entities that depend on them are hidden (see entity.py).
        self.no_data_sections: frozenset[str] = frozenset()
        self._first_poll_done = False
        # Parking climate choices (temperature, seat and steering wheel heating), set by the number/select entities.
        self.climate_temperature: float | None = None
        self.climate_seats: dict[str, str] = {}

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

        snap = self._keep_streamed(snap)

        if not self._first_poll_done:
            self._first_poll_done = True
            self.no_data_sections = sections_without_data(snap)
            if self.no_data_sections:
                _LOGGER.info("No data for %s on the first poll; those entities are hidden", sorted(self.no_data_sections))
        self._adapt_interval(snap)
        return snap

    def _adapt_interval(self, snap: dict[str, Any]) -> bool:
        """Choose the next poll interval from the latest data. Returns True when it changed."""
        previous = self.update_interval
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
                self.client.expect_new_trip()
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
        return self.update_interval != previous

    # ----- live update streams -----

    async def async_start_streams(self) -> None:
        """Keep a connection open per state service so pushed updates arrive as they happen.

        The scheduled poll stays as the safety net. A stream that turns out to be a one-line snapshot is
        dropped (see livestream.py) and that service is simply polled.
        """
        if not self._live_updates or self._stream_tasks:
            return
        for name in STREAMS:
            self._stream_tasks[name] = self.hass.async_create_background_task(
                self._run_stream(name), name=f"volvo-{self.client.vin}-{name}-stream"
            )

    async def async_stop_streams(self) -> None:
        tasks = list(self._stream_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._stream_tasks.clear()

    async def _run_stream(self, name: str) -> None:
        policy = self.stream_policies[name] = StreamPolicy()
        path = STREAMS[name]
        while True:
            started = time.monotonic()
            lines = 0
            try:
                async for frame in self.client.stream_ndjson(path, self.client._vid_body()):
                    lines += 1
                    if lines == 2 and not policy.proven:
                        _LOGGER.info("Volvo pushes %s updates: live updates work for this service", name)
                    self._apply_stream_frame(name, frame)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a broken stream must never take the integration down
                delay = policy.connection_failed()
                _LOGGER.debug("Volvo %s stream failed (%s: %s); retrying in %.0f s", name, type(err).__name__, err, delay)
                await asyncio.sleep(delay)
                continue
            delay = policy.connection_ended(lines, time.monotonic() - started)
            if delay is None:
                _LOGGER.info("Volvo sends %s as a one-off snapshot, not a stream; it is polled instead", name)
                return
            await asyncio.sleep(delay)

    def _keep_streamed(self, snap: dict[str, Any]) -> dict[str, Any]:
        """Keep streamed sections that this poll failed to read or got back empty.

        A live stream may be the only source that works for a section (the same
        bug ericyew fixed for the Polestar odometer). Without this, a failed or
        empty poll would wipe the streamed value until the next stream frame.
        """
        if not self.data or not self._streamed:
            return snap
        kept = {}
        for name in self._streamed:
            new, old = snap.get(name), self.data.get(name)
            if (not new or "_error" in new) and old and "_error" not in old:
                kept[name] = old
        return {**snap, **kept} if kept else snap

    def _apply_stream_frame(self, name: str, frame: dict[str, Any]) -> None:
        if frame:
            self._streamed.add(name)
        if self.data is None or self.data.get(name) == frame:
            return
        data = {**self.data, name: frame}
        self.data = data
        self.last_update_success = True
        if self._adapt_interval(data):
            self._schedule_refresh()  # start the new interval now
        self.async_update_listeners()
