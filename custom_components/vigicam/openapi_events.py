"""OpenAPI subscribeMsg event listener for VIGI cameras.

Maintains a persistent HTTP connection to port 20443 and parses the
multipart/mixed stream of named detection events. Dispatches HA signals
using the same SIGNAL_VIGICAM_EVENT format as ONVIF, so existing binary
sensor entities handle them without modification.

ONVIF handles: Motion, Person, Tamper, Intrusion, LineCrossing, SmartDetection.
subscribeMsg handles: Vehicle, AudioAnomaly, Loitering, SceneChange,
ObjectLeftTaken, AreaEntry, AreaExit — types that ONVIF bundles together as
the SmartDetection catch-all.

Note: msg_push_enabled must be "on" for each detection type (set per camera
in the detection switch settings). The integration checks and enables these
on first connection for the types it handles.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .onvif_events import SIGNAL_VIGICAM_EVENT
from .openapi import VIGIOpenAPIError

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

# Detection types we ensure have msg_push_enabled="on" on first connection.
# Format: (getter_method, setter_method)
_MSG_PUSH_METHODS: tuple[tuple[str, str], ...] = (
    ("getVehicleDetectionSwitch",      "setVehicleDetectionSwitch"),
    ("getAudioAnomalyDetectionSwitch", "setAudioAnomalyDetectionSwitch"),
    ("getLoiterDetectionSwitch",       "setLoiterDetectionSwitch"),
    ("getSceneChangeDetectionSwitch",  "setSceneChangeDetectionSwitch"),
    ("getAreaEntryDetectionSwitch",    "setAreaEntryDetectionSwitch"),
    ("getAreaLeaveDetectionSwitch",    "setAreaLeaveDetectionSwitch"),
    ("getDropAndTakeDetectionSwitch",  "setDropAndTakeDetectionSwitch"),
)


class VIGIOpenAPIEventListener:
    """Persistent subscribeMsg connection for a single VIGI camera."""

    def __init__(
        self, hass: HomeAssistant, openapi: "VIGIOpenAPI", entry_id: str
    ) -> None:
        self._hass = hass
        self._openapi = openapi
        self._entry_id = entry_id
        self._task: asyncio.Task | None = None
        self._msg_push_initialised = False

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
                if not self._msg_push_initialised:
                    await self._ensure_msg_push()
                    self._msg_push_initialised = True
                await self._connect_and_stream()
                backoff = 5
            except asyncio.CancelledError:
                return
            except Exception as exc:
                _LOGGER.warning(
                    "OpenAPI event stream dropped for %s: %s — reconnecting in %ds",
                    self._openapi._ip, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    # ── msg_push initialisation ───────────────────────────────────────────────

    async def _ensure_msg_push(self) -> None:
        """Enable msg_push_enabled for each detection type we handle."""
        for getter, setter in _MSG_PUSH_METHODS:
            try:
                result = await self._openapi.call(getter)
                if result.get("errCode", -1) != 0:
                    continue  # method not supported on this model (-10030 etc.)
                # Response may nest under a key; search top level first, then one level deep.
                push_val = result.get("msg_push_enabled")
                if push_val is None:
                    for val in result.values():
                        if isinstance(val, dict):
                            push_val = val.get("msg_push_enabled")
                            if push_val is not None:
                                break
                if push_val != "on":
                    await self._openapi.call(setter, {"msg_push_enabled": "on"})
                    _LOGGER.debug(
                        "OpenAPI: enabled msg_push for %s on %s", getter, self._openapi._ip
                    )
            except Exception as exc:
                _LOGGER.debug("OpenAPI: could not check/enable msg_push for %s: %s", getter, exc)

    # ── Streaming connection ──────────────────────────────────────────────────

    async def _connect_and_stream(self) -> None:
        stok = await self._openapi._ensure_stok()
        url = f"{self._openapi._base}/stok={stok}"
        body = {"method": "subscribeMsg", "params": {"event_type": ["all"], "heartbeat": 15}}

        # Persistent connection — must NOT use force_close.
        conn = aiohttp.TCPConnector(ssl=self._openapi._ssl)
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=None)

        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                if resp.status != 200:
                    raise VIGIOpenAPIError(f"subscribeMsg returned HTTP {resp.status}")
                _LOGGER.debug("OpenAPI event stream connected for %s", self._openapi._ip)
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
                    self._openapi._ip, line[:120],
                )
                continue

            if "Heartbeat" in data:
                _LOGGER.debug("OpenAPI heartbeat from %s", self._openapi._ip)
            elif "event_type" in data:
                _LOGGER.debug(
                    "OpenAPI raw event from %s: %s", self._openapi._ip, data
                )
                self._dispatch(data["event_type"])

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, event_type: str) -> None:
        mapped = _EVENT_TYPE_MAP.get(event_type)
        if mapped is None:
            if event_type not in _EVENT_TYPE_MAP:
                _LOGGER.debug(
                    "OpenAPI: unknown event type '%s' from %s — add to _EVENT_TYPE_MAP",
                    event_type, self._openapi._ip,
                )
            return
        _LOGGER.debug(
            "OpenAPI event: %s → %s on %s", event_type, mapped, self._openapi._ip
        )
        async_dispatcher_send(
            self._hass,
            SIGNAL_VIGICAM_EVENT.format(self._entry_id),
            {"type": mapped, "active": True},
        )
