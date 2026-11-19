"""Async Volvo iOS gateway client for Home Assistant.

Ported from projects/volvo-ha/client/{oauth_login,volvo_client}.py.

Design:
- DPoP private key + refresh token live in the config entry (HA encrypts at rest)
- access_token is in-memory only, refreshed via DPoP-bound refresh_token
- data endpoints use plain Bearer (the iOS gateway does not require DPoP there)
- ndjson "stream" endpoints are read first-frame-then-close (= snapshot poll)
- Lock/Unlock use hand-rolled protobuf in a gRPC frame
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import struct
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from . import climate, journal
from .polltiers import SlowTiers
from .const import (
    API_BASE,
    COMMAND_TIMEOUT_S,
    CLIENT_ID,
    DEFAULT_APP_INSTALLATION_ID,
    TOKEN_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Seconds of silence on an open update stream before we give up on that connection and reconnect.
STREAM_IDLE_TIMEOUT = 900.0


def _parse_parked_location_frame(frame: bytes) -> dict[str, Any] | None:
    """Decode a StreamLastParkedLocations protobuf frame."""
    def _varint(d: bytes, p: int) -> tuple[int, int]:
        r = 0
        s = 0
        while True:
            b = d[p]
            p += 1
            r |= (b & 0x7F) << s
            if not (b & 0x80):
                return r, p
            s += 7

    # Outer: f1=vin (str), f2=position (msg)
    # Position: f1=longitude (double), f2=latitude (double), f3=timestamp (msg{secs, nanos})
    out: dict[str, Any] = {}
    p = 0
    pos_buf: bytes | None = None
    while p < len(frame):
        tag, p = _varint(frame, p)
        fld = tag >> 3
        wt = tag & 7
        if wt == 2:
            ln, p = _varint(frame, p)
            pay = frame[p : p + ln]
            p += ln
            if fld == 2:
                pos_buf = pay
        elif wt == 0:
            _, p = _varint(frame, p)
        else:
            return None
    if not pos_buf:
        return None
    p = 0
    while p < len(pos_buf):
        tag, p = _varint(pos_buf, p)
        fld = tag >> 3
        wt = tag & 7
        if wt == 1:  # fixed64 -> double
            val = struct.unpack("<d", pos_buf[p : p + 8])[0]
            p += 8
            if fld == 1:
                out["longitude"] = val
            elif fld == 2:
                out["latitude"] = val
        elif wt == 2:
            ln, p = _varint(pos_buf, p)
            ts_buf = pos_buf[p : p + ln]
            p += ln
            if fld == 3:
                tp = 0
                while tp < len(ts_buf):
                    ttag, tp = _varint(ts_buf, tp)
                    twt = ttag & 7
                    tfld = ttag >> 3
                    if twt == 0:
                        tv, tp = _varint(ts_buf, tp)
                        if tfld == 1:
                            out["timestamp"] = tv
                    else:
                        return out
        else:
            return out
    return out


def _parse_weather_report_frame(frame: bytes) -> dict[str, Any] | None:
    """Decode a GetWeatherReport gRPC frame.

    Schema:
      f1 = msg {
        f1 = timestamp ms (varint)
        f2 = temperature °C (double, wire-type 1 / i64)
        f3 = source/quality enum (varint)
      }
    """

    def _varint(d: bytes, p: int) -> tuple[int, int]:
        r = 0
        s = 0
        while True:
            b = d[p]
            p += 1
            r |= (b & 0x7F) << s
            if not (b & 0x80):
                return r, p
            s += 7

    info: dict[str, Any] = {}
    p = 0
    while p < len(frame):
        try:
            tag, p = _varint(frame, p)
        except IndexError:
            break
        fld = tag >> 3
        wt = tag & 7
        if fld == 1 and wt == 2:
            ln, p = _varint(frame, p)
            sub = frame[p : p + ln]
            p += ln
            q = 0
            while q < len(sub):
                stag, q = _varint(sub, q)
                sf = stag >> 3
                swt = stag & 7
                if swt == 0:
                    v, q = _varint(sub, q)
                    if sf == 1:
                        info["timestamp_ms"] = v
                    elif sf == 3:
                        info["source"] = v
                elif swt == 1:
                    if sf == 2 and q + 8 <= len(sub):
                        info["temperature_c"] = struct.unpack(
                            "<d", sub[q : q + 8]
                        )[0]
                    q += 8
                elif swt == 2:
                    ln2, q = _varint(sub, q)
                    q += ln2
                elif swt == 5:
                    q += 4
                else:
                    break
        elif wt == 0:
            _, p = _varint(frame, p)
        elif wt == 1:
            p += 8
        elif wt == 2:
            ln, p = _varint(frame, p)
            p += ln
        elif wt == 5:
            p += 4
        else:
            break
    return info or None


def _parse_software_info_frame(frame: bytes) -> dict[str, Any] | None:
    """Decode a GetSoftwareInfo gRPC response frame.

    Observed schema:
      f1 = Update msg:
        f1 = update id (uuid str)
        f2 = Content msg:
          f1 = title  (e.g. "5.0.5 Display update")
          f2 = ref label (e.g. "Software update Ref: C00018")
          f3 = release notes (HTML-ish blob)
          f4 = ref code (e.g. "C00018")
          f5 = msg { f1 = some int (e.g. 5400) }
          f6 = version string (e.g. "5.0.5")  <-- what we expose as version
        f4 = status enum (varint)
        f5 = msg { f1 = version str (mirror of content.f6) }
        f10 = msg { f1 = unix timestamp }
    """
    def _varint(d: bytes, p: int) -> tuple[int, int]:
        r = 0
        s = 0
        while True:
            b = d[p]
            p += 1
            r |= (b & 0x7F) << s
            if not (b & 0x80):
                return r, p
            s += 7

    def _walk(buf: bytes) -> list[tuple[int, int, Any]]:
        out: list[tuple[int, int, Any]] = []
        p = 0
        while p < len(buf):
            try:
                tag, p = _varint(buf, p)
            except IndexError:
                break
            fld = tag >> 3
            wt = tag & 7
            if wt == 0:
                v, p = _varint(buf, p)
                out.append((fld, wt, v))
            elif wt == 1:
                out.append((fld, wt, buf[p : p + 8]))
                p += 8
            elif wt == 2:
                ln, p = _varint(buf, p)
                out.append((fld, wt, buf[p : p + ln]))
                p += ln
            elif wt == 5:
                out.append((fld, wt, buf[p : p + 4]))
                p += 4
            else:
                break
        return out

    info: dict[str, Any] = {}
    for fld, wt, val in _walk(frame):
        if fld == 1 and wt == 2:
            update = val
            for ufld, uwt, uval in _walk(update):
                if ufld == 1 and uwt == 2:
                    try:
                        info["update_id"] = uval.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                elif ufld == 2 and uwt == 2:
                    # Content sub-message: f1=title, f2=ref_label, f3=release_notes
                    for cfld, cwt, cval in _walk(uval):
                        if cwt != 2:
                            continue
                        try:
                            text = cval.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                        if cfld == 1:
                            info["title"] = text
                        elif cfld == 2:
                            info["ref_label"] = text
                        elif cfld == 3:
                            info["release_notes"] = text
                elif ufld == 3 and uwt == 2:
                    try:
                        info["ref_code"] = uval.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                elif ufld == 4 and uwt == 0:
                    info["status"] = uval
                elif ufld == 6 and uwt == 2:
                    try:
                        info["version"] = uval.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                elif ufld == 10 and uwt == 2:
                    for tfld, twt, tval in _walk(uval):
                        if tfld == 1 and twt == 0:
                            info["timestamp"] = tval
    return info or None


# What the car's final command answer means, in words for a person (enum values from the Volvo app).
COMMAND_STATUS_REASONS: dict[int, str] = {
    0: "an unknown error",
    5: "the car did not receive the command in time",
    7: "the car did not answer in time",
    8: "the car reported an unknown error",
    9: "privacy mode is on in the car, which blocks remote commands",
    10: "the car is not in a state that allows this command (for example it is being driven)",
    11: "the car rejected this particular command",
    12: "another command is still in progress",
}
# 2 (car offline) is left out on purpose: that value was seen in answers to commands that worked (15 Nov 2026).


def _proto_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """Top-level (field, wire type, value) triples of a protobuf message; stops at anything malformed."""
    out: list[tuple[int, int, Any]] = []
    p = 0

    def varint() -> int:
        nonlocal p
        r = s = 0
        while True:
            b = buf[p]
            p += 1
            r |= (b & 0x7F) << s
            if not b & 0x80:
                return r
            s += 7

    try:
        while p < len(buf):
            tag = varint()
            fld, wt = tag >> 3, tag & 7
            if wt == 0:
                out.append((fld, wt, varint()))
            elif wt == 2:
                ln = varint()
                out.append((fld, wt, buf[p : p + ln]))
                p += ln
            elif wt == 1:
                out.append((fld, wt, buf[p : p + 8]))
                p += 8
            elif wt == 5:
                out.append((fld, wt, buf[p : p + 4]))
                p += 4
            else:
                break
    except IndexError:
        pass
    return out


def command_refusal(frames: list[bytes]) -> str | None:
    """Why the car refused a command, from the invocation reply (None when it did not refuse).

    The reply is an invocation response (field 3 = status, field 4 = message), either on its own or wrapped
    in field 1. Only statuses in COMMAND_STATUS_REASONS count as a refusal.
    """
    for frame in reversed(frames):
        fields = _proto_fields(frame)
        for _, wt, val in fields:
            if wt == 2 and not any(f == 3 and w == 0 for f, w, _ in fields):
                inner = _proto_fields(val)
                if any(f == 3 and w == 0 for f, w, _ in inner):
                    fields = inner
                    break
        status = next((v for f, w, v in fields if f == 3 and w == 0), None)
        if status is None:
            continue
        reason = COMMAND_STATUS_REASONS.get(status)
        if reason is None:
            return None
        message = next((v for f, w, v in fields if f == 4 and w == 2), b"")
        text = message.decode("utf-8", "replace").strip() if message else ""
        return f"{reason} ({text})" if text else reason
    return None



# ---------------------------------------------------------------------------
# DPoP helpers (sync; cheap, no I/O)
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_int(value: int, length: int) -> str:
    return _b64url(value.to_bytes(length, "big"))


def generate_dpop_key_pem() -> str:
    """Make a new P-256 DPoP keypair and return as PEM string."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def load_dpop_key(pem: str) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem.encode("ascii"), password=None)


def _jwk_from_key(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    nums = key.public_key().public_numbers()
    return {
        "crv": "P-256",
        "kty": "EC",
        "x": _b64url_int(nums.x, 32),
        "y": _b64url_int(nums.y, 32),
    }


def _es256_sign(key: ec.EllipticCurvePrivateKey, message: bytes) -> bytes:
    der_sig = key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def make_dpop_proof(
    key: ec.EllipticCurvePrivateKey,
    http_method: str,
    http_url: str,
    *,
    nonce: str | None = None,
) -> str:
    parsed = urlparse(http_url)
    htu = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    header = {"alg": "ES256", "typ": "dpop+jwt", "jwk": _jwk_from_key(key)}
    payload: dict[str, Any] = {
        "iat": int(time.time()),
        "htm": http_method.upper(),
        "htu": htu,
        "jti": str(uuid.uuid4()).upper(),
    }
    if nonce is not None:
        payload["nonce"] = nonce
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = _es256_sign(key, f"{h}.{p}".encode())
    return f"{h}.{p}.{_b64url(sig)}"


def _basic_auth_header() -> str:
    raw = f"{CLIENT_ID}:".encode()
    return "Basic " + base64.b64encode(raw).decode()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VolvoAuthError(Exception):
    """Auth or token refresh failed."""


class VolvoApiError(Exception):
    """Data API returned non-200."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class VolvoClient:
    """Async client. One instance per config entry."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        vin: str,
        dpop_key_pem: str,
        refresh_token: str,
        app_installation_id: str = DEFAULT_APP_INSTALLATION_ID,
    ) -> None:
        self._tiers = SlowTiers()
        self.supports_journal = True  # the coordinator turns this off for cars without a driving journal
        self._last_trip: dict[str, Any] | None = None
        self._last_trip_at = 0.0
        self._trip_expect_until = 0.0
        self.local_now = lambda: datetime.now(UTC)  # the coordinator swaps in Home Assistant's local time
        self._month: dict[str, Any] | None = None
        self._month_key: tuple[Any, ...] | None = None
        self._month_at = 0.0
        self._session = session
        self.vin = vin
        self._dpop_key = load_dpop_key(dpop_key_pem)
        self._refresh_token = refresh_token
        self._app_installation_id = app_installation_id
        # in-memory access token state
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        # callback invoked with new (refresh_token, dpop_key_pem) when rotated
        self._on_tokens_updated = None

        # CPU work serialised so we don't hammer the executor
        self._refresh_lock = asyncio.Lock()

    def set_token_updated_callback(self, cb) -> None:
        """cb(refresh_token: str) -> None; called when refresh token rotates."""
        self._on_tokens_updated = cb

    # -------- access token lifecycle --------

    async def _ensure_access_token(self) -> str:
        if self._access_token and time.time() < self._access_token_expires_at - 60:
            return self._access_token
        async with self._refresh_lock:
            if self._access_token and time.time() < self._access_token_expires_at - 60:
                return self._access_token
            await self._refresh()
            assert self._access_token is not None
            return self._access_token

    async def _refresh(self) -> None:
        loop = asyncio.get_running_loop()
        dpop = await loop.run_in_executor(
            None, make_dpop_proof, self._dpop_key, "POST", TOKEN_URL
        )
        headers = {
            "Authorization": _basic_auth_header(),
            "DPoP": dpop,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = urlencode(
            {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
        )
        async with self._session.post(TOKEN_URL, headers=headers, data=body) as r:
            text = await r.text()
            if r.status != 200:
                # DPoP nonce dance
                nonce = r.headers.get("dpop-nonce")
                if r.status == 400 and nonce:
                    dpop2 = await loop.run_in_executor(
                        None,
                        lambda: make_dpop_proof(
                            self._dpop_key, "POST", TOKEN_URL, nonce=nonce
                        ),
                    )
                    headers["DPoP"] = dpop2
                    async with self._session.post(
                        TOKEN_URL, headers=headers, data=body
                    ) as r2:
                        text = await r2.text()
                        if r2.status != 200:
                            raise VolvoAuthError(
                                f"refresh failed: {r2.status} {text[:200]}"
                            )
                        data = json.loads(text)
                else:
                    raise VolvoAuthError(f"refresh failed: {r.status} {text[:200]}")
            else:
                data = json.loads(text)

        self._access_token = data["access_token"]
        self._access_token_expires_at = time.time() + int(data.get("expires_in", 1800))
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_tokens_updated:
                # callback may be sync or async; support both
                res = self._on_tokens_updated(new_refresh)
                if asyncio.iscoroutine(res):
                    await res
        _LOGGER.debug(
            "Refreshed Volvo token: expires_in=%ss rotated=%s",
            data.get("expires_in"),
            bool(new_refresh and new_refresh != self._refresh_token),
        )

    # -------- request helpers --------

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._ensure_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "App-Installation-Id": self._app_installation_id,
        }

    async def _json_get(self, path: str, *, with_vin: bool = False) -> Any:
        headers = await self._auth_headers()
        headers["Accept"] = "application/json"
        if with_vin:
            headers["vin"] = self.vin
        async with self._session.get(API_BASE + path, headers=headers) as r:
            if r.status != 200:
                raise VolvoApiError(f"GET {path}: {r.status} {await r.text()}")
            return await r.json(content_type=None)

    async def _ndjson_first_frame(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = await self._auth_headers()
        headers.update(
            {
                "Accept": "application/x-ndjson",
                "Content-Type": "application/x-ndjson",
                "vin": self.vin,
            }
        )
        async with self._session.post(
            API_BASE + path, headers=headers, data=json.dumps(body)
        ) as r:
            if r.status != 200:
                txt = await r.text()
                raise VolvoApiError(f"POST {path}: {r.status} {txt[:200]}")
            # Read the first non-empty line, then drop the connection
            async for raw in r.content:
                line = raw.strip()
                if line:
                    return json.loads(line)
        raise VolvoApiError(f"No data from {path}")

    async def stream_ndjson(self, path: str, body: dict[str, Any]):
        """Yield every JSON line the gateway sends, until it closes the connection.

        Unlike ``_ndjson_first_frame`` this keeps the connection open, so a car that pushes updates
        delivers them as they happen. The read timeout only fires when the connection goes silent for
        ``STREAM_IDLE_TIMEOUT`` seconds (the caller reconnects).
        """
        headers = await self._auth_headers()
        headers.update({"Accept": "application/x-ndjson", "Content-Type": "application/x-ndjson", "vin": self.vin})
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=STREAM_IDLE_TIMEOUT)
        async with self._session.post(API_BASE + path, headers=headers, data=json.dumps(body), timeout=timeout) as r:
            if r.status != 200:
                raise VolvoApiError(f"POST {path}: {r.status} {(await r.text())[:200]}")
            async for raw in r.content:
                line = raw.strip()
                if line:
                    yield json.loads(line)

    # -------- protobuf / gRPC bits (same as sync client) --------

    @staticmethod
    def _pb_len_delim(field: int, payload: bytes) -> bytes:
        tag = (field << 3) | 2
        out = bytearray()
        v = tag
        while True:
            byte = v & 0x7F
            v >>= 7
            if v:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
        v = len(payload)
        while True:
            byte = v & 0x7F
            v >>= 7
            if v:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
        out.extend(payload)
        return bytes(out)

    @classmethod
    def _grpc_frame(cls, payload: bytes) -> bytes:
        return b"\x00" + struct.pack(">I", len(payload)) + payload

    async def _vin_only_grpc(self, path: str, *, timeout: float = 30.0) -> dict[str, Any]:
        vin_bytes = self.vin.encode("ascii")
        inner = self._pb_len_delim(1, vin_bytes)
        outer = self._pb_len_delim(1, inner)
        body = self._grpc_frame(outer)
        return await self._grpc_post(path, body, timeout=timeout)

    async def _chronos_set_int_grpc(
        self, path: str, value: int, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Chronos service "set" verb: {request: {id, vin, source: "mapp", meta: {1=600}}, value: <int>}.

        Outer field 1 = Request msg, field 2 = the new value (varint).
        """
        @staticmethod
        def _varint(v: int) -> bytes:
            out = bytearray()
            while True:
                b = v & 0x7F
                v >>= 7
                if v:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        def _varint_fn(v: int) -> bytes:
            out = bytearray()
            while True:
                b = v & 0x7F
                v >>= 7
                if v:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        request_id = str(uuid.uuid4()).lower()
        # Request submessage: id(1) + vin(2) + source(3)="mapp" + meta(4)={1=600 varint}
        meta = self._pb_len_delim(4, b"\x08" + _varint_fn(600))
        req = (
            self._pb_len_delim(1, request_id.encode("ascii"))
            + self._pb_len_delim(2, self.vin.encode("ascii"))
            + self._pb_len_delim(3, b"mapp")
            + meta
        )
        outer = self._pb_len_delim(1, req) + b"\x10" + _varint_fn(value)
        body = self._grpc_frame(outer)
        return await self._grpc_post(path, body, timeout=timeout)

    async def _chronos_set_target_soc_grpc(
        self,
        path: str,
        level: int,
        *,
        setting_type: int = 3,  # ChargeTargetLevelSettingType.CUSTOM
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Chronos "set" verb for SetTargetSoc: same Request envelope as
        _chronos_set_int_grpc, but with two varint fields on the outer
        message — batteryChargeTargetLevel (field 2) and settingType
        (field 3, 1=DAILY/2=LONG_TRIP/3=CUSTOM).
        """

        def _varint(v: int) -> bytes:
            out = bytearray()
            while True:
                b = v & 0x7F
                v >>= 7
                if v:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        request_id = str(uuid.uuid4()).lower()
        meta = self._pb_len_delim(4, b"\x08" + _varint(600))
        req = (
            self._pb_len_delim(1, request_id.encode("ascii"))
            + self._pb_len_delim(2, self.vin.encode("ascii"))
            + self._pb_len_delim(3, b"mapp")
            + meta
        )
        outer = (
            self._pb_len_delim(1, req)
            + b"\x10" + _varint(level)  # field 2, varint
            + b"\x18" + _varint(setting_type)  # field 3, varint
        )
        body = self._grpc_frame(outer)
        return await self._grpc_post(path, body, timeout=timeout)

    async def _grpc_post(
        self, path: str, body: bytes, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        headers = await self._auth_headers()
        headers.update(
            {
                "content-type": "application/grpc",
                "te": "trailers",
                "vin": self.vin,
            }
        )
        loop = asyncio.get_running_loop()
        # A car command (lock, unlock, climate, ...) normally answers in under 1 s: HTTP 200, one message
        # (request id, VIN, field 3 = 2) and the reply closes (15 Nov 2026). From 22:11 to 22:28 that evening
        # Volvo sent nothing at all, even in 120 s, and then recovered; the Volvo app still worked. A timeout now
        # returns {"ok": False, "timeout": True} and the lock entity reports it, instead of an unexpected error.
        if path.startswith("/invocation."):
            timeout = max(timeout, COMMAND_TIMEOUT_S)
        t0 = time.monotonic()
        # What arrived so far. _do fills it while it reads, so it is still there when a timeout ends the read.
        prog: dict[str, Any] = {"http_status": None, "headers": {}, "buf": b"", "times": [], "headers_at": None}

        def _do() -> None:
            with httpx.Client(http2=True, timeout=timeout) as cli:
                with cli.stream("POST", API_BASE + path, headers=headers, content=body) as r:
                    prog["http_status"] = r.status_code
                    prog["headers"] = dict(r.headers)
                    prog["headers_at"] = round(time.monotonic() - t0, 1)
                    for chunk in r.iter_bytes():
                        prog["buf"] += chunk
                        prog["times"].append(round(time.monotonic() - t0, 1))

        def _frames(buf: bytes) -> list[bytes]:
            out, i = [], 0
            while i + 5 <= len(buf):
                length = struct.unpack(">I", buf[i + 1 : i + 5])[0]
                if i + 5 + length > len(buf):
                    break
                out.append(buf[i + 5 : i + 5 + length])
                i += 5 + length
            return out

        timed_out, error = False, None
        try:
            await loop.run_in_executor(None, _do)
        except httpx.TimeoutException:
            timed_out = True
        except httpx.HTTPError as err:
            error = f"request failed: {type(err).__name__}"
        waited = round(time.monotonic() - t0, 1)
        frames = _frames(prog["buf"])
        http_status = prog["http_status"]
        grpc_status = prog["headers"].get("grpc-status")
        grpc_message = prog["headers"].get("grpc-message")
        if path.startswith("/invocation."):
            # Kept at INFO while we learn what Volvo sends for a command (15 Nov 2026).
            _LOGGER.info(
                "Volvo %s: HTTP %s (headers at %s s), grpc-status %s, frames %s at %s s, %s after %s s",
                path, http_status, prog["headers_at"], grpc_status, [f.hex() for f in frames], prog["times"],
                "TIMEOUT" if timed_out else (error or "closed"), waited,
            )
        if error:
            _LOGGER.warning("Volvo %s: %s", path, error)
            return {"ok": False, "timeout": False, "error": error}
        if timed_out:
            _LOGGER.warning(
                "Volvo %s: the reply did not end within %s s (HTTP %s, %d message(s) received).",
                path, waited, http_status, len(frames),
            )
            return {"ok": False, "timeout": True, "waited_s": waited, "http_status": http_status,
                    "frame_count": len(frames), "frames": [f.hex() for f in frames],
                    "error": f"no final answer from the car after {waited:.0f} s"}
        refusal = command_refusal(frames) if path.startswith("/invocation.") else None
        if refusal:
            _LOGGER.warning("Volvo %s: the car refused the command: %s", path, refusal)
        return {
            "ok": http_status == 200 and (grpc_status in (None, "0")) and not refusal,
            **({"error": refusal} if refusal else {}),
            "http_status": http_status,
            "grpc_status": grpc_status,
            "grpc_message": grpc_message,
            "frame_count": len(frames),
            "frames": [f.hex() for f in frames],
        }

    # -------- request body templates --------

    def _vid_body(self) -> dict[str, str]:
        return {"vin": self.vin, "id": str(uuid.uuid4())}

    def _chronos_body(self) -> dict[str, Any]:
        return {
            "request": {
                "id": str(uuid.uuid4()),
                "vin": self.vin,
                "source": "c3-package",
            }
        }

    # =====================================================================
    # PUBLIC API
    # =====================================================================

    async def list_cars(self) -> list[dict[str, Any]]:
        return await self._json_get("/car-information/car")

    async def get_battery(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.battery.BatteryService/GetBattery",
            self._vid_body(),
        )

    async def get_odometer(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.odometer.OdometerService/GetOdometer",
            self._vid_body(),
        )

    async def get_exterior(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.exterior.ExteriorService/GetExterior",
            self._vid_body(),
        )

    async def get_health(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.health.HealthService/GetHealth",
            self._vid_body(),
        )

    async def get_availability(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.availability.AvailabilityService/GetAvailability",
            self._vid_body(),
        )

    async def get_parking_climatization(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.parkingclimatization."
            "ParkingClimatizationService/GetParkingClimatization",
            self._vid_body(),
        )

    async def get_pre_cleaning(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/services.vehiclestates.precleaning.PreCleaningService/GetPreCleaning",
            self._vid_body(),
        )

    async def get_last_parked_location(self) -> dict[str, Any] | None:
        """Read the most recent parked GPS coordinate via gRPC streaming.

        Returns {"longitude": float, "latitude": float, "timestamp": int} or None.
        """
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        body = self._grpc_frame(inner)
        headers = await self._auth_headers()
        headers.update(
            {
                "content-type": "application/grpc",
                "te": "trailers",
                "vin": self.vin,
            }
        )
        loop = asyncio.get_running_loop()

        def _do() -> bytes | None:
            with httpx.Client(http2=True, timeout=20) as cli:
                with cli.stream(
                    "POST",
                    API_BASE + "/dtlinternet.DtlInternetService/StreamLastParkedLocations",
                    headers=headers,
                    content=body,
                ) as r:
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) >= 5:
                            length = struct.unpack(">I", bytes(buf[1:5]))[0]
                            if len(buf) >= 5 + length:
                                return bytes(buf[5 : 5 + length])
                    return None

        try:
            frame = await loop.run_in_executor(None, _do)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("parked-location stream failed: %s", exc)
            return None
        if not frame:
            return None

        # Decode {1: vin, 2: {1: lon double, 2: lat double, 3: {1: secs, 2: nanos}}}
        return _parse_parked_location_frame(frame)

    async def get_software_info(self) -> dict[str, Any] | None:
        """Read OTA software info (server-streaming gRPC, first frame only).

        Returns a dict with at least {version, title, ref_code, ...} or None.
        """
        payload = self._pb_len_delim(1, self.vin.encode("ascii")) + self._pb_len_delim(
            2, b"en-GB"
        )
        body = self._grpc_frame(payload)
        headers = await self._auth_headers()
        headers.update(
            {
                "content-type": "application/grpc",
                "te": "trailers",
                "vin": self.vin,
            }
        )
        loop = asyncio.get_running_loop()

        def _do() -> bytes | None:
            with httpx.Client(http2=True, timeout=20) as cli:
                with cli.stream(
                    "POST",
                    API_BASE
                    + "/ota_mobcache.OtaDiscoveryService/GetSoftwareInfo",
                    headers=headers,
                    content=body,
                ) as r:
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) >= 5:
                            length = struct.unpack(">I", bytes(buf[1:5]))[0]
                            if len(buf) >= 5 + length:
                                return bytes(buf[5 : 5 + length])
                    return None

        try:
            frame = await loop.run_in_executor(None, _do)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("software-info stream failed: %s", exc)
            return None
        if not frame:
            return None
        parsed = _parse_software_info_frame(frame)
        _LOGGER.debug(
            "GetSoftwareInfo frame len=%d, parsed=%s", len(frame), parsed
        )
        return parsed

    async def get_weather_report(self) -> dict[str, Any] | None:
        """Read Volvo's weather report for the car's location.

        Returns {temperature_c, timestamp_ms, source} or None.
        Volvo serves this to the iOS app to show outside temperature.
        """
        payload = self._pb_len_delim(1, self.vin.encode("ascii"))
        body = self._grpc_frame(payload)
        headers = await self._auth_headers()
        headers.update(
            {
                "content-type": "application/grpc",
                "te": "trailers",
                "vin": self.vin,
            }
        )
        loop = asyncio.get_running_loop()

        def _do() -> bytes | None:
            with httpx.Client(http2=True, timeout=15) as cli:
                with cli.stream(
                    "POST",
                    API_BASE + "/weather.WeatherService/GetWeatherReport",
                    headers=headers,
                    content=body,
                ) as r:
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) >= 5:
                            length = struct.unpack(">I", bytes(buf[1:5]))[0]
                            if len(buf) >= 5 + length:
                                return bytes(buf[5 : 5 + length])
                    return None

        try:
            frame = await loop.run_in_executor(None, _do)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("weather-report stream failed: %s", exc)
            return None
        if not frame:
            return None
        return _parse_weather_report_frame(frame)

    async def get_target_soc(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/chronos.services.v1.TargetSocService/GetTargetSoc",
            self._chronos_body(),
        )

    async def get_amp_limit(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/chronos.services.v1.AmpLimitService/GetAmpLimit",
            self._chronos_body(),
        )

    async def lock(self) -> dict[str, Any]:
        return await self._vin_only_grpc("/invocation.InvocationService/Lock")

    async def unlock(self) -> dict[str, Any]:
        return await self._vin_only_grpc("/invocation.InvocationService/Unlock")

    async def unlock_tailgate(self) -> dict[str, Any]:
        """Unlock the tailgate only (target=1)."""
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        outer = self._pb_len_delim(1, inner) + b"\x10\x01"  # field 2 varint = 1
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/Unlock", body
        )

    async def climatization_start(
        self, temperature: float | None = None, seats: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Start parking climatization (default ~5min runtime).

        ``temperature`` and ``seats`` (see climate.py) are only sent for cars that support those settings.
        """
        body = self._grpc_frame(climate.start_message(self.vin, temperature, seats))
        return await self._grpc_post(
            "/invocation.InvocationService/ClimatizationStart", body
        )

    async def climatization_stop(self) -> dict[str, Any]:
        return await self._vin_only_grpc(
            "/invocation.InvocationService/ClimatizationStop"
        )

    async def precleaning_start(self) -> dict[str, Any]:
        """Start air purification."""
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        outer = self._pb_len_delim(1, inner) + b"\x10\x01"  # f2 varint 1
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/PreCleaning", body
        )

    async def precleaning_stop(self) -> dict[str, Any]:
        return await self._vin_only_grpc(
            "/invocation.InvocationService/PreCleaning"
        )

    async def flash(self) -> dict[str, Any]:
        """Flash the side markers (HonkFlash mode=2)."""
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        outer = self._pb_len_delim(1, inner) + b"\x10\x02"  # f2 varint = 2
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/HonkFlash", body
        )

    async def honk(self) -> dict[str, Any]:
        """Sound the horn only (HonkFlash mode=1)."""
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        outer = self._pb_len_delim(1, inner) + b"\x10\x01"  # f2 varint = 1
        return await self._grpc_post("/invocation.InvocationService/HonkFlash", self._grpc_frame(outer))

    async def window_control(self, open: bool) -> dict[str, Any]:
        """Control windows (open or close).
        
        Sends a WindowControl command to the vehicle.
        """
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        mode = b"\x10\x01" if open else b"\x10\x02"  # f2 enum: 1=OPEN_ALL, 2=CLOSE_ALL
        outer = self._pb_len_delim(1, inner) + mode
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/WindowControl", body
        )

    async def engine_remote_start(self) -> dict[str, Any]:
        """Start engine remote start via EngineRemoteStartService."""
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        outer = self._pb_len_delim(1, inner) + b"\x10\x01"  # f2 varint = 1
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/EngineStart", body
        )

    async def reduced_alarm(self) -> dict[str, Any]:
        """Lock with reduced alarm level (CarLockRequest alarm_level=2).
        
        Sends a Lock command with ALARM_LEVEL_REDUCED (2) via CarLockRequest.
        """
        # CarLockRequest: field 1=feedback (FEEDBACK_IDLE=1), field 2=alarm_level (ALARM_LEVEL_REDUCED=2)
        # feedback=1 (varint): tag=0x08, value=0x01
        # alarm_level=2 (varint): tag=0x10, value=0x02
        car_lock_req = b"\x08\x01\x10\x02"
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        outer = self._pb_len_delim(1, inner) + self._pb_len_delim(2, car_lock_req)
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/Lock", body
        )

    async def tailgate_control(self, open: bool) -> dict[str, Any]:
        """Control the tailgate (open or close).
        
        Sends a TailgateControl command to the vehicle.
        """
        inner = self._pb_len_delim(1, self.vin.encode("ascii"))
        mode = b"\x10\x01" if open else b"\x10\x02"  # f2 enum: 1=OPEN_TAILGATE, 2=CLOSE_TAILGATE
        outer = self._pb_len_delim(1, inner) + mode
        body = self._grpc_frame(outer)
        return await self._grpc_post(
            "/invocation.InvocationService/TailgateControl", body
        )

    async def charge_start(self) -> dict[str, Any]:
        """Start charging the vehicle (ChargeNowService/StartOverrideChargeTimer)."""
        return await self._vin_only_grpc(
            "/chronos.services.v1.ChargeNowService/StartOverrideChargeTimer"
        )

    async def charge_stop(self) -> dict[str, Any]:
        """Stop charging the vehicle (ChargeNowService/StopOverrideChargeTimer)."""
        return await self._vin_only_grpc(
            "/chronos.services.v1.ChargeNowService/StopOverrideChargeTimer"
        )

    async def honk_and_flash(self) -> dict[str, Any]:
        """Honk horn and flash side markers (HonkFlash, no mode field = default)."""
        return await self._vin_only_grpc(
            "/invocation.InvocationService/HonkFlash"
        )

    async def set_amp_limit(self, amps: int) -> dict[str, Any]:
        """Set the AC charge current limit (6–32 A)."""
        if not 1 <= amps <= 100:
            raise ValueError(f"amps out of range: {amps}")
        try:
            return await self._chronos_set_int_grpc(
                "/chronos.services.v1.AmpLimitService/SetAmpLimit", amps
            )
        finally:
            self._tiers.invalidate("amp_limit")

    async def set_target_soc(self, level: int) -> dict[str, Any]:
        """Set the charge target state-of-charge (1-100%).

        Always writes with settingType=CUSTOM. The car only honours a manually
        entered target level while the setting type is CUSTOM (DAILY/LONG_TRIP
        targets come from the car's own schedule); see the field-2/field-3
        layout documented in unofficial-polestar-api's target_soc.py
        (kildahldev/unofficial-polestar-api).
        """
        if not 1 <= level <= 100:
            raise ValueError(f"target SoC out of range: {level}")
        try:
            return await self._chronos_set_target_soc_grpc(
                "/chronos.services.v1.TargetSocService/SetTargetSoc", level
            )
        finally:
            self._tiers.invalidate("target_soc")

    async def set_global_charge_timer(
        self,
        *,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        enabled: bool,
    ) -> dict[str, Any]:
        """Set the daily charging window."""
        def _varint(v: int) -> bytes:
            out = bytearray()
            while True:
                b = v & 0x7F
                v >>= 7
                if v:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        def _time_msg(h: int, m: int) -> bytes:
            # Protobuf default: omit zero values
            out = b""
            if h:
                out += b"\x08" + _varint(h)  # field 1 varint
            if m:
                out += b"\x10" + _varint(m)  # field 2 varint
            return out

        request_id = str(uuid.uuid4()).lower()
        meta = self._pb_len_delim(4, b"\x08" + _varint(600))
        req = (
            self._pb_len_delim(1, request_id.encode("ascii"))
            + self._pb_len_delim(2, self.vin.encode("ascii"))
            + self._pb_len_delim(3, b"mapp")
            + meta
        )
        start_pb = self._pb_len_delim(1, _time_msg(start_hour, start_minute))
        end_pb = self._pb_len_delim(2, _time_msg(end_hour, end_minute))
        enabled_pb = b"\x18" + _varint(1 if enabled else 0)  # field 3 varint
        timer = start_pb + end_pb + enabled_pb
        outer = self._pb_len_delim(1, req) + self._pb_len_delim(2, timer)
        body = self._grpc_frame(outer)
        try:
            return await self._grpc_post(
                "/chronos.services.v2.GlobalChargeTimerService/SetGlobalChargeTimer", body
            )
        finally:
            self._tiers.invalidate("global_charge_timer")

    async def get_global_charge_timer(self) -> dict[str, Any]:
        return await self._ndjson_first_frame(
            "/chronos.services.v2.GlobalChargeTimerService/GetGlobalChargeTimerStream",
            self._chronos_body(),
        )

    # ----- driving journal (trips) -----

    def expect_new_trip(self) -> None:
        """A trip just ended: look for it in the journal every 30 s for a while, not every 15 minutes."""
        self._trip_expect_until = time.monotonic() + 1200

    async def get_trips(
        self, *, quantity: int | None = None, start: datetime | None = None, stop: datetime | None = None,
        with_coordinates: bool = False,
    ) -> list[dict[str, Any]]:
        """Trips from the journal, newest first. Raises VolvoApiError when the journal is not available."""
        params: dict[str, Any] = {}
        if quantity:
            params["quantity"] = quantity
        if start:
            params["startDate"] = journal.iso_utc(start)
        if stop:
            params["stopDate"] = journal.iso_utc(stop)
        path = journal.TRIPS_PATH.format(vin=self.vin) + (f"?{urlencode(params)}" if params else "")
        return journal.parse_trips(await self._json_get(path, with_vin=True), with_coordinates=with_coordinates)

    async def get_last_trip(self) -> dict[str, Any] | None:
        """The newest trip, cached: every 15 minutes, or every 30 s while a just-ended trip is awaited."""
        if not self.supports_journal:
            return None
        now = time.monotonic()
        wait = 30.0 if now < self._trip_expect_until else 900.0
        if self._last_trip_at and now - self._last_trip_at < wait:
            return self._last_trip
        trips = await self.get_trips(quantity=1)
        trip = trips[0] if trips else None
        if trip and self._last_trip and trip["id"] != self._last_trip["id"]:
            self._trip_expect_until = 0.0  # the new trip arrived
        self._last_trip, self._last_trip_at = trip or self._last_trip, now
        return self._last_trip

    async def get_trips_month(self) -> dict[str, Any] | None:
        """Totals since the start of this month, cached 15 minutes (and re-read when a new trip arrives or the month turns)."""
        if not self.supports_journal:
            return None
        now = self.local_now()
        start = journal.month_start(now)
        key = (start, (self._last_trip or {}).get("id"))
        mono = time.monotonic()
        if self._month is not None and key == self._month_key and mono - self._month_at < 900:
            return self._month
        summary = await self.get_trips_summary(start, now)
        summary["month_start"] = start.isoformat()
        self._month, self._month_key, self._month_at = summary, key, mono
        return summary

    async def get_trips_summary(self, start: datetime, stop: datetime) -> dict[str, Any]:
        """Totals (count, distance, time, energy, cost) over a time range."""
        body = journal.summary_request(self.vin, int(start.timestamp() * 1000), int(stop.timestamp() * 1000))
        res = await self._grpc_post(journal.SUMMARY_PATH, body)
        if not res.get("ok") or not res.get("frames"):
            raise VolvoApiError(f"trip summary failed: {res.get('grpc_message') or res.get('error') or res.get('http_status')}")
        return journal.parse_summary(bytes.fromhex(res["frames"][0]))

    async def snapshot(self) -> dict[str, Any]:
        """Pull everything in parallel-ish (sequential is fine; ~1s total)."""
        out: dict[str, Any] = {}
        readers = {
            "battery": self.get_battery,
            "odometer": self.get_odometer,
            "exterior": self.get_exterior,
            "health": self.get_health,
            "availability": self.get_availability,
            "parking_climatization": self.get_parking_climatization,
            "pre_cleaning": self.get_pre_cleaning,
            "location": self.get_last_parked_location,
            "target_soc": self.get_target_soc,
            "amp_limit": self.get_amp_limit,
            "global_charge_timer": self.get_global_charge_timer,
            "software_info": self.get_software_info,
            "weather": self.get_weather_report,
            "last_trip": self.get_last_trip,
            "trips_month": self.get_trips_month,
        }
        # Slow readers are served from cache until their tier is due (see polltiers.py).
        due = {n: fn for n, fn in readers.items() if not (self._tiers.is_slow(n) and self._tiers.cached(n)[0])}
        # Run concurrently — gateway handles parallel requests fine
        fetched = dict(zip(due.keys(), await asyncio.gather(*(fn() for fn in due.values()), return_exceptions=True)))
        results = []
        for name in readers:
            if name in fetched:
                res = fetched[name]
                if self._tiers.is_slow(name):
                    if res is not None and not isinstance(res, Exception):
                        self._tiers.put(name, res)
                    elif self._tiers.has(name) and not isinstance(res, VolvoAuthError):
                        res = self._tiers.cached(name)[1]  # keep the stale value rather than an error
            else:
                res = self._tiers.cached(name)[1]
            results.append(res)
        # A dead/revoked refresh token fails identically for every reader
        # (each independently calls _ensure_access_token() -> _refresh()).
        # Without this check that gets buried as 13 separate per-field
        # "_error" strings and snapshot() returns normally — the
        # coordinator never sees a failure, so it never distinguishes a
        # real auth failure from a one-off flaky endpoint and never
        # prompts for reauth. If literally every reader failed with
        # VolvoAuthError, propagate one instead of swallowing it.
        auth_errors = [r for r in results if isinstance(r, VolvoAuthError)]
        if auth_errors and len(auth_errors) == len(results):
            raise auth_errors[0]

        for name, res in zip(readers.keys(), results):
            if isinstance(res, Exception):
                out[name] = {"_error": f"{type(res).__name__}: {res}"}
            else:
                out[name] = res
        return out
