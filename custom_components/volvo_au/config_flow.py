"""Config flow for Volvo (AU) integration.

Flow:
1. user step: we generate a fresh DPoP keypair + PKCE pair, show the authorize URL.
2. user pastes the redirect URL (containing ?code=...).
3. we exchange the code for tokens (with DPoP) — verify by listing cars.
4. ask which VIN to attach (if multiple); persist refresh_token + DPoP key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ACR_VALUES,
    AUTHORIZE_URL,
    CLIENT_ID,
    CONF_ACTIVE_INTERVAL,
    CONF_APP_INSTALLATION_ID,
    CONF_DPOP_PRIVATE_KEY_PEM,
    CONF_IDLE_INTERVAL,
    CONF_DRIVING_JOURNAL,
    CONF_LIVE_UPDATES,
    CONF_MODEL_NAME,
    CONF_MODEL_YEAR,
    CONF_REFRESH_TOKEN,
    CONF_REGISTRATION_PLATE,
    CONF_VIN,
    DEFAULT_APP_INSTALLATION_ID,
    POLL_ACTIVE,
    POLL_IDLE,
    DOMAIN,
    REDIRECT_URI,
    SCOPES,
    TOKEN_URL,
    UI_LOCALES,
    USER_AGENT,
)
from .volvo_api import (
    VolvoApiError,
    VolvoAuthError,
    VolvoClient,
    _basic_auth_header,
    _b64url,
    generate_dpop_key_pem,
    load_dpop_key,
    make_dpop_proof,
)

_LOGGER = logging.getLogger(__name__)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_hex(32).upper()
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _normalise_plate(value: Any) -> str:
    """Return the plate upper-cased with surrounding whitespace removed ('' when unset)."""
    return str(value or "").strip().upper()


class VolvoAuOptionsFlow(config_entries.OptionsFlow):
    """Options: poll intervals, and an optional registration plate that overrides the one Volvo reports."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            plate = _normalise_plate(user_input.get(CONF_REGISTRATION_PLATE))
            options: dict[str, Any] = {
                CONF_IDLE_INTERVAL: user_input[CONF_IDLE_INTERVAL],
                CONF_ACTIVE_INTERVAL: user_input[CONF_ACTIVE_INTERVAL],
                CONF_LIVE_UPDATES: user_input.get(CONF_LIVE_UPDATES, True),
                CONF_DRIVING_JOURNAL: user_input.get(CONF_DRIVING_JOURNAL, False),
            }
            # A blank plate removes the override, so the plate from Volvo is used again.
            if plate:
                options[CONF_REGISTRATION_PLATE] = plate
            return self.async_create_entry(title="", data=options)
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_IDLE_INTERVAL, default=opts.get(CONF_IDLE_INTERVAL, POLL_IDLE)): vol.All(
                        int, vol.Range(min=60, max=86400)
                    ),
                    vol.Optional(CONF_ACTIVE_INTERVAL, default=opts.get(CONF_ACTIVE_INTERVAL, POLL_ACTIVE)): vol.All(
                        int, vol.Range(min=10, max=3600)
                    ),
                    vol.Optional(CONF_LIVE_UPDATES, default=opts.get(CONF_LIVE_UPDATES, True)): bool,
                    vol.Optional(CONF_DRIVING_JOURNAL, default=opts.get(CONF_DRIVING_JOURNAL, False)): bool,
                    vol.Optional(
                        CONF_REGISTRATION_PLATE,
                        description={"suggested_value": opts.get(CONF_REGISTRATION_PLATE, "")},
                    ): str,
                }
            ),
        )


class VolvoAuConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the OAuth-paste config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return VolvoAuOptionsFlow()

    def __init__(self) -> None:
        self._dpop_pem: str | None = None
        self._verifier: str | None = None
        self._authorize_url: str | None = None
        self._tokens: dict[str, Any] | None = None
        self._cars: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    # Step 1: present the URL the user must open
    async def async_step_user(self, user_input=None):
        if self._dpop_pem is None:
            self._dpop_pem = generate_dpop_key_pem()
            self._verifier, challenge = _pkce_pair()
            params = {
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "ui_locales": UI_LOCALES,
                "prompt": "login",
                "acr_values": ACR_VALUES,
            }
            self._authorize_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required("redirect_url"): str}
                ),
                description_placeholders={"authorize_url": self._authorize_url or ""},
            )

        raw = user_input["redirect_url"].strip()
        code = self._extract_code(raw)
        if code == "__oauth_error__":
            return self._show_user_error("oauth_error")
        if not code:
            return self._show_user_error("missing_code")

        try:
            self._tokens = await self._exchange_code(code)
        except VolvoAuthError as e:
            _LOGGER.warning("Volvo token exchange failed: %s", e)
            return self._show_user_error("auth_failed")

        # Verify by fetching the car list
        session = async_get_clientsession(self.hass)
        client = VolvoClient(
            session,
            vin="UNKNOWN",  # any value; list_cars doesn't need it
            dpop_key_pem=self._dpop_pem,
            refresh_token=self._tokens["refresh_token"],
        )
        # Seed in-memory access token to skip refresh
        client._access_token = self._tokens["access_token"]
        client._access_token_expires_at = (
            time.time() + int(self._tokens.get("expires_in", 1800))
        )
        try:
            self._cars = await client.list_cars()
        except VolvoApiError as e:
            _LOGGER.warning("list_cars failed after login: %s", e)
            return self._show_user_error("list_cars_failed")

        if not self._cars:
            return self._show_user_error("no_cars")

        if len(self._cars) == 1:
            return await self._finish_with_vin(self._cars[0].get("vin"))

        return await self.async_step_pick_vin()

    def _show_user_error(self, code: str):
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders={"authorize_url": self._authorize_url or ""},
            errors={"base": code},
        )

    @staticmethod
    def _extract_code(raw: str) -> str | None:
        """Pull an OAuth code out of whatever the user pasted.

        Accepts:
          - the full callback URL: ``volvooncall://auth/callback?code=ABC&state=...``
          - just the query string: ``code=ABC&state=...``
          - just the code itself: ``ABC``

        Returns ``"__oauth_error__"`` if the URL contains ``error=...``.
        """
        if not raw:
            return None
        # Try as a URL/query string first.
        try:
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query or raw)
        except Exception:  # noqa: BLE001
            qs = {}
        if qs.get("error"):
            return "__oauth_error__"
        code = (qs.get("code") or [None])[0]
        if code:
            return code
        # Fall back: bare code. Volvo codes are URL-safe alnum/_- and ~30+ chars.
        if re.fullmatch(r"[A-Za-z0-9_\-]{16,}", raw):
            return raw
        return None

    # Step 2 (optional): pick VIN when account has more than one car
    async def async_step_pick_vin(self, user_input=None):
        # Build a {vin: friendly_label} mapping so users see car info, not raw VINs.
        choices: dict[str, str] = {}
        # Cars already set up are left out, so a second car is added by running the setup again.
        configured = {entry.data.get(CONF_VIN) for entry in self._async_current_entries()}
        for c in self._cars:
            vin = c.get("vin")
            if not vin or vin in configured:
                continue
            model = c.get("modelName") or "Volvo"
            year = c.get("modelYear")
            plate = c.get("registrationPlate")
            bits = [model]
            if year:
                bits.append(f"({year})")
            label = " ".join(bits)
            if plate:
                label = f"{label} — {plate} — {vin}"
            else:
                label = f"{label} — {vin}"
            choices[vin] = label
        if not choices:
            return self.async_abort(reason="already_configured")
        if user_input is None:
            return self.async_show_form(
                step_id="pick_vin",
                data_schema=vol.Schema({vol.Required(CONF_VIN): vol.In(choices)}),
            )
        return await self._finish_with_vin(user_input[CONF_VIN])

    async def _finish_with_vin(self, vin: str):
        assert self._tokens is not None and self._dpop_pem is not None
        await self.async_set_unique_id(f"{DOMAIN}_{vin}")
        self._abort_if_unique_id_configured()
        # Use model+year for a friendlier entry title (falls back to VIN).
        car = next((c for c in self._cars if c.get("vin") == vin), {})
        model = car.get("modelName") or "Volvo"
        year = car.get("modelYear")
        plate = car.get("registrationPlate")
        title = f"{model} {year}" if year else f"{model} {vin}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_VIN: vin,
                CONF_DPOP_PRIVATE_KEY_PEM: self._dpop_pem,
                CONF_REFRESH_TOKEN: self._tokens["refresh_token"],
                CONF_APP_INSTALLATION_ID: DEFAULT_APP_INSTALLATION_ID,
                CONF_MODEL_NAME: model,
                CONF_MODEL_YEAR: year,
                CONF_REGISTRATION_PLATE: plate,
            },
        )

    # ----- reauth -----
    # Triggered when the coordinator raises ConfigEntryAuthFailed (refresh
    # token dead/revoked) — see coordinator.py's _is_auth_failure(). Reuses
    # the same authorize-URL/paste-the-callback flow as initial setup, then
    # updates the existing entry in place rather than creating a new one.

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return await self._async_relogin(user_input, "reauth_confirm", "reauth_successful")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Sign in again with a different Volvo ID, keeping the entry, its entity IDs and history.

        The new account must have the same car (VIN), for example a guest login for the same vehicle.
        """
        self._reauth_entry = self._get_reconfigure_entry()
        return await self._async_relogin(user_input, "reconfigure", "reconfigure_successful")

    async def _async_relogin(
        self, user_input: dict[str, Any] | None, step_id: str, success_reason: str
    ) -> config_entries.ConfigFlowResult:
        if self._dpop_pem is None:
            # Fresh DPoP keypair + PKCE pair, same as async_step_user — the
            # old DPoP key is presumed dead along with the refresh token.
            self._dpop_pem = generate_dpop_key_pem()
            self._verifier, challenge = _pkce_pair()
            params = {
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "ui_locales": UI_LOCALES,
                "prompt": "login",
                "acr_values": ACR_VALUES,
            }
            self._authorize_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

        if user_input is None:
            return self._show_reauth_form(step_id=step_id)

        raw = user_input["redirect_url"].strip()
        code = self._extract_code(raw)
        if code == "__oauth_error__":
            return self._show_reauth_form(step_id=step_id, error="oauth_error")
        if not code:
            return self._show_reauth_form(step_id=step_id, error="missing_code")

        try:
            tokens = await self._exchange_code(code)
        except VolvoAuthError as e:
            _LOGGER.warning("Volvo reauth token exchange failed: %s", e)
            return self._show_reauth_form(step_id=step_id, error="auth_failed")

        # Verify this login is for the same car as the entry being
        # reauthenticated — otherwise someone could silently swap the
        # entry onto a different vehicle by pasting a different account's
        # callback URL.
        session = async_get_clientsession(self.hass)
        client = VolvoClient(
            session,
            vin="UNKNOWN",
            dpop_key_pem=self._dpop_pem,
            refresh_token=tokens["refresh_token"],
        )
        client._access_token = tokens["access_token"]
        client._access_token_expires_at = (
            time.time() + int(tokens.get("expires_in", 1800))
        )
        try:
            cars = await client.list_cars()
        except VolvoApiError as e:
            _LOGGER.warning("list_cars failed during reauth: %s", e)
            return self._show_reauth_form(step_id=step_id, error="list_cars_failed")

        assert self._reauth_entry is not None
        expected_vin = self._reauth_entry.data.get(CONF_VIN)
        if expected_vin and not any(c.get("vin") == expected_vin for c in cars):
            return self._show_reauth_form(step_id=step_id, error="vin_mismatch")

        self.hass.config_entries.async_update_entry(
            self._reauth_entry,
            data={
                **self._reauth_entry.data,
                CONF_DPOP_PRIVATE_KEY_PEM: self._dpop_pem,
                CONF_REFRESH_TOKEN: tokens["refresh_token"],
            },
        )
        await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
        return self.async_abort(reason=success_reason)

    def _show_reauth_form(self, *, step_id: str = "reauth_confirm", error: str | None = None):
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders={"authorize_url": self._authorize_url or ""},
            errors={"base": error} if error else {},
        )

    # ----- internals -----

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        assert self._dpop_pem is not None and self._verifier is not None
        key = load_dpop_key(self._dpop_pem)
        loop = asyncio.get_running_loop()
        dpop = await loop.run_in_executor(
            None, make_dpop_proof, key, "POST", TOKEN_URL
        )
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": self._verifier,
            }
        )
        headers = {
            "Authorization": _basic_auth_header(),
            "DPoP": dpop,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        session = async_get_clientsession(self.hass)
        async with session.post(TOKEN_URL, headers=headers, data=body) as r:
            txt = await r.text()
            if r.status != 200:
                nonce = r.headers.get("dpop-nonce")
                if r.status == 400 and nonce:
                    dpop2 = await loop.run_in_executor(
                        None,
                        lambda: make_dpop_proof(key, "POST", TOKEN_URL, nonce=nonce),
                    )
                    headers["DPoP"] = dpop2
                    async with session.post(
                        TOKEN_URL, headers=headers, data=body
                    ) as r2:
                        txt = await r2.text()
                        if r2.status != 200:
                            raise VolvoAuthError(f"token exchange: {r2.status} {txt[:200]}")
                        return json.loads(txt)
                raise VolvoAuthError(f"token exchange: {r.status} {txt[:200]}")
            return json.loads(txt)
