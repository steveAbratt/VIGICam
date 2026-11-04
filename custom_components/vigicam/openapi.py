"""TP-Link VIGI IPC OpenAPI client (port 20443).

Auth flow (two-step SHA-256, not the RSA+MD5 flow used on port 443):
  POST https://<ip>:20443/        {"method":"doAuth","params":null}
  → realm, nonce, uri, method
  a1       = SHA256(user:realm:password)
  a2       = SHA256(method:uri)
  response = SHA256(a1:nonce:a2)
  POST https://<ip>:20443/        {"method":"doAuth","params":{"nonce":...,"response":...}}
  → stok

Each request (including each auth step) requires a fresh TCP connection — the
camera closes the connection after every single response.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

OPENAPI_PORT = 20443
_STOK_TTL = 25 * 60  # 25 min (spec is 30 min — be conservative)
_TIMEOUT = aiohttp.ClientTimeout(total=10)


# Detection-switch getters for the types the event listener handles, with the names the
# entities use.
MSG_PUSH_GETTERS: tuple[tuple[str, str], ...] = (
    ("getVehicleDetectionSwitch",      "Vehicle"),
    ("getAudioAnomalyDetectionSwitch", "Audio Anomaly"),
    ("getLoiterDetectionSwitch",       "Loitering"),
    ("getSceneChangeDetectionSwitch",  "Scene Change"),
    ("getAreaEntryDetectionSwitch",    "Area Entry"),
    ("getAreaLeaveDetectionSwitch",    "Area Exit"),
    ("getDropAndTakeDetectionSwitch",  "Object Left or Taken"),
)


def unwrap_switch(result: dict) -> dict:
    """Return the settings object from a get*DetectionSwitch reply.

    Some firmware returns the fields at the top level, others nest them one level deep
    under a key whose name varies by method, which is why this searches rather than
    indexing a known key.
    """
    if "enabled" in result:
        return result
    for value in result.values():
        if isinstance(value, dict) and "enabled" in value:
            return value
    return result


def push_off(result: dict) -> bool:
    """True if a get*DetectionSwitch reply has the detection on but msg_push_enabled off.

    Anything else, including a reply shape we don't recognise, is False: a false alarm
    would send the user to change a setting for nothing.
    """
    switch = unwrap_switch(result)
    if "enabled" not in switch:
        _LOGGER.debug("Detection switch reply not recognised. Keys seen: %s", sorted(switch))
    return switch.get("enabled") == "on" and switch.get("msg_push_enabled") == "off"


async def read_push_off(api: "VIGIOpenAPI") -> list[str] | None:
    """Labels of the enabled detection types whose Push Notifications are off. Read-only.

    None if the camera couldn't be read, so the caller can try again later. An auth
    failure is raised rather than retried per type: seven failed logins in a row is how
    an account gets locked.
    """
    off = []
    for getter, label in MSG_PUSH_GETTERS:
        try:
            result = await api.call(getter, raise_on_error=False)
        except VIGIOpenAPIAuthError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError, VIGIOpenAPIError) as exc:
            _LOGGER.debug("OpenAPI: could not read %s on %s: %r", getter, api.host, exc)
            return None
        if not isinstance(result, dict) or api.err_code(result) != 0:
            continue  # empty reply, or not supported on this model (-10030 etc.)
        if push_off(result):
            off.append(label)
    return off


class VIGIOpenAPIError(Exception):
    pass


class VIGIOpenAPIAuthError(VIGIOpenAPIError):
    pass


class VIGIOpenAPICallError(VIGIOpenAPIError):
    """A method returned a non-zero errCode — the camera rejected the call."""

    def __init__(self, method_name: str, err_code: Any) -> None:
        super().__init__(f"{method_name} failed (errCode={err_code})")
        self.method_name = method_name
        self.err_code = err_code


class VIGIOpenAPI:
    """Async client for the TP-Link VIGI IPC OpenAPI on port 20443."""

    def __init__(self, ip: str, username: str, password: str) -> None:
        self._ip = ip
        self._username = username
        self._password = password
        self._base = f"https://{ip}:{OPENAPI_PORT}"
        self._stok: str | None = None
        self._stok_expiry: float = 0.0

        # Non-blocking SSL context (avoids load_default_certs() in HA event loop)
        self._ssl: ssl.SSLContext = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE

    def _session(self) -> aiohttp.ClientSession:
        """Fresh session per request — camera closes connection after each response."""
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=self._ssl, force_close=True),
            timeout=_TIMEOUT,
        )

    @staticmethod
    def _sha256(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()

    async def _do_auth(self) -> str:
        """Two-step doAuth. Each step uses a separate TCP connection."""
        async with self._session() as s:
            r1 = await s.post(self._base, json={"method": "doAuth", "params": None})
            d1 = await r1.json(content_type=None)

        auth = d1.get("authenticate", {})
        realm = auth.get("realm", "TP-LINK IP-Camera")
        nonce = auth.get("nonce", "")
        uri = auth.get("uri", "doAuth")
        meth = auth.get("method", "POST")

        a1 = self._sha256(f"{self._username}:{realm}:{self._password}")
        a2 = self._sha256(f"{meth}:{uri}")
        response = self._sha256(f"{a1}:{nonce}:{a2}")

        async with self._session() as s:
            r2 = await s.post(self._base, json={
                "method": "doAuth",
                "params": {"nonce": nonce, "response": response},
            })
            d2 = await r2.json(content_type=None)

        stok = d2.get("stok")
        if not stok:
            err = d2.get("errCode", d2.get("error_code", "unknown"))
            raise VIGIOpenAPIAuthError(f"doAuth failed (errCode={err})")
        return stok

    @property
    def host(self) -> str:
        return self._ip

    @property
    def ssl_context(self) -> ssl.SSLContext:
        return self._ssl

    async def authed_url(self) -> str:
        """The base URL with a valid stok, for callers that open their own connection."""
        return f"{self._base}/stok={await self._ensure_stok()}"

    async def _ensure_stok(self) -> str:
        if self._stok and time.monotonic() < self._stok_expiry:
            return self._stok
        self._stok = await self._do_auth()
        self._stok_expiry = time.monotonic() + _STOK_TTL
        _LOGGER.debug("OpenAPI: new stok for %s (expires in %ds)", self._ip, _STOK_TTL)
        return self._stok

    async def call(
        self,
        method_name: str,
        params: dict[str, Any] | None = None,
        *,
        raise_on_error: bool = True,
    ) -> dict:
        """Call an OpenAPI method. Re-authenticates automatically on -10002.

        Raises VIGIOpenAPICallError when the camera replies with a non-zero errCode.
        A rejected call used to return normally, so a write that the firmware refused
        looked like a success — the entity showed the new state and then sprang back on
        the next poll. Pass raise_on_error=False to inspect the reply yourself.
        """
        stok = await self._ensure_stok()
        result = await self._call_raw(stok, method_name, params)

        if self._err_code(result) == -10002:
            _LOGGER.debug("OpenAPI: stok expired on %s, re-authenticating", self._ip)
            self._stok = None
            self._stok_expiry = 0.0
            stok = await self._ensure_stok()
            result = await self._call_raw(stok, method_name, params)

        err = self._err_code(result)
        if err != 0:
            _LOGGER.debug(
                "OpenAPI: %s rejected by %s (errCode=%s, params=%s)",
                method_name, self._ip, err, params,
            )
            if raise_on_error:
                raise VIGIOpenAPICallError(method_name, err)

        return result

    @staticmethod
    def err_code(result: dict) -> Any:
        """Public alias — callers that inspect errCode themselves must use this.

        Two call sites pass raise_on_error=False because a rejection there means "none"
        or "unsupported" rather than "broken". They previously read result["errCode"]
        directly, which treats a reply carrying no status field as a FAILURE — the
        opposite of the convention below, and enough to blind the msg_push check on
        firmware whose getters omit the field.
        """
        return VIGIOpenAPI._err_code(result)

    @staticmethod
    def _err_code(result: dict) -> Any:
        """Normalise the error field. Firmware uses errCode; some replies use error_code.

        A reply that carries neither is treated as success — several getters return their
        payload with no status field at all.
        """
        for key in ("errCode", "error_code"):
            if key in result:
                return result[key]
        return 0

    async def _call_raw(
        self, stok: str, method_name: str, params: dict[str, Any] | None
    ) -> dict:
        url = f"{self._base}/stok={stok}"
        body: dict[str, Any] = {"method": method_name}
        if params is not None:
            body["params"] = params
        async with self._session() as s:
            r = await s.post(url, json=body)
            return await r.json(content_type=None)


async def try_openapi(ip: str, username: str, password: str) -> bool:
    """Return True if OpenAPI is reachable on port 20443 and auth succeeds."""
    client = VIGIOpenAPI(ip, username, password)
    try:
        await asyncio.wait_for(client._do_auth(), timeout=8)
        return True
    except Exception as exc:
        _LOGGER.debug("OpenAPI probe failed for %s: %s", ip, exc)
        return False
