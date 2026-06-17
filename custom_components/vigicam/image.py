"""Image entity — last Smart Frame detection image.

Downloads the most recent AI-cropped Smart Frame from the camera via WebSocket
each time an ONVIF detection event fires, and caches it as an HA ImageEntity.

Smart Frame capture must be enabled in the camera (Event → Smart Frame) and an
SD card must be present. If neither is available the entity will never populate,
which is logged at debug level.

The grab runs in the background so it does not block the event dispatcher.
A 3-second delay is applied after the event fires to allow the camera time to
write the captured frame to the SD card before we try to fetch it.
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

from .const import DOMAIN
from .entity import VIGIEntity
from .onvif_events import SIGNAL_VIGICAM_EVENT
from .smart_frame import fetch_latest_smart_frame

_LOGGER = logging.getLogger(__name__)

_POST_EVENT_DELAY = 3.0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    if not data.get("has_smart_frames"):
        _LOGGER.debug(
            "Smart Frame not supported on this camera — Last Detection image entity not registered"
        )
        return
    async_add_entities([VIGILastDetectionImage(data["coordinator"], data)])


class VIGILastDetectionImage(VIGIEntity, ImageEntity):
    """AI-cropped Smart Frame captured at the moment of detection."""

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
        self.hass.async_create_task(self._grab_frame(event["type"]))

    async def _grab_frame(self, event_type: str) -> None:
        try:
            await asyncio.sleep(_POST_EVENT_DELAY)
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
                self._attr_extra_state_attributes = {
                    "detection_type": event_type,
                    "smart_frame_label": result["label"],
                    "file_id": result["file_id"],
                }
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
        except Exception as exc:
            _LOGGER.debug("Smart Frame fetch failed: %s", exc)
        finally:
            self._grabbing = False

    async def async_image(self) -> bytes | None:
        return self._cached_image
