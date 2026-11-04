"""Find the port a camera serves ONVIF on.

Most VIGI firmware answers ONVIF on port 80. Older firmware (seen on a VIGI C340, 3.0.0
Build 231229) closes the connection on port 80 and serves ONVIF on 2020 instead, so every
ONVIF subscribe failed with "Server disconnected" and no detection event ever arrived.
TP-Link's own guide for third-party software lists both ports.

Kept free of Home Assistant imports so the tests can load it on their own.
"""
from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

ONVIF_PORTS: tuple[int, ...] = (80, 2020)
DEFAULT_ONVIF_PORT = ONVIF_PORTS[0]

# GetSystemDateAndTime needs no authentication in ONVIF, so this probes the port
# only, whatever the credentials are.
_PROBE_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>'
    '<GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl"/>'
    "</s:Body></s:Envelope>"
)


def onvif_service_url(ip: str, port: int) -> str:
    return f"http://{ip}:{port}/onvif/service"


async def discover_onvif_port(
    ip: str,
    fallback: int = DEFAULT_ONVIF_PORT,
    ports: tuple[int, ...] = ONVIF_PORTS,
    timeout: float = 3.0,
) -> int:
    """The port the camera serves ONVIF on: 80, or 2020 on older firmware.

    Port 80 wins if it answers anything at all, so a camera that has always worked on 80
    never moves because of a slow reply or a SOAP fault. 2020 is used only when 80 drops or
    refuses the connection and 2020 really answers ONVIF. If neither can be reached right
    now (camera still booting), *fallback* is used: the port found last time.
    """
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True),
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as session:
        if await _answers(session, ip, ports[0]) is not None:
            return ports[0]
        for port in ports[1:]:
            body = await _answers(session, ip, port)
            if body is not None and b"GetSystemDateAndTimeResponse" in body:
                return port
    _LOGGER.debug("No ONVIF port answered on %s, using %s", ip, fallback)
    return fallback


async def _answers(session: aiohttp.ClientSession, ip: str, port: int) -> bytes | None:
    """The body of the camera's reply on *port*, or None if it gave no HTTP reply."""
    try:
        async with session.post(
            onvif_service_url(ip, port),
            data=_PROBE_BODY.encode(),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        ) as resp:
            return await resp.read()
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        _LOGGER.debug("ONVIF probe %s:%s failed: %s", ip, port, exc)
        return None
