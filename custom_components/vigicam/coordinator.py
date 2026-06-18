"""VIGI camera data update coordinator — polls all state every 30 s."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

if TYPE_CHECKING:
    from .openapi import VIGIOpenAPI

_LOGGER = logging.getLogger(__name__)

# How many coordinator ticks between OpenAPI re-checks when has_openapi=False.
# 10 ticks × 30 s = 5 minutes.
_OPENAPI_RECHECK_TICKS = 10


class VIGICoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        camera: VIGICamera,
        *,
        has_ptz: bool = False,
        has_sd_card: bool = False,
        has_openapi: bool = False,
        openapi: "VIGIOpenAPI | None" = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.camera = camera
        self.has_ptz = has_ptz
        self.has_sd_card = has_sd_card
        self.has_openapi = has_openapi
        self.openapi: VIGIOpenAPI | None = openapi
        self.presets: list[dict] = []
        self.last_preset: str | None = None  # last preset selected from HA; None = unknown
        self._openapi_recheck_counter: int = 0

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
                _LOGGER.debug("Could not fetch %s: %s", key, exc)
                results[key] = {}
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Unexpected error fetching %s: %s", key, exc)
                results[key] = {}

        await safe_get("device_info", self.camera.get_device_info())
        await safe_get("motion", self.camera.get_motion_detection())
        await safe_get("image_switch", self.camera.get_image_switch())
        await safe_get("image_common", self.camera.get_image_common())
        await safe_get("alarm", self.camera.get_alarm())
        await safe_get("led", self.camera.get_led())
        await safe_get("speaker", self.camera.get_audio_speaker())
        await safe_get("microphone", self.camera.get_audio_microphone())
        await safe_get("storage", self.camera.get_storage())
        await safe_get("tamper", self.camera.get_tamper())
        await safe_get("network", self.camera.get_network())
        await safe_get("lens_mask", self.camera.get_lens_mask())

        if self.has_ptz:
            if not self.presets:
                try:
                    self.presets = await self.camera.get_presets()
                except VIGIError:
                    self.presets = []
            results["presets"] = self.presets
            await safe_get("target_track", self.camera.get_target_track())

        if self.has_openapi and self.openapi:
            async def safe_openapi(key: str, method: str) -> None:
                try:
                    raw = await self.openapi.call(method)
                    results[key] = raw.get("result", {})
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("OpenAPI %s failed: %s", method, exc)
                    results[key] = {}

            await safe_openapi("openapi_sd", "getSdCardStatus")
            await safe_openapi("openapi_device", "getDeviceStatus")
            await safe_openapi("openapi_people", "getPeopleDetectionSwitch")
            await safe_openapi("openapi_vehicle", "getVehicleDetectionSwitch")

        # Update has_sd_card from live storage data on every refresh.
        storage = results.get("storage", {})
        self.has_sd_card = _detect_sd_card(storage)

        # Periodically probe for OpenAPI becoming available.
        if not self.has_openapi:
            self._openapi_recheck_counter += 1
            if self._openapi_recheck_counter >= _OPENAPI_RECHECK_TICKS:
                self._openapi_recheck_counter = 0
                await self._recheck_openapi()

        return results

    async def _recheck_openapi(self) -> None:
        from .openapi import VIGIOpenAPI, try_openapi

        ip = self.camera._ip
        if await try_openapi(ip, self.camera._username, self.camera._password):
            self.has_openapi = True
            self.openapi = VIGIOpenAPI(ip, self.camera._username, self.camera._password)
            _LOGGER.info(
                "OpenAPI is now available on %s — reload the VIGICam integration "
                "to enable Vehicle Detection and other OpenAPI sensors",
                ip,
            )


def _detect_sd_card(storage: dict) -> bool:
    """Return True only when a usable SD card is present."""
    return storage.get("status", "").lower() in {"normal", "full"}
