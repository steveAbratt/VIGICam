"""Set up, unload and remove a VIGI NVR config entry.

An NVR entry is a separate path through the integration: one NVR device, one child
device per channel, all reached through the NVR's OpenAPI. The camera code paths are
untouched. Platform modules hand NVR entries to nvr_entities.py.
"""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_DEVICE_TYPE,
    CONF_NVR_EVENT_PUSH,
    CONF_WEBHOOK_ID,
    DEFAULT_NVR_EVENT_PUSH,
    DEVICE_TYPE_NVR,
    DOMAIN,
    REPAIRS_NVR_EVENT_SERVER,
)
from .nvr_api import VIGINVR, VIGINVRAuthError, VIGINVRError
from .nvr_coordinator import VIGINVRCoordinator
from .nvr_events import (
    async_configure_event_server,
    async_register_webhook,
    async_remove_event_server,
)

_LOGGER = logging.getLogger(__name__)

NVR_PLATFORMS = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
]


def is_nvr_entry(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_NVR


async def async_setup_nvr_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    name = entry.data.get(CONF_NAME) or entry.title or host
    api = VIGINVR(host, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    entry.async_on_unload(api.close)

    try:
        await api.login()
        modules = await api.module_list()
    except VIGINVRAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except (VIGINVRError, aiohttp.ClientError, TimeoutError) as exc:
        raise ConfigEntryNotReady(f"{type(exc).__name__}: {exc}") from exc
    _LOGGER.debug("NVR %s modules: %s", host, modules)

    coordinator = VIGINVRCoordinator(hass, api, modules, name)
    await coordinator.async_config_entry_first_refresh()

    events_on = entry.options.get(CONF_NVR_EVENT_PUSH, DEFAULT_NVR_EVENT_PUSH)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "device_type": DEVICE_TYPE_NVR,
        "api": api,
        "coordinator": coordinator,
        "modules": modules,
        "name": name,
        "ip": host,
        "events": events_on,
    }

    if events_on:
        async_register_webhook(hass, entry, name)
        await async_configure_event_server(hass, entry, api, name)
    else:
        ir.async_delete_issue(hass, DOMAIN, f"{REPAIRS_NVR_EVENT_SERVER}_{entry.entry_id}")

    await hass.config_entries.async_forward_entry_setups(entry, NVR_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Entities are built per channel at setup, so a camera added to or removed from the
    # NVR needs a reload to appear or go.
    known = set((coordinator.data or {}).get("devices") or {})

    @callback
    def _on_update() -> None:
        now = set((coordinator.data or {}).get("devices") or {})
        if coordinator.last_update_success and now and now != known:
            _LOGGER.info("NVR %s: channels changed (%s -> %s), reloading", host, sorted(known), sorted(now))
            known.clear()
            known.update(now)
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(coordinator.async_add_listener(_on_update))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_nvr_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, NVR_PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_remove_nvr_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Give the event-server slot back to the NVR, and clear this entry's repairs."""
    ir.async_delete_issue(hass, DOMAIN, f"{REPAIRS_NVR_EVENT_SERVER}_{entry.entry_id}")
    if not entry.data.get(CONF_WEBHOOK_ID):
        return
    api = VIGINVR(entry.data[CONF_HOST], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        await async_remove_event_server(api, entry.data[CONF_WEBHOOK_ID])
    finally:
        await api.close()
