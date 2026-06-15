"""VIGI & InSight Camera integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import CONF_VERIFY_SSL, DOMAIN
from .coordinator import VIGICoordinator
from .onvif_events import VIGIOnvifEvents

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ip = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)

    # Use HA's session helpers — they handle SSL context creation properly
    # without blocking the event loop.
    if verify_ssl:
        session = async_get_clientsession(hass)
    else:
        session = async_create_clientsession(hass, verify_ssl=False)

    camera_api = VIGICamera(ip, username, password, session=session)
    try:
        await camera_api.authenticate()
        device_info = await camera_api.get_device_info()
        presets = await camera_api.get_presets()
    except VIGIAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except VIGIError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    has_ptz = len(presets) > 0
    coordinator = VIGICoordinator(hass, camera_api, has_ptz=has_ptz)
    await coordinator.async_config_entry_first_refresh()

    onvif_events = VIGIOnvifEvents(hass, ip, username, password, entry.entry_id)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": camera_api,
        "coordinator": coordinator,
        "onvif_events": onvif_events,
        "device_info": device_info,
        "has_ptz": has_ptz,
        "presets": presets,
        "ip": ip,
        "username": username,
        "password": password,
        "entry_id": entry.entry_id,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await onvif_events.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["onvif_events"].async_stop()
        await data["api"].close()
    return unloaded
