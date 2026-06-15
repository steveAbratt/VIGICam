"""Camera platform — RTSP stream via HA's stream integration."""
from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VIGICoordinator
from .entity import VIGIEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VIGIRTSPCamera(data["coordinator"], data)])


class VIGIRTSPCamera(VIGIEntity, Camera):
    """Live RTSP stream from the camera.

    stream_source → stream1 (HD, used when the user taps to go live).
    async_camera_image → stream2 (sub-stream, used for dashboard thumbnails
    and history snapshots). The sub-stream is much lower bitrate so thumbnail
    grabs are significantly lighter on the host CPU.
    """

    _attr_name = "Stream"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: VIGICoordinator, entry_data: dict) -> None:
        VIGIEntity.__init__(self, coordinator, entry_data)
        Camera.__init__(self)
        base = f"rtsp://{entry_data['username']}:{entry_data['password']}@{entry_data['ip']}:554"
        self._stream_url = f"{base}/stream1"
        self._snapshot_url = f"{base}/stream2"

    @property
    def _unique_id_suffix(self) -> str:
        return "stream"

    async def stream_source(self) -> str | None:
        return self._stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Grab a thumbnail from the sub-stream (stream2) — lighter than the HD stream."""
        manager = get_ffmpeg_manager(self.hass)
        return await manager.get_image(self._snapshot_url, extra_cmd="-pred 1")
