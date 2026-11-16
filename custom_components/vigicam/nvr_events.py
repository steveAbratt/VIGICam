"""Receive the VIGI NVR's event pushes through a Home Assistant webhook.

The NVR OpenAPI does not offer polling or a stream for events: it POSTs each one to an
"event server" (up to three slots) configured on the NVR. On setup the integration
registers a local-only webhook and points one free slot at it, keeping any slot that is
already in use by something else. The slot is released when the entry is deleted.

Every message is fired on the bus as `vigicam_nvr_event` (so codes this integration has
no entity for can still drive automations) and dispatched to the channel's sensors.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (
    CONF_NVR_EVENT_HOST,
    CONF_WEBHOOK_ID,
    DEFAULT_NVR_EVENT_HOST,
    DOMAIN,
    EVENT_NVR,
    REPAIRS_NVR_EVENT_SERVER,
    SIGNAL_NVR_EVENT,
)
from .nvr_api import VIGINVR, extract_event_json, parse_event

_LOGGER = logging.getLogger(__name__)

EVENT_SERVER_SLOTS = (1, 2, 3)


def webhook_path(webhook_id: str) -> str:
    return f"/api/webhook/{webhook_id}"


def event_target(hass: HomeAssistant, override: str = "") -> tuple[str, int, bool]:
    """(host, port, https) the NVR should post to. Raises NoURLAvailableError.

    The override is for setups where HA's internal URL is not an address the NVR can
    reach (a Docker bridge address, say): "192.168.1.10:8123" or a full URL.
    """
    raw = override.strip() or get_url(
        hass, allow_internal=True, allow_external=False, allow_cloud=False, prefer_external=False
    )
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.hostname:
        raise NoURLAvailableError
    https = parsed.scheme == "https"
    return parsed.hostname, parsed.port or (443 if https else 80), https


def pick_slot(servers: list[dict], path: str) -> tuple[int | None, bool]:
    """(slot, already_ours) for our webhook path. slot is None when all three are taken."""
    used: set[int] = set()
    for server in servers:
        try:
            slot = int(server.get("id"))
        except (TypeError, ValueError):
            continue
        if str(server.get("url", "")).rstrip("/") == path:
            return slot, True
        if server.get("ip_or_domain"):
            used.add(slot)
    for slot in EVENT_SERVER_SLOTS:
        if slot not in used:
            return slot, False
    return None, False


async def async_configure_event_server(
    hass: HomeAssistant, entry: ConfigEntry, api: VIGINVR, name: str
) -> bool:
    """Point an NVR event-server slot at this entry's webhook. True when it is in place."""
    issue_id = f"{REPAIRS_NVR_EVENT_SERVER}_{entry.entry_id}"
    path = webhook_path(entry.data[CONF_WEBHOOK_ID])
    try:
        host, port, https = event_target(
            hass, entry.options.get(CONF_NVR_EVENT_HOST, DEFAULT_NVR_EVENT_HOST)
        )
    except NoURLAvailableError:
        _LOGGER.warning(
            "NVR %s: Home Assistant has no internal URL the NVR can post events to. "
            "Set one under Settings > System > Network, or set the event address in "
            "the NVR's options.", api.host,
        )
        _raise_issue(hass, issue_id, name, "no_url")
        return False

    try:
        servers = await api.event_servers()
        slot, ours = pick_slot(servers, path)
        if slot is None:
            _LOGGER.warning(
                "NVR %s: all three event-server slots are in use; not changing them. "
                "Free one on the NVR to receive events in Home Assistant.", api.host,
            )
            _raise_issue(hass, issue_id, name, "slots_full")
            return False
        current = next((s for s in servers if str(s.get("id")) == str(slot)), {})
        wanted = {
            "ip_or_domain": host,
            "port": port,
            "protocol": "HTTPS" if https else "HTTP",
        }
        if ours and all(str(current.get(k, "")).upper() == str(v).upper() for k, v in wanted.items()):
            _LOGGER.debug("NVR %s: event server slot %s already points at HA", api.host, slot)
        else:
            await api.set_event_server(slot, host, port, path, https=https, pictures=False)
            _LOGGER.info(
                "NVR %s: event server slot %s now posts to %s://%s:%s%s",
                api.host, slot, "https" if https else "http", host, port, "/api/webhook/***",
            )
    except Exception as exc:  # noqa: BLE001 - events are optional; setup must go on
        _LOGGER.warning("NVR %s: could not configure the event server: %s", api.host, exc)
        _raise_issue(hass, issue_id, name, "failed")
        return False

    ir.async_delete_issue(hass, DOMAIN, issue_id)
    return True


async def async_remove_event_server(api: VIGINVR, webhook_id: str) -> None:
    """Free every slot that points at this webhook. Errors are logged, not raised."""
    path = webhook_path(webhook_id)
    try:
        for server in await api.event_servers():
            if str(server.get("url", "")).rstrip("/") == path:
                await api.delete_event_server(int(server["id"]))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("NVR %s: could not remove the event server: %s", api.host, exc)


def _raise_issue(hass: HomeAssistant, issue_id: str, name: str, reason: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=f"nvr_event_server_{reason}",
        translation_placeholders={"name": name},
    )


def async_register_webhook(hass: HomeAssistant, entry: ConfigEntry, name: str) -> None:
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    async def _handle(hass: HomeAssistant, _webhook_id: str, request: web.Request) -> web.Response:
        body = await request.read()
        event = extract_event_json(body, request.headers.get("Content-Type", ""))
        if event is None:
            _LOGGER.debug("NVR %s: push with no event JSON (%d bytes)", name, len(body))
            return web.Response(status=200)
        handle_event(hass, entry.entry_id, name, event)
        return web.Response(status=200)

    webhook.async_register(
        hass, DOMAIN, f"VIGI NVR {name}", webhook_id, _handle,
        local_only=True, allowed_methods=["POST"],
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))


def handle_event(hass: HomeAssistant, entry_id: str, name: str, event: dict[str, Any]) -> None:
    device = event.get("device_name") or event.get("dev_name") or name
    for msg in parse_event(event):
        _LOGGER.debug("NVR %s event: %s", name, {k: v for k, v in msg.items() if k != "raw"})
        hass.bus.async_fire(
            EVENT_NVR,
            {
                "entry_id": entry_id,
                "nvr": device,
                "channel": msg["channel"],
                "channel_name": msg["raw"].get("channel_name"),
                "type": msg["type"],
                "sub_types": msg["sub_types"],
                "events": msg["keys"],
                "exception": msg["exception"],
                "localtime": msg["localtime"],
            },
        )
        if msg["keys"] and msg["channel"] is not None:
            async_dispatcher_send(
                hass, SIGNAL_NVR_EVENT.format(entry_id), msg["channel"], msg["keys"]
            )
