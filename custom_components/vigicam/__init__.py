"""VIGI & InSight Camera integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import CONF_VERIFY_SSL, DOMAIN
from .coordinator import VIGICoordinator
from .onvif_events import VIGIOnvifEvents
from .onvif_ptz import DEFAULT_SPEED, VIGIOnvifPtz

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

# ── Service schemas ────────────────────────────────────────────────────────────

_DIRECTIONS = ("left", "right", "up", "down", "zoom_in", "zoom_out")

_PTZ_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("direction"): vol.In(_DIRECTIONS),
    vol.Optional("speed", default=DEFAULT_SPEED): vol.All(
        vol.Coerce(float), vol.Range(min=0.0, max=1.0)
    ),
    vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=60.0)),
})

_PTZ_STOP_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
})

_GOTO_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("preset"): cv.string,
})

_DIRECTION_VECTORS: dict[str, tuple[float, float, float]] = {
    "left":     (-1.0, 0.0, 0.0),
    "right":    ( 1.0, 0.0, 0.0),
    "up":       ( 0.0, 1.0, 0.0),
    "down":     ( 0.0,-1.0, 0.0),
    "zoom_in":  ( 0.0, 0.0, 1.0),
    "zoom_out": ( 0.0, 0.0,-1.0),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry_data_for_entity(hass: HomeAssistant, entity_id: str) -> dict | None:
    """Return entry_data for the config entry that owns *entity_id*, or None."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry and entry.config_entry_id:
        return hass.data[DOMAIN].get(entry.config_entry_id)
    return None


# ── Service handlers ──────────────────────────────────────────────────────────

def _register_services(hass: HomeAssistant) -> None:
    """Register domain services once (guarded so re-entry is a no-op)."""
    if hass.services.has_service(DOMAIN, "ptz"):
        return

    async def handle_ptz(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("onvif_ptz"):
            _LOGGER.error(
                "vigicam.ptz: no PTZ support for %s", call.data["entity_id"]
            )
            return
        ptz: VIGIOnvifPtz = data["onvif_ptz"]
        direction = call.data["direction"]
        speed = call.data["speed"]
        duration = call.data.get("duration")
        pan_v, tilt_v, zoom_v = _DIRECTION_VECTORS[direction]
        await ptz.continuous_move(pan_v * speed, tilt_v * speed, zoom_v * speed)
        if duration:
            await asyncio.sleep(duration)
            await ptz.stop()

    async def handle_ptz_stop(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("onvif_ptz"):
            return
        await data["onvif_ptz"].stop()

    async def handle_goto_preset(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error(
                "vigicam.goto_preset: cannot find camera for %s",
                call.data["entity_id"],
            )
            return
        preset_name = call.data["preset"]
        presets: list[dict] = data.get("presets", [])
        preset = next((p for p in presets if p["name"] == preset_name), None)
        if not preset:
            _LOGGER.error(
                "vigicam.goto_preset: preset '%s' not found (available: %s)",
                preset_name, [p["name"] for p in presets],
            )
            return
        await data["api"].goto_preset(preset["id"])

    hass.services.async_register(DOMAIN, "ptz", handle_ptz, schema=_PTZ_SCHEMA)
    hass.services.async_register(DOMAIN, "ptz_stop", handle_ptz_stop, schema=_PTZ_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, "goto_preset", handle_goto_preset, schema=_GOTO_PRESET_SCHEMA)


# ── Setup / teardown ──────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ip = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)

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
    onvif_ptz = VIGIOnvifPtz(ip, username, password) if has_ptz else None

    coordinator = VIGICoordinator(hass, camera_api, has_ptz=has_ptz)
    await coordinator.async_config_entry_first_refresh()

    onvif_events = VIGIOnvifEvents(hass, ip, username, password, entry.entry_id)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": camera_api,
        "coordinator": coordinator,
        "onvif_events": onvif_events,
        "onvif_ptz": onvif_ptz,
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
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["onvif_events"].async_stop()
        if data.get("onvif_ptz"):
            await data["onvif_ptz"].close()
        await data["api"].close()
    return unloaded
