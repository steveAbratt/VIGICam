"""VIGI camera data update coordinator — polls all state every 30 s."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VIGICoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, camera: VIGICamera, has_ptz: bool = False
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.camera = camera
        self.has_ptz = has_ptz
        self.presets: list[dict] = []

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch_all()
        except VIGIAuthError as exc:
            raise UpdateFailed(f"Authentication error: {exc}") from exc
        except VIGIError as exc:
            raise UpdateFailed(f"Camera communication error: {exc}") from exc

    async def _fetch_all(self) -> dict[str, Any]:
        results: dict[str, Any] = {}

        async def safe_get(key: str, coro):
            try:
                results[key] = await coro
            except VIGIError as exc:
                _LOGGER.debug("Failed to fetch %s: %s", key, exc)
                results[key] = {}

        await safe_get("device_info", self.camera.get_device_info())
        await safe_get("motion", self.camera.get_motion_detection())
        await safe_get("image_switch", self.camera.get_image_switch())
        await safe_get("alarm", self.camera.get_alarm())
        await safe_get("led", self.camera.get_led())
        await safe_get("speaker", self.camera.get_audio_speaker())
        await safe_get("microphone", self.camera.get_audio_microphone())
        await safe_get("storage", self.camera.get_storage())
        await safe_get("tamper", self.camera.get_tamper())

        if self.has_ptz:
            if not self.presets:
                try:
                    self.presets = await self.camera.get_presets()
                except VIGIError:
                    self.presets = []
            results["presets"] = self.presets

        return results
