"""Camera platform — RTSP stream via HA's stream integration."""
from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
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
    """Live RTSP stream from the camera (HD stream1)."""

    _attr_name = "Stream"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: VIGICoordinator, entry_data: dict) -> None:
        VIGIEntity.__init__(self, coordinator, entry_data)
        Camera.__init__(self)
        self._stream_url = (
            f"rtsp://{entry_data['username']}:{entry_data['password']}"
            f"@{entry_data['ip']}:554/stream1"
        )

    @property
    def _unique_id_suffix(self) -> str:
        return "stream"

    async def stream_source(self) -> str | None:
        return self._stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        # No direct snapshot endpoint on VIGI cameras; HA will grab a frame
        # from the RTSP stream via ffmpeg when a snapshot is requested.
        return None
