"""Push Notifications check: which replies count as "on, but not pushed", and how the read loop fails."""
import asyncio

import aiohttp
import pytest

from vigicam_pure.openapi import (
    VIGIOpenAPI,
    VIGIOpenAPIAuthError,
    VIGIOpenAPIError,
    push_off,
    read_push_off,
)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"enabled": "on", "msg_push_enabled": "off"}, True),
        ({"vehicle_detection": {"enabled": "on", "msg_push_enabled": "off"}}, True),
        ({"errCode": 0, "enabled": "on", "msg_push_enabled": "off"}, True),
        ({"enabled": "on", "msg_push_enabled": "on"}, False),
        # A detection the user turned off is their choice, not a problem to report.
        ({"enabled": "off", "msg_push_enabled": "off"}, False),
        ({"enabled": "on"}, False),
        # Unrecognised shapes must not raise a false alarm.
        ({"a": {"b": {"enabled": "on", "msg_push_enabled": "off"}}}, False),
        ({}, False),
    ],
)
def test_push_off(reply, expected):
    assert push_off(reply) is expected


class _FakeAPI:
    """Answers each getter from a dict: a reply, or an exception to raise."""

    host = "camera"

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    async def call(self, method, raise_on_error=True):
        self.calls.append(method)
        reply = self.replies.get(method, {"enabled": "off"})
        if isinstance(reply, Exception):
            raise reply
        return reply

    err_code = staticmethod(VIGIOpenAPI.err_code)


def _read(replies):
    api = _FakeAPI(replies)
    return asyncio.run(read_push_off(api)), api


def test_read_push_off_labels_and_skips():
    off, _ = _read({
        "getVehicleDetectionSwitch": {"enabled": "on", "msg_push_enabled": "off"},
        "getLoiterDetectionSwitch": {"errCode": -10030},
        "getSceneChangeDetectionSwitch": None,
        "getAreaLeaveDetectionSwitch": {"area": {"enabled": "on", "msg_push_enabled": "off"}},
    })
    assert off == ["Vehicle", "Area Exit"]


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ClientError(), TimeoutError(), ValueError("not json"), VIGIOpenAPIError("x")],
)
def test_read_push_off_unreachable_gives_none_and_stops(exc):
    off, api = _read({"getVehicleDetectionSwitch": exc})
    assert off is None
    assert api.calls == ["getVehicleDetectionSwitch"]


def test_read_push_off_auth_failure_raises_at_once():
    api = _FakeAPI({"getVehicleDetectionSwitch": VIGIOpenAPIAuthError("bad password")})
    with pytest.raises(VIGIOpenAPIAuthError):
        asyncio.run(read_push_off(api))
    assert len(api.calls) == 1
