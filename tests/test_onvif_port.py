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


async def _status_server(port: int, status: int, body: bytes) -> web.AppRunner:
    async def handle(_request):
        return web.Response(status=status, body=body)

    app = web.Application()
    app.router.add_post("/onvif/service", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


def test_any_reply_on_the_first_port_keeps_it():
    # A camera that has always worked on 80 must not move because 80 sent a fault or a
    # 401 instead of the exact reply, even if it also answers on 2020.
    async def scenario():
        first, second = _free_port(), _free_port()
        r1 = await _status_server(first, 401, b"Unauthorized")
        r2 = await _onvif_server(second)
        try:
            return first, await discover_onvif_port("127.0.0.1", ports=(first, second), timeout=2)
        finally:
            await r1.cleanup()
            await r2.cleanup()

    first, port = asyncio.run(scenario())
    assert port == first


def test_undecodable_reply_does_not_raise():
    async def scenario():
        bad, odd = _free_port(), _free_port()
        dropper = await _dropping_server(bad)
        runner = await _status_server(odd, 200, b"\xff\xfe\x00 not utf-8")
        try:
            return await discover_onvif_port("127.0.0.1", ports=(bad, odd), timeout=2)
        finally:
            dropper.close()
            await runner.cleanup()

    assert asyncio.run(scenario()) == DEFAULT_ONVIF_PORT


def test_nothing_answering_keeps_the_port_found_last_time():
    ports = (_free_port(), _free_port())
    assert asyncio.run(
        discover_onvif_port("127.0.0.1", fallback=2020, ports=ports, timeout=1)
    ) == 2020
