"""Image entity — last detection snapshot.

On cameras with Smart Frame capture enabled (and SD card formatted for image
storage), downloads the AI-cropped Smart Frame via WebSocket on each detection
event.

On cameras without Smart Frame support (e.g. VIGI C540V), falls back to
grabbing a still from the live RTSP stream when any detection event fires.

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
from .smart_frame import fetch_latest_smart_frame

_LOGGER = logging.getLogger(__name__)

_POST_EVENT_DELAY_SF = 3.0    # seconds to wait for Smart Frame SD card write
_POST_EVENT_DELAY_RTSP = 2.0  # seconds to wait for subject to enter RTSP frame


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.options.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS):
        return
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VIGILastDetectionImage(data["coordinator"], data)])


class VIGILastDetectionImage(VIGIEntity, ImageEntity):
    """Last detection snapshot — Smart Frame if supported, RTSP fallback otherwise."""

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
        self._has_smart_frames: bool = entry_data.get("has_smart_frames", False)

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
            if self._has_smart_frames:
                await self._grab_smart_frame(event)
            else:
                await self._grab_rtsp_snapshot(event)
        except Exception as exc:
            _LOGGER.debug("Detection image grab failed: %s", exc)
        finally:
            self._grabbing = False

    async def _grab_smart_frame(self, event: dict) -> None:
        event_type = event["type"]
        area = event.get("area")
        await asyncio.sleep(_POST_EVENT_DELAY_SF)
        try:
            ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        except Exception:
            ffmpeg_bin = "ffmpeg"
        result = await fetch_latest_smart_frame(
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
                "smart_frame_label": result["label"],
                "file_id": result["file_id"],
                "source": "smart_frame",
            }
            if area:
                attrs["detection_zone"] = area
            self._attr_extra_state_attributes = attrs
            self.async_write_ha_state()
            _LOGGER.debug(
                "Smart Frame updated: %s / %s (%d bytes)",
                event_type, result["label"], len(result["jpeg"]),
            )
        else:
            _LOGGER.debug(
                "No Smart Frame for %s event — Smart Frame capture enabled in camera settings?",
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
