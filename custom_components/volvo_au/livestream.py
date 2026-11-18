"""Rules for the live update streams (pure Python, no Home Assistant imports).

Volvo's gateway answers each ``Get...`` state call with a stream of JSON lines. Until now we read the first
line and hung up. A live stream keeps the connection open and applies every further line. We do not know
whether Volvo pushes more lines for a given car, so the policy below watches what happens and gives up on a
stream that only ever sends one line (polling carries on regardless).
"""

from __future__ import annotations

# A connection that lasted at least this long, or delivered a second line, proves the stream pushes updates.
PROVEN_AFTER_SECONDS = 120.0
# A connection that closed sooner than this having sent at most one line is a snapshot, not a stream.
SNAPSHOT_ONLY_BELOW_SECONDS = 60.0
BACKOFF_START = 30.0
BACKOFF_MAX = 600.0

# Snapshot key -> (service path, "what it is"). Same calls as the one-off readers in volvo_api.py.
STREAMS: dict[str, str] = {
    "battery": "/services.vehiclestates.battery.BatteryService/GetBattery",
    "odometer": "/services.vehiclestates.odometer.OdometerService/GetOdometer",
    "exterior": "/services.vehiclestates.exterior.ExteriorService/GetExterior",
    "availability": "/services.vehiclestates.availability.AvailabilityService/GetAvailability",
    "parking_climatization": (
        "/services.vehiclestates.parkingclimatization.ParkingClimatizationService/GetParkingClimatization"
    ),
    "pre_cleaning": "/services.vehiclestates.precleaning.PreCleaningService/GetPreCleaning",
}


class StreamPolicy:
    """Decides what to do after a stream connection ends."""

    def __init__(self) -> None:
        self.proven = False
        self.snapshot_only = False
        self._backoff = BACKOFF_START

    def connection_ended(self, lines: int, seconds: float) -> float | None:
        """Seconds to wait before reconnecting, or None to stop (this stream is a snapshot only)."""
        if lines >= 2 or seconds >= PROVEN_AFTER_SECONDS:
            self.proven = True
            self._backoff = BACKOFF_START  # a healthy session: start the backoff over
        elif not self.proven and lines <= 1 and seconds < SNAPSHOT_ONLY_BELOW_SECONDS:
            self.snapshot_only = True
            return None
        delay = self._backoff
        self._backoff = min(self._backoff * 2, BACKOFF_MAX)
        return delay

    def connection_failed(self) -> float:
        """Seconds to wait after an error (a refused or broken connection)."""
        delay = self._backoff
        self._backoff = min(self._backoff * 2, BACKOFF_MAX)
        return delay
