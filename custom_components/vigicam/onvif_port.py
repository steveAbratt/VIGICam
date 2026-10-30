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
    ports: tuple[int, ...] = ONVIF_PORTS,
    timeout: float = 3.0,
) -> int:
    """The first port in *ports* where the camera answers an ONVIF request.

    Falls back to the default port when none does (camera unreachable right now), so a
    camera that is off at startup keeps the behaviour it always had and the subscribe
    loop's own retries sort it out.
    """
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(force_close=True),
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as session:
        for port in ports:
            try:
                async with session.post(
                    onvif_service_url(ip, port),
                    data=_PROBE_BODY.encode(),
                    headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                ) as resp:
                    body = await resp.text()
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                _LOGGER.debug("ONVIF probe %s:%s failed: %s", ip, port, exc)
                continue
            if "GetSystemDateAndTimeResponse" in body:
                return port
    _LOGGER.debug("No ONVIF port answered on %s, using %s", ip, DEFAULT_ONVIF_PORT)
    return DEFAULT_ONVIF_PORT
