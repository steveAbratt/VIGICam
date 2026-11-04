"""push_off: which detection-switch replies mean "on, but the camera won't push it"."""
import pytest

from vigicam_pure.openapi import push_off


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
