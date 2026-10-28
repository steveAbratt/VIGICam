"""Camera identity: the frozen keying function and the MAC comparison."""
import pytest

from vigicam_pure.api import camera_unique_id, canonical_mac


@pytest.mark.parametrize(
    ("info", "fallback", "expected"),
    [
        ({"mac": "AA:BB:CC:DD:EE:FF"}, "192.168.1.5", "aabbccddeeff"),
        # Dashes are kept: real VIGI cameras report dashed MACs, so every installed
        # entry is keyed like this. Stripping them would orphan those entries.
        ({"mac": "AA-BB-CC-DD-EE-FF"}, "192.168.1.5", "aa-bb-cc-dd-ee-ff"),
        ({"mac": ""}, "192.168.1.5", "192.168.1.5"),
        ({}, "192.168.1.5", "192.168.1.5"),
    ],
)
def test_camera_unique_id_is_frozen(info, fallback, expected):
    assert camera_unique_id(info, fallback) == expected


@pytest.mark.parametrize(
    "value",
    [
        "F0-09-0D-1A-2B-3C",
        "f0:09:0d:1a:2b:3c",
        "F0090D1A2B3C",
        "F0%3A09%3A0D%3A1A%3A2B%3A3C",
        "f0-09-0d-1a-2b-3c\n",
    ],
)
def test_canonical_mac_accepts_every_mac_format(value):
    assert canonical_mac(value) == "f0090d1a2b3c"


@pytest.mark.parametrize(
    "value",
    [
        # Twelve decimal digits, all of them hex — the digit count is not a MAC test.
        "192.168.100.110",
        "192.168.178.123",
        "172.16.100.150",
        "192.168.1.5",
        "cam-front",
        "fe80::1",
        "f0-09:0d-1a:2b-3c",  # mixed separators
        "aa-bbccddeeff",      # partial separators
        "",
        None,
    ],
)
def test_canonical_mac_rejects_non_macs(value):
    assert canonical_mac(value) is None


def test_ip_keyed_entry_is_never_compared():
    """An entry keyed on an address must not look like a MAC to the identity check."""
    assert canonical_mac(camera_unique_id({}, "192.168.178.123")) is None
