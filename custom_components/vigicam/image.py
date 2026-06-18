"""Image entity — last detection snapshot.

On cameras that support event image capture (SD card with a capture partition,
and Upload Capture enabled in event settings), downloads the most recent
event image via WebSocket on each detection event. The image is a full-frame
still saved by the camera at the exact moment of detection.

On cameras without event image capture support (e.g. VIGI C540V, or cameras
where the SD card has not been formatted with a capture partition), falls back
to grabbing a still from the live RTSP stream when any detection event fires.
The RTSP grab runs ~2 seconds after the notification arrives, so it may
occasionally miss fast-moving subjects.

The grab runs in the background so it does not block the event dispatcher.
Only one concurrent grab per camera is allowed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS, DOMAIN
from .entity import VIGIEntity
from .onvif_events import SIGNAL_VIGICAM_EVENT
from .event_image import fetch_latest_event_image

_LOGGER = logging.getLogger(__name__)

_POST_EVENT_DELAY_SF = 3.0    # seconds to wait for event image SD card write
_POST_EVENT_DELAY_RTSP = 2.0  # seconds to wait for subject to enter RTSP frame


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.options.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS):
        return
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VIGILastDetectionImage(data["coordinator"], data)])


class VIGILastDetectionImage(VIGIEntity, ImageEntity):
    """Last detection snapshot — event image from SD card if supported, RTSP fallback otherwise."""

    _attr_name = "Last Detection"
    _attr_icon = "mdi:image-search"

    def __init__(self, coordinator, entry_data: dict) -> None:
        VIGIEntity.__init__(self, coordinator, entry_data)
        ImageEntity.__init__(self, coordinator.hass)
        self._cached_image: bytes | None = None
        self._image_last_updated: datetime | None = None
        self._grabbing = False
        self._unsub_dispatcher: object = None
        self._attr_extra_state_attributes: dict = {}
        self._has_event_capture: bool = entry_data.get("has_event_capture", False)

    @property
    def _unique_id_suffix(self) -> str:
        return "last_detection"

    @property
    def image_last_updated(self) -> datetime | None:
        return self._image_last_updated

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            SIGNAL_VIGICAM_EVENT.format(self._entry_data["entry_id"]),
            self._handle_event,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_dispatcher:
            self._unsub_dispatcher()

    @callback
    def _handle_event(self, event: dict) -> None:
        if not event.get("active"):
            return
        if self._grabbing:
            return
        self._grabbing = True
        self.hass.async_create_task(self._grab_frame(event))

    async def _grab_frame(self, event: dict) -> None:
        try:
            if self._has_event_capture:
                await self._grab_event_image(event)
            else:
                await self._grab_rtsp_snapshot(event)
        except Exception as exc:
            _LOGGER.debug("Detection image grab failed: %s", exc)
        finally:
            self._grabbing = False

    async def _grab_event_image(self, event: dict) -> None:
        event_type = event["type"]
        area = event.get("area")
        await asyncio.sleep(_POST_EVENT_DELAY_SF)
        try:
            ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        except Exception:
            ffmpeg_bin = "ffmpeg"
        result = await fetch_latest_event_image(
            ip=self._entry_data["ip"],
            username=self._entry_data["username"],
            password=self._entry_data["password"],
            camera=self._entry_data["api"],
            ffmpeg_bin=ffmpeg_bin,
        )
        if result:
            self._cached_image = result["jpeg"]
            self._image_last_updated = datetime.now(timezone.utc)
            attrs: dict = {
                "detection_type": event_type,
                "event_label": result["label"],
                "file_id": result["file_id"],
                "source": "event_capture",
            }
            if area:
                attrs["detection_zone"] = area
            self._attr_extra_state_attributes = attrs
            self.async_write_ha_state()
            _LOGGER.debug(
                "Event image captured: %s / %s (%d bytes)",
                event_type, result["label"], len(result["jpeg"]),
            )
        else:
            _LOGGER.debug(
                "No event image for %s — Upload Capture enabled in camera event settings?",
                event_type,
            )

    async def _grab_rtsp_snapshot(self, event: dict) -> None:
        event_type = event["type"]
        area = event.get("area")
        await asyncio.sleep(_POST_EVENT_DELAY_RTSP)
        try:
            ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        except Exception:
            ffmpeg_bin = "ffmpeg"
        ip = self._entry_data["ip"]
        user = self._entry_data["username"]
        pw = self._entry_data["password"]
        stream_url = f"rtsp://{user}:{pw}@{ip}:554/stream1"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-rtsp_transport", "tcp",
            "-i", stream_url,
            "-frames:v", "1",
            "-f", "image2", "-vcodec", "mjpeg",
            "pipe:1",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if stdout and proc.returncode == 0:
            self._cached_image = stdout
            self._image_last_updated = datetime.now(timezone.utc)
            attrs: dict = {
                "detection_type": event_type,
                "source": "rtsp_snapshot",
            }
            if area:
                attrs["detection_zone"] = area
            self._attr_extra_state_attributes = attrs
            self.async_write_ha_state()
            _LOGGER.debug(
                "RTSP snapshot captured for %s (%d bytes)", event_type, len(stdout)
            )
        else:
            _LOGGER.debug(
                "RTSP snapshot returned no data for %s — stream accessible?", event_type
            )
            _LOGGER.debug(
                "RTSP snapshot URL was: rtsp://%s:***@%s:554/stream1", user, ip
            )

    async def async_image(self) -> bytes | None:
        return self._cached_image
