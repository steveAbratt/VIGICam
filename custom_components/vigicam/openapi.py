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


class VIGIOpenAPIError(Exception):
    pass


class VIGIOpenAPIAuthError(VIGIOpenAPIError):
    pass


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

    async def _ensure_stok(self) -> str:
        if self._stok and time.monotonic() < self._stok_expiry:
            return self._stok
        self._stok = await self._do_auth()
        self._stok_expiry = time.monotonic() + _STOK_TTL
        _LOGGER.debug("OpenAPI: new stok for %s (expires in %ds)", self._ip, _STOK_TTL)
        return self._stok

    async def call(self, method_name: str, params: dict[str, Any] | None = None) -> dict:
        """Call an OpenAPI method. Re-authenticates automatically on -10002."""
        stok = await self._ensure_stok()
        result = await self._call_raw(stok, method_name, params)

        if result.get("errCode") == -10002:
            _LOGGER.debug("OpenAPI: stok expired on %s, re-authenticating", self._ip)
            self._stok = None
            self._stok_expiry = 0.0
            stok = await self._ensure_stok()
            result = await self._call_raw(stok, method_name, params)

        return result

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
