"""Binary sensor entities for VIGI cameras.

Real-time detection events (motion, person, vehicle, tamper) require ONVIF event
subscriptions. Polling the local API only returns configuration state (enabled/disabled),
not live detections — so those are exposed as switches instead.

ONVIF event support is planned for a future release.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    pass  # No binary sensors until ONVIF event support is added
