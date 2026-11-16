"""TP-Link VIGI NVR OpenAPI client (HTTPS, port 20443).

Written against TP-Link's "VIGI NVR OpenAPI Document" v1.4. The NVR API is REST-style,
unlike the camera's JSON-RPC one in openapi.py, but the login hash is the same:

  GET /openapi/token                  → 401, WWW-Authenticate: Digest realm, nonce, url
  a1       = SHA256(user:realm:password)
  a2       = SHA256(GET:url)            (TP-Link's guide: the method is always GET)
  response = SHA256(a1:nonce:a2)
  GET /openapi/token + Authorization: Digest username, realm, nonce, response
  → {"access_token", "refresh_token", "expires_in", "token_type": "bearer"}

Every other call carries "Authorization: Bearer <access_token>". String values in JSON
bodies, both ways, are percent-encoded; the token itself arrives encoded too.

Events are not polled: the NVR POSTs them to an "event server" (see nvr_events.py).

This module has no Home Assistant imports so the tests can load it on its own.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
import time
import urllib.parse
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

NVR_OPENAPI_PORT = 20443
NVR_RTSP_PORT = 554
_TIMEOUT = aiohttp.ClientTimeout(total=15)
# Refresh this long before the NVR's stated expiry (1800 s in the doc's examples).
_TOKEN_MARGIN = 120

# error_code values that mean "the token is no longer good": log in again and retry once.
_SESSION_ERRORS = frozenset({-40402, -40403, -40404, -40406})
# error_code values that mean the credentials themselves are wrong or the account is locked.
# Retrying these is how an account gets locked, so they are raised straight away.
_AUTH_ERRORS = frozenset({-40401, -40405, -40407, -40408})

# Characters left as-is when percent-encoding outgoing string values. The doc's own
# examples send paths and IP addresses readable, so "/", ":" and "." stay literal.
_ENCODE_SAFE = "/:@-._~"


class VIGINVRError(Exception):
    """Any failure talking to the NVR."""


class VIGINVRAuthError(VIGINVRError):
    """The NVR rejected the username or password, or the account is locked."""


class VIGINVRCallError(VIGINVRError):
    """The NVR answered a call with a non-zero error_code."""

    def __init__(self, path: str, error_code: Any) -> None:
        super().__init__(f"{path} failed (error_code={error_code})")
        self.path = path
        self.error_code = error_code


# ── Encoding helpers ───────────────────────────────────────────────────────────

def decode_values(obj: Any) -> Any:
    """Percent-decode every string value in a reply, recursively. Keys are left alone."""
    if isinstance(obj, str):
        return urllib.parse.unquote(obj)
    if isinstance(obj, list):
        return [decode_values(v) for v in obj]
    if isinstance(obj, dict):
        return {k: decode_values(v) for k, v in obj.items()}
    return obj


def encode_values(obj: Any) -> Any:
    """Percent-encode every string value in a request body, recursively."""
    if isinstance(obj, str):
        return urllib.parse.quote(obj, safe=_ENCODE_SAFE)
    if isinstance(obj, list):
        return [encode_values(v) for v in obj]
    if isinstance(obj, dict):
        return {k: encode_values(v) for k, v in obj.items()}
    return obj


def parse_digest_challenge(header: str) -> dict[str, str]:
    """Parse 'Digest realm="x", nonce="y", algorithm=SHA-256' into a dict (lower-case keys)."""
    _, _, rest = header.partition(" ")
    out: dict[str, str] = {}
    for match in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|[^,\s]+)', rest):
        key = match.group(1).lower()
        out[key] = match.group(3) if match.group(3) is not None else match.group(2)
    return out


def digest_response(
    username: str, password: str, realm: str, nonce: str, uri: str, method: str = "GET"
) -> str:
    """The response hash TP-Link's NVR expects (SHA-256 flavour of RFC 2617 without qop)."""
    def sha(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()

    a1 = sha(f"{username}:{realm}:{password}")
    a2 = sha(f"{method}:{uri}")
    return sha(f"{a1}:{nonce}:{a2}")


def error_code(reply: Any) -> Any:
    """The reply's error_code, or 0 if it has none (some getters return bare payloads)."""
    if isinstance(reply, dict):
        for key in ("error_code", "errCode"):
            if key in reply:
                try:
                    return int(reply[key])
                except (TypeError, ValueError):
                    return reply[key]
    return 0


# ── Client ─────────────────────────────────────────────────────────────────────

class VIGINVR:
    """Async client for one VIGI NVR's OpenAPI."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = NVR_OPENAPI_PORT,
        rtsp_port: int = NVR_RTSP_PORT,
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._rtsp_port = rtsp_port
        self._base = f"https://{host}:{port}"
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expiry = 0.0
        self._session: aiohttp.ClientSession | None = None

        # The NVR serves a self-signed certificate. Built here rather than with
        # ssl.create_default_context() so no CA bundle is loaded inside HA's event loop.
        self._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def host(self) -> str:
        return self._host

    @property
    def username(self) -> str:
        return self._username

    def rtsp_url(self, channel: int, stream: int = 2) -> str:
        """Live view of one channel through the NVR. stream 1 = main, 2 = sub."""
        user = urllib.parse.quote(self._username, safe="")
        pw = urllib.parse.quote(self._password, safe="")
        return (
            f"rtsp://{user}:{pw}@{self._host}:{self._rtsp_port}"
            f"/live/{int(channel)}/{int(stream)}/avm"
        )

    # ── Session ───────────────────────────────────────────────────────────────

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=self._ssl, force_close=True),
                timeout=_TIMEOUT,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def login(self) -> None:
        """Full digest login. Raises VIGINVRAuthError on bad credentials."""
        session = self._get_session()
        url = f"{self._base}/openapi/token"
        async with session.get(url) as r1:
            challenge_header = r1.headers.get("WWW-Authenticate", "")
            body1 = await _read_json(r1)
        if not challenge_header.lower().startswith("digest"):
            code = error_code(body1)
            if code in _AUTH_ERRORS:
                raise VIGINVRAuthError(f"NVR refused login (error_code={code})")
            raise VIGINVRError(
                f"No digest challenge from {self._host} (HTTP {r1.status}). "
                "Is OpenAPI enabled under Settings > Network > OpenAPI?"
            )
        challenge = parse_digest_challenge(challenge_header)
        realm = challenge.get("realm", "TP-LINK NVR")
        nonce = challenge.get("nonce", "")
        uri = challenge.get("url") or challenge.get("uri") or "/openapi/token"
        response = digest_response(self._username, self._password, realm, nonce, uri)
        header = (
            f'Digest username="{self._username}", realm="{realm}", '
            f'nonce="{nonce}", response="{response}"'
        )
        if "opaque" in challenge:
            header += f', opaque="{challenge["opaque"]}"'

        async with session.get(url, headers={"Authorization": header}) as r2:
            status = r2.status
            body2 = await _read_json(r2)
        self._store_tokens(body2, status, "login")

    async def _refresh(self) -> bool:
        """Swap the refresh token for a new access token. False if that didn't work."""
        if not self._refresh_token or not self._access_token:
            return False
        session = self._get_session()
        params = {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
        try:
            async with session.get(
                f"{self._base}/openapi/token",
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            ) as r:
                status = r.status
                body = await _read_json(r)
            self._store_tokens(body, status, "refresh")
            return True
        except VIGINVRError as exc:
            _LOGGER.debug("NVR %s: token refresh failed (%s), logging in again", self._host, exc)
            return False

    def _store_tokens(self, body: Any, status: int, what: str) -> None:
        body = decode_values(body) if isinstance(body, dict) else {}
        token = body.get("access_token")
        if not token:
            code = error_code(body)
            if status == 401 or code in _AUTH_ERRORS:
                raise VIGINVRAuthError(
                    f"NVR {self._host} rejected the {what} (HTTP {status}, error_code={code})"
                )
            raise VIGINVRError(f"NVR {self._host} {what} failed (HTTP {status}, error_code={code})")
        self._access_token = token
        self._refresh_token = body.get("refresh_token") or self._refresh_token
        try:
            expires_in = int(body.get("expires_in", 1800))
        except (TypeError, ValueError):
            expires_in = 1800
        self._expiry = time.monotonic() + max(60, expires_in - _TOKEN_MARGIN)
        _LOGGER.debug("NVR %s: %s ok, token valid for %ss", self._host, what, expires_in)

    async def _ensure_token(self) -> str:
        if self._access_token and time.monotonic() < self._expiry:
            return self._access_token
        if not await self._refresh():
            await self.login()
        assert self._access_token
        return self._access_token

    def _drop_token(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._expiry = 0.0

    # ── Requests ──────────────────────────────────────────────────────────────

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        raw: bool = False,
        raise_on_error: bool = True,
    ) -> Any:
        """Call one endpoint. Logs in again once if the token was rejected.

        raw=True returns the response bytes (snapshots). Otherwise the decoded JSON.
        """
        for attempt in (1, 2):
            token = await self._ensure_token()
            session = self._get_session()
            async with session.request(
                method,
                f"{self._base}{path}",
                params=params,
                json=encode_values(body) if body is not None else None,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                status = resp.status
                ctype = resp.headers.get("Content-Type", "")
                data = await resp.read()

            if raw and status == 200 and "json" not in ctype.lower():
                return data

            try:
                reply = decode_values(json.loads(data)) if data else {}
            except ValueError:
                reply = {}
            code = error_code(reply)

            if attempt == 1 and (status == 401 or code in _SESSION_ERRORS):
                _LOGGER.debug("NVR %s: token rejected on %s, logging in again", self._host, path)
                self._drop_token()
                continue
            if status == 401 or code in _AUTH_ERRORS:
                raise VIGINVRAuthError(f"{path}: NVR rejected credentials (error_code={code})")
            if status >= 400 and not code:
                raise VIGINVRError(f"{path}: HTTP {status}")
            if code and raise_on_error:
                raise VIGINVRCallError(path, code)
            if raw:
                # Expected an image, got JSON: the channel is offline or unsupported.
                raise VIGINVRCallError(path, code or "not an image")
            return reply
        raise VIGINVRError(f"{path}: unreachable")  # pragma: no cover

    async def get(self, path: str, **params: Any) -> dict:
        return await self.request("GET", path, params=params or None)

    async def post(self, path: str, body: dict[str, Any]) -> dict:
        return await self.request("POST", path, body=body)

    # ── Endpoints ─────────────────────────────────────────────────────────────

    async def module_list(self) -> dict[str, int]:
        """{module name: version} for the API modules this firmware offers."""
        reply = await self.request("GET", "/openapi/module_list", raise_on_error=False)
        modules = reply.get("module_list") or []
        out: dict[str, int] = {}
        for item in modules:
            if isinstance(item, dict) and item.get("name"):
                try:
                    out[str(item["name"])] = int(item.get("version", 1))
                except (TypeError, ValueError):
                    out[str(item["name"])] = 1
        return out

    async def added_devices(self) -> list[dict]:
        """Cameras added to the NVR: [{id (channel), name, alias, online, ip, mac}, ...]."""
        reply = await self.get("/openapi/added_devices")
        devices = reply.get("devices") or []
        return [d for d in devices if isinstance(d, dict) and "id" in d]

    async def snapshot(self, channel: int) -> bytes:
        return await self.request(
            "GET", "/openapi/snapshot", params={"channel": int(channel)}, raw=True
        )

    async def disks(self) -> list[dict]:
        reply = await self.get("/openapi/disks")
        return [d for d in reply.get("disks") or [] if isinstance(d, dict)]

    async def get_alarm_mode(self) -> str | None:
        reply = await self.get("/openapi/alarm_device/alarm_mode", device_id=1)
        return reply.get("alarm_mode")

    async def set_alarm_mode(self, mode: str) -> None:
        await self.post("/openapi/alarm_device/alarm_mode", {"device_id": 1, "alarm_mode": mode})

    async def get_arm_status(self, channel: int) -> str | None:
        reply = await self.get("/openapi/active_defense/arm_status", channel=int(channel))
        return reply.get("arm_status")

    async def set_arm_status(self, channel: int, armed: bool) -> None:
        await self.post(
            "/openapi/active_defense/arm_status",
            {"channel": int(channel), "arm_status": "armed" if armed else "disarmed"},
        )

    async def ipc_manual_alarm(self, channel: int, start: bool = True) -> dict:
        return await self.post(
            "/openapi/alarm_output/ipc_manual_alarm",
            {"channel": int(channel), "action": "start" if start else "stop"},
        )

    async def nvr_manual_alarm(self, start: bool = True) -> dict:
        return await self.post(
            "/openapi/alarm_output/nvr_manual_alarm",
            {"device_id": 1, "action": "start" if start else "stop"},
        )

    async def set_recording(self, channel: int, enabled: bool) -> None:
        await self.post(
            "/openapi/record_control",
            {"channel": int(channel), "enable": "auto" if enabled else "off"},
        )

    async def ptz_move(self, channel: int, direction: int, speed: float = 0.5) -> None:
        await self.post(
            "/openapi/ptz/move",
            {"channel": int(channel), "direction": int(direction), "speed": f"{speed:g}"},
        )

    async def poe_status(self) -> dict:
        return await self.get("/openapi/poe/status")

    async def event_servers(self) -> list[dict]:
        reply = await self.get("/openapi/event_server")
        servers = reply.get("event_server") or []
        return [s for s in servers if isinstance(s, dict)]

    async def set_event_server(
        self, slot: int, host: str, port: int, url: str, *, https: bool, pictures: bool
    ) -> None:
        await self.post(
            "/openapi/event_server",
            {
                "id": int(slot),
                "ip_or_domain": host,
                "port": int(port),
                "url": url,
                "protocol": "HTTPS" if https else "HTTP",
                "picture_switch": "on" if pictures else "off",
            },
        )

    async def delete_event_server(self, slot: int) -> None:
        await self.post("/openapi/event_server/delete_server", {"id": int(slot)})

    async def reboot(self) -> None:
        await self.post("/openapi/systemctl", {"action": "reboot"})


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return {}


# ── Event push parsing ────────────────────────────────────────────────────────

# type 1 = channel event. sub_type → the key the binary sensors use.
CHANNEL_EVENT_KEYS: dict[int, str] = {
    2: "motion",
    3: "tamper",
    4: "line_cross",
    5: "intrusion",
    6: "area_entry",
    7: "area_exit",
    8: "loitering",
    9: "crowd",
    21: "person",
    22: "vehicle",
    38: "alarm_input",
}
# Extra sub_type values that qualify a detection: who or what crossed the line.
_TARGET_KEYS = {101: "person", 102: "vehicle"}

EVENT_TYPE_CHANNEL = 1
EVENT_TYPE_DEVICE = 2
EVENT_TYPE_STORAGE = 7

DEVICE_EXCEPTIONS: dict[int, str] = {
    1: "disk_missing",
    4: "disk_full",
    5: "device_offline",
    7: "disk_error",
    9: "poe_short_circuit",
    10: "poe_overload",
    13: "alarm_input",
    15: "ip_conflict",
}
STORAGE_EXCEPTIONS: dict[int, str] = {
    1: "raid_offline",
    2: "raid_degraded",
    5: "no_array",
    6: "array_full",
}


def _ints(values: Any) -> list[int]:
    """sub_type as a list of ints. Accepts [2], 2, "2", "4/101" and ["4", "101"]."""
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    out: list[int] = []
    for v in values:
        for part in re.split(r"[/,\s]+", str(v)):
            if part.strip().lstrip("-").isdigit():
                out.append(int(part))
    return out


def extract_event_json(body: bytes, content_type: str = "") -> dict | None:
    """Pull the 'event' JSON part out of the NVR's multipart push.

    The doc disagrees with itself about the boundary string, so this reads it from the
    Content-Type header when there is one, and otherwise just finds the first JSON object
    in the body. A plain JSON body (no multipart) works too.
    """
    text = body.decode("utf-8", errors="replace")
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    candidates: list[str] = []
    if match:
        boundary = match.group(1).strip()
        for part in text.split(f"--{boundary}"):
            if '"messages"' in part or 'name="event"' in part:
                candidates.append(part)
    candidates.append(text)

    decoder = json.JSONDecoder()
    for chunk in candidates:
        start = chunk.find("{")
        while start != -1:
            try:
                obj, _ = decoder.raw_decode(chunk, start)
            except ValueError:
                start = chunk.find("{", start + 1)
                continue
            if isinstance(obj, dict):
                return decode_values(obj)
            break
    return None


def parse_event(event: dict) -> list[dict]:
    """Flatten one push into a list of normalised messages.

    Each item: {"type", "sub_types", "channel", "keys", "exception", "localtime", "raw"}.
    "keys" holds the channel-event names the binary sensors listen for (a line crossing
    by a person gives ["line_cross", "person"]); "exception" names a device or storage
    problem. Unknown codes keep their raw numbers so they can still be fired as events.
    """
    out: list[dict] = []
    for msg in event.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        try:
            mtype = int(msg.get("type"))
        except (TypeError, ValueError):
            continue
        subs = _ints(msg.get("sub_type"))
        channel: int | None
        try:
            channel = int(msg["channel"]) if msg.get("channel") not in (None, "") else None
        except (TypeError, ValueError):
            channel = None

        keys: list[str] = []
        exception: str | None = None
        if mtype == EVENT_TYPE_CHANNEL:
            for sub in subs:
                key = CHANNEL_EVENT_KEYS.get(sub) or _TARGET_KEYS.get(sub)
                if key and key not in keys:
                    keys.append(key)
        elif mtype == EVENT_TYPE_DEVICE and subs:
            exception = DEVICE_EXCEPTIONS.get(subs[0], f"device_{subs[0]}")
        elif mtype == EVENT_TYPE_STORAGE and subs:
            exception = STORAGE_EXCEPTIONS.get(subs[0], f"storage_{subs[0]}")

        out.append({
            "type": mtype,
            "sub_types": subs,
            "channel": channel,
            "keys": keys,
            "exception": exception,
            "localtime": msg.get("localtime"),
            "raw": msg,
        })
    return out
