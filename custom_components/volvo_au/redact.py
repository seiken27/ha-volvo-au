"""Remove personal data from a Volvo snapshot before it is put in a diagnostics download.

Pure Python (no Home Assistant imports) so it can be tested on its own.
"""

from __future__ import annotations

from typing import Any

# Whole values replaced when the key is exactly one of these (lower case).
_REDACT_KEYS = {
    "vin", "id", "refresh_token", "dpop_private_key_pem", "id_token", "app_installation_id",
    "registrationplate", "registration_plate", "latitude", "longitude", "altitude", "heading",
    "streetaddress", "streetname", "streetnumber", "postalcode", "city", "name", "usernotes", "notes",
}
REDACTED = "**REDACTED**"


def redact(value: Any, *, vin: str | None = None) -> Any:
    """A copy of ``value`` with sensitive keys blanked and the VIN removed from every string."""
    if isinstance(value, dict):
        return {
            k: REDACTED if str(k).lower() in _REDACT_KEYS else redact(v, vin=vin)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, vin=vin) for v in value]
    if isinstance(value, str) and vin and vin in value:
        return value.replace(vin, "<VIN>")
    return value
