"""The ONVIF port is found by probing: 80 on most firmware, 2020 on older firmware."""
import asyncio
import socket

from aiohttp import web

from vigicam_pure.onvif_port import (
    DEFAULT_ONVIF_PORT,
    discover_onvif_port,
    onvif_service_url,
)

_SOAP_OK = (
    '<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
    "<s:Body><tds:GetSystemDateAndTimeResponse/></s:Body></s:Envelope>"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _onvif_server(port: int) -> web.AppRunner:
    async def handle(_request):
        return web.Response(text=_SOAP_OK, content_type="application/soap+xml")

    app = web.Application()
    app.router.add_post("/onvif/service", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def _dropping_server(port: int) -> asyncio.AbstractServer:
    """Accepts a connection and closes it without a reply, as port 80 does on old firmware."""

    async def handle(_reader, writer):
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", port)


def test_service_url():
    assert onvif_service_url("192.0.2.1", 2020) == "http://192.0.2.1:2020/onvif/service"


def test_picks_the_port_that_answers_after_one_that_drops_the_connection():
    async def scenario():
        bad, good = _free_port(), _free_port()
        dropper = await _dropping_server(bad)
        runner = await _onvif_server(good)
        try:
            return (bad, good), await discover_onvif_port("127.0.0.1", ports=(bad, good), timeout=2)
        finally:
            dropper.close()
            await runner.cleanup()

    (bad, good), port = asyncio.run(scenario())
    assert port == good != bad


def test_first_answering_port_wins():
    async def scenario():
        a, b = _free_port(), _free_port()
        ra, rb = await _onvif_server(a), await _onvif_server(b)
        try:
            return a, await discover_onvif_port("127.0.0.1", ports=(a, b), timeout=2)
        finally:
            await ra.cleanup()
            await rb.cleanup()

    first, port = asyncio.run(scenario())
    assert port == first


def test_falls_back_to_the_default_port_when_nothing_answers():
    ports = (_free_port(), _free_port())
    assert asyncio.run(discover_onvif_port("127.0.0.1", ports=ports, timeout=1)) == DEFAULT_ONVIF_PORT
