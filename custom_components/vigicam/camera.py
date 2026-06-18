"""Camera platform — RTSP stream via HA's stream integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM, DOMAIN
from .coordinator import VIGICoordinator
from .entity import VIGIEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if not entry.options.get(CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM):
        return
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VIGIRTSPCamera(data["coordinator"], data)])


class VIGIRTSPCamera(VIGIEntity, Camera):
    """Live RTSP stream from the camera.

    stream_source  → stream1 (HD main stream, used for live view).
    async_camera_image → stream2 (sub-stream, lower bitrate dashboard thumbnails).
    """

    _attr_name = "Stream"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: VIGICoordinator, entry_data: dict) -> None:
        VIGIEntity.__init__(self, coordinator, entry_data)
        Camera.__init__(self)
        ip = entry_data["ip"]
        user = entry_data["username"]
        password = entry_data["password"]
        self._stream_url = f"rtsp://{user}:{password}@{ip}:554/stream1"
        self._snapshot_url = f"rtsp://{user}:{password}@{ip}:554/stream2"
        self._redacted_snapshot_url = f"rtsp://{user}:***@{ip}:554/stream2"

    @property
    def _unique_id_suffix(self) -> str:
        return "stream"

    async def stream_source(self) -> str | None:
        return self._stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-rtsp_transport", "tcp",
                "-i", self._snapshot_url,
                "-frames:v", "1",
                "-f", "image2", "-vcodec", "mjpeg",
                "pipe:1",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return stdout if proc.returncode == 0 and stdout else None
        except Exception as exc:
            safe = str(exc).replace(self._snapshot_url, self._redacted_snapshot_url)
            _LOGGER.debug("Snapshot grab failed: %s", safe)
            return None
