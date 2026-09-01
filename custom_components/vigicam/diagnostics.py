"""Diagnostics for VIGICam.

Model differences are the main source of bugs here — night-vision fields, recording event
types and alarm behaviour have all varied between models — and each one has cost a round of
"please open DevTools on your camera and paste the JSON". This gives a reporter one button
instead: Settings → Devices & Services → VIGICam → ⋮ → Download diagnostics.

Everything the camera reports is included so model differences are visible; anything that
identifies the camera, its owner or the network it sits on is redacted first.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Credentials, network addresses, hardware identifiers, and the user's own naming —
# `device_alias` in particular is whatever the owner called the camera, which is usually
# a room or a place.
TO_REDACT = {
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    "barcode",
    "dev_id",
    "device_alias",
    "device_name",
    "camera_name",
    "fw_cur_id",
    "hw_id",
    "oem_id",
    "imei",
    "mac",
    "mac_address",
    "sn",
    "serial",
    "serial_number",
    "device_id",
    "ip",
    "ipaddr",
    "ip_address",
    "gateway",
    "netmask",
    "dns",
    "ssid",
    "hostname",
    "latitude",
    "longitude",
}


def _clean(value: Any) -> Any:
    """The camera URL-encodes several strings; decode so they are readable in a report."""
    if isinstance(value, str) and "%" in value:
        try:
            return urllib.parse.unquote(value)
        except Exception:  # noqa: BLE001
            return value
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return what is needed to diagnose a model-specific problem."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = data.get("coordinator")
    device_info = data.get("device_info") or {}

    return async_redact_data(
        {
            # The first three questions in any model-specific bug report.
            "model": _clean(device_info.get("device_model")),
            "firmware": _clean(device_info.get("sw_version")),
            "hardware": _clean(device_info.get("hw_version")),
            "capabilities": {
                "has_ptz": bool(data.get("has_ptz")),
                "has_openapi": bool(data.get("has_openapi")),
                "has_onvif_ptz": data.get("onvif_ptz") is not None,
                "has_onvif_events": data.get("onvif_events") is not None,
                "has_event_capture": bool(data.get("has_event_capture")),
                "has_sd_card": bool(data.get("has_sd_card")),
                "has_frigate": bool(data.get("has_frigate")),
                "preset_count": len(data.get("presets") or []),
            },
            "options": dict(entry.options),
            "device_info": {k: _clean(v) for k, v in device_info.items()},
            # The raw payload every entity is built from. This is the part that actually
            # differs between models, so it is what to read first.
            "coordinator_data": (coordinator.data or {}) if coordinator else {},
        },
        TO_REDACT,
    )
