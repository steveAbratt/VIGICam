"""OpenAPI subscribeMsg event listener for VIGI cameras.

Maintains a persistent HTTP connection to port 20443 and parses the
multipart/mixed stream of named detection events. Dispatches HA signals
using the same SIGNAL_VIGICAM_EVENT format as ONVIF, so existing binary
sensor entities handle them without modification.

ONVIF handles: Motion, Person, Tamper, Intrusion, LineCrossing, SmartDetection.
subscribeMsg handles: Vehicle, AudioAnomaly, Loitering, SceneChange,
ObjectLeftTaken, AreaEntry, AreaExit — types that ONVIF bundles together as
the SmartDetection catch-all.

Each detection type has a msg_push_enabled setting (Processing Mode → Push Notifications
in the VIGI app) that controls whether the camera pushes it. The integration reads it on
first connection and raises a Repairs notice for enabled types that have it off. It never writes it: that is the user's camera configuration, and up
to 0.7.5b9 the integration switched it on for all seven types, including ones the
user had turned off (#95).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, REPAIRS_PUSH_OFF
from .onvif_events import SIGNAL_VIGICAM_EVENT
from .openapi import VIGIOpenAPIAuthError, VIGIOpenAPIError, read_push_off

if TYPE_CHECKING:
    from .openapi import VIGIOpenAPI

_LOGGER = logging.getLogger(__name__)

# Camera sends a heartbeat every 15s; allow 3× before declaring the stream dead.
_HEARTBEAT_TIMEOUT = 45

# Maps OpenAPI event_type strings → internal type strings used by binary sensors.
# None = handled by ONVIF; suppress debug noise but don't dispatch.
_EVENT_TYPE_MAP: dict[str, str | None] = {
    "VehicleDetection":      "vehicle",
    "AudioAnomalyDetection": "audio_anomaly",
    "LoiterDetection":       "loitering",
    "SceneChangeDetection":  "scene_change",
    "DropAndTakeDetection":  "object_left_taken",
    "AreaEntryDetection":    "area_entry",
    "AreaLeaveDetection":    "area_exit",
    # Received from subscribeMsg but handled by ONVIF — mapped to None.
    "MotionDetection":       None,
    "PeopleDetection":       None,
    "TamperDetection":       None,
    "CrossLineDetection":    None,
    "InvasionDetection":     None,
}

# Attempts at the push-notification check before giving up on it for this load.
_MSG_PUSH_ATTEMPTS = 3


class VIGIOpenAPIEventListener:
    """Persistent subscribeMsg connection for a single VIGI camera."""

    def __init__(
        self, hass: HomeAssistant, openapi: "VIGIOpenAPI", entry_id: str, camera_name: str
    ) -> None:
        self._hass = hass
        self._camera_name = camera_name
        self._openapi = openapi
        self._entry_id = entry_id
        self._task: asyncio.Task | None = None
        self._msg_push_attempts = 0
        self._connected = False

    async def async_start(self) -> None:
        self._task = self._hass.async_create_background_task(
            self._run_loop(),
            f"vigicam_openapi_events_{self._entry_id}",
        )

    async def async_stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        backoff = 5
        while True:
            try:
                if self._msg_push_attempts < _MSG_PUSH_ATTEMPTS:
                    await self._check_msg_push()
                await self._connect_and_stream()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._connected:
                    # The stream only ends by raising, so a drop after a healthy connection
                    # starts the backoff again rather than carrying it from earlier failures.
                    self._connected = False
                    backoff = 5
                _LOGGER.warning(
                    "OpenAPI event stream dropped for %s: %s — reconnecting in %ds",
                    self._openapi.host, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    # ── msg_push check ────────────────────────────────────────────────────────

    async def _check_msg_push(self) -> None:
        """Raise or clear the Repairs notice for detection types with Push Notifications off.

        Read-only. A failure here must never stop the event stream connecting, so anything
        but an auth failure counts as an attempt and is otherwise ignored.
        """
        self._msg_push_attempts += 1
        try:
            off = await read_push_off(self._openapi)
        except VIGIOpenAPIAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("OpenAPI: push notification check failed on %s: %r", self._openapi.host, exc)
            return
        if off is None:
            return
        self._msg_push_attempts = _MSG_PUSH_ATTEMPTS
        issue_id = f"{REPAIRS_PUSH_OFF}_{self._entry_id}"
        if off:
            ir.async_create_issue(
                self._hass, DOMAIN, issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=REPAIRS_PUSH_OFF,
                translation_placeholders={"camera_name": self._camera_name, "types": ", ".join(off)},
            )
        else:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)

    # ── Streaming connection ──────────────────────────────────────────────────

    async def _connect_and_stream(self) -> None:
        url = await self._openapi.authed_url()
        body = {"method": "subscribeMsg", "params": {"event_type": ["all"], "heartbeat": 15}}

        # Persistent connection — must NOT use force_close.
        conn = aiohttp.TCPConnector(ssl=self._openapi.ssl_context)
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=None)

        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                if resp.status != 200:
                    raise VIGIOpenAPIError(f"subscribeMsg returned HTTP {resp.status}")
                _LOGGER.debug("OpenAPI event stream connected for %s", self._openapi.host)
                self._connected = True
                await self._read_stream(resp)

    async def _read_stream(self, resp: aiohttp.ClientResponse) -> None:
        while True:
            try:
                line = await asyncio.wait_for(
                    resp.content.readline(), timeout=_HEARTBEAT_TIMEOUT
                )
            except asyncio.TimeoutError:
                raise VIGIOpenAPIError("Heartbeat timeout — no data received in 45s")
            if not line:
                raise VIGIOpenAPIError("Stream closed by camera")

            line = line.strip()
            if not line:
                continue
            # Skip multipart boundary and header lines.
            if line.startswith(b"--") or line.lower().startswith(b"content-"):
                continue

            try:
                data = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                _LOGGER.debug(
                    "OpenAPI: unrecognised stream data from %s: %r",
                    self._openapi.host, line[:120],
                )
                continue

            if "Heartbeat" in data:
                _LOGGER.debug("OpenAPI heartbeat from %s", self._openapi.host)
            elif "event_type" in data:
                _LOGGER.debug(
                    "OpenAPI raw event from %s: %s", self._openapi.host, data
                )
                area: str | None = (
                    data.get("detection_area")
                    or data.get("area_name")
                    or data.get("rule_name")
                    or data.get("region_name")
                )
                self._dispatch(data["event_type"], area=area)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, event_type: str, area: str | None = None) -> None:
        mapped = _EVENT_TYPE_MAP.get(event_type)
        if mapped is None:
            if event_type not in _EVENT_TYPE_MAP:
                _LOGGER.debug(
                    "OpenAPI: unknown event type '%s' from %s — add to _EVENT_TYPE_MAP",
                    event_type, self._openapi.host,
                )
            return
        _LOGGER.debug(
            "OpenAPI event: %s → %s on %s", event_type, mapped, self._openapi.host
        )
        async_dispatcher_send(
            self._hass,
            SIGNAL_VIGICAM_EVENT.format(self._entry_id),
            {"type": mapped, "active": True, "area": area, "source": "openapi"},
        )
