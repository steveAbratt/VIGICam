"""VIGI & InSight Camera integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import DOMAIN
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

    camera_api = VIGICamera(ip, username, password)
    try:
        await camera_api.authenticate()
        device_info = await camera_api.get_device_info()
        presets = await camera_api.get_presets()
    except VIGIAuthError as exc:
        await camera_api.close()
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except VIGIError as exc:
        await camera_api.close()
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
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()
    return unloaded
