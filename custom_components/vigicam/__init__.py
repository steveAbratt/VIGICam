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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": camera_api,
        "coordinator": coordinator,
        "device_info": device_info,
        "has_ptz": has_ptz,
        "presets": presets,
        "ip": ip,
        "username": username,
        "password": password,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # api.close() only cleans up sessions the API created itself;
        # sessions provided by HA helpers are managed by HA.
        await hass.data[DOMAIN].pop(entry.entry_id)["api"].close()
    return unloaded
